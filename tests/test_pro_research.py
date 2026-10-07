import json
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Inexact, getcontext, localcontext

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tidebench.derivatives import FundingEvent, LinearContract, MarginTier
from tidebench.engine import ACCOUNTING_CONTEXT, DAY_MS, Candle, EngineError, Instrument, StrategyConfig
from tidebench.pro_research import (
    ResearchConfig,
    ResearchPlanConfig,
    directional_signal,
    replay_research_snapshot,
    run_professional_backtest,
    run_research_plan,
)

D = Decimal
HOUR = 3_600_000
SPOT = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".01"), D(".01"))
PERP = LinearContract("BTC-USDT-SWAP", "BTC", "USDT", "USDT", D(1), D(1), "BTC", D(".01"), D(1), D(1))
TIERS = [MarginTier(1, D(0), D(100000), D(".01"), D(".005"), D(100))]
CONFIG = ResearchConfig(
    strategy=StrategyConfig(kind="buy_hold", allocation=D(1)),
    fee_bps=D(0),
    slippage_bps=D(0),
    initial_cash=D(1000),
)


def candles(prices, interval=HOUR, opens=None):
    prices = [D(str(price)) for price in prices]
    opens = [D(str(price)) for price in opens] if opens is not None else prices
    return [
        Candle(index * interval, opening, max(opening, close), min(opening, close), close, D(10))
        for index, (opening, close) in enumerate(zip(opens, prices, strict=True))
    ]


def run_perp(data, config=CONFIG, funding=(), marks=None, tiers=TIERS, provenance=None):
    return run_professional_backtest(
        data,
        HOUR,
        PERP,
        config,
        mark_candles=marks or data,
        funding_events=funding,
        margin_tiers=tiers,
        provenance=provenance,
    )


def test_spot_next_open_last_signal_and_attribution():
    data = candles([100, 110, 120], opens=[100, 105, 115])
    result = run_professional_backtest(data, HOUR, SPOT, CONFIG)
    assert len(result["fills"]) == 1
    assert result["fills"][0]["ts"] == HOUR
    assert result["fills"][0]["price"] == "105"
    assert result["fills"][0]["signal_id"] == result["signals"][0]["id"]
    assert result["signals"][-1]["status"] == "last_bar_unfilled"
    assert D(result["metrics"]["final_equity"]) == D("1142.8")
    assert result["metrics"]["round_trips"] == 0
    assert result["metrics"]["sharpe"] is None


def test_spot_rejects_short_and_leverage():
    for config in (replace(CONFIG, direction="long_short"), replace(CONFIG, leverage=D(2))):
        with pytest.raises(EngineError, match="spot"):
            run_professional_backtest(candles([100] * 3), HOUR, SPOT, config)


def test_perpetual_contract_sizing_is_fee_inclusive_and_isolated():
    config = replace(CONFIG, leverage=D(2), fee_bps=D(100))
    result = run_perp(candles([100] * 3), config)
    assert result["fills"][0]["quantity"] == "19"
    assert result["equity"][1]["cash"] == "31"
    assert result["equity"][1]["isolated_margin"] == "950"
    assert result["metrics"]["fees_paid"] == "19"
    assert result["metrics"]["final_equity"] == "981"
    assert result["metrics"]["max_drawdown_pct"] == pytest.approx(1.9)


def test_real_timestamps_and_signed_funding_not_fixed_eight_hours():
    data = candles([100] * 5)
    funding = [
        FundingEvent(HOUR, D(".01")),
        FundingEvent(2 * HOUR, D(".01")),
        FundingEvent(3 * HOUR + 123, D("-.002"), D(100)),
    ]
    result = run_perp(data, replace(CONFIG, leverage=D(2)), funding)
    assert [item["payment"] for item in result["funding"]] == ["0", "20", "-4"]
    assert result["metrics"]["funding_paid"] == "16"
    assert result["metrics"]["final_equity"] == "984"
    short = run_perp(data, replace(CONFIG, leverage=D(2), direction="short_only"), funding)
    assert short["metrics"]["funding_paid"] == "-16"
    assert short["metrics"]["final_equity"] == "1016"


def test_missing_intrabar_mark_is_rejected_instead_of_future_close():
    with pytest.raises(EngineError, match="settlement"):
        run_perp(candles([100] * 4), funding=[FundingEvent(HOUR + 1, D(".001"))])


