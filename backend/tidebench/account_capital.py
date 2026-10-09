"""Durable capital promises and gross underlying budgets for one paper account.

Promises reserve a percentage of current equity, not cash or a statistical risk
forecast. Admission uses max(actual marked use + pending orders, promised use)
per owner. Spot and perpetual absolute exposures to the same underlying add;
offsetting delta never hides gross concentration. BEGIN IMMEDIATE is required
for changes/admission so competing releases cannot both promise the same capital.
"""

import json
from contextlib import nullcontext
from decimal import Decimal, DecimalException, localcontext
from functools import wraps

from .contributions import ContributionBook
from .engine import ACCOUNTING_CONTEXT
from .pending_funding import _read as funding_obligation
from .platform import PlatformError
from .store import dumps, encode, now_ms
from .strategy_registry import digest

D = Decimal
DEFAULT_POLICY = {"capital_limit_pct": "100", "max_base_asset_gross_pct": "100"}
LIVE = {"reserved", "retained"}


def accounted(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext(ACCOUNTING_CONTEXT):
            return function(*args, **kwargs)

    return wrapped


def amount(value):
    try:
        result = D(str(value))
        if (
            not result.is_finite()
            or abs(result.adjusted()) > 1000
            or abs(result.as_tuple().exponent) > 1000
            or len(result.as_tuple().digits) > 1000
        ):
            raise ValueError
        return result
    except (ValueError, DecimalException):
        raise PlatformError(
            "account_capital_integrity", "Capital evidence contains an invalid amount.", 409
        ) from None


@accounted
def checked(row):
    try:
        body = json.loads(row["body"])
        if not isinstance(body, dict) or digest(body) != row["content_hash"]:
            raise ValueError
        if body.get("source") != row["source"]:
            raise ValueError
        if "owner" in row.keys():
            if body.get("owner") != row["owner"] or not isinstance(body.get("definition_hash"), str):
                raise ValueError
            if not 0 < amount(body.get("capital_pct")) <= 100:
                raise ValueError
            markets, assets = body.get("market_gross_pct"), body.get("base_asset_gross_pct")
            if not isinstance(markets, dict) or not isinstance(assets, dict):
                raise ValueError
            expected = {}
            for symbol, pct in sorted(markets.items()):
                if (
                    not isinstance(symbol, str)
                    or not symbol.endswith(("-USDT", "-USDT-SWAP"))
                    or amount(pct) < 0
                ):
                    raise ValueError
                base = symbol.split("-")[0]
                expected[base] = expected.get(base, D(0)) + amount(pct)
            if {k: amount(v) for k, v in assets.items()} != expected or amount(body.get("gross_pct")) != sum(
                expected.values(), D(0)
            ):
                raise ValueError
            if row["status"] not in {"reserved", "retained", "released"}:
                raise ValueError
        elif (
            amount(body.get("capital_limit_pct")) != 100
            or not 1 <= amount(body.get("max_base_asset_gross_pct")) <= 1000
        ):
            raise ValueError
        return body
    except (ValueError, TypeError):
        raise PlatformError(
            "account_capital_integrity", "Capital commitment integrity check failed.", 409
        ) from None


def unit(meta):
    return D(1) if meta["inst_type"] == "SPOT" else amount(meta["ct_val"]) * amount(meta["ct_mult"])


def capital_cash_budget(account, capital_pct, marked_owned_capital_and_pending):
    """Cash allowance covers new capital and all predicted entry fees.

    For percentage P<=100, A + P*fees <= A + fees <= P*equity - marked
    use. Each immutable child still passes fresh transaction admission.
    """
    if account.get("equity") is None or account.get("valuation_status") not in {"fresh", "example"}:
        raise PlatformError(
            "account_capital_valuation", "Complete fresh valuation is required for capital admission.", 409
        )
    with localcontext(ACCOUNTING_CONTEXT):
        pct = amount(capital_pct)
        if not 0 < pct <= 100:
            raise PlatformError(
                "account_capital_policy",
                "Portfolio capital percentage must be greater than zero and at most 100%.",
                422,
            )
        equity, used = amount(account["equity"]), amount(marked_owned_capital_and_pending)
        if used < 0:
            raise PlatformError("account_capital_policy", "Marked capital use cannot be negative.", 422)
        limit = max(D(0), equity * pct / 100)
        remaining = max(D(0), limit - used)
        cash = max(D(0), min(amount(account["available_cash"]), remaining))
        return encode(
            {
                "commitment_pct": pct,
                "account_equity": equity,
                "marked_owned_capital_and_pending": used,
                "capital_ceiling": limit,
                "remaining_capital_including_entry_costs": remaining,
                "account_available_cash": account["available_cash"],
                "budget_cash": cash,
                "basis": "spot_marked_value_or_swap_posted_margin_plus_pending; new_fill_capital_and_full_entry_fees_share_remaining_budget",
                "quote_change_policy": "each_child_rechecked_at_its_fresh_execution_quote_no_tolerance",
            }
        )


class AccountCapital:
    def __init__(self, store, contributions=None):
        self.store, self.contributions = store, contributions
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_capital_policy(source TEXT PRIMARY KEY,body TEXT NOT NULL,content_hash TEXT NOT NULL,updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS account_capital_commitments(source TEXT NOT NULL,owner TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(source,owner));
                CREATE INDEX IF NOT EXISTS account_capital_active ON account_capital_commitments(source,status);
            """)
            for source in ("okx", "example"):
                body = DEFAULT_POLICY | {"source": source}
                conn.execute(
                    "INSERT OR IGNORE INTO account_capital_policy VALUES(?,?,?,?)",
                    (source, dumps(body), digest(body), now_ms()),
                )

    @staticmethod
    def _transaction(conn):
        if not conn.in_transaction:
            raise RuntimeError("Capital changes and admission require the caller's write transaction.")

    def policy(self, source, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM account_capital_policy WHERE source=?", (source,)
            ).fetchone()
            if not row:
                raise PlatformError("invalid_source", "Unknown execution source.", 422)
            body = checked(row)
            if (
                amount(body["capital_limit_pct"]) != 100
                or not 1 <= amount(body["max_base_asset_gross_pct"]) <= 1000
            ):
                raise PlatformError(
                    "account_capital_integrity", "Capital policy bounds cannot be verified.", 409
                )
            return body

    def set_policy(self, source, values, actor, conn=None):
        if set(values) != {"max_base_asset_gross_pct"}:
            raise PlatformError(
                "account_capital_policy",
                "Set the base-asset gross percentage only; total capital promises are capped at 100%.",
                422,
            )
        limit = amount(values["max_base_asset_gross_pct"])
        if not 1 <= limit <= 1000:
            raise PlatformError(
                "account_capital_policy", "Base-asset gross limit must be between 1% and 1000%.", 422
            )
        with nullcontext(conn) if conn is not None else self.store.write() as connection:
            self._transaction(connection)
            body = self.policy(source, connection) | {"max_base_asset_gross_pct": str(limit)}
            connection.execute(
                "UPDATE account_capital_policy SET body=?,content_hash=?,updated_at=? WHERE source=?",
                (dumps(body), digest(body), now_ms(), source),
            )
            self.store.audit(
                connection,
                source,
                "account.capital_policy",
                "Account underlying gross budget updated; existing positions retained",
                {"policy": body, "actor": actor},
            )
            return body

    def commitments(self, source, conn=None, *, active_only=False):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            rows = connection.execute(
                "SELECT * FROM account_capital_commitments WHERE source=?"
                + (" AND status!='released'" if active_only else "")
                + " ORDER BY owner",
                (source,),
            ).fetchall()
            return [dict(row) | {"body": checked(row)} for row in rows]

    @staticmethod
    @accounted
    def promise(source, owner, definition, *, legacy=False):
        capital = amount(definition["capital_pct"])
        markets = {leg["inst_id"]: capital * abs(amount(leg["weight"])) for leg in definition["legs"]}
        base_assets = {}
        for symbol, pct in sorted(markets.items()):
            base = symbol.split("-")[0]
            base_assets[base] = base_assets.get(base, D(0)) + pct
        return encode(
            {
                "source": source,
                "owner": owner,
                "capital_pct": capital,
                "market_gross_pct": markets,
                "base_asset_gross_pct": base_assets,
                "gross_pct": sum((base_assets[k] for k in sorted(base_assets)), D(0)),
                "legacy_backfill": legacy,
                "definition_hash": digest(definition),
                "basis": "current_account_equity_percentage_not_cash_or_covariance_risk",
            }
        )

    def _save(self, conn, body, status):
        self._transaction(conn)
        timestamp = now_ms()
        conn.execute(
            "INSERT INTO account_capital_commitments VALUES(?,?,?,?,?,?,?) ON CONFLICT(source,owner) DO UPDATE SET body=excluded.body,content_hash=excluded.content_hash,status=excluded.status,updated_at=excluded.updated_at",
            (body["source"], body["owner"], dumps(body), digest(body), status, timestamp, timestamp),
        )

    def bootstrap(self, conn):
        """Retain every existing declaration; an old overcommit is never erased."""
        self._transaction(conn)
        for row in conn.execute("SELECT * FROM managed_portfolios").fetchall():
            owner = "portfolio:" + row["id"]
            if conn.execute(
                "SELECT 1 FROM account_capital_commitments WHERE source=? AND owner=?", (row["source"], owner)
            ).fetchone():
                continue
            try:
                manifest = json.loads(row["manifest"])
                if digest(manifest) != row["manifest_hash"]:
                    raise ValueError
                body = self.promise(row["source"], owner, manifest["definition"], legacy=True)
            except (ValueError, KeyError, TypeError):
                # Damaged legacy evidence cannot grant a budget to another owner.
                symbols = [
                    r[0]
                    for r in conn.execute(
                        "SELECT inst_id FROM pro_deployments WHERE source=? AND json_valid(config) AND json_extract(config,'$.group_id')=?",
                        (row["source"], row["id"]),
                    )
                ]
                body = self.promise(
                    row["source"],
                    owner,
                    {"capital_pct": 100, "legs": [{"inst_id": s, "weight": 1} for s in symbols]},
                    legacy=True,
                )
                body["unverified_legacy_manifest"] = True
            self._save(conn, body, "reserved" if row["status"] in {"running", "compensating"} else "retained")
            self.store.audit(
                conn,
                row["source"],
                "account.capital_backfilled",
                "Existing portfolio declaration retained during capital-budget upgrade",
                {"owner": owner, "capital_pct": body["capital_pct"]},
            )
        for source in ("okx", "example"):
            self.reconcile(source, conn)

    def _release_reason(self, conn, row):
        body, owner = row["body"], row["owner"]
        group = conn.execute(
            "SELECT status FROM managed_portfolios WHERE id=?", (owner.removeprefix("portfolio:"),)
        ).fetchone()
        if group and group["status"] in {"running", "compensating"}:
            return "controller_active"
        if self.contributions is None:
            return "attribution_unavailable"
        try:
            self.contributions._require(conn, row["source"])
            sleeves = self.contributions.rows(conn, row["source"])
        except PlatformError:
            return "attribution_unverified"
        if any(s["owner"] == owner and amount(s["quantity"]) for s in sleeves):
            return "owned_inventory"
        # Pre-upgrade ownership was intentionally unattributed. Do not silently
        # infer flatness while an old group's markets still carry legacy units.
        if body.get("legacy_backfill") and any(
            s["owner"] == "legacy" and s["inst_id"] in body["market_gross_pct"] and amount(s["quantity"])
            for s in sleeves
        ):
            return "legacy_inventory_unattributed"
        for order in conn.execute(
            "SELECT body FROM pro_orders WHERE source=? AND status='pending'", (row["source"],)
        ):
            try:
                record = json.loads(order[0])
            except (ValueError, TypeError):
                return "pending_order_evidence_unverified"
            if not isinstance(record, dict) or not isinstance(record.get("actor", "legacy"), str):
                return "pending_order_evidence_unverified"
            try:
                pending_owner = ContributionBook.owner(conn, record.get("actor", "legacy"))
            except (ValueError, TypeError, PlatformError):
                return "pending_order_evidence_unverified"
            if pending_owner == owner:
                return "pending_orders"
        if conn.execute(
            "SELECT 1 FROM portfolio_commands c JOIN portfolio_batches b ON b.id=c.batch_id WHERE b.group_id=? AND c.status='pending' LIMIT 1",
            (owner.removeprefix("portfolio:"),),
        ).fetchone():
            return "pending_commands"
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pro_funding_obligations'"
        ).fetchone():
            for event in conn.execute(
                "SELECT * FROM pro_funding_obligations WHERE source=? AND status='pending'",
                (row["source"],),
            ):
                try:
                    obligation = funding_obligation(event)
                except PlatformError:
                    return "funding_evidence_unverified"
                owners = obligation.get("owners")
                if (
                    isinstance(owners, dict)
                    and owner in owners
                    or owners is None
                    and event["inst_id"] in body["market_gross_pct"]
                ):
                    return "unsettled_funding"
        if body.get("unverified_legacy_manifest"):
            return "legacy_manifest_unverified"
        return None

    def reconcile(self, source, conn):
        """Release terminal promises only on verified flatness, never on stop alone."""
        self._transaction(conn)
        for row in self.commitments(source, conn, active_only=True):
            reason = self._release_reason(conn, row)
            status = "reserved" if reason == "controller_active" else "retained" if reason else "released"
            if status != row["status"]:
                conn.execute(
                    "UPDATE account_capital_commitments SET status=?,updated_at=? WHERE source=? AND owner=?",
                    (status, now_ms(), source, row["owner"]),
                )
                self.store.audit(
                    conn,
                    source,
                    "account.capital_" + status,
                    "Portfolio capital promise " + status,
                    {"owner": row["owner"], "reason": reason, "capital_pct": row["body"]["capital_pct"]},
                )

    def terminal(self, source, owner, conn):
        # Stopping a controller is protective. Damaged budget evidence retains
        # the promise and blocks new risk; it must not prevent the stop itself.
        try:
            self.reconcile(source, conn)
            return self.get(source, owner, conn)
        except PlatformError as exc:
            conn.execute(
                "UPDATE account_capital_commitments SET status='retained',updated_at=? WHERE source=? AND owner=? AND status!='released'",
                (now_ms(), source, owner),
            )
            self.store.audit(
                conn,
                source,
                "account.capital_retention_unverified",
                "Stopped portfolio retains capital because budget evidence cannot be verified",
                {"owner": owner, "code": exc.code},
            )
            return None

    def get(self, source, owner, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM account_capital_commitments WHERE source=? AND owner=?", (source, owner)
            ).fetchone()
            if not row:
                return None
            result = dict(row) | {"body": checked(row)}
            result["retention_reason"] = (
                self._release_reason(connection, result) if result["status"] in LIVE else None
            )
            return result

    @accounted
    def preview(self, source, definition, conn=None, *, account=None):
        """Deterministic declaration admission; actual use is rechecked per order."""
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            policy = self.policy(source, connection)
            rows = self.commitments(source, connection, active_only=True)
            proposed = self.promise(source, "proposed", definition)
            capital = sum((amount(r["body"]["capital_pct"]) for r in rows), D(0))
            gross = sum((amount(r["body"]["gross_pct"]) for r in rows), D(0))
            assets = {}
            for body in [*(r["body"] for r in rows), proposed]:
                for base, pct in body["base_asset_gross_pct"].items():
                    assets[base] = assets.get(base, D(0)) + amount(pct)
            risk = json.loads(
                connection.execute("SELECT body FROM pro_risk WHERE source=?", (source,)).fetchone()[0]
            )
            blockers = (
                (["account_capital_overcommitted"] if capital + amount(proposed["capital_pct"]) > 100 else [])
                + (
                    ["account_promised_gross_limit"]
                    if gross + amount(proposed["gross_pct"]) > amount(risk["max_gross_exposure_pct"])
                    else []
                )
                + (
                    ["account_base_asset_limit"]
                    if any(v > amount(policy["max_base_asset_gross_pct"]) for v in assets.values())
                    else []
                )
            )
            actual_admission = None
            if account is not None:
                if account.get("equity") is None or account.get("valuation_status") not in {
                    "fresh",
                    "example",
                }:
                    blockers.append("account_capital_valuation")
                else:
                    actual_admission = self.exposure(connection, source, account, proposed_promise=proposed)
                    if amount(actual_admission["capital_committed_or_used_pct"]) > 100:
                        blockers.append("account_capital_overcommitted")
                    if any(
                        amount(v) > amount(policy["max_base_asset_gross_pct"])
                        for v in actual_admission["base_asset_gross_pct"].values()
                    ):
                        blockers.append("account_base_asset_limit")
                    if amount(actual_admission["gross_committed_or_used_pct"]) > amount(
                        risk["max_gross_exposure_pct"]
                    ):
                        blockers.append("account_promised_gross_limit")
            return encode(
                {
                    "actual_admission": actual_admission,
                    "policy": policy,
                    "committed_capital_pct": capital,
                    "remaining_declared_capital_pct": max(D(0), 100 - capital),
                    "proposed_capital_pct": proposed["capital_pct"],
                    "projected_committed_capital_pct": capital + amount(proposed["capital_pct"]),
                    "projected_promised_gross_pct": gross + amount(proposed["gross_pct"]),
                    "projected_base_asset_gross_pct": assets,
                    "owners": [
                        {"owner": r["owner"], "status": r["status"], "capital_pct": r["body"]["capital_pct"]}
                        for r in rows
                    ],
                    "blockers": sorted(set(blockers)),
                    "actual_valuation": "rechecked_transactionally_at_each_new_risk_order",
                    "concentration_policy": "sum_absolute_spot_and_swap_exposure_same_base_no_netting",
                }
            )

    def reserve(self, conn, source, owner, definition, actor, *, account=None):
        self._transaction(conn)
        existing = self.get(source, owner, conn)
        promised = self.promise(source, owner, definition)
        if existing:
            if existing["body"] != promised or existing["status"] == "released":
                raise PlatformError(
                    "account_capital_identity",
                    "An existing capital promise cannot be reused with a different definition or after release.",
                    409,
                )
            return existing
        self.reconcile(source, conn)
        preview = self.preview(source, definition, conn, account=account)
        if preview["blockers"]:
            raise PlatformError(
                preview["blockers"][0],
                "Account capital or underlying promise exceeds the declared budget; reduce the allocation or review the policy.",
                409,
            )
        self._save(conn, promised, "reserved")
        self.store.audit(
            conn,
            source,
            "account.capital_reserved",
            "Portfolio capital admitted before deployment in the same transaction",
            {"owner": owner, "actor": actor, "promise": promised},
        )
        return self.get(source, owner, conn)

    @accounted
    def _actual(self, conn, source, account, *, exclude_order_id=None):
        """Marked actual use per owner plus durable pending new-risk orders."""
        if account.get("source") != source:
            raise PlatformError(
                "account_capital_valuation",
                "The account valuation source does not match the capital promise.",
                409,
            )
        if account.get("equity") is None or account["valuation_status"] not in {"fresh", "example"}:
            raise PlatformError(
                "account_capital_valuation",
                "Complete fresh valuation is required for capital admission.",
                409,
            )
        owners = {}

        def add(owner, base, capital, gross):
            row = owners.setdefault(owner, {"capital": D(0), "gross": {}})
            row["capital"] += capital
            row["gross"][base] = row["gross"].get(base, D(0)) + gross

        if self.contributions:
            self.contributions._require(conn, source)
            sleeves = self.contributions.rows(conn, source)
        else:
            sleeves = []
        for position in account["positions"]:
            qty = abs(amount(position["quantity"]))
            if not qty:
                continue
            meta = position["instrument"]
            value = amount(position["market_value"])
            capital = value if meta["inst_type"] == "SPOT" else max(D(0), amount(position["margin"]))
            matching = [r for r in sleeves if r["inst_id"] == position["inst_id"] and amount(r["quantity"])]
            if matching:
                for sleeve in matching:
                    share = abs(amount(sleeve["quantity"])) / qty
                    add(sleeve["owner"], meta["base"], capital * share, value * share)
            else:
                add("legacy", meta["base"], capital, value)
        for row in conn.execute(
            "SELECT id,payload,body FROM pro_orders WHERE source=? AND status='pending' AND (? IS NULL OR id!=?)",
            (source, exclude_order_id, exclude_order_id),
        ):
            payload, body = json.loads(row["payload"]), json.loads(row["body"])
            if payload.get("reduce_only"):
                continue
            meta = body["instrument"]
            gross = amount(body["notional"])
            capital = gross if meta["inst_type"] == "SPOT" else gross / amount(payload.get("leverage", 1))
            add(ContributionBook.owner(conn, body.get("actor", "legacy")), meta["base"], capital, gross)
        return owners

    def exposure(
        self, conn, source, account, *, candidate=None, exclude_order_id=None, proposed_promise=None
    ):
        with localcontext(ACCOUNTING_CONTEXT):
            equity = amount(account["equity"])
            if equity <= 0:
                raise PlatformError(
                    "account_capital_insolvent", "Positive equity is required for capital admission.", 409
                )
            actual = self._actual(conn, source, account, exclude_order_id=exclude_order_id)
            if candidate:
                owner, base, capital, gross = candidate
                row = actual.setdefault(owner, {"capital": D(0), "gross": {}})
                row["capital"] += capital
                row["gross"][base] = row["gross"].get(base, D(0)) + gross
            promises = {r["owner"]: r["body"] for r in self.commitments(source, conn, active_only=True)}
            if proposed_promise is not None:
                promises[proposed_promise["owner"]] = proposed_promise
            capital_total, assets, details = D(0), {}, []
            for owner in sorted(set(actual) | set(promises)):
                use = actual.get(owner, {"capital": D(0), "gross": {}})
                promise = promises.get(owner, {"capital_pct": "0", "base_asset_gross_pct": {}})
                actual_pct = use["capital"] / equity * 100
                reserved_pct = amount(promise["capital_pct"])
                capital_total += max(actual_pct, reserved_pct)
                owner_assets = {}
                for base in set(use["gross"]) | set(promise["base_asset_gross_pct"]):
                    pct = max(
                        use["gross"].get(base, D(0)) / equity * 100,
                        amount(promise["base_asset_gross_pct"].get(base, 0)),
                    )
                    assets[base] = assets.get(base, D(0)) + pct
                    owner_assets[base] = pct
                details.append(
                    {
                        "owner": owner,
                        "actual_capital_pct": actual_pct,
                        "promised_capital_pct": reserved_pct,
                        "base_asset_gross_pct": owner_assets,
                    }
                )
            return encode(
                {
                    "equity": account["equity"],
                    "as_of": account.get("as_of"),
                    "valuation_status": account.get("valuation_status"),
                    "capital_committed_or_used_pct": capital_total,
                    "base_asset_gross_pct": assets,
                    "gross_committed_or_used_pct": sum(assets.values(), D(0)),
                    "owners": details,
                    "policy": self.policy(source, conn),
                }
            )

    def addition_budget(self, conn, source, owner, account):
        promise = self.get(source, owner, conn)
        if not promise or promise["status"] != "reserved":
            raise PlatformError(
                "account_capital_unreserved",
                "A portfolio needs its admitted capital promise before additions.",
                409,
            )
        with localcontext(ACCOUNTING_CONTEXT):
            actual = self._actual(conn, source, account)
            used = actual.get(owner, {"capital": D(0)})["capital"]
            return capital_cash_budget(account, promise["body"]["capital_pct"], used) | {"owner": owner}

    def admit_order(
        self,
        conn,
        source,
        actor,
        account,
        metadata,
        signed_quantity,
        fill_price,
        *,
        leverage=1,
        fee=0,
        mark_price=None,
        exclude_order_id=None,
    ):
        """Call only for increases; protective reductions bypass these limits."""
        self._transaction(conn)
        self.reconcile(source, conn)
        owner = ContributionBook.owner(conn, actor)
        with localcontext(ACCOUNTING_CONTEXT):
            signed = amount(signed_quantity)
            fill, mark = amount(fill_price), amount(fill_price if mark_price is None else mark_price)
            costs = amount(fee)
            if mark <= 0 or fill <= 0 or costs < 0:
                raise PlatformError(
                    "account_capital_valuation",
                    "Projected admission requires positive prices and nonnegative execution fees.",
                    409,
                )
            fill_notional = abs(signed) * unit(metadata) * fill
            gross = abs(signed) * unit(metadata) * mark
            capital = gross if metadata["inst_type"] == "SPOT" else fill_notional / amount(leverage)
            projected_equity = amount(account["equity"]) - costs + signed * unit(metadata) * (mark - fill)
            projected_account = account | {"equity": str(projected_equity)}
            evidence = self.exposure(
                conn,
                source,
                projected_account,
                candidate=(owner, metadata["base"], capital, gross),
                exclude_order_id=exclude_order_id,
            )
            evidence |= {
                "pre_fill_equity": str(account["equity"]),
                "projected_post_fill_equity": str(projected_equity),
                "candidate_fee": str(costs),
                "candidate_marked_gross": str(gross),
                "valuation_basis": "post_fill_equity_including_fee_and_fill_to_mark_pnl",
            }
            promise = self.get(source, owner, conn)
            if owner.startswith("portfolio:") and (not promise or promise["status"] != "reserved"):
                raise PlatformError(
                    "account_capital_unreserved",
                    "A running portfolio needs its admitted capital commitment before new risk.",
                    409,
                )
            if promise:
                owned = next(r for r in evidence["owners"] if r["owner"] == owner)
                if amount(owned["actual_capital_pct"]) > amount(promise["body"]["capital_pct"]):
                    raise PlatformError(
                        "portfolio_capital_limit",
                        "The portfolio's marked use and pending orders exceed its admitted capital percentage.",
                        409,
                    )
            if amount(evidence["capital_committed_or_used_pct"]) > 100:
                raise PlatformError(
                    "account_capital_limit",
                    "Marked use, pending orders and unused portfolio promises exceed account capital.",
                    409,
                )
            if any(
                amount(v) > amount(evidence["policy"]["max_base_asset_gross_pct"])
                for v in evidence["base_asset_gross_pct"].values()
            ):
                raise PlatformError(
                    "account_base_asset_limit",
                    "Spot and perpetual gross exposure plus outstanding promises exceed the same-underlying limit.",
                    409,
                )
            risk = json.loads(
                conn.execute("SELECT body FROM pro_risk WHERE source=?", (source,)).fetchone()[0]
            )
            if amount(evidence["gross_committed_or_used_pct"]) > amount(risk["max_gross_exposure_pct"]):
                raise PlatformError(
                    "account_promised_gross_limit",
                    "Actual and promised account gross exposure exceeds the account risk policy.",
                    409,
                )
            return evidence
