"""Reproducible bar-level spot/linear-perpetual research and bounded experiments.

This is an event simulation with an explicit OHLC risk ordering. It cannot
reconstruct exchange queue priority, order-book liquidity or a hidden intrabar
path. Every result carries its inputs and modeling assumptions for replay.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, DecimalException, localcontext
from hashlib import sha256
from itertools import product
from math import sqrt
from statistics import mean, median, stdev
from threading import Lock
from typing import Any

from .derivatives import (
    FundingEvent,
    LinearContract,
    MarginTier,
    contract_base_quantity,
    contract_notional,
    funding_payment,
    linear_pnl,
    liquidation_condition,
    liquidation_price,
    select_margin_tier,
    validate_margin_tiers,
)
from .engine import (
    ACCOUNTING_CONTEXT,
    BPS,
    DAY_MS,
    ONE,
    ZERO,
    Candle,
    EngineError,
    Instrument,
    StrategyConfig,
    _accounting_value,
    _buy_quantity,
    _decimal,
    _fill_price,
    _float,
    _floor_lot,
    _integer,
    _string,
    _validate_strategy,
    validate_candles,
)
from .strategy_risk import exit_on_close, risk_notional

ENGINE_VERSION = "pro-research-1"
MAX_CASES = 64
MAX_FOLDS = 20
MAX_BAR_WORK = 2_000_000
MAX_CANDLES = 100_000
research_progress = ContextVar("research_progress", default=None)


@dataclass(frozen=True)
class ResearchConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    direction: str = "long_only"
    initial_cash: Decimal = Decimal("10000")
    leverage: Decimal = ONE
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    liquidation_fee_bps: Decimal = Decimal("5")
    start_ts: int | None = None
    end_ts: int | None = None


@dataclass(frozen=True)
class ResearchPlanConfig:
    train_fraction: float = 0.7
    train_bars: int | None = None
    test_bars: int | None = None
    step_bars: int | None = None
    purge_bars: int = 0
    grid: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    fee_bps: Sequence[Decimal] = ()
    slippage_bps: Sequence[Decimal] = ()
    max_workers: int = 2


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _string(value)
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise EngineError(f"snapshot contains an unsupported value: {type(value).__name__}")


def _hash(value: Any) -> str:
    encoded = json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode()).hexdigest()


def _validate_config(config: ResearchConfig, instrument: Instrument | LinearContract) -> None:
    if not isinstance(config, ResearchConfig):
        raise EngineError("config must be a ResearchConfig")
    _validate_strategy(config.strategy)
    for name, maximum in (
        ("stop_loss_pct", 100),
        ("take_profit_pct", 1000),
        ("trailing_stop_pct", 100),
        ("risk_per_trade_pct", 10),
    ):
        if not ZERO <= _decimal(getattr(config.strategy, name), name) <= maximum:
            raise EngineError(f"{name} is outside supported bounds")
    _integer(config.strategy.max_holding_bars, "max_holding_bars", 0)
    if config.strategy.risk_per_trade_pct and not config.strategy.stop_loss_pct:
        raise EngineError("Loss-budget sizing requires a positive close-based stop loss")
    if config.direction not in {"long_only", "short_only", "long_short"}:
        raise EngineError("direction must be long_only, short_only or long_short")
    if not isinstance(instrument, (Instrument, LinearContract)):
        raise EngineError("unsupported research instrument")
    for name in ("initial_cash", "leverage"):
        if _decimal(getattr(config, name), name) <= ZERO:
            raise EngineError(f"{name} must be positive")
        _accounting_value(getattr(config, name), name)
    if config.leverage < ONE or config.leverage > Decimal("100"):
        raise EngineError("leverage must be between 1 and 100")
    for name in ("fee_bps", "slippage_bps", "liquidation_fee_bps"):
        if not ZERO <= _decimal(getattr(config, name), name) < BPS:
            raise EngineError(f"{name} must be between 0 inclusive and 10000 exclusive")
    for name in ("start_ts", "end_ts"):
        if getattr(config, name) is not None:
            _integer(getattr(config, name), name, 0)
    if config.start_ts is not None and config.end_ts is not None and config.start_ts >= config.end_ts:
        raise EngineError("start_ts must be before end_ts")
    if isinstance(instrument, Instrument):
        if config.direction != "long_only" or config.leverage != ONE:
            raise EngineError("spot research supports long_only without leverage")
        if instrument.quote != "USDT" or instrument.state != "live":
            raise EngineError("spot research requires a live USDT-quoted instrument")
        for name in ("tick_size", "lot_size", "min_size"):
            if _decimal(getattr(instrument, name), name) <= ZERO:
                raise EngineError(f"{name} must be positive")


class _DecisionState:
    """One causal indicator implementation across research and forward execution."""

    def __init__(self, config: ResearchConfig):
        self.config = config
        self.fast, self.slow, self.window = deque(), deque(), deque()
        self.fast_sum = self.slow_sum = self.gain = self.loss = ZERO
        self.previous = None
        self.changes = 0
        self.atr = ZERO
        self.atr_count = 0
        self.volume = self.true_range = None

    def snapshot(self):
        return json_safe(
            {
                "schema_version": 2,
                "fast": list(self.fast),
                "slow": list(self.slow),
                "window": list(self.window),
                "fast_sum": self.fast_sum,
                "slow_sum": self.slow_sum,
                "previous": self.previous,
                "gain": self.gain,
                "loss": self.loss,
                "changes": self.changes,
                "atr": self.atr,
                "atr_count": self.atr_count,
            }
        )

    def restore(self, body):
        if body.get("schema_version") != 2:
            raise EngineError("unsupported indicator checkpoint")
        for name, limit in (
            ("fast", self.config.strategy.fast),
            ("slow", self.config.strategy.slow),
            ("window", self.config.strategy.window),
        ):
            values = deque(Decimal(v) for v in body[name])
            if len(values) > limit:
                raise EngineError("indicator checkpoint exceeds its window")
            setattr(self, name, values)
        for name in ("fast_sum", "slow_sum", "gain", "loss", "atr"):
            setattr(self, name, Decimal(body[name]))
        self.previous = Decimal(body["previous"]) if body["previous"] is not None else None
        self.changes, self.atr_count = body["changes"], body["atr_count"]

    def on_bar(self, candle):
        self.volume = candle.volume
        self.true_range = (
            max(candle.high - candle.low, abs(candle.high - self.previous), abs(candle.low - self.previous))
            if self.previous is not None
            else candle.high - candle.low
        )
        return self.on_close(candle.close)

    def on_close(self, close):
        from .strategy_program import compare_rule

        strategy = self.config.strategy
        previous = self.previous
        channel = (
            (max(self.window), min(self.window)) if len(self.window) == strategy.window else (None, None)
        )
        for values, length, total in (
            (self.fast, strategy.fast, "fast_sum"),
            (self.slow, strategy.slow, "slow_sum"),
        ):
            values.append(close)
            setattr(self, total, getattr(self, total) + close)
            if len(values) > length:
                setattr(self, total, getattr(self, total) - values.popleft())
        self.window.append(close)
        if len(self.window) > strategy.window:
            self.window.popleft()
        rsi = None
        if previous is not None:
            change = close - previous
            self.changes += 1
            period = strategy.rsi_period
            if self.changes <= period:
                self.gain += max(change, ZERO)
                self.loss += max(-change, ZERO)
                if self.changes == period:
                    self.gain /= period
                    self.loss /= period
            else:
                self.gain = (self.gain * (period - 1) + max(change, ZERO)) / period
                self.loss = (self.loss * (period - 1) + max(-change, ZERO)) / period
            if self.changes >= period:
                rsi = (
                    (Decimal("50") if self.gain == self.loss == ZERO else Decimal("100"))
                    if self.loss == ZERO
                    else Decimal("100") - Decimal("100") / (ONE + self.gain / self.loss)
                )
        self.previous = close
        if self.true_range is not None:
            self.atr_count += 1
            if self.atr_count <= strategy.atr_period:
                self.atr += self.true_range
                if self.atr_count == strategy.atr_period:
                    self.atr /= strategy.atr_period
            else:
                self.atr = (self.atr * (strategy.atr_period - 1) + self.true_range) / strategy.atr_period
        zscore = None
        if strategy.kind in {"zscore_reversion", "program"} and len(self.window) == strategy.window:
            average = sum(self.window, ZERO) / strategy.window
            variance = sum(((v - average) ** 2 for v in self.window), ZERO) / strategy.window
            zscore = (close - average) / variance.sqrt() if variance > ZERO else ZERO
        features = {
            "close": close,
            "volume": self.volume,
            "fast_sma": self.fast_sum / strategy.fast if len(self.fast) == strategy.fast else None,
            "slow_sma": self.slow_sum / strategy.slow if len(self.slow) == strategy.slow else None,
            "rsi": rsi,
            "zscore": zscore,
            "channel_upper": channel[0],
            "channel_lower": channel[1],
            "atr": self.atr if self.atr_count >= strategy.atr_period else None,
        }
        tag = None
        if strategy.kind == "buy_hold":
            raw = -1 if self.config.direction == "short_only" else 1
        elif strategy.kind == "sma_cross":
            fast, slow = features["fast_sma"], features["slow_sma"]
            raw = None if slow is None else 1 if fast > slow else -1 if fast < slow else 0
        elif strategy.kind == "rsi_reversion":
            raw = None if rsi is None else 1 if rsi < strategy.entry else -1 if rsi > strategy.exit else None
        elif strategy.kind == "close_breakout":
            raw = (
                None
                if channel[0] is None
                else 1
                if close > channel[0]
                else -1
                if close < channel[1]
                else None
            )
        elif strategy.kind == "zscore_reversion":
            raw = (
                None
                if zscore is None
                else 1
                if zscore < -strategy.z_entry
                else -1
                if zscore > strategy.z_entry
                else 0
                if abs(zscore) <= strategy.z_exit
                else None
            )
        else:
            raw = None
            for rule in strategy.rules:
                if compare_rule(rule, features):
                    raw, tag = rule["signal"], rule["tag"]
                    break
        if (
            raw == 1
            and self.config.direction == "short_only"
            or raw == -1
            and self.config.direction == "long_only"
        ):
            raw = 0
        # Retain the historical indicator keys for existing result consumers.
        names = {
            "sma_cross": ("fast_sma", "slow_sma"),
            "rsi_reversion": ("rsi",),
            "buy_hold": (),
            "close_breakout": ("channel_upper", "channel_lower"),
            "zscore_reversion": ("zscore",),
        }.get(strategy.kind, tuple(features))
        indicators = {key: _string(features[key]) for key in names if features[key] is not None}
        if strategy.kind == "sma_cross" and features["slow_sma"] is None:
            indicators = {}
        ready = strategy.kind == "buy_hold" or bool(indicators)
        return raw, indicators | {
            "reason": "warmup" if not ready else "hold_regime" if raw is None else "closed_bar_signal",
            **({"tag": tag} if tag else {}),
        }


def directional_signal(
    history: list[Candle], strategy: StrategyConfig, direction: str = "long_only"
) -> int | None:
    """Shared causal regime policy for research and persistent paper execution.

    +1 is long, -1 short, 0 flat and None retains inventory or warms up. SMA
    equality is flat in every direction; it must not be confused with short.
    """
    _validate_strategy(strategy)
    if direction not in {"long_only", "short_only", "long_short"}:
        raise EngineError("unsupported direction")
    previous = None
    for candle in history:
        if not isinstance(candle, Candle) or candle.confirmed is not True:
            raise EngineError("directional signals require confirmed candles")
        if _decimal(candle.close, "signal close") <= ZERO:
            raise EngineError("signal close must be positive")
        _accounting_value(candle.close, "signal close")
        if previous is not None and candle.ts <= previous:
            raise EngineError("signal history must be strictly ascending")
        previous = candle.ts
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            state = _DecisionState(ResearchConfig(strategy=strategy, direction=direction))
            target = None
            for candle in history:
                target, _ = state.on_bar(candle)
            return target
    except DecimalException as exc:
        raise EngineError("Signal arithmetic exceeds the supported numeric domain") from exc


def _selection(candles: list[Candle], interval_ms: int, config: ResearchConfig) -> list[int]:
    selected = [
        index
        for index, candle in enumerate(candles)
        if (config.start_ts is None or candle.ts >= config.start_ts)
        and (config.end_ts is None or candle.ts + interval_ms <= config.end_ts)
    ]
    if len(selected) < 2:
        raise EngineError("research window must contain at least two complete bars")
    return selected


def _funding_records(events: Sequence[FundingEvent | Mapping[str, Any]] | None) -> list[FundingEvent]:
    if events is None:
        raise EngineError("perpetual research requires an explicit realized funding schedule")
    normalized = [
        event if isinstance(event, FundingEvent) else FundingEvent.from_record(event) for event in events
    ]
    if any(left.ts >= right.ts for left, right in zip(normalized, normalized[1:], strict=False)):
        raise EngineError("funding events must be strictly ascending and unique")
    return normalized


def _tier_records(
    tiers: Sequence[MarginTier | Mapping[str, Any]] | Mapping[str, Any] | None,
) -> list[MarginTier]:
    if isinstance(tiers, Mapping):
        tiers = tiers.get("tiers")
    normalized = [
        tier if isinstance(tier, MarginTier) else MarginTier.from_record(tier) for tier in (tiers or [])
    ]
    validate_margin_tiers(normalized)
    return normalized


def _assert_sources(provenance: Mapping[str, Any]) -> None:
    sources = set()
    for key in ("trade", "mark", "funding", "tiers"):
        record = provenance.get(key)
        if isinstance(record, Mapping) and record.get("source"):
            sources.add(record["source"])
    if len(sources) > 1:
        raise EngineError("research cannot combine synthetic and exchange data sources")


def run_professional_backtest(
    trade_candles: list[Candle],
    interval_ms: int,
    instrument: Instrument | LinearContract,
    config: ResearchConfig,
    *,
    mark_candles: list[Candle] | None = None,
    funding_events: Sequence[FundingEvent | Mapping[str, Any]] | None = None,
    margin_tiers: Sequence[MarginTier | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    _shared_input: tuple[str, dict] | None = None,
    progress=None,
) -> dict[str, Any]:
    """Replay one flat-start, close-signal/next-open experiment.

    Funding at a bar boundary applies to inventory carried into that boundary,
    before strategy fills. For intrabar settlements an exact supplied mark is
    mandatory. Intrabar funding is processed before the adverse OHLC risk
    excursion: a declared deterministic scenario, not a recovered tick path.
    """
    if len(trade_candles) > MAX_CANDLES:
        raise EngineError(f"research accepts at most {MAX_CANDLES} bars per dataset")
    quality = validate_candles(trade_candles, interval_ms)
    _validate_config(config, instrument)
    selected = _selection(trade_candles, interval_ms, config)
    provenance = dict(provenance or {})
    _assert_sources(provenance)
    is_perp = isinstance(instrument, LinearContract)
    if is_perp:
        if mark_candles is None:
            raise EngineError("perpetual research requires independent historical mark candles")
        mark_quality = validate_candles(mark_candles, interval_ms)
        marks = {candle.ts: candle for candle in mark_candles}
        if any(trade_candles[index].ts not in marks for index in selected):
            raise EngineError("mark candles do not cover every selected trade bar")
        events = _funding_records(funding_events)
        if any(event.inst_id is not None and event.inst_id != instrument.inst_id for event in events):
            raise EngineError("funding instrument does not match the researched contract")
        for name in ("trade", "mark", "funding", "tiers"):
            record = provenance.get(name)
            if isinstance(record, Mapping) and record.get("inst_id") not in (None, instrument.inst_id):
                raise EngineError(f"{name} provenance instrument does not match the researched contract")
        tiers = _tier_records(margin_tiers)
        funding_manifest = provenance.get("funding", {})
        source = funding_manifest.get("source") if isinstance(funding_manifest, Mapping) else None
        trade_manifest = provenance.get("trade", {})
        trade_source = trade_manifest.get("source") if isinstance(trade_manifest, Mapping) else None
        if source == "okx" or trade_source == "okx":
            if not isinstance(funding_manifest, Mapping) or not funding_manifest:
                raise EngineError("exchange funding research requires a source provenance manifest")
            coverage = funding_manifest.get("quality", {})
            start, end = trade_candles[selected[0]].ts, trade_candles[selected[-1]].ts + interval_ms
            if (
                not coverage.get("complete")
                or coverage.get("coverage_start") is None
                or coverage.get("coverage_end") is None
                or coverage["coverage_start"] > start
                or coverage["coverage_end"] < end
            ):
                raise EngineError(
                    "realized funding coverage must be complete across the selected research window"
                )
            if funding_manifest.get("transport") == "user_import" and not funding_manifest.get("provenance"):
                raise EngineError("imported historical funding requires declared provider provenance")
    else:
        marks, mark_quality, events, tiers = {}, None, [], []
        if funding_events or margin_tiers or mark_candles:
            raise EngineError("spot research does not accept derivative risk inputs")
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            return _simulate(
                trade_candles,
                interval_ms,
                instrument,
                config,
                selected,
                marks,
                events,
                tiers,
                quality,
                mark_quality,
                provenance,
                _shared_input,
                progress,
            )
    except DecimalException as exc:
        raise EngineError("Research accounting exceeds the supported numeric domain") from exc


def _simulate(
    candles,
    interval_ms,
    instrument,
    config,
    selected,
    marks,
    events,
    tiers,
    quality,
    mark_quality,
    provenance,
    shared_input,
    progress=None,
):
    is_perp = isinstance(instrument, LinearContract)
    first, last = selected[0], selected[-1]
    start, end = candles[first].ts, candles[last].ts + interval_ms
    fee_rate, slip, liq_fee = (
        config.fee_bps / BPS,
        config.slippage_bps / BPS,
        config.liquidation_fee_bps / BPS,
    )
    risk_close_fee = fee_rate + liq_fee
    cash, quantity, margin, entry, entry_fee, entry_notional = (
        config.initial_cash,
        ZERO,
        ZERO,
        ZERO,
        ZERO,
        ZERO,
    )
    entry_ts, entry_signal, position_funding = None, None, ZERO
    fees, funding_total, realized, shortfall_total, turnover = ZERO, ZERO, ZERO, ZERO, ZERO
    peak, maximum_dd = cash, ZERO
    signals, orders, fills, funding_rows, liquidations, round_trips, equity = [], [], [], [], [], [], []
    observations = [(start, cash)]
    pending = None
    exit_peak, holding_closes = None, 0
    state = _DecisionState(config)
    for candle in candles[:first]:
        state.on_bar(candle)
    # A cost-adjusted, unlevered underlying benchmark starts at the same first
    # executable open as the strategy, irrespective of its indicator warmup.
    benchmark_instrument = (
        Instrument(
            instrument.inst_id,
            instrument.base,
            instrument.quote,
            instrument.tick_size,
            contract_base_quantity(instrument.lot_size, instrument),
            contract_base_quantity(instrument.min_size, instrument),
        )
        if is_perp
        else instrument
    )
    benchmark_cash, benchmark_quantity = cash, ZERO
    event_groups: dict[int, list[FundingEvent]] = {}
    for event in events:
        if start <= event.ts < end:
            bar_ts = event.ts // interval_ms * interval_ms
            if event.mark_price is None and (event.ts != bar_ts or event.ts not in marks):
                raise EngineError(
                    "funding settlement needs its exact historical mark price; future bar closes are forbidden"
                )
            event_groups.setdefault(bar_ts, []).append(event)
    skipped = 0
    exposed_bars = 0

    def check_accounting():
        for name, value in (
            ("cash", cash),
            ("contracts" if is_perp else "base quantity", quantity),
            ("isolated margin", margin),
            ("entry notional", entry_notional),
            ("fees", fees),
            ("funding", funding_total),
            ("realized pnl", realized),
            ("turnover", turnover),
            ("insurance shortfall", shortfall_total),
        ):
            _accounting_value(value, name)
        if cash < ZERO or (not is_perp and quantity < ZERO):
            raise EngineError("research accounting invariant violated: cash or spot inventory below zero")

    def close_position(ts, reference, reason, signal_id, phase):
        nonlocal cash, quantity, margin, entry, entry_fee, entry_notional, fees, realized
        nonlocal position_funding, shortfall_total, turnover, entry_ts, entry_signal, exit_peak
        signed = quantity
        side = "sell" if signed > ZERO else "buy"
        price = _fill_price(reference, side, instrument, slip)
        base = contract_base_quantity(signed, instrument) if is_perp else signed
        notional = _accounting_value(base.copy_abs() * price, "closing notional")
        close_fee = _accounting_value(
            notional * (fee_rate + (liq_fee if reason == "liquidation" else ZERO)), "closing fee"
        )
        gross = _accounting_value(base * (price - entry), "gross realized pnl")
        release = margin + gross - close_fee if is_perp else notional - close_fee
        shortfall = max(-release, ZERO) if is_perp else ZERO
        cash += max(release, ZERO) if is_perp else release
        net = gross - entry_fee - close_fee - position_funding
        realized += net
        fees += close_fee
        turnover += notional
        shortfall_total += shortfall
        order_id = f"order-{len(orders) + 1}"
        orders.append(
            {
                "id": order_id,
                "ts": ts,
                "signal_id": signal_id,
                "side": side,
                "reduce_only": True,
                "quantity": _string(signed.copy_abs()),
                "status": "filled",
                "reason": reason,
            }
        )
        fills.append(
            {
                "id": f"fill-{len(fills) + 1}",
                "order_id": order_id,
                "signal_id": signal_id,
                "ts": ts,
                "phase": phase,
                "side": side,
                "quantity": _string(signed.copy_abs()),
                "signed_quantity": _string(-signed),
                "price": _string(price),
                "reference_price": _string(reference),
                "notional": _string(notional),
                "fee": _string(close_fee),
                "cash_credit": _string(max(release, ZERO) if is_perp else release),
                "isolated_margin_released": _string(margin),
                "gross_pnl": _string(gross),
                "cash": _string(cash),
                "reason": reason,
            }
        )
        round_trips.append(
            {
                "id": f"trip-{len(round_trips) + 1}",
                "entry_ts": entry_ts,
                "exit_ts": ts,
                "entry_signal_id": entry_signal,
                "exit_signal_id": signal_id,
                "direction": "long" if signed > ZERO else "short",
                "quantity": _string(signed.copy_abs()),
                "entry_price": _string(entry),
                "exit_price": _string(price),
                "gross_pnl": _string(gross),
                "fees": _string(entry_fee + close_fee),
                "funding_payment": _string(position_funding),
                "net_pnl": _string(net),
                "insurance_shortfall": _string(shortfall),
                "insurance_liability": _string(shortfall),
                "exit_reason": reason,
            }
        )
        if reason == "liquidation":
            liquidations.append(
                {
                    "ts": ts,
                    "phase": phase,
                    "contracts": _string(signed),
                    "trigger_mark": _string(reference),
                    "execution_price": _string(price),
                    "tier": select_margin_tier(signed, tiers).tier,
                    "insurance_shortfall": _string(shortfall),
                    "insurance_liability": _string(shortfall),
                    "model": "full_isolated_liquidation",
                }
            )
        quantity, margin, entry, entry_fee, entry_notional, position_funding = (
            ZERO,
            ZERO,
            ZERO,
            ZERO,
            ZERO,
            ZERO,
        )
        entry_ts, entry_signal, exit_peak = None, None, None
        check_accounting()

    def open_position(ts, reference, direction, signal_id):
        nonlocal \
            cash, \
            quantity, \
            margin, \
            entry, \
            entry_fee, \
            entry_notional, \
            fees, \
            turnover, \
            entry_ts, \
            entry_signal, \
            skipped
        nonlocal exit_peak
        exit_peak = None
        side = "buy" if direction > 0 else "sell"
        if shortfall_total > ZERO:
            orders.append(
                {
                    "id": f"order-{len(orders) + 1}",
                    "ts": ts,
                    "signal_id": signal_id,
                    "side": side,
                    "reduce_only": False,
                    "quantity": "0",
                    "status": "skipped",
                    "reason": "insurance_liability_halts_new_risk",
                }
            )
            skipped += 1
            return
        price = _fill_price(reference, side, instrument, slip)
        budget = _accounting_value(cash * config.strategy.allocation, "position budget")
        loss_notional = risk_notional(cash, config.strategy, config.fee_bps, config.slippage_bps)
        if loss_notional is not None:
            budget = min(budget, loss_notional * (ONE / config.leverage + fee_rate))
        if is_perp:
            unit_notional = contract_base_quantity(ONE, instrument) * price
            cost_per_contract = unit_notional * (ONE / config.leverage + fee_rate)
            fill_quantity = _floor_lot(budget / cost_per_contract, instrument.lot_size)
            while fill_quantity > ZERO and fill_quantity * cost_per_contract > budget:
                corrected = fill_quantity - instrument.lot_size
                if corrected >= fill_quantity:
                    raise EngineError("accounting precision cannot correct one contract lot")
                fill_quantity = corrected
            if fill_quantity < instrument.min_size:
                fill_quantity = ZERO
        else:
            fill_quantity = _buy_quantity(budget, price, fee_rate, instrument)
        order_id = f"order-{len(orders) + 1}"
        order = {
            "id": order_id,
            "ts": ts,
            "signal_id": signal_id,
            "side": side,
            "reduce_only": False,
            "quantity": _string(fill_quantity),
            "status": "skipped" if fill_quantity == ZERO else "filled",
            "reason": "below_minimum_or_zero_allocation" if fill_quantity == ZERO else "regime_entry",
        }
        orders.append(order)
        if fill_quantity == ZERO:
            skipped += 1
            return
        _accounting_value(fill_quantity, "entry quantity")
        signed = fill_quantity * direction
        notional = contract_notional(signed, price, instrument) if is_perp else fill_quantity * price
        notional = _accounting_value(notional, "entry notional")
        trading_fee = _accounting_value(notional * fee_rate, "entry fee")
        collateral = notional / config.leverage if is_perp else ZERO
        if is_perp:
            tier = select_margin_tier(signed, tiers)
            if config.leverage > tier.max_leverage or ONE / config.leverage < tier.imr:
                raise EngineError(
                    "requested leverage exceeds the selected maintenance tier initial-margin limit"
                )
        debit = collateral + trading_fee if is_perp else notional + trading_fee
        if debit > cash:
            raise EngineError("entry would overdraw available cash")
        cash -= debit
        quantity, margin, entry, entry_fee, entry_notional = signed, collateral, price, trading_fee, notional
        fees += trading_fee
        turnover += notional
        entry_ts, entry_signal = ts, signal_id
        fills.append(
            {
                "id": f"fill-{len(fills) + 1}",
                "order_id": order_id,
                "signal_id": signal_id,
                "ts": ts,
                "phase": "next_open",
                "side": side,
                "quantity": _string(fill_quantity),
                "signed_quantity": _string(signed),
                "price": _string(price),
                "reference_price": _string(reference),
                "notional": _string(notional),
                "fee": _string(trading_fee),
                "cash_debit": _string(debit),
                "isolated_margin_posted": _string(collateral),
                "cash": _string(cash),
                "reason": "regime_entry",
            }
        )
        check_accounting()

    def apply_funding(event, phase):
        nonlocal margin, funding_total, position_funding
        mark = event.mark_price if event.mark_price is not None else marks[event.ts].open
        payment = funding_payment(quantity, mark, event.rate, instrument) if quantity != ZERO else ZERO
        if quantity != ZERO:
            margin -= payment
            funding_total += payment
            position_funding += payment
        funding_rows.append(
            {
                "ts": event.ts,
                "inst_id": event.inst_id or instrument.inst_id,
                "rate": _string(event.rate),
                "mark_price": _string(mark),
                "contracts": _string(quantity),
                "payment": _string(payment),
                "phase": phase,
                "formula_type": event.formula_type,
                "method": event.method,
                "mark_price_source": event.mark_price_source
                or (
                    "supplied_historical_mark"
                    if event.mark_price is not None
                    else "historical_mark_bar_open_approximation"
                ),
                "mark_ts": event.mark_ts if event.mark_ts is not None else event.ts,
            }
        )
        check_accounting()
        if quantity != ZERO and liquidation_condition(
            margin, quantity, entry, mark, instrument, select_margin_tier(quantity, tiers), risk_close_fee
        ):
            close_position(event.ts, mark, "liquidation", None, "funding_settlement")
            return True
        return False

    for index in selected:
        if progress and (index - first) % 128 == 0:
            progress((index - first) / len(selected))
        candle = candles[index]
        mark = marks[candle.ts] if is_perp else candle
        liquidated = False
        if is_perp:
            for event in event_groups.get(candle.ts, []):
                if event.ts == candle.ts:
                    liquidated = apply_funding(event, "boundary_before_orders") or liquidated
            if quantity != ZERO and liquidation_condition(
                margin,
                quantity,
                entry,
                mark.open,
                instrument,
                select_margin_tier(quantity, tiers),
                risk_close_fee,
            ):
                close_position(candle.ts, mark.open, "liquidation", None, "mark_open_gap")
                liquidated = True
        if pending is not None and not liquidated:
            target, signal_id, exit_reason = pending
            current = 1 if quantity > ZERO else -1 if quantity < ZERO else 0
            if current != target:
                if quantity != ZERO:
                    close_position(candle.ts, candle.open, exit_reason, signal_id, "next_open")
                if target != 0:
                    open_position(candle.ts, candle.open, target, signal_id)
        pending = None
        if index == first + 1:
            price = _fill_price(candle.open, "buy", benchmark_instrument, slip)
            benchmark_quantity = _buy_quantity(benchmark_cash, price, fee_rate, benchmark_instrument)
            benchmark_cash -= _accounting_value(
                benchmark_quantity * price * (ONE + fee_rate), "benchmark cost"
            )
        if is_perp:
            if quantity != ZERO and liquidation_condition(
                margin,
                quantity,
                entry,
                mark.open,
                instrument,
                select_margin_tier(quantity, tiers),
                risk_close_fee,
            ):
                close_position(candle.ts, mark.open, "liquidation", None, "post_fill_mark_open")
                liquidated = True
            # Exact settlement timestamps/rates are honored. OHLC gives no
            # timestamps for extrema: this scenario places intrabar funding
            # before the adverse excursion, rather than inventing tick history.
            for event in event_groups.get(candle.ts, []):
                if event.ts > candle.ts:
                    liquidated = apply_funding(event, "intrabar_before_adverse_mark") or liquidated
            if quantity != ZERO:
                tier = select_margin_tier(quantity, tiers)
                if liquidation_condition(
                    margin, quantity, entry, mark.open, instrument, tier, risk_close_fee
                ):
                    close_position(candle.ts, mark.open, "liquidation", None, "post_fill_mark_open")
                    liquidated = True
                else:
                    adverse = mark.low if quantity > ZERO else mark.high
                    if liquidation_condition(
                        margin, quantity, entry, adverse, instrument, tier, risk_close_fee
                    ):
                        trigger = liquidation_price(margin, quantity, entry, instrument, tier, risk_close_fee)
                        close_position(
                            candle.ts + interval_ms, trigger, "liquidation", None, "adverse_mark_excursion"
                        )
                        liquidated = True
        unrealized = (
            (
                linear_pnl(quantity, entry, mark.close, instrument)
                if is_perp
                else quantity * (candle.close - entry)
            )
            if quantity != ZERO
            else ZERO
        )
        position_value = margin + unrealized if is_perp else quantity * candle.close
        value = _accounting_value(cash + position_value - shortfall_total, "equity")
        benchmark_value = _accounting_value(
            benchmark_cash + benchmark_quantity * candle.close, "benchmark equity"
        )
        peak = max(peak, value)
        dd = (ONE - value / peak) * Decimal("100")
        maximum_dd = max(maximum_dd, dd)
        close_ts = candle.ts + interval_ms
        if quantity != ZERO:
            exposed_bars += 1
        notional = contract_notional(quantity, mark.close, instrument) if is_perp else quantity * candle.close
        equity.append(
            {
                "ts": close_ts,
                "equity": _string(value),
                "benchmark": _string(benchmark_value),
                "drawdown_pct": _float(dd),
                "cash": _string(cash),
                "quantity": _string(quantity),
                "isolated_margin": _string(margin),
                "insurance_liability": _string(shortfall_total),
                "unrealized_pnl": _string(unrealized),
                "gross_notional": _string(notional),
                "mark_price": _string(mark.close),
            }
        )
        observations.append((close_ts, value))
        target, indicators = state.on_bar(candle)
        if quantity:
            holding_closes = (close_ts - entry_ts) // interval_ms
            reason, exit_peak = exit_on_close(
                config.strategy, quantity, entry, candle.close, exit_peak, holding_closes
            )
            if reason:
                target = 0
                indicators = indicators | {"exit_reason": reason, "exit_reference": _string(candle.close)}
        else:
            reason, exit_peak, holding_closes = None, None, 0
        signal_id = f"signal-{len(signals) + 1}"
        signals.append(
            {
                "id": signal_id,
                "ts": close_ts,
                "bar_ts": candle.ts,
                "target": target,
                "indicators": indicators,
                "available_after_close": True,
                "status": "last_bar_unfilled" if index == last and target is not None else "observed",
            }
        )
        if target is not None:
            pending = (target, signal_id, reason or "signal_exit")
        check_accounting()
    metrics = _metrics(
        observations,
        start,
        end,
        interval_ms,
        config.initial_cash,
        equity,
        maximum_dd,
        fills,
        round_trips,
        fees,
        funding_total,
        realized,
        shortfall_total,
        turnover,
        exposed_bars,
    )
    tier_meta = provenance.get("tiers", {})
    funding_meta = provenance.get("funding", {})
    coverage = funding_meta.get("quality", funding_meta) if isinstance(funding_meta, Mapping) else {}
    assumptions = {
        "execution": "confirmed trade-close signals; next trade-bar open; adverse slippage then tick rounding",
        "fill_model": "market-order bar approximation; no order book, queue position, partial fills or liquidity capacity model",
        "inventory": "single isolated net perpetual position"
        if is_perp
        else "unlevered long-only spot inventory",
        "quantity_unit": "contracts" if is_perp else instrument.base,
        "rebalance": "regime transitions only; no repeated target-weight rebalance",
        "warmup_bars": first,
        "window": "complete bars; start inclusive; end exclusive; flat/cash start; no forced final liquidation",
        "funding": "actual supplied settlement times and realized rates; boundary debits precede strategy fills; no fixed interval",
        "funding_coverage": "importer_declared_coverage"
        if isinstance(funding_meta, Mapping) and funding_meta.get("transport") == "user_import"
        else "source_manifest_complete"
        if coverage.get("complete")
        else "unverified_supplied_schedule",
        "mark_risk": "boundary funding, mark-open gap, strategy fills, intrabar funding, adverse mark OHLC excursion, close valuation",
        "intrabar_ambiguity": "all intrabar funding precedes adverse mark excursion; extrema have no observed timestamp; sensitivity scenario, not recovered venue path",
        "liquidation": "full isolated position at continuous mark threshold (or gap mark), then adverse execution costs; no partial tier reduction; shortfall is an insurance liability deducted from net equity; free cash remains separate and any liability halts new risk",
        "tier_boundaries": "engine convention min exclusive, max inclusive; venue equality not inferred",
        "tier_history": tier_meta.get("history_policy", "supplied_snapshot_scenario")
        if isinstance(tier_meta, Mapping)
        else "supplied_snapshot_scenario",
        "benchmark": "cost-adjusted unlevered trade-price buy-and-hold proxy at second selected trade-bar open; for SWAP this is a hypothetical base-price benchmark, not independently observed spot returns; no perpetual funding",
        "risk_free_rate": 0,
        "drawdown_observation": "selected bar-close net equity with initial-cash baseline; intrabar unrealized peaks/troughs are not reconstructed",
        "liquidation_fee_policy": "liquidation_fee_bps is an additional stress/clearance allowance; maintenance threshold reserves trading fee plus this allowance; liquidation fills charge both",
        "daily_sample_policy": "30 complete UTC daily returns for Sharpe/Sortino/annualized volatility; 365 complete days for CAGR/Calmar",
        "funding_mark": "historical mark-bar open at settlement timestamp is a bar approximation; supplied marks retain source labels; no claim of exact venue account settlement",
        "ledger_replay": "replay cash_debit/cash_credit in recorded fill order under the fixed 50-significant-digit context; regrouping rounded PnL algebra can differ at the final decimal place",
        "accounting": {
            "precision": 50,
            "rounding": "ROUND_HALF_EVEN",
            "exclusive_absolute_limit": "1e30",
            "finite_precision": True,
        },
        "skipped_entries": skipped,
    }
    snapshot = (
        {**shared_input[1], "config": json_safe(config)}
        if shared_input
        else {
            "trade_candles": json_safe(candles),
            "mark_candles": json_safe(list(marks.values())) if is_perp else None,
            "funding_events": json_safe(events),
            "margin_tiers": json_safe(tiers),
            "instrument": json_safe(instrument),
            "instrument_type": "SWAP" if is_perp else "SPOT",
            "interval_ms": interval_ms,
            "config": json_safe(config),
            "provenance": json_safe(provenance),
        }
    )
    reference = (
        {"shared_input_hash": shared_input[0], "config": json_safe(config)} if shared_input else snapshot
    )
    return {
        "engine_version": ENGINE_VERSION,
        "metrics": metrics,
        "equity": equity,
        "signals": signals,
        "orders": orders,
        "fills": fills,
        **({} if shared_input else {"trades": fills}),
        "funding": funding_rows,
        "liquidations": liquidations,
        "round_trips": round_trips,
        "quality": {"trade": quality, "mark": mark_quality},
        "assumptions": assumptions,
        "provenance": json_safe(provenance),
        "input_hash": _hash(snapshot),
        "input_snapshot": reference,
    }


def _metrics(
    observations,
    start,
    end,
    interval_ms,
    initial,
    equity,
    maximum_dd,
    fills,
    trips,
    fees,
    funding,
    realized,
    shortfall,
    turnover,
    exposed_bars,
):
    at = dict(observations)
    daily = []
    day = ((start + DAY_MS - 1) // DAY_MS) * DAY_MS
    if interval_ms <= DAY_MS and DAY_MS % interval_ms == 0:
        while day + DAY_MS <= end:
            opening, closing = at.get(day), at.get(day + DAY_MS)
            if opening is not None and closing is not None and opening > ZERO:
                daily.append(_float(closing / opening - ONE))
            day += DAY_MS
    reasons = {}
    sharpe = sortino = volatility = cagr = calmar = None
    count = len(daily)
    if any(value <= ZERO for _, value in observations):
        reasons.update(
            sharpe="nonpositive_equity",
            sortino="nonpositive_equity",
            annualized_volatility="nonpositive_equity",
        )
    elif count < 30:
        reasons.update(
            sharpe="insufficient_complete_utc_days",
            sortino="insufficient_complete_utc_days",
            annualized_volatility="insufficient_complete_utc_days",
        )
    else:
        deviation = stdev(daily)
        volatility = deviation * sqrt(365) * 100
        if deviation > 1e-12:
            sharpe = mean(daily) / deviation * sqrt(365)
        else:
            reasons["sharpe"] = "zero_variance"
        downside = sqrt(sum(min(item, 0.0) ** 2 for item in daily) / count)
        if downside > 1e-12:
            sortino = mean(daily) / downside * sqrt(365)
        else:
            reasons["sortino"] = "no_downside_variation"
    final = Decimal(equity[-1]["equity"])
    total_return = _float((final / initial - ONE) * 100)
    if count >= 365 and final > ZERO:
        cagr = (_float(final / initial) ** (365 * DAY_MS / (end - start)) - 1) * 100
        if maximum_dd > ZERO:
            calmar = cagr / _float(maximum_dd)
        else:
            reasons["calmar"] = "zero_drawdown"
    else:
        reasons.update(
            cagr="insufficient_complete_utc_days_or_zero_equity",
            calmar="insufficient_complete_utc_days_or_zero_equity",
        )
    pnl = [Decimal(trip["net_pnl"]) for trip in trips]
    gains, losses = (
        sum((item for item in pnl if item > ZERO), ZERO),
        -sum((item for item in pnl if item < ZERO), ZERO),
    )
    profit_factor = _float(gains / losses) if losses > ZERO else None
    if losses == ZERO:
        reasons["profit_factor"] = "no_completed_losing_round_trips"
    wins = sum(item > ZERO for item in pnl)
    if not trips:
        reasons["win_rate"] = "no_completed_round_trips"
    benchmark = Decimal(equity[-1]["benchmark"])
    return {
        "initial_cash": _string(initial),
        "final_equity": _string(final),
        "total_return_pct": total_return,
        "benchmark_return_pct": _float((benchmark / initial - ONE) * 100),
        "max_drawdown_pct": _float(maximum_dd),
        "sharpe": sharpe,
        "sharpe_reason": reasons.get("sharpe"),
        "sortino": sortino,
        "annualized_volatility_pct": volatility,
        "cagr_pct": cagr,
        "calmar": calmar,
        "turnover": _float(turnover / initial),
        "turnover_definition": "sum absolute executed notional / initial equity",
        "profit_factor": profit_factor,
        "win_rate_pct": wins / len(trips) * 100 if trips else None,
        "exposure_pct": exposed_bars / len(equity) * 100,
        "exposure_definition": "fraction of selected bar closes with nonzero inventory",
        "trades": len(fills),
        "round_trips": len(trips),
        "wins": wins,
        "fees_paid": _string(fees),
        "funding_paid": _string(funding),
        "realized_pnl": _string(realized),
        "insurance_shortfall": _string(shortfall),
        "insurance_liability": _string(shortfall),
        "complete_utc_days": count,
        "liquidations": sum(trip["exit_reason"] == "liquidation" for trip in trips),
        "metric_reasons": reasons,
        "round_trip_sample_warning": "fewer_than_30_completed_round_trips" if len(trips) < 30 else None,
    }


def _plan_options(options: ResearchPlanConfig | Mapping[str, Any] | None) -> ResearchPlanConfig:
    if options is None:
        plan = ResearchPlanConfig()
    elif isinstance(options, ResearchPlanConfig):
        plan = options
    elif isinstance(options, Mapping):
        unknown = set(options) - set(ResearchPlanConfig.__dataclass_fields__)
        if unknown:
            raise EngineError(f"unsupported research options: {', '.join(sorted(unknown))}")
        plan = ResearchPlanConfig(**options)
    else:
        raise EngineError("research options must be a mapping or ResearchPlanConfig")
    _integer(plan.max_workers, "max_workers")
    if plan.max_workers > 4:
        raise EngineError("research workers are limited to four")
    _integer(plan.purge_bars, "purge_bars", 0)
    for name in ("train_bars", "test_bars", "step_bars"):
        if getattr(plan, name) is not None:
            _integer(getattr(plan, name), name, 2 if name != "step_bars" else 1)
    if (
        isinstance(plan.train_fraction, bool)
        or not isinstance(plan.train_fraction, (int, float))
        or not 0 < plan.train_fraction < 1
    ):
        raise EngineError("train_fraction must be between zero and one")
    if not isinstance(plan.grid, Mapping):
        raise EngineError("grid must be a parameter mapping")
    return plan


def _strategy_candidates(strategy: StrategyConfig, grid: Mapping[str, Sequence[Any]]) -> list[StrategyConfig]:
    allowed = {
        "kind",
        "fast",
        "slow",
        "rsi_period",
        "entry",
        "exit",
        "allocation",
        "window",
        "z_entry",
        "z_exit",
    }
    if set(grid) - allowed:
        raise EngineError("grid contains unsupported strategy parameters")
    keys, dimensions = [], []
    count = 1
    for key in sorted(grid):
        values = grid[key]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
            raise EngineError("each grid dimension must contain a nonempty sequence")
        if len(values) > MAX_CASES:
            raise EngineError(f"parameter grid exceeds the {MAX_CASES}-case limit")
        converted = []
        for item in values:
            if key in {"entry", "exit", "allocation", "z_entry", "z_exit"}:
                try:
                    item = item if isinstance(item, Decimal) else Decimal(str(item))
                except (ValueError, DecimalException) as exc:
                    raise EngineError("grid decimal value is invalid") from exc
            if item in converted:
                raise EngineError("grid dimensions must not contain duplicate values")
            converted.append(item)
        keys.append(key)
        dimensions.append(converted)
        count *= len(converted)
        if count > MAX_CASES:
            raise EngineError(f"parameter grid exceeds the {MAX_CASES}-case limit")
    candidates = (
        [replace(strategy, **dict(zip(keys, values, strict=True))) for values in product(*dimensions)]
        if keys
        else [strategy]
    )
    for candidate in candidates:
        _validate_strategy(candidate)
    return candidates


def _bounded_work(cases: int, bar_count: int) -> None:
    if cases > (MAX_CASES + 1) * MAX_FOLDS or cases * bar_count > MAX_BAR_WORK:
        raise EngineError(f"research exceeds the {MAX_BAR_WORK} bar-case work budget")


def _rank(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "id": item["id"],
                "parameters": item["parameters"],
                "total_return_pct": item["result"]["metrics"]["total_return_pct"],
                "max_drawdown_pct": item["result"]["metrics"]["max_drawdown_pct"],
                "sharpe": item["result"]["metrics"]["sharpe"],
                "round_trips": item["result"]["metrics"]["round_trips"],
                "input_hash": item["result"]["input_hash"],
            }
            for item in experiments
        ),
        key=lambda row: (-row["total_return_pct"], row["max_drawdown_pct"], row["id"]),
    )


def run_research_plan(
    trade_candles: list[Candle],
    interval_ms: int,
    instrument: Instrument | LinearContract,
    config: ResearchConfig,
    *,
    mode: str = "single",
    options: ResearchPlanConfig | Mapping[str, Any] | None = None,
    mark_candles: list[Candle] | None = None,
    funding_events: Sequence[FundingEvent | Mapping[str, Any]] | None = None,
    margin_tiers: Sequence[MarginTier | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    progress=None,
) -> dict[str, Any]:
    """Run deterministic, resource-bounded experiments with explicit OOS folds.

    Grid ranking is descriptive in-sample research. Train/test and walk-forward
    select candidates on training returns only, reset each test account, and
    report independent test results without inventing a continuous equity path.
    """
    if mode not in {"single", "train_test", "walk_forward", "grid", "cost_stress"}:
        raise EngineError("unsupported research mode")
    from .strategy_program import strategy_complexity

    plan = _plan_options(options)
    if len(trade_candles) > MAX_CANDLES:
        raise EngineError(f"research accepts at most {MAX_CANDLES} bars per dataset")
    _validate_config(config, instrument)
    validate_candles(trade_candles, interval_ms)
    selected = _selection(trade_candles, interval_ms, config)
    candidates = _strategy_candidates(config.strategy, plan.grid)
    dependencies = {
        "mark_candles": mark_candles,
        "funding_events": funding_events,
        "margin_tiers": margin_tiers,
        "provenance": provenance,
    }

    shared = None
    shared_inputs = {}
    if mode != "single":
        is_perp = isinstance(instrument, LinearContract)
        common = {
            "trade_candles": json_safe(trade_candles),
            "mark_candles": json_safe(mark_candles) if is_perp else None,
            "funding_events": json_safe(_funding_records(funding_events)) if is_perp else [],
            "margin_tiers": json_safe(_tier_records(margin_tiers)) if is_perp else [],
            "instrument": json_safe(instrument),
            "instrument_type": "SWAP" if is_perp else "SPOT",
            "interval_ms": interval_ms,
            "provenance": json_safe(dict(provenance or {})),
        }
        identity = _hash(common)
        shared = (identity, common)
        shared_inputs = {identity: common}

    expected_calls = len(candidates) if mode == "grid" else 1
    if mode == "cost_stress":
        expected_calls = max(1, len(plan.fee_bps)) * max(1, len(plan.slippage_bps))
    if mode in {"train_test", "walk_forward"}:
        size = len(selected)
        train = plan.train_bars or int(size * plan.train_fraction)
        test = plan.test_bars or (
            size - train - plan.purge_bars
            if mode == "train_test"
            else max(2, int(size * (1 - plan.train_fraction)))
        )
        step = plan.step_bars or max(1, test)
        folds_count = (
            1 if mode == "train_test" else max(1, 1 + (size - train - plan.purge_bars - test) // step)
        )
        expected_calls = folds_count * (len(candidates) + 1)
    fractions, progress_lock = [], Lock()

    def run(candidate_config):
        with progress_lock:
            position = len(fractions)
            fractions.append(0)

        def report(fraction):
            with progress_lock:
                fractions[position] = fraction
                if progress:
                    progress(min(1, sum(fractions) / expected_calls))

        result = run_professional_backtest(
            trade_candles,
            interval_ms,
            instrument,
            candidate_config,
            _shared_input=shared,
            progress=report if progress else None,
            **dependencies,
        )
        report(1)
        return result

    def experiments(configurations):
        with ThreadPoolExecutor(max_workers=plan.max_workers, thread_name_prefix="research") as pool:
            results = list(pool.map(run, configurations))
        return [
            {"id": f"experiment-{index + 1}", "parameters": json_safe(candidate), "result": result}
            for index, (candidate, result) in enumerate(zip(configurations, results, strict=True))
        ]

    if mode == "single":
        if plan.grid or plan.fee_bps or plan.slippage_bps:
            raise EngineError("single mode does not consume grid or cost dimensions")
        _bounded_work(strategy_complexity(config.strategy), len(trade_candles))
        result = run(config)
        return {
            "mode": mode,
            "engine_version": ENGINE_VERSION,
            "shared_inputs": shared_inputs,
            "result": result,
            "comparison": [{"id": "experiment-1", **result["metrics"]}],
            "plan_hash": _hash({"input_hash": result["input_hash"], "mode": mode, "options": plan}),
        }
    if mode == "grid":
        if plan.fee_bps or plan.slippage_bps:
            raise EngineError("grid mode does not consume cost dimensions")
        _bounded_work(max(strategy_complexity(c) for c in candidates) * len(candidates), len(trade_candles))
        runs = experiments([replace(config, strategy=candidate) for candidate in candidates])
        comparison = _rank(runs)
        return {
            "mode": mode,
            "engine_version": ENGINE_VERSION,
            "shared_inputs": shared_inputs,
            "selection_scope": "in_sample_descriptive_only",
            "selection_metric": "total_return_pct",
            "comparison": comparison,
            "experiments": runs,
            "best_experiment_id": comparison[0]["id"],
            "work_budget": len(candidates) * len(trade_candles),
            "plan_hash": _hash(
                {"hashes": [item["result"]["input_hash"] for item in runs], "mode": mode, "options": plan}
            ),
        }
    if mode == "cost_stress":
        if plan.grid:
            raise EngineError("cost_stress evaluates a fixed strategy; grid is not accepted")
        try:
            fee_values = [
                value if isinstance(value, Decimal) else Decimal(str(value))
                for value in (plan.fee_bps or (config.fee_bps,))
            ]
            slip_values = [
                value if isinstance(value, Decimal) else Decimal(str(value))
                for value in (plan.slippage_bps or (config.slippage_bps,))
            ]
        except (ValueError, DecimalException) as exc:
            raise EngineError("cost stress values must be valid decimals") from exc
        if len(fee_values) * len(slip_values) > 25:
            raise EngineError("cost stress is limited to 25 cells")
        if len(set(fee_values)) != len(fee_values) or len(set(slip_values)) != len(slip_values):
            raise EngineError("cost stress dimensions must not contain duplicates")
        configurations = [
            replace(config, fee_bps=fee, slippage_bps=slip) for fee, slip in product(fee_values, slip_values)
        ]
        for candidate in configurations:
            _validate_config(candidate, instrument)
        _bounded_work(sum(strategy_complexity(c.strategy) for c in configurations), len(trade_candles))
        runs = experiments(configurations)
        return {
            "mode": mode,
            "engine_version": ENGINE_VERSION,
            "shared_inputs": shared_inputs,
            "selection_scope": "fixed_strategy_cost_sensitivity",
            "comparison": _rank(runs),
            "experiments": runs,
            "matrix": [
                {
                    "fee_bps": item["parameters"]["fee_bps"],
                    "slippage_bps": item["parameters"]["slippage_bps"],
                    "id": item["id"],
                    "metrics": item["result"]["metrics"],
                }
                for item in runs
            ],
            "work_budget": len(runs) * len(trade_candles),
            "plan_hash": _hash(
                {"hashes": [item["result"]["input_hash"] for item in runs], "mode": mode, "options": plan}
            ),
        }
    if plan.fee_bps or plan.slippage_bps:
        raise EngineError("train/test modes do not select costs as alpha parameters")
    size = len(selected)
    train_bars = plan.train_bars or int(size * plan.train_fraction)
    if train_bars < 2:
        raise EngineError("training window must contain at least two bars")
    test_bars = plan.test_bars or (
        size - train_bars - plan.purge_bars
        if mode == "train_test"
        else max(2, int(size * (1 - plan.train_fraction)))
    )
    if test_bars < 2:
        raise EngineError("test window must contain at least two bars after purging")
    step_bars = plan.step_bars or test_bars
    if mode == "walk_forward" and step_bars < test_bars:
        raise EngineError("walk-forward test windows must not overlap: step_bars >= test_bars")
    folds = []
    offset = 0
    while offset + train_bars + plan.purge_bars + test_bars <= size:
        folds.append(
            (
                offset,
                offset + train_bars,
                offset + train_bars + plan.purge_bars,
                offset + train_bars + plan.purge_bars + test_bars,
            )
        )
        if mode == "train_test":
            break
        offset += step_bars
        if len(folds) > MAX_FOLDS:
            raise EngineError(f"walk-forward is limited to {MAX_FOLDS} folds")
    if not folds:
        raise EngineError("requested training, purge and test windows do not fit the selected date range")
    _bounded_work(
        len(folds) * (len(candidates) + 1),
        len(trade_candles) * max(strategy_complexity(c) for c in candidates),
    )
    results = []
    for number, (train_start, train_end, test_start, test_end) in enumerate(folds, 1):
        train_config = replace(
            config,
            start_ts=trade_candles[selected[train_start]].ts,
            end_ts=trade_candles[selected[train_end - 1]].ts + interval_ms,
        )
        train_runs = experiments([replace(train_config, strategy=candidate) for candidate in candidates])
        ranking = _rank(train_runs)
        winner_id = ranking[0]["id"]
        winner_index = int(winner_id.rsplit("-", 1)[1]) - 1
        chosen = candidates[winner_index]
        test_config = replace(
            config,
            strategy=chosen,
            start_ts=trade_candles[selected[test_start]].ts,
            end_ts=trade_candles[selected[test_end - 1]].ts + interval_ms,
        )
        test_result = run(test_config)
        results.append(
            {
                "id": f"fold-{number}",
                "train_start_ts": train_config.start_ts,
                "train_end_ts": train_config.end_ts,
                "test_start_ts": test_config.start_ts,
                "test_end_ts": test_config.end_ts,
                "purge_bars": plan.purge_bars,
                "selected_strategy": json_safe(chosen),
                "selection_metric": "training_total_return_pct",
                "training_comparison": ranking,
                "training_experiments": train_runs,
                "test_result": test_result,
            }
        )
    test_returns = [item["test_result"]["metrics"]["total_return_pct"] for item in results]
    test_drawdowns = [item["test_result"]["metrics"]["max_drawdown_pct"] for item in results]
    return {
        "mode": mode,
        "engine_version": ENGINE_VERSION,
        "shared_inputs": shared_inputs,
        "folds": results,
        "selection_scope": "training_only; independent flat-start out-of-sample test accounts",
        "oos_summary": {
            "folds": len(results),
            "median_return_pct": median(test_returns),
            "mean_return_pct": mean(test_returns),
            "worst_return_pct": min(test_returns),
            "worst_max_drawdown_pct": max(test_drawdowns),
            "aggregation": "independent fold statistics, not a compounded portfolio",
        },
        "unused_tail_bars": size - folds[-1][-1],
        "work_budget": len(folds) * (len(candidates) + 1) * len(trade_candles),
        "plan_hash": _hash(
            {
                "hashes": [item["test_result"]["input_hash"] for item in results],
                "mode": mode,
                "options": plan,
                "selection": [item["selected_strategy"] for item in results],
            }
        ),
    }


def replay_research_snapshot(
    snapshot: Mapping[str, Any], shared_inputs: Mapping[str, Any] | None = None, *, _plan=None, progress=None
) -> dict[str, Any]:
    """Reconstruct domain inputs from the JSON-safe saved input snapshot."""

    shared = None
    if snapshot.get("shared_input_hash"):
        reference = snapshot["shared_input_hash"]
        common = (shared_inputs or {}).get(reference)
        if common is None or _hash(common) != reference:
            raise EngineError("Shared research input reference is missing or corrupt")
        shared = (reference, common)
        snapshot = {**common, "config": snapshot["config"]}

    def candle(record):
        return Candle(
            record["ts"],
            *(Decimal(record[name]) for name in ("open", "high", "low", "close", "volume")),
            record.get("confirmed", True),
        )

    instrument_record = dict(snapshot["instrument"])
    for key in ("tick_size", "lot_size", "min_size", "ct_val", "ct_mult"):
        if key in instrument_record:
            instrument_record[key] = Decimal(instrument_record[key])
    instrument = (
        LinearContract(**instrument_record)
        if snapshot["instrument_type"] == "SWAP"
        else Instrument(**instrument_record)
    )
    config_record = dict(snapshot["config"])
    strategy_record = dict(config_record.pop("strategy"))
    for key in (
        "entry",
        "exit",
        "allocation",
        "z_entry",
        "z_exit",
        "stop_loss_pct",
        "take_profit_pct",
        "trailing_stop_pct",
        "risk_per_trade_pct",
    ):
        if key not in strategy_record:
            continue
        strategy_record[key] = Decimal(strategy_record[key])
    for key in ("initial_cash", "leverage", "fee_bps", "slippage_bps", "liquidation_fee_bps"):
        config_record[key] = Decimal(config_record[key])
    config = ResearchConfig(strategy=StrategyConfig(**strategy_record), **config_record)
    funding = [
        FundingEvent.from_record(
            dict(item)
            | {
                "rate": Decimal(item["rate"]),
                "mark_price": Decimal(item["mark_price"]) if item["mark_price"] is not None else None,
            }
        )
        for item in snapshot["funding_events"]
    ]
    tiers = [
        MarginTier(
            item["tier"],
            *(Decimal(item[key]) for key in ("min_contracts", "max_contracts", "imr", "mmr", "max_leverage")),
        )
        for item in snapshot["margin_tiers"]
    ]
    function = run_professional_backtest if _plan is None else run_research_plan
    options = (
        {"_shared_input": shared}
        if _plan is None
        else {"mode": _plan["mode"], "options": _plan["options"], "progress": progress}
    )
    return function(
        [candle(item) for item in snapshot["trade_candles"]],
        snapshot["interval_ms"],
        instrument,
        config,
        mark_candles=[candle(item) for item in snapshot["mark_candles"]]
        if snapshot["mark_candles"] is not None
        else None,
        funding_events=funding if isinstance(instrument, LinearContract) else None,
        margin_tiers=tiers if isinstance(instrument, LinearContract) else None,
        provenance=snapshot.get("provenance"),
        **options,
    )