@pytest.mark.parametrize("missing", ["marks", "funding", "tiers"])
def test_perpetual_risk_inputs_are_required(missing):
    data = candles([100] * 3)
    kwargs = {"mark_candles": data, "funding_events": (), "margin_tiers": TIERS}
    kwargs[{"marks": "mark_candles", "funding": "funding_events", "tiers": "margin_tiers"}[missing]] = None
    with pytest.raises(EngineError):
        run_professional_backtest(data, HOUR, PERP, CONFIG, **kwargs)


def test_mark_alignment_and_duplicate_funding_rejected():
    data = candles([100] * 4)
    with pytest.raises(EngineError, match="cover"):
        run_perp(data, marks=data[:2])
    event = FundingEvent(HOUR, D(".001"))
    with pytest.raises(EngineError, match="unique"):
        run_perp(data, funding=[event, event])


@pytest.mark.parametrize("direction,adverse", [("long_only", 80), ("short_only", 120)])
def test_mark_ohlc_liquidation_even_when_trade_candles_are_flat(direction, adverse):
    data = candles([100] * 4)
    marks = data.copy()
    marks[1] = replace(marks[1], low=D(adverse)) if adverse < 100 else replace(marks[1], high=D(adverse))
    config = replace(CONFIG, leverage=D(10), direction=direction, liquidation_fee_bps=D(10))
    result = run_perp(data, config, marks=marks)
    assert result["liquidations"][0]["phase"] == "adverse_mark_excursion"
    assert result["round_trips"][0]["exit_reason"] == "liquidation"
    assert D(result["round_trips"][0]["net_pnl"]) < 0
    assert D(result["equity"][1]["equity"]) >= 0
    assert result["equity"][1]["quantity"] == "0"


def test_gap_loss_records_shortfall_and_does_not_seize_free_cash():
    data = candles([100] * 4)
    marks = data.copy()
    marks[2] = replace(marks[2], open=D(1), low=D(1), close=D(1))
    config = replace(
        CONFIG,
        strategy=replace(CONFIG.strategy, allocation=D(".5")),
        leverage=D(10),
        liquidation_fee_bps=D(0),
    )
    result = run_perp(data, config, marks=marks)
    assert result["liquidations"][0]["phase"] == "mark_open_gap"
    assert D(result["liquidations"][0]["insurance_shortfall"]) > 0
    assert result["equity"][2]["cash"] == "500"
    assert result["equity"][2]["equity"] == "-3950"
    assert result["equity"][2]["insurance_liability"] == "4450"
    assert result["round_trips"][0]["net_pnl"] == "-4950"
    assert any(order["reason"] == "insurance_liability_halts_new_risk" for order in result["orders"])


def test_funding_can_trigger_liquidation_at_settlement():
    config = replace(CONFIG, leverage=D(100))
    result = run_perp(candles([100] * 4), config, [FundingEvent(2 * HOUR, D(".01"))])
    assert result["liquidations"][0]["phase"] == "funding_settlement"
    assert result["liquidations"][0]["ts"] == 2 * HOUR


def test_tier_leverage_limits_and_coverage_fail_closed():
    data = candles([100] * 4)
    restrictive = [MarginTier(1, D(0), D(100000), D(".1"), D(".05"), D(10))]
    with pytest.raises(EngineError, match="leverage"):
        run_perp(data, replace(CONFIG, leverage=D(20)), tiers=restrictive)
    small = [MarginTier(1, D(0), D(1), D(".01"), D(".005"), D(100))]
    with pytest.raises(EngineError, match="coverage"):
        run_perp(data, tiers=small)


def test_regime_reversal_has_reduce_only_close_then_signed_open():
    config = replace(CONFIG, strategy=StrategyConfig(fast=1, slow=2, allocation=D(1)), direction="long_short")
    result = run_perp(candles([100, 110, 120, 100, 90, 100]), config)
    assert any(trip["direction"] == "long" for trip in result["round_trips"])
    exits = [order for order in result["orders"] if order["reduce_only"]]
    assert exits
    short_entries = [
        fill
        for fill in result["fills"]
        if fill["reason"] == "regime_entry" and D(fill["signed_quantity"]) < 0
    ]
    assert short_entries
    close = next(
        fill
        for fill in result["fills"]
        if fill["ts"] == short_entries[0]["ts"] and fill["reason"] == "signal_exit"
    )
    assert result["fills"].index(close) < result["fills"].index(short_entries[0])


