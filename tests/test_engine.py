"""Small causal examples and accounting properties, independent of network I/O."""

import json
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, Context, Decimal, Inexact, Rounded, localcontext

import pytest
import tidebench.engine as engine_module
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tidebench.engine import (
    DAY_MS,
    BacktestConfig,
    Candle,
    EngineError,
    Instrument,
    StrategyConfig,
    run_backtest,
    target_position,
    validate_candles,
)

D = Decimal
HOUR = 3_600_000
INSTRUMENT = Instrument("BTC-USDT", "BTC", "USDT", D("0.01"), D("0.001"), D("0.001"))


def bars(closes, opens=None, interval=HOUR, start=0):
    opening = closes if opens is None else opens
    return [
        Candle(
            start + i * interval,
            D(str(o)),
            max(D(str(o)), D(str(c))),
            min(D(str(o)), D(str(c))),
            D(str(c)),
            D("1000"),
        )
        for i, (o, c) in enumerate(zip(opening, closes, strict=True))
    ]


def config(kind="buy_hold", **kwargs):
    return BacktestConfig(fee_bps=D("0"), slippage_bps=D("0"), strategy=StrategyConfig(kind=kind, **kwargs))


def test_close_signal_executes_at_following_open():
    candles = bars([10, 10, 20, 30], [10, 10, 15, 22])
    result = run_backtest(candles, HOUR, INSTRUMENT, config("sma_cross", fast=1, slow=2))
    assert len(result["trades"]) == 1
    fill = result["trades"][0]
    assert fill["ts"] == candles[3].ts
    assert D(fill["price"]) == D("22")
    assert result["equity"][2]["equity"] == "10000"


def test_final_bar_signal_is_not_filled_and_inventory_is_not_force_sold():
    result = run_backtest(bars([10, 10, 20]), HOUR, INSTRUMENT, config("sma_cross", fast=1, slow=2))
    assert result["trades"] == []
    assert result["assumptions"]["final_signal_unfilled"] is True
    held = run_backtest(bars([10, 12, 15]), HOUR, INSTRUMENT, config(allocation=D("1")))
    assert [trade["side"] for trade in held["trades"]] == ["buy"]
    assert D(held["metrics"]["final_equity"]) > D("10000")


def test_future_mutations_and_prefix_runs_preserve_existing_history():
    candles = bars([10, 9, 12, 11, 15, 8, 14, 7])
    cfg = config("sma_cross", fast=2, slow=3)
    original = run_backtest(candles, HOUR, INSTRUMENT, cfg)
    changed = candles[:5] + bars([900, 1, 700], start=5 * HOUR)
    perturbed = run_backtest(changed, HOUR, INSTRUMENT, cfg)
    prefix = run_backtest(candles[:5], HOUR, INSTRUMENT, cfg)
    assert original["equity"][:5] == prefix["equity"] == perturbed["equity"][:5]

    def before_end(run):
        return [t for t in run["trades"] if t["ts"] < 5 * HOUR]

    assert before_end(original) == prefix["trades"] == before_end(perturbed)
    for count in range(1, 6):
        assert target_position(candles[:count], cfg.strategy) == target_position(
            changed[:count], cfg.strategy
        )


def test_sizing_includes_fees_and_initial_equity_in_drawdown():
    instrument = replace(INSTRUMENT, lot_size=D("1"), min_size=D("1"))
    cfg = replace(config(allocation=D("1")), initial_cash=D("1000"), fee_bps=D("100"))
    result = run_backtest(bars([100, 100, 100]), HOUR, instrument, cfg)
    assert result["trades"] == [
        {"ts": HOUR, "side": "buy", "quantity": "9", "price": "100", "fee": "9", "cash": "91"}
    ]
    assert result["metrics"]["final_equity"] == "991"
    assert result["metrics"]["fees_paid"] == "9"
    assert result["metrics"]["max_drawdown_pct"] == pytest.approx(0.9)
    assert result["equity"][0]["equity"] == "1000"
    assert result["metrics"]["benchmark_return_pct"] == pytest.approx(-0.9)


