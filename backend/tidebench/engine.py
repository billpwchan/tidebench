"""Deterministic, long-only spot research engine.

Candles are labelled by their UTC opening timestamp in milliseconds. Signals use
closed bars; executions occur at the following bar's open. Equity observations
are labelled by their closing timestamp. This deliberately models neither an
order book nor intrabar order paths.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from math import isfinite, sqrt
from statistics import mean, stdev
from typing import Any

ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
DAY_MS = 86_400_000
ACCOUNTING_PRECISION = 50
ACCOUNTING_LIMIT = Decimal("1e30")
ACCOUNTING_CONTEXT = Context(
    prec=ACCOUNTING_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class EngineError(ValueError):
    """A requested simulation violates the engine's explicit assumptions."""


@dataclass(frozen=True)
class Candle:
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    confirmed: bool = True


@dataclass(frozen=True)
class Instrument:
    inst_id: str
    base: str
    quote: str
    tick_size: Decimal
    lot_size: Decimal
    min_size: Decimal
    state: str = "live"


@dataclass(frozen=True)
class StrategyConfig:
    kind: str = "sma_cross"
    fast: int = 12
    slow: int = 26
    rsi_period: int = 14
    entry: Decimal = Decimal("30")
    exit: Decimal = Decimal("60")
    allocation: Decimal = Decimal("0.25")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: Decimal = Decimal("10000")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    strategy: StrategyConfig = field(default_factory=StrategyConfig)


def _decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise EngineError(f"{name} must be a finite Decimal")
    digits = value.as_tuple().digits
    end = len(digits)
    while end > 0 and digits[end - 1] == 0:
        end -= 1
    if end > ACCOUNTING_PRECISION:
        raise EngineError(
            f"Accounting precision exceeds the supported numeric domain (50 significant digits): {name}"
        )
    return value


def _accounting_value(value: Decimal, name: str) -> Decimal:
    if not value.is_finite() or value.copy_abs() >= ACCOUNTING_LIMIT:
        raise EngineError(
            f"Accounting value is outside the supported numeric domain (absolute value < 1e30): {name}"
        )
    return value