def test_saved_snapshot_replay_is_identical_and_json_safe():
    result = run_perp(
        candles([100, 110, 105, 112]), replace(CONFIG, leverage=D(2)), [FundingEvent(2 * HOUR, D(".001"))]
    )
    encoded = json.loads(json.dumps(result, allow_nan=False))
    assert replay_research_snapshot(encoded["input_snapshot"]) == result


def test_date_window_uses_past_warmup_and_resets_capital():
    data = candles([100, 110, 120, 130, 140, 150])
    config = replace(
        CONFIG, strategy=StrategyConfig(fast=1, slow=3, allocation=D(1)), start_ts=2 * HOUR, end_ts=5 * HOUR
    )
    result = run_professional_backtest(data, HOUR, SPOT, config)
    assert result["assumptions"]["warmup_bars"] == 2
    assert result["equity"][0]["ts"] == 3 * HOUR
    assert result["equity"][0]["equity"] == "1000"
    assert result["fills"][0]["ts"] == 3 * HOUR
    assert result["equity"][-1]["ts"] == 5 * HOUR


def test_prefix_causality_for_signals_fills_and_equity():
    data = candles([100, 110, 105, 130, 90, 100, 110, 80, 140])
    config = replace(
        CONFIG,
        strategy=StrategyConfig(fast=1, slow=3, allocation=D(".5")),
        direction="long_short",
        leverage=D(2),
    )
    short = run_perp(data[:6], config)
    long = run_perp(data, config)
    assert short["fills"] == [fill for fill in long["fills"] if fill["ts"] < 6 * HOUR]
    assert short["equity"] == long["equity"][:6]
    assert [
        {key: value for key, value in signal.items() if key != "status"} for signal in short["signals"]
    ] == [{key: value for key, value in signal.items() if key != "status"} for signal in long["signals"][:6]]


def test_caller_context_does_not_change_research_or_get_mutated():
    data = candles([100, 110, 105, 112, 109])
    expected = run_perp(data, replace(CONFIG, leverage=D(3), fee_bps=D(17), slippage_bps=D(4)))
    with localcontext() as context:
        context.prec, context.rounding, context.Emax, context.Emin = 6, ROUND_DOWN, 5, -5
        context.traps[Inexact] = True
        before = context.copy()
        assert run_perp(data, replace(CONFIG, leverage=D(3), fee_bps=D(17), slippage_bps=D(4))) == expected
        assert getcontext().prec == before.prec
        assert getcontext().rounding == before.rounding
        assert getcontext().flags == before.flags
        assert getcontext().traps == before.traps
        assert getcontext().Emax == before.Emax


def test_daily_metric_sample_policy_and_short_sample_null():
    config = replace(CONFIG, strategy=replace(CONFIG.strategy, allocation=D(".5")))
    short = run_professional_backtest(
        candles([100 + index % 5 for index in range(29)], DAY_MS), DAY_MS, SPOT, config
    )
    assert short["metrics"]["sharpe"] is None
    long = run_professional_backtest(
        candles([100 + index % 5 for index in range(31)], DAY_MS), DAY_MS, SPOT, config
    )
    assert long["metrics"]["complete_utc_days"] == 31
    assert long["metrics"]["sharpe"] is not None
    assert long["metrics"]["sortino"] is not None
    assert long["metrics"]["annualized_volatility_pct"] > 0
    assert long["metrics"]["calmar"] is None


def test_synthetic_sources_cannot_mix():
    with pytest.raises(EngineError, match="sources"):
        run_perp(candles([100] * 4), provenance={"trade": {"source": "example"}, "mark": {"source": "okx"}})