def test_tick_and_lot_rounding_are_adverse_and_never_exceed_budget():
    instrument = replace(INSTRUMENT, tick_size=D("0.05"), lot_size=D("0.03"), min_size=D("0.06"))
    cfg = replace(config(allocation=D("1")), initial_cash=D("100"), fee_bps=D("10"), slippage_bps=D("5"))
    result = run_backtest(bars([100, 100.003]), HOUR, instrument, cfg)
    fill = result["trades"][0]
    assert fill["price"] == "100.1"
    assert fill["quantity"] == "0.99"
    assert D(fill["quantity"]) % instrument.lot_size == 0
    assert D(fill["cash"]) >= 0


def test_signal_regime_holds_without_continuous_rebalancing():
    result = run_backtest(bars([100, 110, 120, 90, 160]), HOUR, INSTRUMENT, config(allocation=D("0.25")))
    assert len(result["trades"]) == 1
    assert D(result["trades"][0]["quantity"]) * D("110") <= D("2500")


def test_sell_clears_inventory_and_realized_pnl_includes_both_fees():
    cfg = replace(
        config("sma_cross", fast=1, slow=2, allocation=D("1")), initial_cash=D("100"), fee_bps=D("100")
    )
    result = run_backtest(bars([3, 2, 4, 1, 1], [3, 2, 4, 2, 1]), HOUR, INSTRUMENT, cfg)
    buy, sell = result["trades"]
    assert [buy["side"], sell["side"]] == ["buy", "sell"]
    assert buy["quantity"] == sell["quantity"]
    expected_pnl = D(sell["quantity"]) * (D(sell["price"]) - D(buy["price"])) - D(buy["fee"]) - D(sell["fee"])
    assert D(result["metrics"]["realized_pnl"]) == expected_pnl
    assert D(result["metrics"]["final_equity"]) == D("100") + expected_pnl


def test_minimum_order_size_skips_and_zero_allocation_is_flat():
    cfg = replace(config(allocation=D("1")), initial_cash=D("1"))
    result = run_backtest(bars([10000, 10000, 10000]), HOUR, INSTRUMENT, cfg)
    assert result["trades"] == []
    assert result["metrics"]["final_equity"] == "1"
    assert result["assumptions"]["skipped_buys_below_min_size"] == 2
    assert run_backtest(bars([1, 100]), HOUR, INSTRUMENT, config(allocation=D("0")))["trades"] == []


def test_rsi_warmup_monotonic_extremes_and_neutral_regime():
    strategy = StrategyConfig(kind="rsi_reversion", rsi_period=2)
    assert target_position(bars([3, 2]), strategy) is None
    assert target_position(bars([3, 2, 1]), strategy) == strategy.allocation
    assert target_position(bars([1, 2, 3]), strategy) == D("0")
    assert target_position(bars([1, 1, 1]), strategy) is None
    assert target_position(bars([1, 1, 1]), replace(strategy, entry=D("50"), exit=D("60"))) is None


def test_rsi_neutral_decisions_retain_inventory_until_exit():
    result = run_backtest(
        bars([100, 90, 80, 85, 90, 95, 100]), HOUR, INSTRUMENT, config("rsi_reversion", rsi_period=2)
    )
    assert [(trade["ts"], trade["side"]) for trade in result["trades"]] == [
        (3 * HOUR, "buy"),
        (6 * HOUR, "sell"),
    ]


def test_sell_rounds_down_after_adverse_slippage():
    instrument = replace(INSTRUMENT, tick_size=D("0.05"))
    cfg = replace(config("sma_cross", fast=1, slow=2), slippage_bps=D("5"))
    result = run_backtest(bars([3, 2, 4, 1, 1], [3, 2, 4, 2.003, 1.003]), HOUR, instrument, cfg)
    assert [trade["price"] for trade in result["trades"]] == ["2.05", "1"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda values: [values[1], values[0]],
        lambda values: [values[0], values[0]],
        lambda values: [values[0], replace(values[1], ts=2 * HOUR)],
        lambda values: [values[0], replace(values[1], ts=HOUR + 1)],
        lambda values: [values[0], replace(values[1], confirmed=False)],
        lambda values: [values[0], replace(values[1], close=D("0"))],
        lambda values: [values[0], replace(values[1], high=D("0.5"))],
        lambda values: [values[0], replace(values[1], volume=D("-1"))],
        lambda values: [values[0], replace(values[1], close=D("NaN"))],
    ],
)
def test_invalid_series_and_gaps_are_rejected_without_silent_repairs(mutation):
    with pytest.raises(EngineError):
        validate_candles(mutation(bars([1, 1])), HOUR)


