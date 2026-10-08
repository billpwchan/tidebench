"""Transactional virtual sleeves under one economic net position.

Reductions allocate quantity proportionally across existing owners; their own
entry costs determine their realized contribution. Fees and funding follow
those same quantities. Finite-context differences reconcile explicitly to the
actual economic book. This is contribution P&L, not independent sleeve equity
or a causal allocation of portfolio returns.
"""

import json
from contextlib import nullcontext
from decimal import Decimal, DecimalException, localcontext

from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .store import dumps, encode, now_ms
from .strategy_registry import digest

D = Decimal
FIELDS = ("quantity", "entry_notional", "realized", "fees", "funding")


def _integrity():
    return PlatformError("contribution_integrity", "Contribution record integrity check failed.", 409)


def _object(raw, content_hash=None):
    """Validate attribution-owned serialized data, never economic book data."""
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or content_hash is not None and digest(body) != content_hash:
            raise _integrity()
        return body
    except (TypeError, ValueError, RecursionError):
        raise _integrity() from None


def _amount(raw):
    # Exact allocation remainders legitimately exceed the book's 50-digit input
    # precision. Bound parsing work without rounding those retained digits.
    if not isinstance(raw, str) or not 1 <= len(raw) <= 4096:
        raise _integrity()
    try:
        value = D(raw)
        if not value.is_finite() or abs(value.as_tuple().exponent) > 4096 or abs(value.adjusted()) > 4096:
            raise _integrity()
        return value
    except DecimalException:
        raise _integrity() from None


def _baseline(raw, content_hash, source):
    body = _object(raw, content_hash)
    account, positions = body.get("account"), body.get("positions")
    if (
        not isinstance(account, dict)
        or account.get("source") != source
        or not isinstance(account.get("day_key"), str)
        or not isinstance(body.get("policy"), str)
        or not isinstance(positions, list)
    ):
        raise _integrity()
    for key in ("initial_cash", "cash", "realized", "fees", "funding", "debt", "day_equity"):
        _amount(account.get(key))
    for position in positions:
        if (
            not isinstance(position, dict)
            or position.get("source") != source
            or not isinstance(position.get("inst_id"), str)
            or not position["inst_id"]
            or type(position.get("funding_cursor")) is not int
        ):
            raise _integrity()
        for key in ("quantity", "entry_price", "margin", "basis", "leverage"):
            _amount(position.get(key))
        meta = _object(position.get("metadata"))
        if meta.get("inst_type") not in ("SPOT", "SWAP"):
            raise _integrity()
    return body


def exact_sum(values):
    values = list(values)
    if not values:
        return D(0)
    exponent = min(v.as_tuple().exponent for v in values)
    highest = max(v.adjusted() for v in values)
    with localcontext(ACCOUNTING_CONTEXT) as ctx:
        ctx.prec = max(100, highest - exponent + len(str(len(values))) + 3)
        return sum(values, D(0))


def difference(left, right):
    return exact_sum([left, right.copy_negate()])


def allocate(amount, weights):
    """The last positive owner receives the exact finite-decimal remainder."""
    result, total = {}, exact_sum(weights.values())
    positive = [(key, value) for key, value in sorted(weights.items()) if value]
    if not total:
        return result
    with localcontext(ACCOUNTING_CONTEXT):
        for key, weight in positive[:-1]:
            result[key] = amount * weight / total
    if positive:
        result[positive[-1][0]] = difference(amount, exact_sum(result.values()))
    return result


