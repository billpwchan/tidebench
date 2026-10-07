"""Persistent, source-isolated spot and linear-swap simulation with a native-asset journal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException, localcontext
from functools import wraps

from .derivatives import LinearContract, MarginTier, contract_base_quantity, select_margin_tier
from .engine import ACCOUNTING_CONTEXT, EngineError, _decimal, _floor_lot, _round_to_step
from .platform import PlatformError
from .store import Store, dumps, new_id, now_ms

D = Decimal
ZERO = D(0)
DEFAULT_RISK = {
    "max_order_notional": "2500",
    "max_gross_exposure_pct": 200,
    "max_leverage": 10,
    "max_daily_loss_pct": 5,
    "halted": False,
    "halt_reason": "",
    "fee_bps": "10",
    "slippage_bps": "5",
    "liquidation_fee_bps": "50",
}


def _accounted(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with localcontext(ACCOUNTING_CONTEXT):
                return function(*args, **kwargs)
        except (DecimalException, EngineError) as exc:
            raise PlatformError(
                "accounting_domain", f"Simulation accounting rejected the request: {exc}", 422
            ) from exc

    return wrapped


def number(value):
    try:
        raw = str(value)
        if len(raw) > 160:
            raise ValueError
        result = D(raw)
        _decimal(result, "simulation number")
        if not result.is_finite() or result.copy_abs() >= D("1e30") or (result and result.adjusted() < -80):
            raise ValueError
        return result
    except (ValueError, ArithmeticError):
        raise PlatformError(
            "invalid_number",
            "A finite supported decimal value is required (50 significant digits, abs < 1e30).",
            422,
        ) from None


def _exact_sum(values):
    values = list(values)
    if not values:
        return ZERO
    exponent = min(value.as_tuple().exponent for value in values)
    highest = max(value.adjusted() for value in values)
    with localcontext(ACCOUNTING_CONTEXT) as context:
        context.prec = max(100, highest - exponent + len(str(len(values))) + 3)
        return sum(values, ZERO)


def _cash_journal(journal, before, after):
    # Ledger cash records the actual persisted balance delta. Extra precision
    # only takes an exact difference of finite decimals; it changes no balance.
    delta = _exact_sum([after, before.copy_negate()])
    return [entry for entry in journal if not (entry[0] == "USDT" and entry[1] == "cash")] + [
        ("USDT", "cash", delta)
    ]


def base_size(metadata):
    if metadata["inst_type"] == "SPOT":
        return D(1)
    record = {**metadata}
    for key in ("ct_val", "ct_mult", "tick_size", "lot_size", "min_size"):
        record[key] = number(record.get(key))
    return contract_base_quantity(D(1), LinearContract.from_catalog(record))


def tier_for(metadata, quantity, tiers):
    if metadata["inst_type"] == "SPOT":
        return {"mmr": "0", "imr": "1", "max_leverage": "1"}
    parsed = []
    for raw in tiers:
        record = {
            **raw,
            "min_contracts": number(raw.get("min_contracts", raw.get("min_size", raw.get("minSz", 0)))),
            "max_contracts": number(raw.get("max_contracts", raw.get("max_size", raw.get("maxSz")))),
            "imr": number(raw["imr"]),
            "mmr": number(raw["mmr"]),
            "max_leverage": number(raw.get("max_leverage", raw.get("maxLever"))),
        }
        parsed.append(MarginTier.from_record(record))
    tier = select_margin_tier(quantity, parsed)
    return {"mmr": str(tier.mmr), "imr": str(tier.imr), "max_leverage": str(tier.max_leverage)}


class SimulationBook:
    def __init__(self, store: Store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pro_accounts(source TEXT PRIMARY KEY,initial_cash TEXT NOT NULL,cash TEXT NOT NULL,realized TEXT NOT NULL DEFAULT '0',fees TEXT NOT NULL DEFAULT '0',funding TEXT NOT NULL DEFAULT '0',debt TEXT NOT NULL DEFAULT '0',day_key TEXT NOT NULL DEFAULT '',day_equity TEXT NOT NULL DEFAULT '10000');
                CREATE TABLE IF NOT EXISTS pro_positions(source TEXT NOT NULL,inst_id TEXT NOT NULL,metadata TEXT NOT NULL,quantity TEXT NOT NULL,entry_price TEXT NOT NULL,margin TEXT NOT NULL,basis TEXT NOT NULL,leverage TEXT NOT NULL,funding_cursor INTEGER NOT NULL,PRIMARY KEY(source,inst_id));
                CREATE TABLE IF NOT EXISTS pro_risk(source TEXT PRIMARY KEY,body TEXT NOT NULL,halted INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pro_orders(id TEXT PRIMARY KEY,source TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,reservation TEXT NOT NULL DEFAULT '0',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(source,key));
                CREATE INDEX IF NOT EXISTS pro_orders_pending ON pro_orders(status,created_at);
                CREATE TABLE IF NOT EXISTS pro_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,tx_id TEXT NOT NULL,source TEXT NOT NULL,ts INTEGER NOT NULL,asset TEXT NOT NULL,account TEXT NOT NULL,debit TEXT NOT NULL,credit TEXT NOT NULL,memo TEXT NOT NULL,reference TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS pro_ledger_source ON pro_ledger(source,id);
                CREATE TABLE IF NOT EXISTS pro_funding(source TEXT NOT NULL,inst_id TEXT NOT NULL,ts INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(source,inst_id,ts));
                CREATE TABLE IF NOT EXISTS pro_deployments(id TEXT PRIMARY KEY,source TEXT NOT NULL,inst_id TEXT NOT NULL,config TEXT NOT NULL,status TEXT NOT NULL,last_bar INTEGER,last_error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS pro_strategy_market ON pro_deployments(source,inst_id) WHERE status='running';
            """)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            for source in ("okx", "example"):
                if not conn.execute("SELECT 1 FROM pro_accounts WHERE source=?", (source,)).fetchone():
                    conn.execute(
                        "INSERT INTO pro_accounts(source,initial_cash,cash) VALUES(?,'10000','10000')",
                        (source,),
                    )
                    self.post(
                        conn,
                        source,
                        "initial",
                        "Workspace capital",
                        "initial",
                        [("USDT", "cash", D(10000)), ("USDT", "contributed_capital", D(-10000))],
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO pro_risk VALUES(?,?,0,?)", (source, dumps(DEFAULT_RISK), now_ms())
                )

    @_accounted
    def post(self, conn, source, tx_id, memo, reference, entries):
        entries = list(entries)
        for asset in {entry[0] for entry in entries}:
            amounts = [entry[2] for entry in entries if entry[0] == asset]
            if any(not amount.is_finite() or amount.copy_abs() >= D("1e30") for amount in amounts):
                raise PlatformError(
                    "accounting_domain", "Ledger entry exceeds the supported numeric domain.", 422
                )
            total = _exact_sum(amounts)
            if total:
                # Disclose only a provable finite-context ULP residual. A
                # material journal error still rolls back the whole command.
                references = list(amounts)
                if asset == "USDT":
                    account = conn.execute(
                        "SELECT cash FROM pro_accounts WHERE source=?", (source,)
                    ).fetchone()
                    if account:
                        references.append(D(account["cash"]))
                    references.extend(
                        D(row[0])
                        for row in conn.execute("SELECT margin FROM pro_positions WHERE source=?", (source,))
                    )
                scale = max((amount.copy_abs().adjusted() for amount in references if amount), default=0) - 49
                bound = D(1).scaleb(scale) * len(amounts)
                if total.copy_abs() > bound:
                    raise RuntimeError("Native-asset ledger transaction is unbalanced")
                entries.append((asset, "rounding_adjustment", total.copy_negate()))
            if _exact_sum(entry[2] for entry in entries if entry[0] == asset) != ZERO:
                raise RuntimeError("Native-asset ledger transaction is unbalanced")
        conn.executemany(
            "INSERT INTO pro_ledger(tx_id,source,ts,asset,account,debit,credit,memo,reference) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    tx_id,
                    source,
                    now_ms(),
                    asset,
                    account,
                    str(amount if amount > ZERO else ZERO),
                    str(amount.copy_negate() if amount < ZERO else ZERO),
                    memo,
                    reference,
                )
                for asset, account, amount in entries
                if amount != 0
            ],
        )

    def risk(self, source, conn=None):
        if conn is None:
            with self.store.read() as connection:
                return self.risk(source, connection)
        row = conn.execute("SELECT * FROM pro_risk WHERE source=?", (source,)).fetchone()
        if not row:
            raise PlatformError("invalid_source", "Unknown execution source.", 422)
        return {
            **json.loads(row["body"]),
            "source": source,
            "halted": bool(row["halted"]),
            "updated_at": row["updated_at"],
        }

    @_accounted
    def set_risk(self, source, values, actor):
        if set(values) - set(DEFAULT_RISK):
            raise PlatformError("risk_policy", "Unknown simulation risk field.", 422)
        with self.store.write() as conn:
            risk = {**self.risk(source, conn), **values}
            risk.pop("updated_at", None)
            if (
                not 0 < number(risk["max_order_notional"]) <= D("1e9")
                or not 1 <= number(risk["max_leverage"]) <= 50
                or not 1 <= number(risk["max_gross_exposure_pct"]) <= 1000
                or not D(".1") <= number(risk["max_daily_loss_pct"]) <= 50
            ):
                raise PlatformError("risk_policy", "Risk limits are outside supported ranges.", 422)
            if (
                any(not 0 <= number(risk[key]) <= 100 for key in ("fee_bps", "slippage_bps"))
                or not 0 <= number(risk["liquidation_fee_bps"]) <= 500
            ):
                raise PlatformError(
                    "risk_policy", "Fee or slippage parameters are outside supported ranges.", 422
                )
            if not isinstance(risk["halted"], bool):
                raise PlatformError("risk_policy", "Halted state must be boolean.", 422)
            conn.execute(
                "UPDATE pro_risk SET body=?,halted=?,updated_at=? WHERE source=?",
                (dumps(risk), int(risk["halted"]), now_ms(), source),
            )
            self.store.audit(
                conn,
                source,
                "pro.risk_updated",
                "Simulation risk policy changed",
                {"actor": actor, "policy": risk},
            )
        return self.risk(source)

    def halt(self, source, active, reason, actor):
        if len(reason.strip()) < 3:
            raise PlatformError("reason_required", "Record a reason for changing execution state.", 422)
        return self.set_risk(source, {"halted": active, "halt_reason": reason[:300]}, actor)

    def positions(self, source=None):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM pro_positions WHERE quantity!='0'" + (" AND source=?" if source else ""),
                (source,) if source else (),
            ).fetchall()
        return [dict(row) for row in rows if number(row["quantity"]) != ZERO]

    @staticmethod
    @_accounted
    def fresh(snapshot):
        if not all(key in snapshot for key in ("source", "ts", "bid", "ask", "instrument")):
            raise PlatformError(
                "invalid_market", "A complete source-matched bid/ask snapshot is required.", 409
            )
        if snapshot.get("source") != "example" and snapshot.get("instrument", {}).get("inst_type") == "SWAP":
            if "mark_ts" not in snapshot or not -5000 <= now_ms() - int(snapshot["mark_ts"]) <= 15000:
                raise PlatformError(
                    "stale_mark", "The independent perpetual mark price is stale or missing.", 409
                )
        if snapshot.get("source") != "example" and not -5000 <= now_ms() - int(snapshot["ts"]) <= 15000:
            raise PlatformError(
                "stale_market", "Market snapshot is stale. New simulation risk is blocked.", 409
            )
        if snapshot.get("instrument", {}).get("inst_type") == "SWAP" and snapshot.get("mark") is None:
            raise PlatformError("missing_mark", "An independent perpetual mark price is required.", 409)
        bid, ask, mark = (
            number(snapshot["bid"]),
            number(snapshot["ask"]),
            number(
                snapshot["mark"]
                if snapshot.get("instrument", {}).get("inst_type") == "SWAP"
                else snapshot.get("mark") or snapshot["last"]
            ),
        )
        if bid <= 0 or ask < bid or ask / bid > D("1.05") or mark <= 0:
            raise PlatformError("invalid_market", "A fresh, valid bid/ask and mark are required.", 409)
        return bid, ask, mark

    @_accounted
    def account(self, source, snapshots, conn=None):
        with localcontext(ACCOUNTING_CONTEXT):
            if conn is None:
                with self.store.read() as connection:
                    return self.account(source, snapshots, connection)
            account = conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
            if not account:
                raise PlatformError("invalid_source", "Unknown execution source.", 422)
            cash, debt = number(account["cash"]), number(account["debt"])
            equity, used, maintenance, pnl = cash - debt, ZERO, ZERO, ZERO
            status = "example" if source == "example" else "fresh"
            rows = conn.execute(
                "SELECT * FROM pro_positions WHERE source=? AND quantity!='0'", (source,)
            ).fetchall()
            output = []
            for row in rows:
                meta, qty = json.loads(row["metadata"]), number(row["quantity"])
                if qty == ZERO:
                    continue
                entry, margin, leverage = (
                    number(row["entry_price"]),
                    number(row["margin"]),
                    number(row["leverage"]),
                )
                snapshot = snapshots.get(row["inst_id"])
                if snapshot and snapshot.get("source") != source:
                    snapshot = None
                mark = upnl = value = mm = liq = None
                if snapshot:
                    try:
                        _, _, mark = self.fresh(snapshot)
                    except PlatformError:
                        status = "stale"
                        try:
                            raw_mark = (
                                snapshot.get("mark")
                                if meta["inst_type"] == "SWAP"
                                else snapshot.get("mark") or snapshot["last"]
                            )
                            mark = number(raw_mark)
                            if mark <= ZERO:
                                mark = None
                        except (PlatformError, KeyError):
                            mark = None
                    if mark is not None:
                        amount = qty * base_size(meta)
                        value = abs(amount) * mark
                        upnl = (
                            amount * (mark - entry)
                            if meta["inst_type"] == "SWAP"
                            else value - number(row["basis"])
                        )
                        if meta["inst_type"] == "SWAP":
                            try:
                                tier = tier_for(meta, qty, snapshot.get("margin_tiers", []))
                                rate = (
                                    number(tier["mmr"])
                                    + (
                                        number(self.risk(source, conn)["fee_bps"])
                                        + number(self.risk(source, conn)["liquidation_fee_bps"])
                                    )
                                    / 10000
                                )
                                mm = value * number(tier["mmr"])
                                liq = (
                                    (entry - margin / abs(amount)) / (1 - rate)
                                    if qty > 0
                                    else (entry + margin / abs(amount)) / (1 + rate)
                                )
                                liq = max(liq, ZERO)
                            except (PlatformError, EngineError):
                                status = "unavailable"
                            equity += margin + upnl
                        else:
                            mm = ZERO
                            equity += value
                        pnl += upnl
                if mark is None:
                    status = "unavailable"
                used += margin
                maintenance += mm or ZERO
                output.append(
                    {
                        "inst_id": row["inst_id"],
                        "inst_type": meta["inst_type"],
                        "quantity": str(qty),
                        "side": "long" if qty > 0 else "short",
                        "entry_price": str(entry),
                        "basis": row["basis"] if meta["inst_type"] == "SPOT" else None,
                        "mark": str(mark) if mark is not None else None,
                        "market_value": str(value) if value is not None else None,
                        "unrealized_pnl": str(upnl) if upnl is not None else None,
                        "margin": str(margin),
                        "maintenance_margin": str(mm) if mm is not None else None,
                        "leverage": str(leverage),
                        "liquidation_price": str(liq) if liq is not None else None,
                        "liquidation_price_kind": "estimate",
                        "instrument": meta,
                        "as_of": snapshot.get("ts") if snapshot else None,
                    }
                )
            reserved = sum(
                (
                    number(row[0])
                    for row in conn.execute(
                        "SELECT reservation FROM pro_orders WHERE source=? AND status='pending'", (source,)
                    )
                ),
                ZERO,
            )
            return {
                "source": source,
                "execution_mode": "local-paper",
                "initial_cash": account["initial_cash"],
                "cash": str(cash),
                "available_cash": str(cash - reserved),
                "reserved_cash": str(reserved),
                "equity": str(equity) if status != "unavailable" else None,
                "used_margin": str(used),
                "maintenance_margin": str(maintenance) if status != "unavailable" else None,
                "unrealized_pnl": str(pnl) if status != "unavailable" else None,
                "realized_pnl": account["realized"],
                "pnl_definition": "Spot disposal PnL includes proportional entry costs; swap realizes trading fees when charged. Funding is separate. Equity deducts recorded insurance liabilities.",
                "ledger_precision": "50 significant digits; finite-context residuals explicitly journaled to rounding_adjustment",
                "liquidation_fee_policy": "Trading fee plus configured additional liquidation allowance; estimate is a local model, not a venue fee-tier guarantee.",
                "fees_paid": account["fees"],
                "funding_paid": account["funding"],
                "insurance_debt": str(debt),
                "valuation_status": status,
                "positions": output,
                "as_of": now_ms(),
            }

    def orders(self, source, limit=200):
        with self.store.read() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT body FROM pro_orders WHERE source=? ORDER BY created_at DESC LIMIT ?",
                    (source, limit),
                )
            ]

    def ledger(self, source, limit=500):
        with self.store.read() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM pro_ledger WHERE source=? ORDER BY id DESC LIMIT ?", (source, limit)
                )
            ]

    def existing(self, source, key, payload):
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT payload,body FROM pro_orders WHERE source=? AND key=?", (source, key)
            ).fetchone()
        if row:
            if row["payload"] != payload:
                raise PlatformError(
                    "idempotency_conflict", "This command key already belongs to a different order.", 409
                )
            return json.loads(row["body"])
        return None

    @_accounted
    def preview(self, order, snapshots):
        with localcontext(ACCOUNTING_CONTEXT), self.store.read() as conn:
            values = self._prepare(conn, order, snapshots)
            return {
                "order": order,
                "estimated_price": str(values["price"]),
                "notional": str(values["notional"]),
                "fee": str(values["fee"]),
                "required_margin": str(values["required"]),
                "estimated_cash_after": str(values["cash"] - values["required"] - values["fee"]),
                "warnings": [
                    "Local full-fill model; no venue order is sent.",
                    "Liquidation estimates use current tiers and exclude future funding and execution gaps.",
                ],
                "market_snapshot": snapshots[order["inst_id"]],
                "risk": values["risk"],
            }

    def _prepare(
        self, conn, order, snapshots, *, exclude_reservation=ZERO, exclude_order_id=None, liquidation=False
    ):
        source, symbol = order["source"], order["inst_id"]
        snapshot = snapshots.get(symbol)
        if not snapshot or snapshot["source"] != source:
            raise PlatformError("market_unavailable", "A source-matched market snapshot is required.", 409)
        bid, ask, mark = self.fresh(snapshot)
        meta = snapshot["instrument"]
        if (
            meta.get("inst_type") not in {"SPOT", "SWAP"}
            or order.get("margin_mode", "isolated") != "isolated"
        ):
            raise PlatformError(
                "unsupported_instrument", "Only spot and isolated linear swaps are supported.", 422
            )
        if meta.get("inst_id") != symbol:
            raise PlatformError(
                "instrument_mismatch", "Snapshot instrument does not match the requested market.", 409
            )
        if meta["state"] != "live":
            raise PlatformError("instrument_unavailable", "The instrument is not tradable.", 409)
        qty, leverage = number(order["quantity"]), number(order.get("leverage", 1))
        if not 1 <= leverage <= 100:
            raise PlatformError("leverage_limit", "Leverage must be between one and 100.", 422)
        if qty <= 0 or qty < number(meta["min_size"]) or qty % number(meta["lot_size"]) != 0:
            raise PlatformError(
                "invalid_quantity", "Quantity must respect the current instrument lot and minimum size.", 422
            )
        if order["side"] not in {"buy", "sell"}:
            raise PlatformError("invalid_side", "Side must be buy or sell.", 422)
        signed = qty if order["side"] == "buy" else -qty
        row = conn.execute(
            "SELECT * FROM pro_positions WHERE source=? AND inst_id=?", (source, symbol)
        ).fetchone()
        old = number(row["quantity"]) if row else ZERO
        reducing = old != 0 and old * signed < 0 and qty <= abs(old)
        if row and old:
            previous_meta = json.loads(row["metadata"])
            for field in ("inst_type", "base", "quote", "settle_ccy", "ct_val", "ct_mult", "ct_val_ccy"):
                if str(previous_meta.get(field)) != str(meta.get(field)):
                    raise PlatformError(
                        "contract_changed",
                        "Contract units changed; reconcile the existing position before trading.",
                        409,
                    )
        if row and old and meta["inst_type"] == "SWAP":
            expected = json.loads(row["metadata"]).get("expected_funding_time")
            if expected is not None and expected <= int(snapshot["ts"]):
                if not conn.execute(
                    "SELECT 1 FROM pro_funding WHERE source=? AND inst_id=? AND ts=?",
                    (source, symbol, expected),
                ).fetchone():
                    raise PlatformError(
                        "funding_pending",
                        "An observed settlement is due; reconcile its realized historical rate before changing inventory.",
                        409,
                    )
        if order.get("reduce_only") and not reducing:
            raise PlatformError("reduce_only", "Reduce-only orders cannot open or reverse a position.", 409)
        if old * signed < 0 and qty > abs(old):
            raise PlatformError(
                "close_before_reverse", "Close the existing position before opening the opposite side.", 409
            )
        if meta["inst_type"] == "SPOT" and (signed < 0 and qty > old or leverage != 1):
            raise PlatformError(
                "spot_inventory", "Spot execution requires existing inventory and leverage one.", 409
            )
        risk = self.risk(source, conn)
        if risk["halted"] and not reducing and not liquidation:
            raise PlatformError(
                "execution_halted", "New risk is halted. Reduce-only exits remain available.", 409
            )
        raw = ask if signed > 0 else bid
        slip = number(risk["slippage_bps"]) / 10000
        price = raw * (1 + slip if signed > 0 else 1 - slip)
        price = _round_to_step(
            price, number(meta["tick_size"]), ROUND_CEILING if signed > 0 else ROUND_FLOOR, "price tick"
        )
        if price <= 0:
            raise PlatformError("invalid_price", "The fill price rounds below the current tick.", 409)
        notional = number(qty * base_size(meta) * price)
        fee_rate = number(risk["fee_bps"]) + (number(risk["liquidation_fee_bps"]) if liquidation else ZERO)
        fee = number(notional * fee_rate / 10000)
        required = ZERO if reducing else notional if meta["inst_type"] == "SPOT" else notional / leverage
        account = self.account(source, snapshots, conn)
        cash = number(account["cash"])
        if not reducing and not liquidation:
            if account["valuation_status"] not in {"fresh", "example"} or account["equity"] is None:
                raise PlatformError(
                    "valuation_unavailable", "All portfolio marks must be fresh before increasing risk.", 409
                )
            equity = number(account["equity"])
            if equity <= 0 or number(account["insurance_debt"]) > 0:
                raise PlatformError("insolvent", "Outstanding simulated loss blocks new risk.", 409)
            if notional > number(risk["max_order_notional"]):
                raise PlatformError("order_limit", "Order notional exceeds the configured limit.", 409)
            if leverage < 1 or leverage > number(risk["max_leverage"]):
                raise PlatformError("leverage_limit", "Leverage exceeds the workspace limit.", 409)
            tier = tier_for(meta, abs(old + signed), snapshot.get("margin_tiers", []))
            if meta["inst_type"] == "SWAP" and (
                1 / leverage < number(tier["imr"]) or leverage > number(tier["max_leverage"])
            ):
                raise PlatformError("margin_tier", "Leverage violates the current initial-margin tier.", 409)
            if old and meta["inst_type"] == "SWAP" and leverage != number(row["leverage"]):
                raise PlatformError(
                    "position_leverage", "Additional fills must use the position's current leverage.", 409
                )
            if meta["inst_type"] == "SWAP":
                post_quantity = number(old + signed)
                post_margin = (number(row["margin"]) if row else ZERO) + required
                post_entry = (
                    ((abs(old) * number(row["entry_price"]) + qty * price) / abs(post_quantity))
                    if old
                    else price
                )
                post_equity = post_margin + post_quantity * base_size(meta) * (mark - post_entry)
                mark_notional = abs(post_quantity) * base_size(meta) * mark
                if post_equity <= mark_notional * (
                    number(tier["mmr"])
                    + (number(risk["fee_bps"]) + number(risk["liquidation_fee_bps"])) / 10000
                ):
                    raise PlatformError(
                        "immediate_liquidation",
                        "The proposed position would already violate mark-based maintenance margin.",
                        409,
                    )
            if required + fee > number(account["available_cash"]) + exclude_reservation:
                raise PlatformError(
                    "insufficient_cash",
                    "Available cash including existing reservations is insufficient.",
                    409,
                )
            gross = sum((number(p["market_value"]) for p in account["positions"]), ZERO)
            pending = sum(
                (
                    number(json.loads(r[0]).get("notional", 0))
                    for r in conn.execute(
                        "SELECT body FROM pro_orders WHERE source=? AND status='pending' AND (? IS NULL OR id!=?)",
                        (source, exclude_order_id, exclude_order_id),
                    )
                ),
                ZERO,
            )
            if (gross + pending + notional) / equity * 100 > number(risk["max_gross_exposure_pct"]):
                raise PlatformError(
                    "gross_exposure_limit",
                    "Portfolio gross exposure including pending orders exceeds the limit.",
                    409,
                )
            baseline = conn.execute(
                "SELECT day_equity FROM pro_accounts WHERE source=?", (source,)
            ).fetchone()[0]
            if number(baseline) > 0 and (number(baseline) - equity + fee) / number(baseline) * 100 >= number(
                risk["max_daily_loss_pct"]
            ):
                raise PlatformError(
                    "daily_loss_limit", "The persisted observed-day loss budget is exhausted.", 409
                )
        return {
            "snapshot": snapshot,
            "metadata": meta,
            "qty": qty,
            "signed": signed,
            "old": old,
            "row": row,
            "reducing": reducing,
            "price": price,
            "mark": mark,
            "notional": notional,
            "fee": fee,
            "required": required,
            "cash": cash,
            "risk": risk,
        }

    @_accounted
    def submit(self, order, key, snapshots, actor="manual", *, pending_id=None, liquidation=False):
        if not 8 <= len(key) <= 160:
            raise PlatformError("idempotency_key", "Command keys must contain 8–160 characters.", 422)
        with localcontext(ACCOUNTING_CONTEXT):
            payload = dumps(order)
            existing = self.existing(order["source"], key, payload)
            if existing and pending_id is None:
                return existing
            with self.store.write() as conn:
                if actor.startswith("strategy:"):
                    deployment_id = actor.split(":", 1)[1]
                    deployment = conn.execute(
                        "SELECT * FROM pro_deployments WHERE id=?", (deployment_id,)
                    ).fetchone()
                    if (
                        not deployment
                        or deployment["status"] != "running"
                        or deployment["source"] != order["source"]
                        or deployment["inst_id"] != order["inst_id"]
                    ):
                        raise PlatformError(
                            "strategy_stopped", "The strategy no longer owns this market.", 409
                        )
                elif actor not in {"pending-order", "risk-engine"} and not order.get("reduce_only"):
                    if conn.execute(
                        "SELECT 1 FROM pro_deployments WHERE source=? AND inst_id=? AND status='running'",
                        (order["source"], order["inst_id"]),
                    ).fetchone():
                        raise PlatformError(
                            "strategy_ownership",
                            "Stop the strategy before adding manual risk to its market.",
                            409,
                        )
                previous = conn.execute(
                    "SELECT * FROM pro_orders WHERE source=? AND key=?", (order["source"], key)
                ).fetchone()
                if previous and pending_id is None:
                    if previous["payload"] != payload:
                        raise PlatformError("idempotency_conflict", "Command key is already in use.", 409)
                    return json.loads(previous["body"])
                if pending_id and (
                    not previous or previous["id"] != pending_id or previous["status"] != "pending"
                ):
                    raise PlatformError("order_not_pending", "This order is no longer pending.", 409)
                values = self._prepare(
                    conn,
                    order,
                    snapshots,
                    exclude_reservation=number(previous["reservation"]) if previous else ZERO,
                    exclude_order_id=pending_id,
                    liquidation=liquidation,
                )
                meta, qty, signed, old, row = (values[k] for k in ("metadata", "qty", "signed", "old", "row"))
                price, fee, notional, cash = (values[k] for k in ("price", "fee", "notional", "cash"))
                identifier = previous["id"] if previous else new_id()
                order_type = order.get("order_type", "market")
                pending = order_type != "market" and pending_id is None
                result = {
                    **order,
                    "id": identifier,
                    "execution_mode": "local-paper",
                    "status": "pending" if pending else "filled",
                    "price": str(price),
                    "notional": str(notional),
                    "fee": str(fee),
                    "created_at": previous["created_at"] if previous else now_ms(),
                    "updated_at": now_ms(),
                    "actor": actor,
                    "instrument": meta,
                    "quote_ts": values["snapshot"]["ts"],
                    "risk_snapshot": values["risk"],
                }
                if pending:
                    if (
                        conn.execute(
                            "SELECT COUNT(*) FROM pro_orders WHERE source=? AND status='pending'",
                            (order["source"],),
                        ).fetchone()[0]
                        >= 100
                    ):
                        raise PlatformError(
                            "pending_order_limit",
                            "At most 100 pending simulation orders are supported per source.",
                            409,
                        )
                    trigger = number(
                        order.get("limit_price") if order_type == "limit" else order.get("stop_price")
                    )
                    if trigger <= 0 or trigger % number(meta["tick_size"]) != 0:
                        raise PlatformError(
                            "invalid_trigger", "A positive tick-aligned limit or stop price is required.", 422
                        )
                    if order_type not in {"limit", "stop_market"}:
                        raise PlatformError("order_type", "Unsupported order type.", 422)
                    reservation = values["required"] + fee
                else:
                    reservation = ZERO
                    if order_type == "limit":
                        limit_price = number(order["limit_price"])
                        if signed > 0 and price > limit_price or signed < 0 and price < limit_price:
                            raise PlatformError(
                                "limit_not_marketable",
                                "The adverse-cost fill price does not satisfy the limit.",
                                409,
                            )
                    source = order["source"]
                    account = conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
                    realized, debt = number(account["realized"]), number(account["debt"])
                    cash_before = cash
                    old_margin, old_basis = (
                        number(row["margin"]) if row else ZERO,
                        number(row["basis"]) if row else ZERO,
                    )
                    entry = number(row["entry_price"]) if row else price
                    new_qty = number(old + signed)
                    if _exact_sum([new_qty, old.copy_negate()]) != signed:
                        raise PlatformError(
                            "accounting_domain",
                            "Accounting precision cannot represent the inventory change.",
                            422,
                        )
                    if _floor_lot(abs(new_qty), number(meta["lot_size"])) != abs(new_qty):
                        raise PlatformError(
                            "accounting_domain",
                            "The resulting quantity cannot resolve the contract lot.",
                            422,
                        )
                    journal = []
                    if meta["inst_type"] == "SPOT":
                        base = meta.get("base") or order["inst_id"].split("-")[0]
                        if signed > 0:
                            cash -= notional + fee
                            basis = old_basis + notional + fee
                            entry = basis / new_qty
                        else:
                            removed = old_basis * qty / old if new_qty else old_basis
                            cash += notional - fee
                            realized += notional - fee - removed
                            basis = old_basis - removed if new_qty else ZERO
                        margin = ZERO
                        journal.extend(
                            [
                                (base, "inventory", signed),
                                (base, "venue_clearing", -signed),
                                ("USDT", "cash", -notional if signed > 0 else notional),
                                ("USDT", "venue_clearing", notional if signed > 0 else -notional),
                            ]
                        )
                    else:
                        basis = ZERO
                        if values["reducing"]:
                            released = old_margin * qty / abs(old) if new_qty else old_margin
                            profit = (price - entry) * (qty if old > 0 else -qty) * base_size(meta)
                            release_net = released + profit - fee
                            deficit = max(-release_net, ZERO)
                            cash += max(release_net, ZERO)
                            if deficit:
                                debt += deficit
                                journal.append(("USDT", "insurance_liability", -deficit))
                                conn.execute("UPDATE pro_risk SET halted=1 WHERE source=?", (source,))
                            realized += profit - fee
                            margin = old_margin - released if new_qty else ZERO
                            journal.extend(
                                [
                                    ("USDT", "margin", -released),
                                    ("USDT", "cash", released + profit),
                                    ("USDT", "derivative_pnl", -profit),
                                ]
                            )
                        else:
                            required = values["required"]
                            margin = old_margin + required
                            entry = (abs(old) * entry + qty * price) / abs(new_qty)
                            cash -= required + fee
                            realized -= fee
                            journal.extend([("USDT", "cash", -required), ("USDT", "margin", required)])
                    journal.extend([("USDT", "cash", -fee), ("USDT", "fee_expense", fee)])
                    if cash < 0:
                        raise PlatformError(
                            "insufficient_cash", "The fill would overdraw available cash.", 409
                        )
                    for amount in (cash, realized, debt, margin, basis, entry, number(account["fees"]) + fee):
                        number(amount)
                    journal = _cash_journal(journal, cash_before, cash)
                    self.post(
                        conn,
                        source,
                        identifier,
                        "Liquidation" if liquidation else "Simulated fill",
                        identifier,
                        journal,
                    )
                    cursor = (
                        (int(values["snapshot"]["ts"]) if source == "example" else now_ms())
                        if not row or old == 0
                        else row["funding_cursor"]
                    )
                    stored_meta = dict(meta)
                    if meta["inst_type"] == "SWAP":
                        previous_meta = json.loads(row["metadata"]) if row and old else {}
                        expected = previous_meta.get("expected_funding_time")
                        if expected is None:
                            observed_times = [
                                values["snapshot"].get("funding_time"),
                                values["snapshot"].get("next_funding_time"),
                            ]
                            future_times = [
                                int(value)
                                for value in observed_times
                                if value is not None and int(value) > cursor
                            ]
                            expected = min(future_times) if future_times else None
                        stored_meta["expected_funding_time"] = expected
                    conn.execute(
                        "INSERT INTO pro_positions VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,inst_id) DO UPDATE SET metadata=excluded.metadata,quantity=excluded.quantity,entry_price=excluded.entry_price,margin=excluded.margin,basis=excluded.basis,leverage=excluded.leverage,funding_cursor=excluded.funding_cursor",
                        (
                            source,
                            order["inst_id"],
                            dumps(stored_meta),
                            str(new_qty) if new_qty else "0",
                            str(entry),
                            str(margin),
                            str(basis),
                            row["leverage"] if row and values["reducing"] else str(order.get("leverage", 1)),
                            cursor,
                        ),
                    )
                    conn.execute(
                        "UPDATE pro_accounts SET cash=?,realized=?,fees=?,debt=? WHERE source=?",
                        (str(cash), str(realized), str(number(account["fees"]) + fee), str(debt), source),
                    )
                    result.update(
                        {
                            "cash_after": str(cash),
                            "position_after": str(new_qty),
                            "margin_after": str(margin),
                            "liquidation": liquidation,
                            "insurance_debt": str(debt),
                        }
                    )
                if previous:
                    conn.execute(
                        "UPDATE pro_orders SET status=?,body=?,reservation=?,updated_at=? WHERE id=?",
                        (result["status"], dumps(result), str(reservation), now_ms(), identifier),
                    )
                else:
                    conn.execute(
                        "INSERT INTO pro_orders VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            order["source"],
                            key,
                            payload,
                            result["status"],
                            dumps(result),
                            str(reservation),
                            result["created_at"],
                            now_ms(),
                        ),
                    )
                self.store.audit(
                    conn,
                    order["source"],
                    "pro.order_" + result["status"],
                    f"{order['side']} {qty} {order['inst_id']}",
                    {"order_id": identifier, "actor": actor, "liquidation": liquidation},
                )
                if actor.startswith("strategy:"):
                    # Cursor and economic fill commit together. A crash cannot
                    # leave a filled signal looking unevaluated after restart.
                    conn.execute(
                        "UPDATE pro_deployments SET last_bar=?,last_error=NULL,updated_at=? WHERE id=?",
                        (int(key.rsplit(":", 1)[1]), now_ms(), actor.split(":", 1)[1]),
                    )
                return result

    def cancel(self, identifier, actor):
        with self.store.write() as conn:
            row = conn.execute("SELECT * FROM pro_orders WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("not_found", "Order not found.", 404)
            body = json.loads(row["body"])
            if row["status"] != "pending":
                return body
            body.update(status="canceled", updated_at=now_ms())
            conn.execute(
                "UPDATE pro_orders SET status='canceled',reservation='0',body=?,updated_at=? WHERE id=?",
                (dumps(body), now_ms(), identifier),
            )
            self.store.audit(
                conn,
                row["source"],
                "pro.order_canceled",
                "Pending simulation order canceled",
                {"order_id": identifier, "actor": actor},
            )
            return body

    def pending(self):
        with self.store.read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM pro_orders WHERE status='pending'")]

    @_accounted
    def observe(self, source, snapshots):
        """Persist a new observed UTC-day baseline before evaluating new risk."""
        with localcontext(ACCOUNTING_CONTEXT), self.store.write() as conn:
            account = self.account(source, snapshots, conn)
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            previous = conn.execute("SELECT day_key FROM pro_accounts WHERE source=?", (source,)).fetchone()[
                0
            ]
            if (
                previous != day
                and account["equity"] is not None
                and account["valuation_status"] in {"fresh", "example"}
            ):
                conn.execute(
                    "UPDATE pro_accounts SET day_key=?,day_equity=? WHERE source=?",
                    (day, account["equity"], source),
                )
                self.store.audit(
                    conn,
                    source,
                    "pro.day_anchor",
                    "Observed UTC-day equity baseline established",
                    {"day": day, "equity": account["equity"]},
                )
        return account

    @_accounted
    def settle_funding(self, source, symbol, events, snapshots):
        if len(events) > 10000:
            raise PlatformError(
                "funding_budget", "Funding reconciliation is limited to 10000 events per command.", 422
            )
        with localcontext(ACCOUNTING_CONTEXT), self.store.write() as conn:
            row = conn.execute(
                "SELECT * FROM pro_positions WHERE source=? AND inst_id=?", (source, symbol)
            ).fetchone()
            if not row or number(row["quantity"]) == 0:
                return []
            meta = json.loads(row["metadata"])
            if meta["inst_type"] != "SWAP":
                return []
            account = conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
            margin, total = number(row["margin"]), number(account["funding"])
            output, cursor = [], row["funding_cursor"]
            expected = meta.get("expected_funding_time")
            snapshot = snapshots.get(symbol, {})
            observed_until = int(snapshot.get("ts", now_ms()))
            if expected is not None and expected <= observed_until:
                already = conn.execute(
                    "SELECT 1 FROM pro_funding WHERE source=? AND inst_id=? AND ts=?",
                    (source, symbol, expected),
                ).fetchone()
                if not already and expected not in {int(event["ts"]) for event in events}:
                    raise PlatformError(
                        "funding_pending",
                        "A previously observed funding settlement is due but its realized historical rate is not published. Trading remains blocked.",
                        409,
                    )
            for event in sorted(events, key=lambda e: int(e["ts"])):
                ts = int(event["ts"])
                if ts > now_ms() or (event.get("inst_id") is not None and event["inst_id"] != symbol):
                    raise PlatformError(
                        "invalid_funding_event",
                        "Funding cannot use a future settlement or a different instrument.",
                        409,
                    )
                if event.get("mark_ts", ts) != ts:
                    raise PlatformError(
                        "invalid_funding_mark", "Historical mark timestamp must match settlement.", 409
                    )
                rate, event_mark = number(event["rate"]), number(event.get("mark_price"))
                if abs(rate) >= 1 or event_mark <= 0:
                    raise PlatformError(
                        "invalid_funding_event",
                        "Funding requires a positive historical mark and an absolute realized rate below one.",
                        422,
                    )
                existing = conn.execute(
                    "SELECT body FROM pro_funding WHERE source=? AND inst_id=? AND ts=?", (source, symbol, ts)
                ).fetchone()
                if existing:
                    booked = json.loads(existing["body"])
                    if number(booked["rate"]) != rate or number(booked["mark_price"]) != event_mark:
                        raise PlatformError(
                            "funding_conflict",
                            "A settled funding timestamp cannot be rewritten with a different rate or mark.",
                            409,
                        )
                    continue
                if ts <= cursor:
                    continue
                # Settlement must use the historical event mark, not today's price.
                if not event.get("mark_price"):
                    raise PlatformError(
                        "funding_mark_missing",
                        "Historical settlement mark is required before funding can be booked.",
                        409,
                    )
                amount = (
                    number(row["quantity"])
                    * base_size(meta)
                    * number(event["mark_price"])
                    * number(event["rate"])
                )
                margin = number(margin - amount)
                total = number(total + amount)
                body = {
                    "inst_id": symbol,
                    "ts": ts,
                    "rate": str(event["rate"]),
                    "mark_price": str(event["mark_price"]),
                    "mark_price_source": event.get("mark_price_source", "user_supplied"),
                    "mark_ts": event.get("mark_ts", ts),
                    "payment": str(amount),
                    "quantity": row["quantity"],
                }
                conn.execute("INSERT INTO pro_funding VALUES(?,?,?,?)", (source, symbol, ts, dumps(body)))
                self.post(
                    conn,
                    source,
                    f"funding:{symbol}:{ts}",
                    "Historical funding settlement",
                    symbol,
                    [("USDT", "margin", -amount), ("USDT", "funding_pnl", amount)],
                )
                cursor = ts
                output.append(body)
            if expected is not None and any(event["ts"] == expected for event in output):
                candidates = [snapshot.get("funding_time"), snapshot.get("next_funding_time")]
                future_times = [
                    int(value) for value in candidates if value is not None and int(value) > cursor
                ]
                meta["expected_funding_time"] = min(future_times) if future_times else None
            conn.execute(
                "UPDATE pro_positions SET margin=?,funding_cursor=?,metadata=? WHERE source=? AND inst_id=?",
                (str(margin), cursor, dumps(meta), source, symbol),
            )
            conn.execute("UPDATE pro_accounts SET funding=? WHERE source=?", (str(total), source))
            for event in output:
                self.store.audit(conn, source, "pro.funding", "Swap funding settled once", event)
            return output