def test_grid_is_bounded_and_deterministic():
    data = candles([100, 110, 100, 120, 110, 130, 110, 140])
    config = replace(CONFIG, strategy=StrategyConfig(fast=1, slow=3, allocation=D(".5")))
    options = ResearchPlanConfig(grid={"fast": [1, 2], "slow": [3, 4]}, max_workers=2)
    result = run_research_plan(data, HOUR, SPOT, config, mode="grid", options=options)
    sequential = run_research_plan(
        data, HOUR, SPOT, config, mode="grid", options=replace(options, max_workers=1)
    )
    assert result["experiments"] == sequential["experiments"]
    assert result["comparison"] == sequential["comparison"]
    assert result["selection_scope"] == "in_sample_descriptive_only"
    assert len(result["experiments"]) == 4
    with pytest.raises(EngineError, match="case limit"):
        run_research_plan(
            data,
            HOUR,
            SPOT,
            config,
            mode="grid",
            options={"grid": {"fast": list(range(1, 65)), "slow": [70, 80]}},
        )


def test_cost_stress_matrix_applies_costs_to_same_fixed_strategy():
    data = candles([100, 100, 110, 120])
    result = run_research_plan(
        data, HOUR, SPOT, CONFIG, mode="cost_stress", options={"fee_bps": [0, 100], "slippage_bps": [0, 100]}
    )
    assert len(result["matrix"]) == 4
    cells = {
        (item["fee_bps"], item["slippage_bps"]): item["metrics"]["total_return_pct"]
        for item in result["matrix"]
    }
    assert cells[("0", "0")] > cells[("100", "100")]


def test_train_test_and_walk_forward_are_chronological_and_reset_test():
    data = candles([100 + index % 7 * 2 for index in range(40)])
    config = replace(CONFIG, strategy=StrategyConfig(fast=1, slow=3, allocation=D(".5")))
    options = {"train_bars": 10, "test_bars": 5, "step_bars": 5, "purge_bars": 2, "grid": {"fast": [1, 2]}}
    result = run_research_plan(data, HOUR, SPOT, config, mode="walk_forward", options=options)
    assert result["oos_summary"]["folds"] == 5
    previous_end = -1
    for fold in result["folds"]:
        assert fold["train_end_ts"] + 2 * HOUR == fold["test_start_ts"]
        assert fold["test_start_ts"] >= previous_end
        assert fold["test_result"]["equity"][0]["equity"] == "1000"
        assert fold["test_result"]["metrics"]["initial_cash"] == "1000"
        previous_end = fold["test_end_ts"]
    single = run_research_plan(data, HOUR, SPOT, config, mode="train_test", options=options)
    assert len(single["folds"]) == 1


def test_test_prices_cannot_affect_training_selection():
    data = candles([100, 110, 100, 120, 100, 130, 90, 120, 110, 140, 1, 200, 1, 300])
    config = replace(CONFIG, strategy=StrategyConfig(fast=1, slow=3, allocation=D(".5")))
    options = {"train_bars": 8, "test_bars": 6, "grid": {"fast": [1, 2]}}
    first = run_research_plan(data, HOUR, SPOT, config, mode="train_test", options=options)
    changed = data[:8] + [
        replace(candle, open=D(100), high=D(100), low=D(100), close=D(100)) for candle in data[8:]
    ]
    second = run_research_plan(changed, HOUR, SPOT, config, mode="train_test", options=options)
    assert first["folds"][0]["selected_strategy"] == second["folds"][0]["selected_strategy"]
    assert [row["total_return_pct"] for row in first["folds"][0]["training_comparison"]] == [
        row["total_return_pct"] for row in second["folds"][0]["training_comparison"]
    ]


@pytest.mark.parametrize(
    "mode,options,match",
    [
        ("walk_forward", {"train_bars": 5, "test_bars": 5, "step_bars": 2}, "overlap"),
        ("single", {"max_workers": 5}, "four"),
        ("grid", {"grid": {"future_close": [1]}}, "unsupported"),
        ("cost_stress", {"fee_bps": list(range(6)), "slippage_bps": list(range(6))}, "25"),
        ("train_test", {"train_bars": 100}, "test window"),
    ],
)
def test_invalid_plan_boundaries(mode, options, match):
    with pytest.raises(EngineError, match=match):
        run_research_plan(candles([100] * 20), HOUR, SPOT, CONFIG, mode=mode, options=options)