class ContributionBook:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS contribution_status(source TEXT PRIMARY KEY,reason TEXT NOT NULL,first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS contribution_accounts(source TEXT PRIMARY KEY,baseline TEXT NOT NULL,baseline_hash TEXT NOT NULL,initialized_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS contribution_sleeves(source TEXT NOT NULL,owner TEXT NOT NULL,inst_id TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(source,owner,inst_id));
                CREATE TABLE IF NOT EXISTS contribution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,reference TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,inst_id TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS contribution_events_history ON contribution_events(source,id DESC);
            """)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            for source in ("okx", "example"):
                if conn.execute("SELECT 1 FROM contribution_accounts WHERE source=?", (source,)).fetchone():
                    continue
                account = conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
                baseline = {
                    "account": dict(account),
                    "policy": "Pre-upgrade realized P&L, fees, funding and inventory are explicitly unattributed legacy; no strategy ownership is inferred.",
                    "positions": [],
                }
                if any(D(account[k]) for k in ("realized", "fees", "funding")):
                    body = self.empty(source, "legacy", "")
                    body.update({key: account[key] for key in ("realized", "fees", "funding")})
                    self.save(conn, body)
                for position in conn.execute(
                    "SELECT * FROM pro_positions WHERE source=? AND quantity!='0'", (source,)
                ).fetchall():
                    metadata = json.loads(position["metadata"])
                    body = self.empty(source, "legacy", position["inst_id"])
                    with localcontext(ACCOUNTING_CONTEXT):
                        unit = self.unit(metadata)
                        body.update(
                            quantity=position["quantity"],
                            entry_notional=position["basis"]
                            if metadata["inst_type"] == "SPOT"
                            else str(abs(D(position["quantity"])) * unit * D(position["entry_price"])),
                        )
                    self.save(conn, body)
                    baseline["positions"].append(dict(position))
                conn.execute(
                    "INSERT INTO contribution_accounts VALUES(?,?,?,?)",
                    (source, dumps(baseline), digest(baseline), now_ms()),
                )

    @staticmethod
    def unit(meta):
        return D(1) if meta["inst_type"] == "SPOT" else D(meta["ct_val"]) * D(meta["ct_mult"])

    @staticmethod
    def empty(source, owner, symbol):
        return {"source": source, "owner": owner, "inst_id": symbol, **dict.fromkeys(FIELDS, "0")}

    @staticmethod
    def decode(row):
        body = _object(row["body"], row["content_hash"])
        if (
            any(
                not isinstance(body.get(key), str) or body[key] != row[key]
                for key in ("source", "owner", "inst_id")
            )
            or not body["owner"]
        ):
            raise _integrity()
        values = {key: _amount(body.get(key)) for key in FIELDS}
        if values["entry_notional"] < 0 or values["fees"] < 0:
            raise _integrity()
        return body

    def rows(self, conn, source):
        return [
            self.decode(r)
            for r in conn.execute(
                "SELECT * FROM contribution_sleeves WHERE source=? ORDER BY owner,inst_id", (source,)
            )
        ]

    @staticmethod
    def save(conn, body):
        conn.execute(
            "INSERT INTO contribution_sleeves VALUES(?,?,?,?,?,?) ON CONFLICT(source,owner,inst_id) DO UPDATE SET body=excluded.body,content_hash=excluded.content_hash,updated_at=excluded.updated_at",
            (body["source"], body["owner"], body["inst_id"], dumps(body), digest(body), now_ms()),
        )

    @staticmethod
    def owner(conn, actor):
        if actor.startswith("strategy:"):
            row = conn.execute(
                "SELECT config FROM pro_deployments WHERE id=?", (actor.split(":", 1)[1],)
            ).fetchone()
            config = json.loads(row[0]) if row else {}
            return "portfolio:" + config["group_id"] if config.get("group_id") else actor
        return "manual:" + actor

    def check(self, conn, source, *, account=None, positions=None):
        account = account or conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
        positions = (
            positions
            if positions is not None
            else conn.execute(
                "SELECT * FROM pro_positions WHERE source=? AND quantity!='0'", (source,)
            ).fetchall()
        )
        rows = self.rows(conn, source)
        mismatches = {}
        for key in ("realized", "fees", "funding"):
            value = difference(exact_sum(D(r[key]) for r in rows), D(account[key]))
            if value:
                mismatches[key] = str(value)
        by_symbol = {p["inst_id"]: p for p in positions if D(p["quantity"])}
        for symbol in sorted(set(by_symbol) | {r["inst_id"] for r in rows if D(r["quantity"])}):
            sleeves = [r for r in rows if r["inst_id"] == symbol]
            position = by_symbol.get(symbol)
            qty = D(position["quantity"]) if position else D(0)
            if any(
                D(r["quantity"]) and (not qty or D(r["quantity"]) * qty < 0) or D(r["entry_notional"]) < 0
                for r in sleeves
            ):
                raise PlatformError(
                    "contribution_reconciliation",
                    "Virtual inventory direction or entry cost differs from the economic net position.",
                    409,
                )
            delta = difference(exact_sum(D(r["quantity"]) for r in sleeves), qty)
            if delta:
                mismatches[symbol + ":quantity"] = str(delta)
            with localcontext(ACCOUNTING_CONTEXT):
                meta = json.loads(position["metadata"]) if position else None
                entry = (
                    D(position["basis"])
                    if position and meta["inst_type"] == "SPOT"
                    else abs(qty) * self.unit(meta) * D(position["entry_price"])
                    if position
                    else D(0)
                )
            delta = difference(exact_sum(D(r["entry_notional"]) for r in sleeves), entry)
            if delta:
                mismatches[symbol + ":entry_notional"] = str(delta)
        return mismatches

    def _require(self, conn, source):
        status = conn.execute("SELECT reason FROM contribution_status WHERE source=?", (source,)).fetchone()
        if status:
            raise PlatformError(
                "contribution_quarantined",
                "Contribution attribution is quarantined; new risk is blocked, protective reductions remain available.",
                409,
            )
        baseline = conn.execute(
            "SELECT baseline,baseline_hash FROM contribution_accounts WHERE source=?", (source,)
        ).fetchone()
        if not baseline:
            raise _integrity()
        _baseline(baseline["baseline"], baseline["baseline_hash"], source)
        mismatches = self.check(conn, source)
        if mismatches:
            raise PlatformError(
                "contribution_reconciliation",
                "Contribution state differs from the economic book; economic mutation is blocked.",
                409,
            )

    def before(self, conn, source):
        self._require(conn, source)
        return dict(conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone())

    def _reconcile(self, conn, source, changed, scale):
        # This only settles finite-context differences after a legitimate event.
        # Existing state is checked *before* the economic mutation; corruption
        # is never silently rebased to a new book state.
        mismatches = self.check(conn, source)
        adjustments = {}
        with localcontext(ACCOUNTING_CONTEXT):
            bound = max(abs(scale), D(10000)) * D("1e-48") * max(20, len(changed) * 4)
        for key, raw in mismatches.items():
            residual = D(raw)
            if abs(residual) > bound:
                raise PlatformError(
                    "contribution_reconciliation",
                    "Contribution arithmetic exceeds its declared finite-precision reconciliation bound.",
                    409,
                )
            if ":" in key:
                symbol, field = key.rsplit(":", 1)
                candidates = [r for r in changed if r["inst_id"] == symbol]
            else:
                field, candidates = key, changed
            if not candidates:
                raise PlatformError(
                    "contribution_reconciliation",
                    "No legitimate event owner can receive the finite-precision residual.",
                    409,
                )
            row = max(candidates, key=lambda r: (D(r["quantity"]).copy_abs(), r["owner"]))
            row[field] = str(difference(D(row[field]), residual))
            self.save(conn, row)
            adjustments[key] = {
                "owner": row["owner"],
                "amount": str(residual.copy_negate()),
                "bound": str(bound),
            }
        self._require(conn, source)
        return adjustments

    def _event(self, conn, source, reference, kind, symbol, body):
        conn.execute(
            "INSERT INTO contribution_events(source,reference,kind,inst_id,body,content_hash,created_at) VALUES(?,?,?,?,?,?,?)",
            (source, reference, kind, symbol, dumps(body), digest(body), now_ms()),
        )

    def fill(self, conn, result, before, old_position, actor):
        source, symbol, meta = result["source"], result["inst_id"], result["instrument"]
        rows = [r for r in self.rows(conn, source) if r["inst_id"] == symbol]
        signed = D(result["quantity"]) * (1 if result["side"] == "buy" else -1)
        old = D(old_position["quantity"]) if old_position else D(0)
        changed = []
        with localcontext(ACCOUNTING_CONTEXT):
            fee, price, unit = D(result["fee"]), D(result["price"]), self.unit(meta)
            reducing = old * signed < 0
            if reducing:
                owners = {r["owner"]: D(r["quantity"]).copy_abs() for r in rows if D(r["quantity"])}
                quantities = owners.copy() if abs(signed) == abs(old) else allocate(abs(signed), owners)
                fees = allocate(fee, owners)
                for row in rows:
                    owner, qty = row["owner"], D(row["quantity"]).copy_abs()
                    if not qty:
                        continue
                    removed = quantities[owner]
                    entry_removed = (
                        D(row["entry_notional"]) * removed / qty
                        if removed != qty
                        else D(row["entry_notional"])
                    )
                    profit = price * unit * removed - entry_removed
                    if meta["inst_type"] == "SWAP" and old < 0:
                        profit = -profit
                    row["quantity"] = str(
                        difference(D(row["quantity"]), removed if old > 0 else removed.copy_negate())
                    )
                    row["entry_notional"] = str(difference(D(row["entry_notional"]), entry_removed))
                    row["realized"] = str(exact_sum([D(row["realized"]), profit, fees[owner].copy_negate()]))
                    row["fees"] = str(exact_sum([D(row["fees"]), fees[owner]]))
                    changed.append(row)
            else:
                owner = self.owner(conn, actor)
                row = next((r for r in rows if r["owner"] == owner), self.empty(source, owner, symbol))
                row["quantity"] = str(exact_sum([D(row["quantity"]), signed]))
                row["entry_notional"] = str(
                    exact_sum(
                        [
                            D(row["entry_notional"]),
                            abs(signed) * unit * price,
                            fee if meta["inst_type"] == "SPOT" else D(0),
                        ]
                    )
                )
                row["fees"] = str(exact_sum([D(row["fees"]), fee]))
                if meta["inst_type"] == "SWAP":
                    row["realized"] = str(difference(D(row["realized"]), fee))
                changed.append(row)
            for row in changed:
                self.save(conn, row)
            adjustments = self._reconcile(conn, source, changed, D(result["notional"]))
        self._event(
            conn,
            source,
            result["id"],
            "fill",
            symbol,
            {
                "order_id": result["id"],
                "actor": actor,
                "allocation": "proportional_existing_quantity" if reducing else "opening_owner",
                "before_account": {k: before[k] for k in ("realized", "fees", "funding")},
                "sleeves_after": changed,
                "finite_precision_adjustments": adjustments,
            },
        )

    def funding(self, conn, source, symbol, events):
        if not events:
            return
        rows = [r for r in self.rows(conn, source) if r["inst_id"] == symbol and D(r["quantity"])]
        with localcontext(ACCOUNTING_CONTEXT):
            amount = exact_sum(D(e["payment"]) for e in events)
            parts = allocate(amount, {r["owner"]: D(r["quantity"]).copy_abs() for r in rows})
            for row in rows:
                row["funding"] = str(exact_sum([D(row["funding"]), parts[row["owner"]]]))
                self.save(conn, row)
            adjustments = self._reconcile(conn, source, rows, amount)
        self._event(
            conn,
            source,
            f"funding:{symbol}:{events[0]['ts']}:{events[-1]['ts']}:{source}",
            "funding",
            symbol,
            {
                "events": events,
                "allocation": "proportional_existing_quantity",
                "sleeves_after": rows,
                "finite_precision_adjustments": adjustments,
            },
        )

    def quarantine(self, conn, source, reference, symbol, kind, evidence, reason):
        timestamp = now_ms()
        conn.execute(
            "INSERT INTO contribution_status VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET reason=excluded.reason,last_seen=excluded.last_seen",
            (source, reason[:1000], timestamp, timestamp),
        )
        conn.execute("UPDATE pro_risk SET halted=1,updated_at=? WHERE source=?", (timestamp, source))
        self._event(
            conn,
            source,
            "quarantine:" + source + ":" + reference,
            kind,
            symbol,
            {
                "attribution_status": "quarantined",
                "reason": reason[:1000],
                "economic_evidence": evidence,
                "ownership_policy": "No ownership is invented or repaired. Original sleeves remain unchanged for investigation.",
            },
        )
        self.store.audit(
            conn,
            source,
            "contribution.quarantined",
            "Attribution quarantined; economic risk reduction preserved",
            {"reference": reference, "reason": reason[:1000]},
        )

    def statuses(self):
        with self.store.read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM contribution_status")]

    def events(self, source, *, owner=None, before=2**63 - 1, limit=100):
        if not 1 <= limit <= 100 or not 0 <= before <= 2**63 - 1:
            raise PlatformError("contribution_history", "Invalid contribution history page.", 422)
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT e.*,o.body order_body FROM contribution_events e LEFT JOIN pro_orders o ON o.id=e.reference AND o.source=e.source WHERE e.source=? AND e.id<? AND (? IS NULL OR EXISTS(SELECT 1 FROM json_each(e.body,'$.sleeves_after') s WHERE json_extract(s.value,'$.owner')=?)) ORDER BY e.id DESC LIMIT ?",
                (source, before, owner, owner, limit),
            ).fetchall()
        result = []
        for row in rows:
            body = _object(row["body"], row["content_hash"])
            result.append(
                dict(row)
                | {"body": body, "order": json.loads(row["order_body"]) if row["order_body"] else None}
            )
        return result

    def report(self, source, account, conn=None):
        with (
            localcontext(ACCOUNTING_CONTEXT),
            nullcontext(conn) if conn is not None else self.store.read() as conn,
        ):
            self._require(conn, source)
            rows = self.rows(conn, source)
            positions = {p["inst_id"]: p for p in account["positions"]}
            grouped = {}
            for row in rows:
                position = positions.get(row["inst_id"])
                qty = D(row["quantity"])
                upnl = D(0)
                if qty:
                    if not position or position["mark"] is None:
                        upnl = None
                    else:
                        value = (
                            abs(qty)
                            * self.unit(
                                json.loads(
                                    conn.execute(
                                        "SELECT metadata FROM pro_positions WHERE source=? AND inst_id=?",
                                        (source, row["inst_id"]),
                                    ).fetchone()[0]
                                )
                            )
                            * D(position["mark"])
                        )
                        upnl = value - D(row["entry_notional"])
                        if qty < 0:
                            upnl = -upnl
                owner = grouped.setdefault(
                    row["owner"],
                    {
                        "owner": row["owner"],
                        "realized_pnl": D(0),
                        "fees_paid": D(0),
                        "funding_paid": D(0),
                        "unrealized_pnl": D(0),
                        "markets": [],
                    },
                )
                for out, key in [
                    ("realized_pnl", "realized"),
                    ("fees_paid", "fees"),
                    ("funding_paid", "funding"),
                ]:
                    owner[out] = exact_sum([owner[out], D(row[key])])
                owner["unrealized_pnl"] = (
                    exact_sum([owner["unrealized_pnl"], upnl])
                    if owner["unrealized_pnl"] is not None and upnl is not None
                    else None
                )
                if row["inst_id"]:
                    owner["markets"].append(row | {"unrealized_pnl": upnl})
            for owner in grouped.values():
                owner["net_pnl"] = (
                    owner["realized_pnl"] + owner["unrealized_pnl"] - owner["funding_paid"]
                    if owner["unrealized_pnl"] is not None
                    else None
                )
            totals = {
                key: exact_sum(owner[key] for owner in grouped.values())
                if all(owner[key] is not None for owner in grouped.values())
                else None
                for key in ("realized_pnl", "fees_paid", "funding_paid", "unrealized_pnl", "net_pnl")
            }
            expected = (
                D(account["realized_pnl"]) + D(account["unrealized_pnl"]) - D(account["funding_paid"])
                if account["unrealized_pnl"] is not None
                else None
            )
            delta = (
                totals["net_pnl"] - expected
                if expected is not None and totals["net_pnl"] is not None
                else None
            )
            return encode(
                {
                    "owners": list(grouped.values()),
                    "totals": totals,
                    "account_net_pnl": expected,
                    "valuation_status": account["valuation_status"],
                    "reconciliation_delta": delta,
                    "reconciled": delta is not None
                    and abs(delta) <= max(abs(expected), D(10000)) * D("1e-46"),
                    "policy": "Proportional net-position reductions; each sleeve retains its own entry costs. Spot entry fees are in basis; swap fees are realized. Net P&L = realized + unrealized − funding. Fees and insurance debt are disclosures, not a second deduction. This is monetary contribution, not independent strategy return.",
                    "legacy_policy": "Pre-upgrade balances remain explicitly unattributed legacy.",
                    "as_of": account["as_of"],
                }
            )
