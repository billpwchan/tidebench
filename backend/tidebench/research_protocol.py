"""Frozen portfolio evaluations and shared, independently recoverable governance facts."""

import json
import re
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .catalog import CATALOG_BARS
from .engine import Candle
from .historical_lifecycle import LifecycleEvent
from .platform import PlatformError
from .portfolio_research import RuleEvent
from .schemas import InputModel, Money
from .store import dumps, new_id, now_ms
from .strategy_registry import canonical, digest

D = Decimal
SCOPE = "research_api_workflow_seal; public data and external experiments are not statistically blinded"


class RejectionCriteria(InputModel):
    min_return_vs_cash_pct: Decimal = Field(default=0, ge=-100, le=1000000)
    max_drawdown_pct: Decimal = Field(default=20, ge=0, le=1000)
    min_trades: int = Field(default=1, ge=0, le=1000000)
    min_observations: int = Field(default=20, ge=2, le=20000)
    require_zero_debt: bool = True


class PortfolioHoldoutInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    portfolio_version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    package_ids: list[str] = Field(min_length=2, max_length=10)
    test_start: int = Field(ge=1577836800000)
    test_end: int = Field(ge=1577836800000)
    warmup_bars: int = Field(default=500, ge=0, le=2000)
    initial_cash: Money = D(10000)
    fee_bps: Decimal = Field(default=10, ge=0, le=100)
    slippage_bps: Decimal = Field(default=5, ge=0, le=100)
    liquidation_fee_bps: Decimal = Field(default=50, ge=0, le=500)
    max_gross_pct: Decimal = Field(default=200, ge=1, le=1000)
    max_order_notional: Decimal = Field(default=2500, gt=0, le=1000000000)
    max_base_asset_gross_pct: Decimal = Field(default=100, ge=1, le=1000)
    max_daily_loss_pct: Decimal = Field(default=5, ge=".1", le=50)
    benchmark: Literal["cash"] = "cash"
    rejection_plan: str = Field(min_length=20, max_length=4000)
    criteria: RejectionCriteria = Field(default_factory=RejectionCriteria)
    rules_mode: Literal["captured_current", "point_in_time"] = "captured_current"
    rule_events: dict[str, list[RuleEvent]] = Field(default_factory=dict, max_length=10)
    universe_mode: Literal["static", "historical_lifecycle"] = "static"
    lifecycle_warmup_bars: int = Field(default=2, ge=1, le=400)
    lifecycle_events: dict[str, list[LifecycleEvent]] = Field(default_factory=dict, max_length=10)


