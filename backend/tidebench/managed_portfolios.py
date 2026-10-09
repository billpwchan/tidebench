"""Durable shared-capital multi-leg local-paper controller.

Targets, command payloads and fills have separate identities. A restart first
reconciles already committed command keys, then uses fresh quotes for unfilled
commands. Multi-leg fills are explicitly sequential and never atomic.
"""

import asyncio
import json
from contextlib import AsyncExitStack, nullcontext
from decimal import Decimal, localcontext

from .account_capital import AccountCapital
from .catalog import CATALOG_BARS
from .engine import ACCOUNTING_CONTEXT, StrategyConfig
from .platform import PlatformError
from .portfolio_construction import apply_weight_caps, construction_weights, funding_carry_evidence
from .portfolio_execution import (
    compensation_quantities,
    portfolio_commands,
    portfolio_residuals,
    require_addition_legs,
    require_compensation_flat,
    require_residual_limit,
)
from .portfolio_risk import constrain_risk_weights, risk_momentum_weights
from .portfolio_targets import addition_plan, reduction_plan, target_quantities
from .pro_execution import base_size, number
from .store import dumps, encode, new_id, now_ms
from .strategy_program import ProStrategyInput
from .strategy_registry import digest
from .strategy_risk import risk_notional

D = Decimal
ACTIVE = {"running", "compensating"}


def verified(row, key, hash_key):
    body = json.loads(row[key])
    if digest(body) != row[hash_key]:
        raise PlatformError(
            "portfolio_evidence_integrity", "Managed portfolio evidence failed its content check.", 409
        )
    return body