def test_quality_can_report_gaps_but_backtest_always_rejects_them():
    candles = [bars([1])[0], bars([1], start=2 * HOUR)[0]]
    report = validate_candles(candles, HOUR, require_contiguous=False)
    assert report["missing_intervals"] == 1
    assert report["contiguous"] is False
    with pytest.raises(EngineError, match="missing intervals"):
        run_backtest(candles, HOUR, INSTRUMENT, config())


@pytest.mark.parametrize("interval", [0, -1, True, 1.5])
def test_invalid_intervals_are_rejected(interval):
    with pytest.raises(EngineError):
        validate_candles(bars([1, 2]), interval)


def test_empty_and_single_candle_series_do_not_fabricate_a_backtest():
    with pytest.raises(EngineError):
        validate_candles([], HOUR)
    with pytest.raises(EngineError):
        run_backtest(bars([1]), HOUR, INSTRUMENT, config())


@pytest.mark.parametrize(
    "cfg",
    [
        replace(config(), initial_cash=D("0")),
        replace(config(), fee_bps=D("-1")),
        replace(config(), slippage_bps=D("10000")),
        config(allocation=D("1.01")),
        config("sma_cross", fast=3, slow=2),
        config("rsi_reversion", entry=D("70"), exit=D("60")),
        config("unregistered"),
    ],
)
def test_invalid_configs_are_rejected(cfg):
    with pytest.raises(EngineError):
        run_backtest(bars([1, 2]), HOUR, INSTRUMENT, cfg)


def test_sharpe_uses_complete_utc_days_and_requires_thirty_returns():
    cfg = config(allocation=D("1"))
    short = run_backtest(bars([100 + i for i in range(29)], interval=DAY_MS), DAY_MS, INSTRUMENT, cfg)
    assert short["metrics"]["sharpe"] is None
    assert short["metrics"]["sharpe_reason"] == "insufficient_sample"
    assert short["assumptions"]["sharpe"]["daily_returns"] == 29
    enough = run_backtest(bars([100 + i for i in range(30)], interval=DAY_MS), DAY_MS, INSTRUMENT, cfg)
    assert isinstance(enough["metrics"]["sharpe"], float)
    assert enough["metrics"]["sharpe_reason"] is None
    partial = run_backtest(bars([100 + i for i in range(30 * 24)], start=HOUR), HOUR, INSTRUMENT, cfg)
    assert partial["assumptions"]["sharpe"]["daily_returns"] == 29
    assert partial["metrics"]["sharpe"] is None
    flat = run_backtest(bars([100] * 30, interval=DAY_MS), DAY_MS, INSTRUMENT, cfg)
    assert flat["metrics"]["sharpe_reason"] == "zero_variance"


def test_serialization_is_json_safe_and_deterministic():
    candles = bars([1, 2, 3, 2, 5])
    first = run_backtest(candles, HOUR, INSTRUMENT, config())
    second = run_backtest(candles, HOUR, INSTRUMENT, config())
    assert json.dumps(first, allow_nan=False, sort_keys=True) == json.dumps(
        second, allow_nan=False, sort_keys=True
    )


@given(
    prices=st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=3, max_size=50),
    initial=st.integers(min_value=1, max_value=1_000_000),
    fee=st.integers(min_value=0, max_value=500),
    slip=st.integers(min_value=0, max_value=500),
    allocation=st.integers(min_value=0, max_value=100),
)
@example(prices=[1, 2, 1, 19910, 1, 2, 1, 9, 1, 515906], initial=159291, fee=1, slip=496, allocation=97)
@settings(max_examples=100, deadline=None)
def test_spot_accounting_remains_solvent_under_random_paths(prices, initial, fee, slip, allocation):
    instrument = replace(INSTRUMENT, lot_size=D("0.000001"), min_size=D("0.000001"))
    cfg = BacktestConfig(
        D(initial), D(fee), D(slip), StrategyConfig(fast=1, slow=2, allocation=D(allocation) / 100)
    )
    try:
        result = run_backtest(bars(prices), HOUR, instrument, cfg)
    except EngineError as exc:
        # A bounded model must reject an unsupported path, rather than return
        # a rounded ledger or loop forever. Every accepted path stays strict.
        assert "supported numeric domain" in str(exc)
        return
    # Replaying decimal-string line items requires the engine's stated 50-digit
    # arithmetic contract. Ambient Decimal's default 28 digits can truncate an
    # otherwise correct high-value cash balance, as the fixed cloud case proved.
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        cash = D(initial)
        holdings = D("0")
        total_fees = D("0")
        for trade in result["trades"]:
            size, price, charged = D(trade["quantity"]), D(trade["price"]), D(trade["fee"])
            assert size >= instrument.min_size and size % instrument.lot_size == 0
            if trade["side"] == "buy":
                assert holdings == 0
                cash -= size * price + charged
                holdings += size
            else:
                assert size == holdings
                cash += size * price - charged
                holdings -= size
            total_fees += charged
            assert cash >= 0 and holdings >= 0
            assert cash == D(trade["cash"])
        assert total_fees == D(result["metrics"]["fees_paid"])
        assert cash + holdings * D(prices[-1]) == D(result["metrics"]["final_equity"])
    assert 0 <= result["metrics"]["max_drawdown_pct"] <= 100