def _integer(value: int, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EngineError(f"{name} must be an integer >= {minimum}")
    return value


def _string(value: Decimal) -> str:
    if value == ZERO:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _float(value: Decimal | float) -> float:
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise EngineError("result exceeds the supported numeric range") from exc
    if not isfinite(result):
        raise EngineError("result exceeds the supported numeric range")
    return result


def _validate_strategy(strategy: StrategyConfig) -> None:
    if not isinstance(strategy, StrategyConfig):
        raise EngineError("strategy must be a StrategyConfig")
    if strategy.kind not in {"sma_cross", "rsi_reversion", "buy_hold"}:
        raise EngineError("unsupported strategy kind")
    allocation = _decimal(strategy.allocation, "allocation")
    if not ZERO <= allocation <= ONE:
        raise EngineError("allocation must be between 0 and 1")
    if strategy.kind == "sma_cross":
        _integer(strategy.fast, "fast")
        _integer(strategy.slow, "slow")
        if strategy.fast >= strategy.slow:
            raise EngineError("fast must be smaller than slow")
    if strategy.kind == "rsi_reversion":
        _integer(strategy.rsi_period, "rsi_period", 2)
        entry = _decimal(strategy.entry, "entry")
        exit_value = _decimal(strategy.exit, "exit")
        if not ZERO <= entry < exit_value <= Decimal("100"):
            raise EngineError("RSI thresholds must satisfy 0 <= entry < exit <= 100")


def _validate_config(instrument: Instrument, config: BacktestConfig) -> None:
    if not isinstance(config, BacktestConfig) or not isinstance(instrument, Instrument):
        raise EngineError("instrument and config must use the engine dataclasses")
    if _decimal(config.initial_cash, "initial_cash") <= ZERO:
        raise EngineError("initial_cash must be positive")
    _accounting_value(config.initial_cash, "initial_cash")
    for name in ("fee_bps", "slippage_bps"):
        if not ZERO <= _decimal(getattr(config, name), name) < BPS:
            raise EngineError(f"{name} must be >= 0 and < 10000")
    for name in ("tick_size", "lot_size", "min_size"):
        if _decimal(getattr(instrument, name), name) <= ZERO:
            raise EngineError(f"{name} must be positive")
    if not all(
        isinstance(value, str) and value for value in (instrument.inst_id, instrument.base, instrument.quote)
    ):
        raise EngineError("instrument identifiers must be non-empty strings")
    if instrument.state != "live":
        raise EngineError("instrument is not live")
    _validate_strategy(config.strategy)


def validate_candles(
    candles: list[Candle], interval_ms: int, require_contiguous: bool = True
) -> dict[str, Any]:
    """Reject malformed input without sorting, dropping or inventing records."""
    _integer(interval_ms, "interval_ms")
    if not candles:
        raise EngineError("at least one candle is required")
    missing = 0
    previous: int | None = None
    for index, candle in enumerate(candles):
        if not isinstance(candle, Candle):
            raise EngineError(f"candle {index} must be a Candle")
        _integer(candle.ts, f"candle {index} ts", 0)
        if candle.ts % interval_ms:
            raise EngineError(f"candle {index} timestamp is not interval-aligned")
        if previous is not None:
            difference = candle.ts - previous
            if difference <= 0:
                raise EngineError("candle timestamps must be strictly ascending and unique")
            missing += difference // interval_ms - 1
        previous = candle.ts
        if candle.confirmed is not True:
            raise EngineError(f"candle {index} is not confirmed closed")
        for name in ("open", "high", "low", "close"):
            if _decimal(getattr(candle, name), f"candle {index} {name}") <= ZERO:
                raise EngineError(f"candle {index} prices must be positive")
            _accounting_value(getattr(candle, name), f"candle {index} {name}")
        if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
            raise EngineError(f"candle {index} has invalid OHLC bounds")
        if _decimal(candle.volume, f"candle {index} volume") < ZERO:
            raise EngineError(f"candle {index} volume must be non-negative")
    if missing and require_contiguous:
        raise EngineError(f"candle series contains {missing} missing intervals")
    return {
        "valid": True,
        "candles": len(candles),
        "confirmed_candles": len(candles),
        "interval_ms": interval_ms,
        "start_ts": candles[0].ts,
        "end_ts": candles[-1].ts + interval_ms,
        "missing_intervals": missing,
        "contiguous": missing == 0,
        "warnings": [] if not missing else ["missing_intervals"],
    }


class _SignalState:
    """Single-pass indicators shared by standalone decisions and replay."""

    def __init__(self, strategy: StrategyConfig):
        self.strategy = strategy
        self.fast_prices: deque[Decimal] = deque()
        self.slow_prices: deque[Decimal] = deque()
        self.fast_sum = ZERO
        self.slow_sum = ZERO
        self.previous: Decimal | None = None
        self.changes = 0
        self.gain = ZERO
        self.loss = ZERO

    def on_close(self, close: Decimal) -> Decimal | None:
        strategy = self.strategy
        if strategy.kind == "buy_hold":
            return strategy.allocation
        if strategy.kind == "sma_cross":
            self.fast_prices.append(close)
            self.slow_prices.append(close)
            self.fast_sum += close
            self.slow_sum += close
            if len(self.fast_prices) > strategy.fast:
                self.fast_sum -= self.fast_prices.popleft()
            if len(self.slow_prices) > strategy.slow:
                self.slow_sum -= self.slow_prices.popleft()
            if len(self.slow_prices) < strategy.slow:
                return None
            return (
                strategy.allocation if self.fast_sum / strategy.fast > self.slow_sum / strategy.slow else ZERO
            )
        if self.previous is None:
            self.previous = close
            return None
        change = close - self.previous
        self.previous = close
        self.changes += 1
        period = strategy.rsi_period
        if self.changes <= period:
            self.gain += max(change, ZERO)
            self.loss += max(-change, ZERO)
            if self.changes < period:
                return None
            self.gain /= period
            self.loss /= period
        else:
            self.gain = (self.gain * (period - 1) + max(change, ZERO)) / period
            self.loss = (self.loss * (period - 1) + max(-change, ZERO)) / period
        if self.loss == ZERO:
            rsi = Decimal("50") if self.gain == ZERO else Decimal("100")
        else:
            rsi = Decimal("100") - Decimal("100") / (ONE + self.gain / self.loss)
        if rsi < strategy.entry:
            return strategy.allocation
        if rsi > strategy.exit:
            return ZERO
        return None


def target_position(history: list[Candle], strategy: StrategyConfig) -> Decimal | None:
    """Return a target regime; None means retain the current position.

    The caller supplies only confirmed history available at the decision time.
    SMA equality is flat. RSI uses Wilder smoothing and strict entry/exit bounds.
    """
    _validate_strategy(strategy)
    if not history:
        return None
    previous = None
    for index, candle in enumerate(history):
        if not isinstance(candle, Candle) or candle.confirmed is not True:
            raise EngineError(f"history candle {index} must be confirmed closed")
        if _decimal(candle.close, f"history candle {index} close") <= ZERO:
            raise EngineError("history prices must be positive")
        _accounting_value(candle.close, f"history candle {index} close")
        _integer(candle.ts, f"history candle {index} ts", 0)
        if previous is not None and candle.ts <= previous:
            raise EngineError("history timestamps must be strictly ascending and unique")
        previous = candle.ts
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            signal = _SignalState(strategy)
            result = None
            for candle in history:
                result = signal.on_close(candle.close)
            return result
    except DecimalException as exc:
        raise EngineError("Accounting precision is outside the supported numeric domain") from exc


def _round_to_step(value: Decimal, step: Decimal, rounding: str, unit: str) -> Decimal:
    try:
        ratio = value / step
    except DecimalException as exc:
        raise EngineError(
            f"Accounting precision cannot resolve one {unit} in the supported numeric domain"
        ) from exc
    if not ratio.is_finite() or ratio.adjusted() >= ACCOUNTING_PRECISION:
        raise EngineError(f"Accounting precision cannot resolve one {unit} in the supported numeric domain")
    units = ratio.to_integral_value(rounding=rounding)
    result = units * step
    # Verify that context rounding did not turn an executable lot multiple into
    # a fractional lot. Extra precision is only used for this exact check.
    with localcontext(ACCOUNTING_CONTEXT) as check_context:
        check_context.prec = max(
            ACCOUNTING_PRECISION, len(units.as_tuple().digits) + len(step.as_tuple().digits)
        )
        if result != units * step:
            raise EngineError(
                f"Accounting precision cannot represent a {unit} multiple in the supported numeric domain"
            )
    return result


def _floor_lot(quantity: Decimal, lot: Decimal) -> Decimal:
    return _round_to_step(quantity, lot, ROUND_FLOOR, "lot")


def _fill_price(open_price: Decimal, side: str, instrument: Instrument, slippage: Decimal) -> Decimal:
    multiplier = ONE + slippage if side == "buy" else ONE - slippage
    rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    price = _round_to_step(open_price * multiplier, instrument.tick_size, rounding, "price tick")
    if price <= ZERO:
        raise EngineError("sell price rounds to zero at the instrument tick size")
    return _accounting_value(price, "execution price")


def _buy_quantity(budget: Decimal, price: Decimal, fee_rate: Decimal, instrument: Instrument) -> Decimal:
    quantity = _floor_lot(budget / (price * (ONE + fee_rate)), instrument.lot_size)
    # Decimal division can round a mathematical boundary upward. Correct the
    # executable quantity, never the resulting balance, to preserve solvency.
    while quantity > ZERO and quantity * price * (ONE + fee_rate) > budget:
        next_quantity = quantity - instrument.lot_size
        if next_quantity >= quantity:
            raise EngineError(
                "Accounting precision cannot make a one-lot correction in the supported numeric domain"
            )
        quantity = next_quantity
    _accounting_value(quantity, "order quantity")
    return quantity if quantity >= instrument.min_size else ZERO


def _daily_sharpe(
    observations: list[tuple[int, Decimal]], start_ts: int, end_ts: int, interval_ms: int
) -> tuple[float | None, str | None, int]:
    if interval_ms > DAY_MS or DAY_MS % interval_ms:
        return None, "unsupported_daily_interval", 0
    equity_at = dict(observations)
    day_start = ((start_ts + DAY_MS - 1) // DAY_MS) * DAY_MS
    returns: list[float] = []
    while day_start + DAY_MS <= end_ts:
        opening = equity_at.get(day_start)
        closing = equity_at.get(day_start + DAY_MS)
        if opening is not None and closing is not None:
            returns.append(_float(closing / opening - ONE))
        day_start += DAY_MS
    count = len(returns)
    if count < 30:
        return None, "insufficient_sample", count
    deviation = stdev(returns)
    if deviation <= 1e-12:
        return None, "zero_variance", count
    return _float(mean(returns) / deviation * sqrt(365)), None, count


def run_backtest(
    candles: list[Candle], interval_ms: int, instrument: Instrument, config: BacktestConfig
) -> dict[str, Any]:
    """Replay a confirmed, contiguous series without leverage or rebalancing."""
    quality = validate_candles(candles, interval_ms)
    _validate_config(instrument, config)
    if len(candles) < 2:
        raise EngineError("at least two candles are required for next-open execution")
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            return _run(candles, interval_ms, instrument, config, quality)
    except DecimalException as exc:
        raise EngineError("Accounting precision is outside the supported numeric domain") from exc


def _run(
    candles: list[Candle],
    interval_ms: int,
    instrument: Instrument,
    config: BacktestConfig,
    quality: dict[str, Any],
) -> dict[str, Any]:
    cash = config.initial_cash
    quantity = ZERO
    cost_basis = ZERO
    fees = ZERO
    realized = ZERO
    high_water = cash
    maximum_drawdown = ZERO
    fee_rate = config.fee_bps / BPS
    slippage = config.slippage_bps / BPS
    benchmark_cash = cash
    benchmark_quantity = ZERO
    pending: tuple[str, Decimal] | None = None
    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    observations: list[tuple[int, Decimal]] = [(candles[0].ts, cash)]
    skipped_buys = 0
    signal = _SignalState(config.strategy)
    for index, candle in enumerate(candles):
        if pending is not None:
            side, allocation = pending
            price = _fill_price(candle.open, side, instrument, slippage)
            fill_quantity = (
                _buy_quantity(cash * allocation, price, fee_rate, instrument) if side == "buy" else quantity
            )
            if fill_quantity > ZERO:
                _accounting_value(fill_quantity, "fill quantity")
                notional = fill_quantity * price
                fee = notional * fee_rate
                _accounting_value(notional, "fill notional")
                _accounting_value(fee, "fill fee")
                if side == "buy":
                    cost_basis = notional + fee
                    cash -= cost_basis
                    quantity = fill_quantity
                else:
                    cash += notional - fee
                    realized += notional - fee - cost_basis
                    quantity = ZERO
                    cost_basis = ZERO
                fees += fee
                for name, amount in (
                    ("cash", cash),
                    ("quantity", quantity),
                    ("cost_basis", cost_basis),
                    ("fees_paid", fees),
                    ("realized_pnl", realized),
                ):
                    _accounting_value(amount, name)
                if cash < ZERO or quantity < ZERO:
                    raise EngineError("spot accounting invariant violated")
                trades.append(
                    {
                        "ts": candle.ts,
                        "side": side,
                        "quantity": _string(fill_quantity),
                        "price": _string(price),
                        "fee": _string(fee),
                        "cash": _string(cash),
                    }
                )
            elif side == "buy":
                skipped_buys += 1
        if index == 1:
            price = _fill_price(candle.open, "buy", instrument, slippage)
            benchmark_quantity = _buy_quantity(benchmark_cash, price, fee_rate, instrument)
            benchmark_notional = _accounting_value(benchmark_quantity * price, "benchmark notional")
            benchmark_fee = _accounting_value(benchmark_notional * fee_rate, "benchmark fee")
            benchmark_cash -= benchmark_notional + benchmark_fee
            _accounting_value(benchmark_cash, "benchmark cash")
        value = cash + quantity * candle.close
        benchmark_value = benchmark_cash + benchmark_quantity * candle.close
        _accounting_value(value, "equity")
        _accounting_value(benchmark_value, "benchmark equity")
        high_water = max(high_water, value)
        drawdown = (ONE - value / high_water) * Decimal("100")
        maximum_drawdown = max(maximum_drawdown, drawdown)
        close_ts = candle.ts + interval_ms
        equity.append(
            {
                "ts": close_ts,
                "equity": _string(value),
                "benchmark": _string(benchmark_value),
                "drawdown_pct": _float(drawdown),
            }
        )
        observations.append((close_ts, value))
        target = signal.on_close(candle.close)
        pending = None
        if target is not None:
            if target > ZERO and quantity == ZERO:
                pending = ("buy", target)
            elif target == ZERO and quantity > ZERO:
                pending = ("sell", ZERO)
    sharpe, sharpe_reason, daily_returns = _daily_sharpe(
        observations, candles[0].ts, candles[-1].ts + interval_ms, interval_ms
    )
    return {
        "metrics": {
            "total_return_pct": _float((value / config.initial_cash - ONE) * Decimal("100")),
            "benchmark_return_pct": _float((benchmark_value / config.initial_cash - ONE) * Decimal("100")),
            "max_drawdown_pct": _float(maximum_drawdown),
            "sharpe": sharpe,
            "sharpe_reason": sharpe_reason,
            "trades": len(trades),
            "fees_paid": _string(fees),
            "final_equity": _string(value),
            "initial_cash": _string(config.initial_cash),
            "realized_pnl": _string(realized),
        },
        "equity": equity,
        "trades": trades,
        "assumptions": {
            "accounting": {
                "precision": ACCOUNTING_PRECISION,
                "rounding": "ROUND_HALF_EVEN",
                "absolute_amount_limit_exclusive": "1e30",
                "mode": "finite_decimal_context",
            },
            "market": "long_only_spot",
            "instrument": instrument.inst_id,
            "strategy": config.strategy.kind,
            "signal": "confirmed_bar_close",
            "execution": "next_bar_open",
            "candle_timestamp": "UTC_open_ms",
            "equity_timestamp": "UTC_close_ms",
            "fee_currency": instrument.quote,
            "fee_bps": _string(config.fee_bps),
            "slippage_bps": _string(config.slippage_bps),
            "slippage_model": "fixed_adverse_bps_plus_adverse_tick_rounding",
            "quantity_rounding": "floor_to_lot",
            "rebalance": False,
            "position_sizing": "allocation_of_available_cash_on_flat_to_long",
            "benchmark": "full_allocation_at_second_bar_open_with_same_costs",
            "terminal_position": "mark_to_market_no_forced_liquidation",
            "trade_count": "filled_orders",
            "skipped_buys_below_min_size": skipped_buys,
            "final_signal_unfilled": pending is not None,
            "sharpe": {
                "frequency": "complete_UTC_days",
                "daily_returns": daily_returns,
                "minimum_returns": 30,
                "annualization": 365,
                "risk_free_rate": "0",
            },
            "limitations": [
                "no_order_book_or_liquidity_model",
                "no_intrabar_path_model",
                "fixed_quote_currency_fee",
                "not_a_forecast_of_live_performance",
            ],
        },
        "quality": quality,
    }