@given(
    st.lists(st.integers(min_value=60, max_value=150), min_size=3, max_size=30),
    st.integers(min_value=1, max_value=20),
)
@settings(max_examples=50, deadline=None)
@example(prices=[60, 61, 101, 60], leverage=6)
def test_perpetual_cash_and_wallet_solvency_property(prices, leverage):
    config = replace(
        CONFIG,
        strategy=StrategyConfig(fast=1, slow=2, allocation=D(".7")),
        direction="long_short",
        leverage=D(leverage),
        fee_bps=D(10),
        slippage_bps=D(10),
    )
    wide_tiers = [MarginTier(1, D(0), D("1e28"), D(".01"), D(".005"), D(100))]
    try:
        result = run_perp(candles(prices), config, tiers=wide_tiers)
    except EngineError as exc:
        assert "numeric domain" in str(exc)
        return
    with localcontext(ACCOUNTING_CONTEXT):
        for row in result["equity"]:
            assert D(row["cash"]) >= 0
            debt = D(row["insurance_liability"])
            assert debt >= 0
            if D(row["equity"]) < 0:
                assert debt > 0
            assert D(row["cash"]) + (D(row["isolated_margin"]) + D(row["unrealized_pnl"])) - debt == D(
                row["equity"]
            )
        replay_cash = D(result["metrics"]["initial_cash"])
        for fill in result["fills"]:
            if "cash_debit" in fill:
                replay_cash -= D(fill["cash_debit"])
            else:
                replay_cash += D(fill["cash_credit"])
            assert replay_cash == D(fill["cash"])
            assert replay_cash >= 0
        assert replay_cash == D(result["equity"][-1]["cash"])


@pytest.mark.parametrize("direction", ["long_only", "short_only", "long_short"])
def test_sma_equality_is_flat_for_every_direction(direction):
    strategy = StrategyConfig(fast=1, slow=2)
    assert directional_signal(candles([100, 100]), strategy, direction) == 0
    assert directional_signal(candles([100]), strategy, direction) is None


@pytest.mark.parametrize(
    "direction,up,down", [("long_only", 1, 0), ("short_only", 0, -1), ("long_short", 1, -1)]
)
def test_shared_directional_signal_policy(direction, up, down):
    strategy = StrategyConfig(fast=1, slow=2)
    assert directional_signal(candles([100, 110]), strategy, direction) == up
    assert directional_signal(candles([110, 100]), strategy, direction) == down


def test_exchange_funding_requires_verified_full_coverage():
    data = candles([100] * 4)
    with pytest.raises(EngineError, match="provenance"):
        run_perp(data, provenance={"trade": {"source": "okx"}})
    provenance = {
        "trade": {"source": "okx"},
        "funding": {
            "source": "okx",
            "quality": {"complete": True, "coverage_start": 0, "coverage_end": 4 * HOUR},
        },
    }
    assert (
        run_perp(data, provenance=provenance)["assumptions"]["funding_coverage"] == "source_manifest_complete"
    )
    provenance["funding"]["quality"]["coverage_end"] = 3 * HOUR
    with pytest.raises(EngineError, match="coverage"):
        run_perp(data, provenance=provenance)


def test_imported_funding_requires_provider_provenance():
    data = candles([100] * 4)
    provenance = {
        "trade": {"source": "okx"},
        "funding": {
            "source": "okx",
            "transport": "user_import",
            "quality": {"complete": True, "coverage_start": 0, "coverage_end": 4 * HOUR},
        },
    }
    with pytest.raises(EngineError, match="provider provenance"):
        run_perp(data, provenance=provenance)


def test_funding_attribution_preserves_mark_approximation_and_formula():
    event = FundingEvent(
        2 * HOUR,
        D(".001"),
        D(100),
        "withRate",
        "current_period",
        "historical_mark_1m_open_approximation",
        2 * HOUR,
    )
    result = run_perp(candles([100] * 4), funding=[event])
    assert result["funding"][0]["mark_price_source"] == "historical_mark_1m_open_approximation"
    assert result["funding"][0]["formula_type"] == "withRate"
    assert replay_research_snapshot(result["input_snapshot"]) == result


def test_wrong_funding_instrument_rejected():
    event = FundingEvent(2 * HOUR, D(".001"), D(100), inst_id="ETH-USDT-SWAP")
    with pytest.raises(EngineError, match="instrument"):
        run_perp(candles([100] * 4), funding=[event])