class ManagedPortfolios:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.capital = getattr(runtime.book, "capital", None) or AccountCapital(
            self.store, runtime.book.contributions
        )
        runtime.book.capital = self.capital
        with self.store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS managed_portfolios(id TEXT PRIMARY KEY,source TEXT NOT NULL,version_id TEXT NOT NULL REFERENCES portfolio_versions(id),release_id TEXT NOT NULL REFERENCES portfolio_releases(id),manifest TEXT NOT NULL,manifest_hash TEXT NOT NULL,status TEXT NOT NULL,anchor_bar INTEGER,last_bar INTEGER,last_error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS managed_portfolios_active ON managed_portfolios(status,source);
                CREATE TABLE IF NOT EXISTS portfolio_batches(id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES managed_portfolios(id),bar INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL,additions TEXT,additions_hash TEXT,residuals TEXT,error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(group_id,bar));
                CREATE INDEX IF NOT EXISTS portfolio_batches_pending ON portfolio_batches(group_id,status,bar);
                CREATE TABLE IF NOT EXISTS portfolio_commands(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES portfolio_batches(id),phase TEXT NOT NULL,sequence INTEGER NOT NULL,deployment_id TEXT NOT NULL,key TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL,order_id TEXT,error TEXT,updated_at INTEGER NOT NULL,UNIQUE(batch_id,phase,sequence));
            """)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            self.capital.bootstrap(conn)

    @staticmethod
    def decode(row):
        try:
            body = verified(row, "manifest", "manifest_hash")
            if not isinstance(body, dict) or any(
                body.get(k) != row[k] for k in ("id", "source", "version_id", "release_id")
            ):
                raise ValueError("Manifest identity differs from its index")
            if (
                not isinstance(body.get("name"), str)
                or not isinstance(body.get("definition"), dict)
                or not isinstance(body.get("legs"), list)
            ):
                raise ValueError("Manifest structure is invalid")
        except (ValueError, TypeError, KeyError) as exc:
            raise PlatformError(
                "portfolio_evidence_integrity", "Managed portfolio evidence cannot be verified.", 409
            ) from exc
        return dict(row) | {"manifest": body}

    @classmethod
    def present(cls, row):
        """Expose a damaged record's trusted index, never its unverified body."""
        try:
            return cls.decode(row)
        except PlatformError as exc:
            return {
                **{
                    key: value for key, value in dict(row).items() if key not in {"manifest", "manifest_hash"}
                },
                "manifest": None,
                "integrity_error": {"code": exc.code, "message": exc.message},
            }

    def _presentation(self, row, conn, *, strict=False):
        group = self.decode(row) if strict else self.present(row)
        try:
            group["capital_commitment"] = self.capital.get(group["source"], "portfolio:" + group["id"], conn)
        except PlatformError as exc:
            if strict:
                raise
            group["capital_commitment"] = None
            group["capital_integrity_error"] = {"code": exc.code, "message": exc.message}
        group["attention"] = self._attention_projection(group, conn)
        return group

    def get(self, identifier, conn=None, *, strict=False):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute("SELECT * FROM managed_portfolios WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("managed_portfolio_missing", "Managed portfolio not found.", 404)
            return self._presentation(row, connection, strict=strict)

    def list(self, source=None):
        """All active/retained commitments, plus bounded terminal history."""
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT g.* FROM managed_portfolios g WHERE (? IS NULL OR g.source=?) "
                "AND (g.status IN ('running','compensating') OR EXISTS(SELECT 1 FROM account_capital_commitments c "
                "WHERE c.source=g.source AND c.owner='portfolio:'||g.id AND c.status!='released')) "
                "ORDER BY g.created_at DESC,g.id DESC",
                (source, source),
            ).fetchall()
            included = {row["id"] for row in rows}
            history = conn.execute(
                "SELECT * FROM managed_portfolios WHERE (? IS NULL OR source=?) "
                "AND status NOT IN ('running','compensating') ORDER BY created_at DESC,id DESC LIMIT 200",
                (source, source),
            ).fetchall()
            return [
                self._presentation(row, conn)
                for row in [*rows, *[r for r in history if r["id"] not in included]]
            ]

    def _attention_projection(self, group, conn):
        """Current economic condition; a historic failed status is not itself an incident."""
        owner, source = "portfolio:" + group["id"], group["source"]
        inventory, unknown, pending = [], False, False
        try:
            sleeves = [
                self.runtime.book.contributions.decode(row)
                for row in conn.execute(
                    "SELECT * FROM contribution_sleeves WHERE source=? AND owner=? ORDER BY inst_id",
                    (source, owner),
                )
            ]
            held = [(r["inst_id"], number(r["quantity"])) for r in sleeves if number(r["quantity"])]
        except PlatformError:
            # Ownership damage cannot be presented as a flat group.
            unknown, held = True, []
            symbols = [leg["inst_id"] for leg in (group.get("manifest") or {}).get("legs", [])]
            for row in conn.execute(
                "SELECT * FROM pro_positions WHERE source=? AND quantity!='0'", (source,)
            ):
                if row["inst_id"] in symbols:
                    held.append((row["inst_id"], number(row["quantity"])))
        for symbol, quantity in held:
            value, quote = None, self.runtime.snapshots.get((source, symbol))
            if quote and not unknown:
                try:
                    _, _, mark = self.runtime.book.fresh(quote)
                    value = abs(quantity) * base_size(quote["instrument"]) * mark
                except PlatformError:
                    pass
            inventory.append(
                {
                    "inst_id": symbol,
                    "quantity": str(quantity),
                    "market_value": str(value) if value is not None else None,
                }
            )
        symbols = {leg["inst_id"] for leg in (group.get("manifest") or {}).get("legs", [])}
        for obligation in self.runtime.book.deferred_funding.pending(source, conn):
            owners = obligation.get("owners")
            if owner in (owners or {}) or owners is None and obligation["inst_id"] in symbols:
                pending = True
        damaged = bool(group.get("integrity_error") or group.get("capital_integrity_error") or unknown)
        actionable = (
            group["status"] == "compensating"
            or group["status"] == "failed"
            and bool(group["last_error"])
            or group["status"] == "running"
            and bool(group["last_error"])
            or damaged
            or group["status"] not in ACTIVE
            and (bool(inventory) or pending)
        )
        if not actionable:
            return None
        batch = conn.execute(
            "SELECT * FROM portfolio_batches WHERE group_id=? ORDER BY bar DESC LIMIT 1", (group["id"],)
        ).fetchone()
        incident = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='ops_incidents'").fetchone():
            incident = conn.execute(
                "SELECT id,first_seen FROM ops_incidents WHERE kind='managed_portfolio' AND subject=? AND status!='resolved'",
                (group["id"],),
            ).fetchone()
        residual = None
        if batch and batch["residuals"]:
            try:
                residual = json.loads(batch["residuals"]).get("notional")
            except (TypeError, ValueError):
                damaged = True
        phase = (
            "integrity"
            if damaged
            else "compensating"
            if group["status"] == "compensating"
            else "retained_inventory"
            if group["status"] not in ACTIVE and inventory
            else "funding_pending"
            if pending or (group["last_error"] or "").startswith("[funding_pending] ")
            else "failed"
            if group["status"] == "failed"
            else "preparing"
        )
        error = (
            group["last_error"]
            or (group.get("integrity_error") or group.get("capital_integrity_error") or {}).get("message")
            or (batch["error"] if batch else None)
            or "Stopped portfolio retains unsettled economic obligations."
        )
        complete = not unknown and all(r["market_value"] is not None for r in inventory)
        return {
            "phase": phase,
            "since": incident["first_seen"]
            if incident
            else batch["created_at"]
            if batch
            else group["updated_at"],
            "as_of": now_ms(),
            "error": error,
            "inventory": inventory,
            "inventory_notional": str(sum((number(r["market_value"]) for r in inventory), D(0)))
            if complete
            else None,
            "valuation_status": "unavailable"
            if not complete
            else "example"
            if source == "example"
            else "fresh",
            "residual_notional": residual,
            "batch_id": batch["id"] if batch else None,
            "incident_id": incident["id"] if incident else None,
        }

    def active(self):
        """Complete scheduling metadata; each task verifies its own evidence."""
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT id,source,status,last_error,created_at,updated_at FROM managed_portfolios WHERE status IN ('running','compensating') ORDER BY created_at,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def attention(self):
        """Unbounded current economic faults, including terminal retained inventory."""
        with self.store.read() as conn:
            groups = [
                self._presentation(row, conn)
                for row in conn.execute("SELECT * FROM managed_portfolios ORDER BY created_at,id")
            ]
            return [group for group in groups if group["attention"] is not None]

    def activate(self, release, actor, conn):
        p = release["approval"]["preview"]
        identifier, timestamp = new_id(), now_ms()
        self.capital.reserve(
            conn,
            p["source"],
            "portfolio:" + identifier,
            p["definition"],
            actor,
            account=self.runtime.book.account(
                p["source"],
                {
                    symbol: snapshot
                    for (source, symbol), snapshot in self.runtime.snapshots.items()
                    if source == p["source"]
                },
                conn,
            ),
        )
        # The caller holds BEGIN IMMEDIATE across every ownership admission.
        # Any leg failure rolls back all deployments and the group together.
        legs = []
        for leg in p["definition"]["legs"]:
            config = leg | {
                "source": p["source"],
                "bar": p["definition"]["bar"],
                "allocation": leg["strategy"]["allocation"],
                "group_id": identifier,
                "portfolio_version_id": p["version_id"],
                "risk_policy_hash": p["risk_policy_hash"],
                "capital_policy_hash": p["capital_policy_hash"],
                "research_run_id": p["run_id"],
                "research_result_hash": p["result_hash"],
                "research_implementation": p["implementation"],
            }
            deployment = self.runtime.deploy(config, actor, conn=conn)
            legs.append(leg | {"deployment_id": deployment["id"]})
        manifest = {
            "id": identifier,
            "name": p["name"],
            "version_revision": p["version_revision"],
            "source": p["source"],
            "version_id": p["version_id"],
            "release_id": release["id"],
            "definition": p["definition"],
            "legs": legs,
            "risk_policy_hash": p["risk_policy_hash"],
            "capital_policy_hash": p["capital_policy_hash"],
            "implementation": p["implementation"],
            "result_hash": p["result_hash"],
            "activated_by": actor,
            "activated_at": timestamp,
        }
        conn.execute(
            "INSERT INTO managed_portfolios VALUES(?,?,?,?,?,?,'running',NULL,NULL,NULL,?,?)",
            (
                identifier,
                p["source"],
                p["version_id"],
                release["id"],
                dumps(manifest),
                digest(manifest),
                timestamp,
                timestamp,
            ),
        )
        self.store.audit(
            conn,
            p["source"],
            "portfolio.activated",
            "Reviewed portfolio activated with atomic market ownership",
            {"group_id": identifier, "release_id": release["id"], "actor": actor},
        )
        return self.get(identifier, conn)

    def stop(self, identifier, actor, conn=None):
        with nullcontext(conn) if conn is not None else self.store.write() as connection:
            group = self.get(identifier, connection)
            timestamp = now_ms()
            # Group ownership is stored independently in deployment configs
            # and durable commands. Stopping never trusts a damaged manifest.
            owned = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM pro_deployments WHERE source=? AND (CASE WHEN json_valid(config) THEN json_extract(config,'$.group_id') END=? OR id IN (SELECT c.deployment_id FROM portfolio_commands c JOIN portfolio_batches b ON b.id=c.batch_id WHERE b.group_id=?))",
                    (group["source"], identifier, identifier),
                )
            ]
            for deployment_id in owned:
                connection.execute(
                    "UPDATE pro_deployments SET status='stopped',updated_at=? WHERE id=? AND source=?",
                    (timestamp, deployment_id, group["source"]),
                )
            if group.get("integrity_error"):
                connection.execute(
                    "UPDATE managed_portfolios SET last_error=? WHERE id=?",
                    ("Portfolio evidence integrity: " + group["integrity_error"]["message"], identifier),
                )
            connection.execute(
                "UPDATE managed_portfolios SET status='stopped',updated_at=? WHERE id=?",
                (timestamp, identifier),
            )
            connection.execute(
                "UPDATE portfolio_batches SET status='canceled',error='Portfolio stopped; inventory retained',updated_at=? WHERE group_id=? AND status IN ('reducing','adding','compensating')",
                (timestamp, identifier),
            )
            connection.execute(
                "UPDATE portfolio_commands SET status='canceled',updated_at=? WHERE batch_id IN (SELECT id FROM portfolio_batches WHERE group_id=?) AND status='pending'",
                (timestamp, identifier),
            )
            self.capital.terminal(group["source"], "portfolio:" + identifier, connection)
            self.store.audit(
                connection,
                group["source"],
                "portfolio.stopped",
                "Portfolio stopped atomically; filled inventory retained",
                {
                    "group_id": identifier,
                    "actor": actor,
                    "deployment_ids": owned,
                    "integrity_error": group.get("integrity_error"),
                },
            )
            return self.get(identifier, connection)

    def batch(self, identifier, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute("SELECT * FROM portfolio_batches WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("portfolio_batch_missing", "Portfolio batch not found.", 404)
        body = verified(row, "body", "content_hash")
        if body["id"] != row["id"] or body["group_id"] != row["group_id"] or body["bar"] != row["bar"]:
            raise PlatformError(
                "portfolio_evidence_integrity", "Batch identity differs from its immutable target.", 409
            )
        result = dict(row) | {
            "body": body,
            "residuals": json.loads(row["residuals"]) if row["residuals"] else None,
        }
        result["additions"] = verified(row, "additions", "additions_hash") if row["additions"] else None
        return result

    def commands(self, batch_id, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            rows = connection.execute(
                "SELECT c.*,o.body order_body FROM portfolio_commands c LEFT JOIN pro_orders o ON o.key=c.key AND o.source=json_extract(c.payload,'$.source') WHERE batch_id=? ORDER BY CASE phase WHEN 'reduce' THEN 0 WHEN 'add' THEN 1 ELSE 2 END,sequence",
                (batch_id,),
            ).fetchall()
        return [
            dict(row)
            | {
                "payload": verified(row, "payload", "payload_hash"),
                "order": json.loads(row["order_body"]) if row["order_body"] else None,
            }
            for row in rows
        ]

    def history(self, identifier, limit=30, before=2**63 - 1):
        if not 1 <= limit <= 100 or not 0 <= before <= 2**63 - 1:
            raise PlatformError("portfolio_history_limit", "Invalid history limit or bar cursor.", 422)
        self.get(identifier)
        with self.store.read() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM portfolio_batches WHERE group_id=? AND bar<? ORDER BY bar DESC LIMIT ?",
                    (identifier, before, limit),
                )
            ]
        return [self.batch(i) | {"commands": self.commands(i)} for i in ids]

    def _commands(self, conn, batch, group, phase, quantities, quotes=None, policy=None):
        by_symbol = {leg["inst_id"]: leg for leg in group["manifest"]["legs"]}
        for command in portfolio_commands(phase, quantities, quotes, policy):
            sequence, symbol, quantity = command["sequence"], command["inst_id"], command["quantity"]
            leg = by_symbol[symbol]
            payload = {
                "source": group["source"],
                "inst_id": symbol,
                "side": "buy" if quantity > 0 else "sell",
                "quantity": str(abs(quantity)),
                "leverage": leg["leverage"],
                "reduce_only": phase != "add",
                "margin_mode": "isolated",
                "order_type": "market",
            }
            key = f"portfolio:{batch['id']}:{phase}:{sequence}:{batch['bar']}"
            conn.execute(
                "INSERT INTO portfolio_commands VALUES(?,?,?,?,?,?,?,?,'pending',NULL,NULL,?)",
                (
                    new_id(),
                    batch["id"],
                    phase,
                    sequence,
                    leg["deployment_id"],
                    key,
                    dumps(payload),
                    digest(payload),
                    now_ms(),
                ),
            )

    def _active(self, group_id, conn):
        group = self.get(group_id, conn, strict=True)
        if group["status"] not in ACTIVE:
            raise PlatformError(
                "portfolio_stopped", "The portfolio has stopped; filled inventory is retained.", 409
            )
        return group

    async def evaluate(self, identifier):
        r = self.runtime
        with localcontext(ACCOUNTING_CONTEXT):
            async with r.strategy_locks["portfolio:" + identifier]:
                group = self.get(identifier, strict=True)
                if group["status"] not in ACTIVE:
                    return
                if group["manifest"]["implementation"] != r.engine_identity["code_fingerprint"]:
                    raise PlatformError(
                        "portfolio_implementation_changed",
                        "Stop and review a new release under the installed implementation.",
                        409,
                    )
                with self.store.read() as conn:
                    row = conn.execute(
                        "SELECT id FROM portfolio_batches WHERE group_id=? AND status IN ('reducing','adding','compensating') ORDER BY bar LIMIT 1",
                        (identifier,),
                    ).fetchone()
                if row:
                    await self._resume(group, self.batch(row[0]))
                    return
                definition = group["manifest"]["definition"]
                interval = CATALOG_BARS[definition["bar"]]
                market_time = r.clock.now() if group["source"] == "example" else now_ms()
                end = market_time // interval * interval
                latest = end - interval
                if group["last_bar"] is not None and latest <= group["last_bar"]:
                    return
                deployments = {p["id"]: p for p in r.deployments(group["source"])}
                slots = asyncio.Semaphore(4)

                async def prepare(leg):
                    async with slots:
                        with self.store.read() as conn:
                            present = conn.execute(
                                "SELECT 1 FROM pro_strategy_intents WHERE deployment_id=? AND bar=?",
                                (leg["deployment_id"], latest),
                            ).fetchone()
                        if not present:
                            r.history.catalog = r.catalog
                            await r.history.prepare(deployments[leg["deployment_id"]], end, r.engine_identity)
                        with self.store.read() as conn:
                            row = conn.execute(
                                "SELECT * FROM forward_decisions WHERE deployment_id=? AND bar=?",
                                (leg["deployment_id"], latest),
                            ).fetchone()
                        if not row:
                            raise PlatformError(
                                "portfolio_decision_missing",
                                "Every portfolio leg needs the same confirmed decision bar.",
                                409,
                            )
                        return leg["inst_id"], verified(row, "body", "content_hash")

                prepared = await asyncio.gather(
                    *(prepare(leg) for leg in group["manifest"]["legs"]), return_exceptions=True
                )
                for result in prepared:
                    if isinstance(result, BaseException):
                        raise result
                decisions = dict(prepared)
                momentum = {}
                risk_bars = {}
                if definition["mode"] in {"momentum", "risk_momentum"}:
                    history_window = (
                        max(definition["lookback"], definition["risk_window"])
                        if definition["mode"] == "risk_momentum"
                        else definition["lookback"]
                    )
                    with self.store.read() as conn:
                        for leg in group["manifest"]["legs"]:
                            rows = conn.execute(
                                "SELECT body,content_hash,ts FROM forward_bars WHERE source=? AND inst_id=? AND bar=? AND ts<=? ORDER BY ts DESC LIMIT ?",
                                (
                                    group["source"],
                                    leg["inst_id"],
                                    definition["bar"],
                                    latest,
                                    history_window + 1,
                                ),
                            ).fetchall()
                            if len(rows) != history_window + 1 or any(
                                row["ts"] != latest - i * interval for i, row in enumerate(rows)
                            ):
                                raise PlatformError(
                                    "portfolio_momentum_history",
                                    "Contiguous momentum history is incomplete.",
                                    409,
                                )
                            prices = [D(verified(row, "body", "content_hash")["close"]) for row in rows]
                            momentum[leg["inst_id"]] = prices[0] / prices[definition["lookback"]] - 1
                            risk_bars[leg["inst_id"]] = [
                                verified(row, "body", "content_hash") for row in reversed(rows)
                            ]
                past_rate = None
                past = []
                if definition["mode"] == "funding_carry":
                    history = await r.catalog.funding_history(
                        definition["legs"][1]["inst_id"], latest - 32 * 86400000, latest, group["source"]
                    )
                    past = [e for e in history if int(e["ts"]) < latest]
                    past_rate = max(past, key=lambda e: int(e["ts"]))["rate"] if past else None
                symbols = sorted(decisions)
                async with AsyncExitStack() as stack:
                    for symbol in symbols:
                        await stack.enter_async_context(r.locks[(group["source"], symbol)])
                    quotes = await r.snapshots_for(group["source"], symbols)
                    self._quotes(group, end, quotes)
                    for symbol in symbols:
                        await r.sync_funding(group["source"], symbol, quotes, protective=True)
                    await self._sync_other_funding(group, quotes)
                    account = await r.offload(
                        r.book.observe, group["source"], quotes, record_performance=False
                    )
                    if account["pending_funding"]:
                        self._wait_for_funding(
                            group,
                            None,
                            PlatformError(
                                "funding_pending",
                                "Unsettled funding blocks new risk; protective reductions remain available.",
                                409,
                            ),
                        )
                        return
                    if account["valuation_status"] not in {"fresh", "example"} or account["equity"] is None:
                        raise PlatformError(
                            "portfolio_valuation",
                            "A complete fresh account valuation is required before a target batch.",
                            409,
                        )
                    positions = {p["inst_id"]: D(p["quantity"]) for p in account["positions"]}
                    policy = r.book.risk(group["source"])
                    carry_evidence = (
                        funding_carry_evidence(
                            definition, past, latest, policy["fee_bps"], policy["slippage_bps"]
                        )
                        if definition["mode"] == "funding_carry"
                        else None
                    )
                    if carry_evidence:
                        past_rate = carry_evidence["mean_rate"] if carry_evidence["allowed"] else None
                    risk_evidence = (
                        risk_momentum_weights(definition, definition["legs"], risk_bars, latest, interval)
                        if definition["mode"] == "risk_momentum"
                        else None
                    )
                    weights = construction_weights(
                        definition,
                        definition["legs"],
                        {s: d["signal"] for s, d in decisions.items()},
                        positions,
                        momentum,
                        past_rate,
                        risk_evidence=risk_evidence,
                    )
                    exits = {
                        s: d["exit_state"]["reason"]
                        for s, d in decisions.items()
                        if d["exit_state"].get("reason")
                    }
                    if definition["mode"] == "funding_carry" and exits:
                        exits = dict.fromkeys(symbols, "carry_group_exit")
                    capital = max(D(0), D(account["equity"]) * D(definition["capital_pct"]) / 100)
                    policy = r.book.risk(group["source"])
                    caps = {
                        leg["inst_id"]: risk_notional(
                            D(account["equity"]),
                            StrategyConfig(**ProStrategyInput.model_validate(leg["strategy"]).model_dump()),
                            policy["fee_bps"],
                            policy["slippage_bps"],
                        )
                        for leg in definition["legs"]
                    }
                    weights = apply_weight_caps(definition["mode"], weights, caps, capital, exits)
                    if risk_evidence:
                        weights, risk_evidence = constrain_risk_weights(definition, weights, risk_evidence)
                    due = (
                        group["anchor_bar"] is None
                        or (latest - group["anchor_bar"]) // interval % definition["rebalance_bars"] == 0
                    )
                    targets = (
                        target_quantities(weights, capital, quotes)
                        if due
                        else {s: D(0) if s in exits else positions.get(s, D(0)) for s in symbols}
                    )
                    reductions = reduction_plan(targets, positions, quotes)
                    batch_id, timestamp = new_id(), now_ms()
                    body = encode(
                        {
                            "id": batch_id,
                            "group_id": identifier,
                            "bar": latest,
                            "available_at": end,
                            "decisions": decisions,
                            "momentum": momentum,
                            "past_funding_rate": past_rate,
                            **({"carry_evidence": carry_evidence} if carry_evidence else {}),
                            **({"risk_evidence": risk_evidence} if risk_evidence else {}),
                            "rebalance_due": due,
                            "risk_exits": exits,
                            "weights": weights,
                            "capital": capital,
                            "account_equity": account["equity"],
                            "positions_before": {s: positions.get(s, D(0)) for s in symbols},
                            "targets": targets,
                            "reduction_skips": reductions.skipped,
                            "quotes": {s: quotes[s] for s in symbols},
                            "policy_hash": digest(policy),
                        }
                    )
                    with self.store.write() as conn:
                        self._active(identifier, conn)
                        conn.execute(
                            "INSERT INTO portfolio_batches VALUES(?,?,?,?,?,'reducing',NULL,NULL,NULL,NULL,?,?)",
                            (batch_id, identifier, latest, dumps(body), digest(body), timestamp, timestamp),
                        )
                        batch = {"id": batch_id, "bar": latest}
                        self._commands(conn, batch, group, "reduce", reductions.quantities)
                        conn.execute(
                            "UPDATE managed_portfolios SET anchor_bar=COALESCE(anchor_bar,?),updated_at=? WHERE id=?",
                            (latest, timestamp, identifier),
                        )
                        self.store.audit(
                            conn,
                            group["source"],
                            "portfolio.target_frozen",
                            "Shared-capital portfolio target batch persisted",
                            {
                                "group_id": identifier,
                                "batch_id": batch_id,
                                "bar": latest,
                                "content_hash": digest(body),
                            },
                        )
                await self._resume(group, self.batch(batch_id))

    def _quotes(self, group, end, quotes, *, complete=True):
        for leg in group["manifest"]["legs"]:
            symbol = leg["inst_id"]
            if symbol not in quotes:
                if not complete:
                    continue
                raise PlatformError("portfolio_quote_missing", "Every leg needs a fresh observed quote.", 409)
            if (self.runtime.market_errors.get((group["source"], symbol)) or {}).get(
                "kind", "quote"
            ) != "funding_reconciliation" and (group["source"], symbol) in self.runtime.market_errors:
                raise PlatformError(
                    "portfolio_market_unavailable",
                    "Every portfolio leg requires a successfully observed quote.",
                    409,
                )
            self.runtime.book.fresh(quotes[symbol])
            if quotes[symbol]["source"] != group["source"] or int(quotes[symbol]["ts"]) < end:
                raise PlatformError(
                    "portfolio_quote_before_signal",
                    "Every leg requires a source-matched post-close quote.",
                    409,
                )

    async def _phase(self, group, batch, phase):
        r = self.runtime
        commands = self.commands(batch["id"])
        replans = 0
        for command in commands:
            if command["phase"] != phase or command["status"] in {"completed", "superseded"}:
                continue
            payload = command["payload"]
            # Reconcile a committed fill before asking for data or rechecking a
            # now-stopped owner. Idempotency does not authorize another fill.
            order = r.book.existing(group["source"], command["key"], dumps(payload))
            if not order:
                async with r.locks[(group["source"], payload["inst_id"])]:
                    with self.store.read() as conn:
                        self._active(group["id"], conn)
                    if phase == "compensate":
                        with self.store.write() as conn:
                            position = conn.execute(
                                "SELECT quantity FROM pro_positions WHERE source=? AND inst_id=?",
                                (group["source"], payload["inst_id"]),
                            ).fetchone()
                            remaining = D(position[0]) if position else D(0)
                            signed = D(payload["quantity"]) * (1 if payload["side"] == "buy" else -1)
                            if remaining == 0 or signed * remaining >= 0 or abs(signed) > abs(remaining):
                                self._active(group["id"], conn)
                                conn.execute(
                                    "UPDATE portfolio_commands SET status='superseded',error='Inventory changed before compensation; original payload retained',updated_at=? WHERE id=?",
                                    (now_ms(), command["id"]),
                                )
                                if remaining:
                                    if replans >= 10:
                                        raise PlatformError(
                                            "portfolio_compensation_replan",
                                            "Compensation inventory changed repeatedly; retry after economic activity settles.",
                                            409,
                                        )
                                    sequence = conn.execute(
                                        "SELECT COALESCE(MAX(sequence),-1)+1 FROM portfolio_commands WHERE batch_id=? AND phase='compensate'",
                                        (batch["id"],),
                                    ).fetchone()[0]
                                    replacement = payload | {
                                        "quantity": str(abs(remaining)),
                                        "side": "sell" if remaining > 0 else "buy",
                                    }
                                    key = f"portfolio:{batch['id']}:compensate:{sequence}:{batch['bar']}"
                                    conn.execute(
                                        "INSERT INTO portfolio_commands VALUES(?,?,?,?,?,?,?,?,'pending',NULL,NULL,?)",
                                        (
                                            new_id(),
                                            batch["id"],
                                            "compensate",
                                            sequence,
                                            command["deployment_id"],
                                            key,
                                            dumps(replacement),
                                            digest(replacement),
                                            now_ms(),
                                        ),
                                    )
                                    replans += 1
                                self.store.audit(
                                    conn,
                                    group["source"],
                                    "portfolio.compensation_superseded",
                                    "Inventory changed; original compensation payload retained and a new reduce-only command captured",
                                    {
                                        "command_id": command["id"],
                                        "remaining_quantity": str(remaining),
                                        "batch_id": batch["id"],
                                    },
                                )
                                commands.extend(
                                    c
                                    for c in self.commands(batch["id"], conn)
                                    if c["phase"] == "compensate"
                                    and c["key"] not in {old["key"] for old in commands}
                                )
                                continue
                    for quote_attempt in range(3):
                        extra = {payload["inst_id"]}
                        if phase == "add":
                            extra.update(
                                p["inst_id"] for p in r.book.deferred_funding.pending(group["source"])
                            )
                        quotes = await r.snapshots_for(group["source"], extra)
                        if (r.market_errors.get((group["source"], payload["inst_id"])) or {}).get(
                            "kind", "quote"
                        ) != "funding_reconciliation" and (
                            group["source"],
                            payload["inst_id"],
                        ) in r.market_errors:
                            raise PlatformError(
                                "portfolio_market_unavailable",
                                "The command requires a successfully observed quote; cached transport fallback is not used.",
                                409,
                            )
                        if payload["inst_id"] not in quotes:
                            raise PlatformError(
                                "portfolio_quote_missing",
                                "A command needs a fresh quote for its own market.",
                                409,
                            )
                        r.book.fresh(quotes[payload["inst_id"]])
                        if int(quotes[payload["inst_id"]]["ts"]) < batch["body"]["available_at"]:
                            raise PlatformError(
                                "portfolio_quote_before_signal",
                                "A post-close quote is required for the command.",
                                409,
                            )
                        await r.sync_funding(group["source"], payload["inst_id"], quotes, protective=True)
                        if phase == "add":
                            await self._sync_other_funding(group, quotes)
                        # Fresh reduce-only commands may proceed despite unrelated
                        # unavailable markets. The book blocks incomplete new risk.
                        try:
                            order = await r.offload(
                                r.book.submit,
                                payload,
                                command["key"],
                                quotes,
                                "strategy:" + command["deployment_id"],
                            )
                            break
                        except PlatformError as exc:
                            # Another market can open after snapshots_for enumerates inventory.
                            # No order is committed when admission rejects incomplete valuation.
                            missing = {p["inst_id"] for p in r.book.positions(group["source"])} - set(quotes)
                            if exc.code != "valuation_unavailable" or not missing:
                                raise
                            if quote_attempt == 2:
                                raise PlatformError(
                                    "portfolio_valuation_retry",
                                    "Concurrent inventory changed the valuation inputs; the frozen command remains pending for retry.",
                                    409,
                                ) from None
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE portfolio_commands SET status='completed',order_id=?,error=NULL,updated_at=? WHERE id=?",
                    (order["id"], now_ms(), command["id"]),
                )

    async def _sync_other_funding(self, group, quotes):
        """Reconcile account obligations without cross-group execution locks.

        Settlements use the book's frozen quantity/ownership and transactional
        idempotency. Unavailable publications leave the account guard in force;
        this attempt runs only after protective reductions have been handled.
        """
        r, source = self.runtime, group["source"]
        owned = {leg["inst_id"] for leg in group["manifest"]["legs"]}
        for symbol in sorted(set(quotes) - owned):
            if (
                not symbol.endswith("-SWAP")
                or (source, symbol) in r.market_errors
                and r.market_errors[(source, symbol)].get("kind") != "funding_reconciliation"
            ):
                continue
            snapshot = quotes[symbol]
            try:
                r.book.fresh(snapshot)
                if snapshot["source"] != source:
                    continue
            except PlatformError:
                continue
            await r.sync_funding(source, symbol, quotes, protective=True)

    async def _freeze_additions(self, group, batch):
        r = self.runtime
        symbols = [leg["inst_id"] for leg in group["manifest"]["legs"]]
        async with AsyncExitStack() as stack:
            for symbol in sorted(symbols):
                await stack.enter_async_context(r.locks[(group["source"], symbol)])
            extra = set(symbols) | {p["inst_id"] for p in r.book.deferred_funding.pending(group["source"])}
            quotes = await r.snapshots_for(group["source"], extra)
            self._quotes(group, batch["body"]["available_at"], quotes)
            for symbol in symbols:
                await r.sync_funding(group["source"], symbol, quotes, protective=True)
            await self._sync_other_funding(group, quotes)
            with self.store.write() as conn:
                self._active(group["id"], conn)
                if self.batch(batch["id"], conn)["additions"]:
                    return
                # Marks, attribution, cash allowance and commands share one
                # transaction. Another group cannot spend between these reads.
                account = r.book.account(group["source"], quotes, conn)
                if account["pending_funding"]:
                    raise PlatformError(
                        "funding_pending",
                        "Unsettled funding blocks new risk; protective reductions remain available.",
                        409,
                    )
                policy = r.book.risk(group["source"], conn)
                capital_budget = self.capital.addition_budget(
                    conn, group["source"], "portfolio:" + group["id"], account
                )
                plan = addition_plan(
                    batch["body"]["targets"],
                    {p["inst_id"]: D(p["quantity"]) for p in account["positions"]},
                    quotes,
                    capital_budget["budget_cash"],
                    {leg["inst_id"]: leg["leverage"] for leg in group["manifest"]["legs"]},
                    policy["fee_bps"],
                    policy["slippage_bps"],
                )
                body = encode(
                    {
                        "quantities": plan.quantities,
                        "requested": plan.requested,
                        "cash_scale": plan.cash_scale,
                        "required_cash": plan.required_cash,
                        "skipped": plan.skipped,
                        "available_cash": account["available_cash"],
                        "capital_budget": capital_budget,
                        "quotes": {s: quotes[s] for s in symbols},
                        "policy_hash": digest(policy),
                    }
                )
                self._commands(conn, batch, group, "add", plan.quantities, quotes, policy)
                conn.execute(
                    "UPDATE portfolio_batches SET status='adding',additions=?,additions_hash=?,updated_at=? WHERE id=?",
                    (dumps(body), digest(body), now_ms(), batch["id"]),
                )

    async def _resume(self, group, batch):
        try:
            if batch["status"] == "compensating":
                await self._compensate(group, batch)
                return
            await self._phase(group, batch, "reduce")
            if not batch["additions"]:
                await self._freeze_additions(group, batch)
                batch = self.batch(batch["id"])
            require_addition_legs(batch["additions"]["skipped"])
            await self._phase(group, batch, "add")
            quotes = await self.runtime.snapshots_for(group["source"], batch["body"]["targets"])
            self._quotes(group, batch["body"]["available_at"], quotes)
            positions = {p["inst_id"]: D(p["quantity"]) for p in self.runtime.book.positions(group["source"])}
            residuals = portfolio_residuals(
                batch["body"]["targets"], positions, quotes, batch["body"]["capital"]
            )
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE portfolio_batches SET residuals=?,updated_at=? WHERE id=?",
                    (dumps(residuals), now_ms(), batch["id"]),
                )
            require_residual_limit(residuals, group["manifest"]["definition"])
            self._finish(group, batch, "completed")
        except asyncio.CancelledError:
            # Owned storage offloads drain on shutdown. Persisted command keys
            # reconcile any fill that committed while cancellation propagated.
            raise
        except Exception as exc:
            if isinstance(exc, PlatformError) and exc.code == "funding_pending":
                self._wait_for_funding(group, batch, exc)
                return
            if isinstance(exc, PlatformError) and exc.code == "portfolio_valuation_retry":
                with self.store.write() as conn:
                    self._active(group["id"], conn)
                    conn.execute(
                        "UPDATE managed_portfolios SET last_error=?,updated_at=? WHERE id=?",
                        (exc.message, now_ms(), group["id"]),
                    )
                    self.store.audit(
                        conn,
                        group["source"],
                        "portfolio.valuation_retry",
                        "Frozen portfolio command deferred after concurrent inventory changes",
                        {"group_id": group["id"], "batch_id": batch["id"]},
                    )
                return
            with self.store.write() as conn:
                active = self.get(group["id"], conn)
                if active["status"] not in ACTIVE:
                    return
                error = str(exc)[:1000]
                conn.execute(
                    "UPDATE managed_portfolios SET status='compensating',last_error=?,updated_at=? WHERE id=?",
                    (error, now_ms(), group["id"]),
                )
                conn.execute(
                    "UPDATE portfolio_batches SET status='compensating',error=?,updated_at=? WHERE id=?",
                    (error, now_ms(), batch["id"]),
                )
                conn.execute(
                    "UPDATE portfolio_commands SET status='canceled',error=?,updated_at=? WHERE batch_id=? AND phase='add' AND status='pending'",
                    (error, now_ms(), batch["id"]),
                )
                self.store.audit(
                    conn,
                    group["source"],
                    "portfolio.compensating",
                    "Portfolio leg failure entered durable reduce-group compensation",
                    {"group_id": group["id"], "batch_id": batch["id"], "error": error},
                )
            await self._compensate(group, self.batch(batch["id"]))

    def _wait_for_funding(self, group, batch, exc):
        """Keep original targets/commands active; evidence waiting is not a fill failure."""
        message = f"[{exc.code}] {exc.message}"[:1000]
        with self.store.write() as conn:
            active = self.get(group["id"], conn)
            if active["status"] not in ACTIVE:
                return
            current = self.batch(batch["id"], conn) if batch else None
            timestamp = now_ms()
            conn.execute(
                "UPDATE managed_portfolios SET last_error=?,updated_at=? WHERE id=?",
                (message, timestamp, group["id"]),
            )
            if batch:
                conn.execute(
                    "UPDATE portfolio_batches SET error=?,updated_at=? WHERE id=?",
                    (message, timestamp, batch["id"]),
                )
                conn.execute(
                    "UPDATE portfolio_commands SET error=?,updated_at=? WHERE batch_id=? AND phase='add' AND status='pending'",
                    (message, timestamp, batch["id"]),
                )
            if active["last_error"] != message or current and current["error"] != message:
                self.store.audit(
                    conn,
                    group["source"],
                    "portfolio.funding_wait",
                    "Frozen portfolio execution awaits realized account funding evidence",
                    {
                        "group_id": group["id"],
                        "batch_id": batch["id"] if batch else None,
                        "code": exc.code,
                        "message": exc.message,
                        "pending_funding": self.runtime.book.deferred_funding.pending(group["source"], conn),
                    },
                )

    async def _compensate(self, group, batch):
        try:
            with self.store.write() as conn:
                self._active(group["id"], conn)
                existing = conn.execute(
                    "SELECT 1 FROM portfolio_commands WHERE batch_id=? AND phase='compensate'", (batch["id"],)
                ).fetchone()
                if not existing:
                    positions = {
                        p["inst_id"]: D(p["quantity"])
                        for p in conn.execute(
                            "SELECT inst_id,quantity FROM pro_positions WHERE source=? AND quantity!='0'",
                            (group["source"],),
                        )
                    }
                    self._commands(
                        conn,
                        batch,
                        group,
                        "compensate",
                        compensation_quantities(batch["body"]["targets"], positions),
                    )
            await self._phase(group, batch, "compensate")
            remaining = {p["inst_id"]: p["quantity"] for p in self.runtime.book.positions(group["source"])}
            require_compensation_flat(batch["body"]["targets"], remaining)
            self._finish(group, batch, "compensated")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with self.store.write() as conn:
                if self.get(group["id"], conn)["status"] not in ACTIVE:
                    return
                error = "Compensation blocked: " + str(exc)[:900]
                conn.execute(
                    "UPDATE managed_portfolios SET last_error=?,updated_at=? WHERE id=?",
                    (error, now_ms(), group["id"]),
                )
                conn.execute(
                    "UPDATE portfolio_batches SET error=?,updated_at=? WHERE id=?",
                    (error, now_ms(), batch["id"]),
                )

    def _finish(self, group, batch, status):
        with self.store.write() as conn:
            self._active(group["id"], conn)
            timestamp = now_ms()
            conn.execute(
                "UPDATE portfolio_batches SET status=?,error=CASE WHEN ?='completed' THEN NULL ELSE error END,updated_at=? WHERE id=?",
                (status, status, timestamp, batch["id"]),
            )
            conn.execute(
                "UPDATE managed_portfolios SET status=?,last_bar=?,last_error=?,updated_at=? WHERE id=?",
                (
                    "failed" if status == "compensated" else "running",
                    batch["bar"],
                    batch["error"] if status == "compensated" else None,
                    timestamp,
                    group["id"],
                ),
            )
            for leg in group["manifest"]["legs"]:
                conn.execute(
                    "UPDATE pro_strategy_intents SET status='completed',updated_at=? WHERE deployment_id=? AND bar=?",
                    (timestamp, leg["deployment_id"], batch["bar"]),
                )
                conn.execute(
                    "UPDATE pro_deployments SET last_bar=?,status=?,last_error=?,updated_at=? WHERE id=?",
                    (
                        batch["bar"],
                        "stopped" if status == "compensated" else "running",
                        batch["error"] if status == "compensated" else None,
                        timestamp,
                        leg["deployment_id"],
                    ),
                )
            if status == "compensated":
                self.capital.terminal(group["source"], "portfolio:" + group["id"], conn)
            self.store.audit(
                conn,
                group["source"],
                "portfolio.batch_" + status,
                "Managed portfolio batch " + status,
                {"group_id": group["id"], "batch_id": batch["id"], "bar": batch["bar"]},
            )