class SealPortfolioInput(InputModel):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvaluatePortfolioInput(InputModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResearchFacts:
    """Facts have no project/run FKs so a restore can retain newer exposure."""

    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_reservations(id TEXT PRIMARY KEY,holdout_id TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,inst_id TEXT NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS research_reservation_market ON research_reservations(source,inst_id,start_ts,end_ts);
                CREATE TABLE IF NOT EXISTS research_exposures(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,inst_id TEXT NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS research_exposure_market ON research_exposures(source,inst_id,start_ts,end_ts);
                CREATE TABLE IF NOT EXISTS research_consumptions(id TEXT PRIMARY KEY,holdout_id TEXT NOT NULL,kind TEXT NOT NULL,run_id TEXT NOT NULL,plan_hash TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(kind,holdout_id));
                CREATE TABLE IF NOT EXISTS research_trials(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,project_id TEXT,version_id TEXT,attempt_type TEXT NOT NULL,config_hash TEXT NOT NULL,config TEXT NOT NULL,candidate_count INTEGER NOT NULL,replay_of TEXT,root_run_id TEXT NOT NULL,created_at INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,UNIQUE(kind,run_id));
                CREATE INDEX IF NOT EXISTS research_trial_project ON research_trials(kind,project_id,created_at DESC,id DESC);
            """)

    @staticmethod
    def checked(row):
        try:
            row = dict(row)
            body = json.loads(row["body"])
            keys = set(row) - {"body", "content_hash"}
            if not isinstance(body, dict) or set(body) != keys or digest(body) != row["content_hash"]:
                raise ValueError()
            if any(body[k] != row[k] for k in keys) or body["kind"] not in {"single", "portfolio"}:
                raise ValueError()
            if type(body["created_at"]) is not int or body["created_at"] < 0:
                raise ValueError()
            for key in ("holdout_id", "run_id", "replay_of", "root_run_id", "project_id", "version_id"):
                if key in body and body[key] is not None and not re.fullmatch(r"[a-f0-9]{32}", body[key]):
                    raise ValueError()
            if "config" in body:
                config = json.loads(body["config"])
                economic = ResearchFacts.economic(config)
                if digest(economic) != body["config_hash"] or body[
                    "candidate_count"
                ] != ResearchFacts.candidates(body["kind"], config):
                    raise ValueError()
                if body["attempt_type"] != ("replay" if body["replay_of"] else "evaluation"):
                    raise ValueError()
                identity = ["trial", body["kind"], body["run_id"]]
            elif "plan_hash" in body:
                if not re.fullmatch(r"[a-f0-9]{64}", body["plan_hash"]):
                    raise ValueError()
                identity = ["consumption", body["kind"], body["holdout_id"]]
            else:
                if body["source"] not in {"example", "okx"} or not isinstance(body["inst_id"], str):
                    raise ValueError()
                if not re.fullmatch(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}(?:-SWAP)?", body["inst_id"]):
                    raise ValueError()
                if (
                    any(type(body[k]) is not int for k in ("start_ts", "end_ts"))
                    or not 1577836800000 <= body["start_ts"] < body["end_ts"]
                ):
                    raise ValueError()
                identity = (
                    ["reservation", body["kind"], body["holdout_id"], body["inst_id"]]
                    if "holdout_id" in body
                    else [
                        "exposure",
                        body["kind"],
                        body["run_id"],
                        body["inst_id"],
                        body["start_ts"],
                        body["end_ts"],
                    ]
                )
            if body["id"] != digest(identity):
                raise ValueError()
            return body
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise PlatformError(
                "governance_integrity", "Research governance fact integrity failed.", 409
            ) from None

    @staticmethod
    def insert(conn, table, body):
        columns = list(body)
        conn.execute(
            f"INSERT OR IGNORE INTO {table}({','.join(columns)},body,content_hash) VALUES({','.join('?' for _ in range(len(columns) + 2))})",
            (*body.values(), dumps(body), digest(body)),
        )
        cursor = conn.execute(f"SELECT * FROM {table} WHERE id=?", (body["id"],))
        raw = cursor.fetchone()
        row = (
            dict(zip((column[0] for column in cursor.description), raw, strict=True))
            if raw is not None
            else None
        )
        if row is None or ResearchFacts.checked(row) != body:
            raise PlatformError(
                "governance_conflict", "Governance fact identity conflicts with retained evidence.", 409
            )

    @staticmethod
    def candidates(kind, config):
        candidates = 1
        if kind == "single":
            if config.get("mode") in {"grid", "train_test", "walk_forward"}:
                for values in config.get("options", {}).get("grid", {}).values():
                    candidates *= len(values)
            elif config.get("mode") == "cost_stress":
                for key in ("fee_bps", "slippage_bps"):
                    candidates *= max(1, len(config.get("options", {}).get(key, [])))
        return candidates

    @staticmethod
    def economic(config):
        return {
            k: v
            for k, v in config.items()
            if k not in {"name", "hypothesis", "portfolio_version_id", "strategy_version_id", "holdout_id"}
        }

    def reserve(self, conn, holdout_id, kind, source, markets, start, end, timestamp):
        for symbol in markets:
            body = dict(
                id=digest(["reservation", kind, holdout_id, symbol]),
                holdout_id=holdout_id,
                kind=kind,
                source=source,
                inst_id=symbol,
                start_ts=start,
                end_ts=end,
                created_at=timestamp,
            )
            self.insert(conn, "research_reservations", body)

    def overlapping(self, conn, table, source, symbol, start, end):
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE source=? AND inst_id=? AND start_ts<? AND end_ts>?",
            (source, symbol, end, start),
        ).fetchall()
        return [self.checked(row) for row in rows]

    def guard(self, conn, source, accesses, *, allowed=None):
        for symbol, start, end in accesses:
            for row in self.overlapping(conn, "research_reservations", source, symbol, start, end):
                if (row["kind"], row["holdout_id"]) != allowed:
                    raise PlatformError(
                        "holdout_reserved",
                        "Research access overlaps a reserved, sealed or consumed market-time holdout.",
                        409,
                    )

    def unseen(self, conn, source, markets, start, end):
        for symbol in markets:
            if self.overlapping(conn, "research_reservations", source, symbol, start, end):
                raise PlatformError(
                    "holdout_overlap", "An existing holdout already reserves this market interval.", 409
                )
            if self.overlapping(conn, "research_exposures", source, symbol, start, end):
                raise PlatformError(
                    "holdout_exposed",
                    "Research already accessed this interval; it cannot become an unseen holdout.",
                    409,
                )

    def consumption(self, conn, kind, holdout_id):
        row = conn.execute(
            "SELECT * FROM research_consumptions WHERE kind=? AND holdout_id=?", (kind, holdout_id)
        ).fetchone()
        return self.checked(row) if row else None

    def consume(self, conn, kind, holdout_id, run_id, plan_hash, timestamp):
        self.insert(
            conn,
            "research_consumptions",
            dict(
                id=digest(["consumption", kind, holdout_id]),
                holdout_id=holdout_id,
                kind=kind,
                run_id=run_id,
                plan_hash=plan_hash,
                created_at=timestamp,
            ),
        )

    def record(
        self,
        conn,
        kind,
        run_id,
        config,
        source,
        accesses,
        timestamp,
        *,
        project_id=None,
        version_id=None,
        replay_of=None,
        root_run_id=None,
    ):
        identifier = digest(["trial", kind, run_id])
        body = dict(
            id=identifier,
            run_id=run_id,
            kind=kind,
            project_id=project_id,
            version_id=version_id,
            attempt_type="replay" if replay_of else "evaluation",
            config_hash=digest(self.economic(config)),
            config=dumps(config),
            candidate_count=self.candidates(kind, config),
            replay_of=replay_of,
            root_run_id=root_run_id or run_id,
            created_at=timestamp,
        )
        self.insert(conn, "research_trials", body)
        for symbol, start, end in accesses:
            self.insert(
                conn,
                "research_exposures",
                dict(
                    id=digest(["exposure", kind, run_id, symbol, start, end]),
                    run_id=run_id,
                    kind=kind,
                    source=source,
                    inst_id=symbol,
                    start_ts=start,
                    end_ts=end,
                    created_at=timestamp,
                ),
            )

    def backfill(self, registry):
        with self.store.write() as conn:
            for table in (
                "research_reservations",
                "research_exposures",
                "research_consumptions",
                "research_trials",
            ):
                for row in conn.execute(f"SELECT * FROM {table}"):
                    self.checked(row)
            for row in conn.execute("SELECT * FROM research_holdouts").fetchall():
                self.reserve(
                    conn,
                    row["id"],
                    "single",
                    row["source"],
                    [row["inst_id"]],
                    row["start_ts"],
                    row["end_ts"],
                    row["created_at"],
                )
                if row["status"] == "consumed":
                    self.consume(
                        conn, "single", row["id"], row["run_id"], row["plan_hash"], row["consumed_at"]
                    )
            for row in conn.execute(
                "SELECT r.*,d.manifest dataset FROM pro_runs r JOIN catalog_datasets d ON d.id=json_extract(r.config,'$.dataset_id') WHERE NOT EXISTS(SELECT 1 FROM research_trials t WHERE t.kind='single' AND t.run_id=r.id)"
            ).fetchall():
                config, data = json.loads(row["config"]), json.loads(row["dataset"])
                start = max(
                    data["start"],
                    (config.get("start_ts") or data["start"]) - 2000 * CATALOG_BARS[data["bar"]],
                )
                version = conn.execute(
                    "SELECT project_id FROM strategy_versions WHERE id=?",
                    (config.get("strategy_version_id"),),
                ).fetchone()
                manifest = json.loads(row["manifest"]) if row["manifest"] else {}
                self.record(
                    conn,
                    "single",
                    row["id"],
                    config,
                    data["source"],
                    [(data["inst_id"], start, config.get("end_ts") or data["end"])],
                    row["created_at"],
                    project_id=version[0] if version else None,
                    version_id=config.get("strategy_version_id"),
                    replay_of=manifest.get("replay_of"),
                )
            for row in conn.execute(
                "SELECT * FROM portfolio_runs p WHERE NOT EXISTS(SELECT 1 FROM research_trials t WHERE t.kind='portfolio' AND t.run_id=p.id)"
            ).fetchall():
                config, manifest = json.loads(row["config"]), json.loads(row["manifest"])
                version = conn.execute(
                    "SELECT project_id FROM portfolio_versions WHERE id=?",
                    (config.get("portfolio_version_id"),),
                ).fetchone()
                self.record(
                    conn,
                    "portfolio",
                    row["id"],
                    config,
                    row["source"],
                    [(p["inst_id"], manifest["start"], manifest["end"]) for p in manifest["packages"]],
                    row["created_at"],
                    project_id=version[0] if version else None,
                    version_id=config.get("portfolio_version_id"),
                    replay_of=manifest.get("replay_of"),
                )


def decode_legs(bundle):
    return [
        leg
        | {
            key: [
                Candle(
                    **{
                        k: D(v) if k in {"open", "high", "low", "close", "volume"} else v
                        for k, v in row.items()
                    }
                )
                for row in leg[key]
            ]
            for key in ("candles", "marks")
        }
        for leg in bundle["legs"]
    ]


def evaluate_frozen_portfolio(config, manifest, legs, progress=lambda _: None):
    from .portfolio_research import _simulate_portfolio

    protocol = manifest["evaluation_plan"]
    first = protocol["warmup_bars"]
    result = _simulate_portfolio(config, manifest, legs, progress, first_trading_index=first)
    criteria = protocol["criteria"]
    metrics = result["metrics"]
    observations = len(result["equity"])
    enough = observations >= criteria["min_observations"] and metrics["orders"] >= criteria["min_trades"]
    checks = [
        dict(
            metric="return_vs_cash_pct",
            actual=metrics["total_return_pct"],
            threshold=criteria["min_return_vs_cash_pct"],
            passed=metrics["total_return_pct"] is not None
            and D(metrics["total_return_pct"]) >= D(criteria["min_return_vs_cash_pct"]),
        ),
        dict(
            metric="max_drawdown_pct",
            actual=metrics["max_drawdown_pct"],
            threshold=criteria["max_drawdown_pct"],
            passed=metrics["max_drawdown_pct"] is not None
            and D(metrics["max_drawdown_pct"]) <= D(criteria["max_drawdown_pct"]),
        ),
        dict(
            metric="zero_debt",
            actual=metrics["insurance_debt"],
            threshold="0",
            passed=not criteria["require_zero_debt"] or D(metrics["insurance_debt"]) == 0,
        ),
    ]
    if result.get("execution_contract"):
        checks.append(
            dict(
                metric="execution_status",
                actual=result["execution_status"],
                threshold="running",
                passed=result["execution_status"] == "running",
            )
        )
    if result.get("lifecycle"):
        checks.append(
            dict(
                metric="lifecycle_economics",
                actual=result["lifecycle"]["status"],
                threshold="complete_within_supplied_scope",
                passed=result["lifecycle"]["status"] == "complete_within_supplied_scope",
            )
        )
    execution_failed = (
        result.get("execution_status") in {"failed", "compensating"}
        or metrics["final_equity"] is None
        or result.get("economic_state") == "incomplete_lifecycle"
    )
    status = (
        "rejected"
        if execution_failed
        else "inconclusive"
        if not enough
        else "passed"
        if all(c["passed"] for c in checks)
        else "rejected"
    )
    return result | {
        "evaluation": dict(
            mode="sealed_holdout",
            **manifest["governance"],
            test_start=protocol["test_start"],
            test_end=protocol["test_end"],
            warmup_bars=first,
            capital_policy="Flat inventory, independent initial capital; warmup initializes indicators only.",
            benchmark={"kind": "cash", "total_return_pct": "0", "final_equity": config["initial_cash"]},
            rejection={
                "status": status,
                "checks": checks,
                "observations": observations,
                "trades": metrics["orders"],
                "criteria": criteria,
                "plan": protocol["rejection_plan"],
            },
        )
    }


class ResearchProtocol:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.facts = ResearchFacts(self.store)
        with self.store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_holdouts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,version_id TEXT NOT NULL,source TEXT NOT NULL,plan TEXT NOT NULL,plan_hash TEXT NOT NULL,input_artifact TEXT NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,run_id TEXT,created_by TEXT NOT NULL,created_at INTEGER NOT NULL,consumed_at INTEGER);
                CREATE INDEX IF NOT EXISTS portfolio_holdout_project ON portfolio_holdouts(project_id,created_at DESC,id DESC);
            """)

    def get(self, identifier, conn=None, *, private=False):
        if conn is None:
            with self.store.read() as connection:
                return self.get(identifier, connection, private=private)
        row = conn.execute("SELECT * FROM portfolio_holdouts WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("holdout_not_found", "Portfolio holdout not found.", 404)
        output = dict(row)
        try:
            plan = json.loads(output["plan"])
            if digest(plan) != output["plan_hash"] or any(
                plan[k] != output[k] for k in ("project_id", "version_id", "source", "input_hash")
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise PlatformError(
                "holdout_integrity", "Portfolio holdout plan integrity failed.", 409
            ) from None
        output["plan"] = plan
        consumption = self.facts.consumption(conn, "portfolio", identifier)
        if consumption:
            output.update(
                status="consumed", run_id=consumption["run_id"], consumed_at=consumption["created_at"]
            )
            if not conn.execute("SELECT 1 FROM portfolio_runs WHERE id=?", (output["run_id"],)).fetchone():
                output["status"] = "consumed_unavailable"
        if not private:
            output.pop("input_artifact")
        return output

    def list(self, source=None, project_id=None, *, limit=100, before=2**63 - 1):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT id FROM portfolio_holdouts WHERE (? IS NULL OR source=?) AND (? IS NULL OR project_id=?) AND status!='draft' AND created_at<? ORDER BY created_at DESC,id DESC LIMIT ?",
                (source, source, project_id, project_id, before, limit),
            ).fetchall()
            return [self.get(row[0], conn) for row in rows]

    def preview(self, body, actor, margin_snapshots=None):
        request = canonical(PortfolioHoldoutInput.model_validate(body).model_dump())
        r = self.runtime
        version = r.portfolio_registry.version(request["portfolio_version_id"])
        definition = version["definition"]
        if "execution_contract" not in definition:
            raise PlatformError(
                "portfolio_execution_legacy",
                "Create and review a new revision with the shared execution contract before freezing new evidence; stored legacy evidence remains unchanged.",
                409,
            )
        interval = CATALOG_BARS[definition["bar"]]
        start, end = request["test_start"], request["test_end"]
        if start % interval or end % interval or end - start < 2 * interval:
            raise PlatformError("holdout_window", "Use an aligned final interval of at least two bars.", 422)
        if (set(request["rule_events"]) | set(request["lifecycle_events"])) - {
            leg["inst_id"] for leg in definition["legs"]
        }:
            raise PlatformError(
                "holdout_rules", "Rule histories must belong to the declared portfolio markets.", 422
            )
        if len(request["package_ids"]) != len(definition["legs"]):
            raise PlatformError(
                "holdout_inputs", "Provide one ordered package for every immutable portfolio leg.", 422
            )
        access_start = start - request["warmup_bars"] * interval
        config = dict(
            name=request["name"],
            hypothesis=version["hypothesis"],
            portfolio_version_id=version["id"],
            **{
                k: definition[k]
                for k in (
                    "mode",
                    "capital_pct",
                    "failure_policy",
                    "max_residual_pct",
                    "execution_contract",
                    "rebalance_bars",
                    "lookback",
                    "top_k",
                    "risk_window",
                    "vol_target_pct",
                    "vol_floor_pct",
                    "covariance_shrinkage",
                    "correlation_stress",
                    "carry_window",
                    "carry_cost_settlements",
                    "carry_buffer_bps",
                    "carry_max_age_hours",
                    "carry_threshold",
                )
            },
            **{
                k: request[k]
                for k in (
                    "initial_cash",
                    "fee_bps",
                    "slippage_bps",
                    "liquidation_fee_bps",
                    "max_gross_pct",
                    "max_order_notional",
                    "max_base_asset_gross_pct",
                    "max_daily_loss_pct",
                    "rules_mode",
                    "universe_mode",
                    "lifecycle_warmup_bars",
                )
            },
            legs=[
                {k: leg[k] for k in ("weight", "leverage", "direction", "strategy")}
                | {
                    "package_id": package,
                    "rule_events": request["rule_events"].get(leg["inst_id"], []),
                    "lifecycle_events": request["lifecycle_events"].get(leg["inst_id"], []),
                }
                for leg, package in zip(definition["legs"], request["package_ids"], strict=True)
            ],
        )
        config, manifest, bundle = r.portfolios.prepare(
            config, actor, margin_snapshots, capture_window=(access_start, end)
        )
        if definition["mode"] == "momentum" and request["warmup_bars"] < definition["lookback"]:
            raise PlatformError(
                "holdout_warmup", "Momentum requires its full frozen lookback before the final window.", 422
            )
        if definition["mode"] == "risk_momentum" and request["warmup_bars"] < max(
            definition["lookback"], definition["risk_window"]
        ):
            raise PlatformError(
                "holdout_warmup",
                "Risk momentum requires the full frozen momentum and covariance history.",
                422,
            )
        if definition["mode"] == "funding_carry" and not any(
            int(e["ts"]) < start for e in bundle["legs"][1]["funding"]
        ):
            raise PlatformError(
                "holdout_funding_seed",
                "Capture a prior realized settlement before the final carry window.",
                409,
            )
        input_hash = digest(bundle)
        plan = dict(
            protocol_version=2,
            kind="portfolio",
            project_id=version["project_id"],
            version_id=version["id"],
            source=manifest["source"],
            name=request["name"],
            definition=definition,
            hypothesis=version["hypothesis"],
            portfolio_content_hash=version["content_hash"],
            implementation=r.engine_identity,
            test_config=config,
            test_start=start,
            test_end=end,
            access_start=access_start,
            warmup_bars=request["warmup_bars"],
            benchmark="cash",
            criteria=request["criteria"],
            rejection_plan=request["rejection_plan"],
            input_hash=input_hash,
            packages=manifest["packages"],
            scope=SCOPE,
            input_identity=[
                {k: v for k, v in leg.items() if k not in {"candles", "marks", "funding"}}
                | {
                    "trade_records_hash": digest(leg["candles"]),
                    "mark_records_hash": digest(leg["marks"]),
                    "funding_records_hash": digest(leg["funding"]),
                }
                for leg in bundle["legs"]
            ],
        )
        bundle["manifest"] = manifest
        # Hash covers the complete computation bundle, including rules, costs and tiers.
        plan["input_hash"] = digest(bundle)
        identifier, timestamp = new_id(), now_ms()
        with self.store.write() as conn:
            self.facts.unseen(
                conn, manifest["source"], [p["inst_id"] for p in manifest["packages"]], start, end
            )
            self.facts.guard(
                conn, manifest["source"], [(p["inst_id"], access_start, end) for p in manifest["packages"]]
            )
            pointer = r.artifacts.put(conn, dumps(bundle))
            conn.execute(
                "INSERT INTO portfolio_holdouts VALUES(?,?,?,?,?,?,?,?,'draft',NULL,?,?,NULL)",
                (
                    identifier,
                    version["project_id"],
                    version["id"],
                    manifest["source"],
                    dumps(plan),
                    digest(plan),
                    dumps(pointer),
                    plan["input_hash"],
                    actor,
                    timestamp,
                ),
            )
        return self.get(identifier) | {"preview_id": identifier, "preview_hash": digest(plan), "blockers": []}

    def seal(self, body, actor):
        body = SealPortfolioInput.model_validate(body)
        with self.store.write() as conn:
            holdout = self.get(body.preview_id, conn, private=True)
            if holdout["plan_hash"] != body.preview_hash:
                raise PlatformError(
                    "holdout_preview_changed", "Review the exact captured holdout preview.", 409
                )
            if holdout["status"] != "draft":
                return self.get(holdout["id"], conn)
            self.bundle(holdout)
            plan = holdout["plan"]
            self.implementation(plan)
            symbols = [p["inst_id"] for p in plan["packages"]]
            self.facts.unseen(conn, holdout["source"], symbols, plan["test_start"], plan["test_end"])
            self.facts.guard(
                conn, holdout["source"], [(s, plan["access_start"], plan["test_end"]) for s in symbols]
            )
            self.facts.reserve(
                conn,
                holdout["id"],
                "portfolio",
                holdout["source"],
                symbols,
                plan["test_start"],
                plan["test_end"],
                holdout["created_at"],
            )
            conn.execute(
                "UPDATE portfolio_holdouts SET status='sealed' WHERE id=? AND status='draft'",
                (holdout["id"],),
            )
            self.store.audit(
                conn,
                holdout["source"],
                "portfolio.holdout_sealed",
                "Full portfolio evaluation inputs pre-registered",
                {"holdout_id": holdout["id"], "plan_hash": holdout["plan_hash"], "actor": actor},
            )
        return self.get(holdout["id"])

    def implementation(self, plan):
        if plan["implementation"]["code_fingerprint"] != self.runtime.engine_identity["code_fingerprint"]:
            raise PlatformError(
                "holdout_implementation",
                "Evaluate with the captured implementation; the seal is not reset on code drift.",
                409,
            )

    def bundle(self, holdout):
        bundle = self.runtime.artifacts.resolve(json.loads(holdout["input_artifact"]))
        if digest(bundle) != holdout["input_hash"]:
            raise PlatformError(
                "holdout_input_integrity", "Frozen evaluation inputs failed their identity check.", 409
            )
        return bundle

    def evaluate(self, identifier, plan_hash, actor):
        with self.store.read() as conn:
            holdout = self.get(identifier, conn, private=True)
        if holdout["plan_hash"] != plan_hash:
            raise PlatformError("holdout_config", "Evaluation must match the exact frozen plan hash.", 409)
        if holdout["status"] == "consumed_unavailable":
            raise PlatformError(
                "holdout_consumed_unavailable",
                "Consumed evaluation evidence is absent after recovery; the holdout cannot be evaluated again.",
                409,
            )
        if holdout["status"] == "consumed":
            return self.runtime.portfolios.get(holdout["run_id"])
        if holdout["status"] != "sealed":
            raise PlatformError("holdout_not_sealed", "Seal the reviewed plan before evaluation.", 409)
        plan = holdout["plan"]
        self.implementation(plan)
        bundle = self.bundle(holdout)
        manifest = bundle["manifest"] | {
            "evaluation_plan": {
                k: plan[k] for k in ("test_start", "test_end", "warmup_bars", "criteria", "rejection_plan")
            }
        }
        return self.runtime.portfolios.enqueue(plan["test_config"], manifest, bundle, actor, holdout=holdout)

    def admit(self, conn, holdout, run_id, timestamp):
        current = self.get(holdout["id"], conn, private=True)
        if current["status"] == "consumed_unavailable":
            raise PlatformError(
                "holdout_consumed_unavailable", "Consumed evaluation is unavailable and cannot be reset.", 409
            )
        if current["status"] == "consumed":
            return current["run_id"]
        if current["status"] != "sealed" or current["plan_hash"] != holdout["plan_hash"]:
            raise PlatformError("holdout_config", "The sealed evaluation identity changed.", 409)
        self.facts.consume(conn, "portfolio", current["id"], run_id, current["plan_hash"], timestamp)
        conn.execute(
            "UPDATE portfolio_holdouts SET status='consumed',run_id=?,consumed_at=? WHERE id=? AND status='sealed'",
            (run_id, timestamp, current["id"]),
        )
        return None

    def verify_run(self, run):
        governance = run["manifest"].get("governance")
        if not governance:
            return {
                "protocol": "chronological_test"
                if run["config"]["evaluation"] == "train_test"
                else "development",
                "one_use": False,
            }
        try:
            with self.store.read() as conn:
                holdout = self.get(governance["holdout_id"], conn)
                primary = holdout["run_id"]
                if holdout["status"] != "consumed":
                    raise ValueError()
                plan = holdout["plan"]
                expected = dict(
                    protocol_version=2,
                    kind="portfolio",
                    holdout_id=holdout["id"],
                    plan_hash=holdout["plan_hash"],
                    input_hash=holdout["input_hash"],
                    primary_run_id=primary,
                    scope="research_api_workflow_seal",
                )
                if (
                    governance != expected
                    or run["manifest"]["input_hash"] != holdout["input_hash"]
                    or canonical(run["config"]) != plan["test_config"]
                ):
                    raise ValueError()
                if run["id"] != primary and not run["manifest"].get("replay_verified"):
                    raise ValueError()
                evaluation = run["result"]["evaluation"]
                if evaluation["mode"] != "sealed_holdout" or any(
                    evaluation[k] != v for k, v in expected.items()
                ):
                    raise ValueError()
                if (
                    evaluation["rejection"]["criteria"] != plan["criteria"]
                    or evaluation["rejection"]["plan"] != plan["rejection_plan"]
                ):
                    raise ValueError()
                for package in plan["packages"]:
                    reservations = self.facts.overlapping(
                        conn,
                        "research_reservations",
                        holdout["source"],
                        package["inst_id"],
                        plan["test_start"],
                        plan["test_end"],
                    )
                    if not any(
                        f["kind"] == "portfolio"
                        and f["holdout_id"] == holdout["id"]
                        and f["start_ts"] == plan["test_start"]
                        and f["end_ts"] == plan["test_end"]
                        for f in reservations
                    ):
                        raise ValueError()
            return {
                "protocol": "sealed_holdout_v2",
                "one_use": True,
                "primary_run_id": primary,
                "is_replay": run["id"] != primary,
                "rejection_status": evaluation["rejection"]["status"],
                "benchmark": evaluation["benchmark"],
                "holdout_id": holdout["id"],
                "plan_hash": holdout["plan_hash"],
                "scope": plan["scope"],
            }
        except (ValueError, KeyError, TypeError):
            raise PlatformError(
                "portfolio_governance_integrity",
                "Sealed portfolio evidence does not verify against its retained primary evaluation.",
                409,
            ) from None

    def trials(self, project_id):
        self.runtime.portfolio_registry.project(project_id)
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM research_trials WHERE kind='portfolio' AND project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
            totals = dict(
                recorded_attempts=len(rows),
                primary_evaluations=sum(r["attempt_type"] != "replay" for r in rows),
                replay_attempts=sum(r["attempt_type"] == "replay" for r in rows),
                candidate_configurations=sum(r["candidate_count"] for r in rows),
                distinct_configurations=len({r["config_hash"] for r in rows}),
            )
            items = []
            for row in rows[:100]:
                run = conn.execute(
                    "SELECT status,error FROM portfolio_runs WHERE id=?", (row["run_id"],)
                ).fetchone()
                self.facts.checked(row)
                items.append(
                    {k: v for k, v in dict(row).items() if k not in {"body", "content_hash"}}
                    | {
                        "config": json.loads(row["config"]),
                        "status": run["status"] if run else "evidence_unavailable",
                        "error": run["error"] if run else None,
                    }
                )
        return dict(
            project_id=project_id,
            **totals,
            items=items,
            holdouts=self.list(project_id=project_id),
            scope="All recorded admitted project attempts across versions, including failed and replayed work; replays are not independent evaluations. Unbound and external experiments cannot be assigned to this project; shared market-time exposure guards still include unbound runs.",
        )
