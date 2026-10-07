"""Local simulated spot fills. There is deliberately no exchange-order endpoint in this module."""

import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, localcontext

from .engine import Instrument
from .schemas import OrderInput
from .store import Store, dumps, new_id, now_ms

FEE_RATE = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.0005")
MAX_QUOTE_AGE_MS = 15_000


class DeskError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def order_payload(order: OrderInput) -> str:
    body = order.model_dump()
    body["quantity"] = str(order.quantity.normalize())
    return dumps(body)


def fresh_quote(quote: dict, source: str):
    if not isinstance(quote.get("ts"), int) or isinstance(quote.get("ts"), bool):
        raise DeskError("invalid_quote", "Quote timestamp is invalid.")
    if source == "example":
        return
    age = now_ms() - quote["ts"]
    if age > MAX_QUOTE_AGE_MS or age < -5000:
        raise DeskError(
            "stale_quote", "OKX quote is stale or the system clock is incorrect. No fill was made."
        )


def valid_mark(quote: dict) -> Decimal:
    try:
        mark = Decimal(quote["last"])
        if not mark.is_finite() or mark <= 0:
            raise ValueError
        return mark
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise DeskError(
            "invalid_quote", "A finite, positive mark price is required for account valuation."
        ) from None


def quote_price(quote: dict, side: str) -> Decimal:
    try:
        bid, ask = Decimal(quote["bid"]), Decimal(quote["ask"])
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid or ask / bid > Decimal("1.05"):
            raise ValueError
        return ask if side == "buy" else bid
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise DeskError(
            "invalid_quote", "A valid, uncrossed bid/ask quote is required for a simulated fill."
        ) from None