def _context_snapshot(context):
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.traps),
        dict(context.flags),
    )


@pytest.mark.parametrize("precision,rounding", [(6, ROUND_DOWN), (28, ROUND_UP), (90, ROUND_HALF_EVEN)])
def test_engine_is_independent_of_caller_decimal_context_and_preserves_it(precision, rounding):
    candles = bars([1, 2, 1, 19910, 1, 2, 1, 9, 1, 515906])
    instrument = replace(INSTRUMENT, lot_size=D("0.000001"), min_size=D("0.000001"))
    cfg = BacktestConfig(D("159291"), D("1"), D("496"), StrategyConfig(fast=1, slow=2, allocation=D("0.97")))
    rsi = StrategyConfig(kind="rsi_reversion", rsi_period=3)
    baseline = run_backtest(candles, HOUR, instrument, cfg)
    baseline_signal = target_position(candles, rsi)
    assert baseline["trades"][-1]["cash"] == "17838442183682199.027932203593"
    with localcontext() as caller:
        caller.prec, caller.rounding = precision, rounding
        caller.Emin, caller.Emax = -9, 9
        caller.capitals, caller.clamp = 0, 1
        caller.traps[Inexact] = caller.traps[Rounded] = True
        caller.clear_flags()
        caller.flags[Rounded] = True
        before = _context_snapshot(caller)
        assert run_backtest(candles, HOUR, instrument, cfg) == baseline
        assert target_position(candles, rsi) == baseline_signal
        assert _context_snapshot(caller) == before


def test_extreme_compounding_path_fails_closed_at_the_numeric_domain_boundary():
    candles = bars([1, 1_000_000] * 25)
    with pytest.raises(EngineError, match="supported numeric domain"):
        run_backtest(candles, HOUR, INSTRUMENT, config("sma_cross", fast=1, slow=2, allocation=D("1")))
    with pytest.raises(EngineError, match="supported numeric domain"):
        run_backtest(bars([100, 100]), HOUR, INSTRUMENT, replace(config(), initial_cash=D("1e30")))


def test_lot_resolution_and_multiples_outside_finite_precision_fail_closed():
    instrument = replace(INSTRUMENT, lot_size=D("0.000001"), min_size=D("0.000001"))
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        with pytest.raises(EngineError, match="supported numeric domain"):
            engine_module._buy_quantity(D("1e60"), D("95"), D("0.0098"), instrument)
        with pytest.raises(EngineError, match="supported numeric domain"):
            engine_module._floor_lot(D("100"), D("0.12345678901234567890123456789012345678901234567891"))
        with pytest.raises(EngineError, match="price tick"):
            engine_module._fill_price(D("100"), "buy", replace(instrument, tick_size=D("1e-50")), D("0"))


def test_one_lot_correction_must_make_progress_even_if_a_prior_precision_gate_is_bypassed(monkeypatch):
    # This rounded quotient used to enter an infinite correction loop: the cost
    # exceeds the budget, while subtracting one micro-lot leaves it unchanged.
    rounded_quantity = D("1.0424159030970176481012394325087823539835923736853e58")
    instrument = replace(INSTRUMENT, lot_size=D("0.000001"), min_size=D("0.000001"))
    monkeypatch.setattr(engine_module, "_floor_lot", lambda *_: rounded_quantity)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        assert rounded_quantity - instrument.lot_size == rounded_quantity
        with pytest.raises(EngineError, match="one-lot correction"):
            engine_module._buy_quantity(D("1e60"), D("95"), D("0.0098"), instrument)