class PaperDesk:
    def __init__(self, store: Store):
        self.store = store

    def account(self, source: str, tickers: dict | None):
        with self.store.read() as conn:
            account = dict(conn.execute("SELECT * FROM accounts WHERE source=?", (source,)).fetchone())
            positions = conn.execute("SELECT * FROM positions WHERE source=?", (source,)).fetchall()
        quotes = {q["inst_id"]: q for q in tickers["items"]} if tickers else {}
        equity = Decimal(account["cash"])
        unrealized = Decimal(0)
        status = "example" if source == "example" else "fresh"
        complete = True
        result = []
        for position in positions:
            qty, basis = Decimal(position["quantity"]), Decimal(position["cost_basis"])
            if qty == 0:
                continue
            quote = quotes.get(position["inst_id"])
            try:
                mark = valid_mark(quote) if quote else None
            except DeskError:
                mark, quote = None, None
            value = qty * mark if mark is not None else None
            pnl = value - basis if value is not None else None
            if quote:
                try:
                    fresh_quote(quote, source)
                except DeskError:
                    status = "stale"
                equity += value
                unrealized += pnl
            else:
                complete = False
                status = "unavailable"
            result.append(
                {
                    "inst_id": position["inst_id"],
                    "quantity": str(qty),
                    "avg_cost": str(basis / qty),
                    "mark": str(mark) if mark is not None else None,
                    "market_value": str(value) if value is not None else None,
                    "unrealized_pnl": str(pnl) if pnl is not None else None,
                    "as_of": quote["ts"] if quote else None,
                }
            )
        if not tickers and source == "okx":
            status = "unavailable"
        return {
            "source": source,
            "initial_cash": account["initial_cash"],
            "cash": account["cash"],
            "equity": str(equity) if complete else None,
            "realized_pnl": account["realized_pnl"],
            "unrealized_pnl": str(unrealized) if complete else None,
            "fees_paid": account["fees_paid"],
            "valuation_status": status,
            "positions": result,
            "as_of": now_ms(),
        }

    def place(
        self,
        order: OrderInput,
        key: str,
        instrument: Instrument,
        tickers: dict,
        origin="manual",
        reason=None,
        deployment_id=None,
        evaluated_bar=None,
    ):
        """Return (order, replay). Market I/O happens before acquiring the write transaction."""
        payload = order_payload(order)
        try:
            with localcontext() as context:
                context.prec = 50
                with self.store.write() as conn:
                    existing = conn.execute(
                        "SELECT payload,body FROM orders WHERE source=? AND idempotency_key=?",
                        (order.source, key),
                    ).fetchone()
                    if existing:
                        if existing["payload"] != payload:
                            raise DeskError(
                                "idempotency_conflict",
                                "This idempotency key was used with a different order.",
                            )
                        return json.loads(existing["body"]), True
                    risk = conn.execute("SELECT * FROM risk WHERE source=?", (order.source,)).fetchone()
                    if risk["kill_switch"]:
                        raise DeskError(
                            "desk_halted",
                            "The paper desk is halted. Resume it in Risk before submitting orders.",
                        )
                    if deployment_id:
                        deployment = conn.execute(
                            "SELECT * FROM deployments WHERE id=?", (deployment_id,)
                        ).fetchone()
                        if not deployment or deployment["status"] != "running":
                            raise DeskError(
                                "deployment_stopped", "This strategy was stopped before its simulated fill."
                            )
                    if instrument.inst_id != order.inst_id or instrument.state != "live":
                        raise DeskError(
                            "instrument_unavailable", "This instrument is not available for paper execution."
                        )
                    qty = order.quantity
                    if qty < instrument.min_size or qty % instrument.lot_size != 0:
                        raise DeskError(
                            "invalid_quantity",
                            f"Quantity must be at least {instrument.min_size} and a multiple of {instrument.lot_size}.",
                            422,
                        )
                    if tickers["source"] != order.source:
                        raise DeskError("source_mismatch", "Quote source and paper account must match.")
                    quotes = {quote["inst_id"]: quote for quote in tickers["items"]}
                    quote = quotes.get(order.inst_id)
                    if not quote:
                        raise DeskError("quote_unavailable", "There is no quote for this instrument.")
                    fresh_quote(quote, order.source)
                    current_mark = valid_mark(quote)
                    raw = quote_price(quote, order.side)
                    adjusted = raw * (1 + SLIPPAGE_RATE if order.side == "buy" else 1 - SLIPPAGE_RATE)
                    rounding = ROUND_CEILING if order.side == "buy" else ROUND_DOWN
                    price = (adjusted / instrument.tick_size).to_integral_value(
                        rounding=rounding
                    ) * instrument.tick_size
                    if price <= 0:
                        raise DeskError(
                            "invalid_execution_price",
                            "The simulated execution price is below the instrument tick size.",
                        )
                    notional, fee = qty * price, qty * price * FEE_RATE
                    account = conn.execute(
                        "SELECT * FROM accounts WHERE source=?", (order.source,)
                    ).fetchone()
                    cash = Decimal(account["cash"])
                    realized = Decimal(account["realized_pnl"])
                    position = conn.execute(
                        "SELECT * FROM positions WHERE source=? AND inst_id=?", (order.source, order.inst_id)
                    ).fetchone()
                    old_qty = Decimal(position["quantity"]) if position else Decimal(0)
                    basis = Decimal(position["cost_basis"]) if position else Decimal(0)
                    day = datetime.now(UTC).strftime("%Y-%m-%d")
                    baseline = Decimal(account["day_equity"])
                    equity, complete = cash, True
                    if order.side == "buy" or day != account["day_key"]:
                        positions = conn.execute(
                            "SELECT * FROM positions WHERE source=?", (order.source,)
                        ).fetchall()
                        for item in positions:
                            amount = Decimal(item["quantity"])
                            if amount == 0:
                                continue
                            mark = quotes.get(item["inst_id"])
                            try:
                                if not mark:
                                    raise DeskError(
                                        "valuation_unavailable",
                                        "All held assets need fresh marks before increasing risk.",
                                    )
                                fresh_quote(mark, order.source)
                                equity += amount * valid_mark(mark)
                            except DeskError:
                                if order.side == "buy":
                                    raise
                                # A missing mark on another holding must not prevent reducing inventory.
                                complete = False
                                break
                        if complete and day != account["day_key"]:
                            baseline = equity
                            conn.execute(
                                "UPDATE accounts SET day_key=?,day_equity=? WHERE source=?",
                                (day, str(equity), order.source),
                            )
                    if order.side == "buy":
                        if notional > Decimal(risk["max_order_notional"]):
                            raise DeskError(
                                "order_limit", "Order notional exceeds the configured per-order limit."
                            )
                        if notional + fee > cash:
                            raise DeskError(
                                "insufficient_cash", "The paper account has insufficient USDT including fees."
                            )
                        post_equity = equity - fee - qty * (price - current_mark)
                        exposure = (old_qty + qty) * current_mark
                        if post_equity <= 0 or exposure / post_equity * 100 > Decimal(
                            str(risk["max_position_pct"])
                        ):
                            raise DeskError(
                                "position_limit", "This order would exceed the per-asset exposure limit."
                            )
                        # First accepted command with complete fresh marks anchors the observed UTC day.
                        if baseline > 0 and (baseline - post_equity) / baseline * 100 >= Decimal(
                            str(risk["max_daily_loss_pct"])
                        ):
                            raise DeskError(
                                "daily_loss_limit",
                                "The observed UTC-day loss limit blocks new buys. Selling remains available.",
                            )
                        cash -= notional + fee
                        new_qty, new_basis = old_qty + qty, basis + notional + fee
                    else:
                        if qty > old_qty:
                            raise DeskError(
                                "insufficient_asset", "The paper account cannot sell more than it holds."
                            )
                        removed_basis = basis if qty == old_qty else basis * qty / old_qty
                        cash += notional - fee
                        realized += notional - fee - removed_basis
                        new_qty, new_basis = old_qty - qty, basis - removed_basis
                    if cash < 0 or new_qty < 0 or new_basis < 0:
                        raise RuntimeError("Paper accounting invariant failed.")
                    conn.execute(
                        "INSERT INTO positions(source,inst_id,quantity,cost_basis) VALUES(?,?,?,?) "
                        "ON CONFLICT(source,inst_id) DO UPDATE SET quantity=excluded.quantity,cost_basis=excluded.cost_basis",
                        (order.source, order.inst_id, str(new_qty), str(new_basis)),
                    )
                    conn.execute(
                        "UPDATE accounts SET cash=?,realized_pnl=?,fees_paid=? WHERE source=?",
                        (str(cash), str(realized), str(Decimal(account["fees_paid"]) + fee), order.source),
                    )
                    result = {
                        "id": new_id(),
                        "source": order.source,
                        "inst_id": order.inst_id,
                        "side": order.side,
                        "quantity": str(qty),
                        "price": str(price),
                        "fee": str(fee),
                        "notional": str(notional),
                        "status": "filled",
                        "created_at": now_ms(),
                        "origin": origin,
                        "reason": reason,
                        "quote_ts": quote["ts"],
                        "cash_after": str(cash),
                        "position_after": str(new_qty),
                        "cost_basis_after": str(new_basis),
                        "realized_pnl_after": str(realized),
                        "fee_bps": "10",
                        "slippage_bps": "5",
                    }
                    conn.execute(
                        "INSERT INTO orders(id,source,idempotency_key,payload,body,created_at) VALUES(?,?,?,?,?,?)",
                        (result["id"], order.source, key, payload, dumps(result), result["created_at"]),
                    )
                    self.store.audit(
                        conn,
                        order.source,
                        "paper.filled",
                        f"{order.side.title()} {qty} {order.inst_id} · local paper",
                        result,
                    )
                    if deployment_id:
                        conn.execute(
                            "UPDATE deployments SET last_bar=?,last_error=NULL,updated_at=? WHERE id=?",
                            (evaluated_bar, now_ms(), deployment_id),
                        )
                    return result, False
        except DeskError as exc:
            with self.store.write() as conn:
                self.store.audit(
                    conn,
                    order.source,
                    "paper.rejected",
                    exc.message,
                    {"code": exc.code, "order": json.loads(payload)},
                )
            raise

    def update_risk(self, source, values):
        with self.store.write() as conn:
            conn.execute(
                "UPDATE risk SET max_order_notional=?,max_position_pct=?,max_daily_loss_pct=?,updated_at=? WHERE source=?",
                (
                    str(values.max_order_notional),
                    values.max_position_pct,
                    values.max_daily_loss_pct,
                    now_ms(),
                    source,
                ),
            )
            self.store.audit(conn, source, "risk.updated", "Paper risk limits updated", values.model_dump())
        return self.store.risk(source)

    def halt(self, source, active, reason):
        with self.store.write() as conn:
            conn.execute(
                "UPDATE risk SET kill_switch=?,updated_at=? WHERE source=?", (int(active), now_ms(), source)
            )
            self.store.audit(
                conn, source, "risk.halted" if active else "risk.resumed", reason, {"active": active}
            )
        return self.store.risk(source)
