import copy
import json
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, Inexact, getcontext, localcontext
from math import sqrt

import pytest
from tidebench.backtest_diagnostics import (
    DIAGNOSTICS_VERSION,
    MAX_CALENDAR_ROWS,
    MAX_ROLLING_POINTS,
    compute_backtest_diagnostics,
)
from tidebench.derivatives import FundingEvent, LinearContract, MarginTier
from tidebench.engine import (
    ACCOUNTING_CONTEXT,
    DAY_MS,
    BacktestConfig,
    Candle,
    EngineError,
    Instrument,
    StrategyConfig,
    run_backtest,
)
from tidebench.pro_research import ResearchConfig, run_professional_backtest

D = Decimal
HOUR = 3_600_000


def utc(year, month, day, hour=0):
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1000)


def recorded(values, *, interval=DAY_MS, start=0, initial="100", quantities=None):
    return {
        "metrics": {"initial_cash": initial, "fees_paid": "0", "funding_paid": "0"},
        "equity": [
            {
                "ts": start + (index + 1) * interval,
                "equity": str(value),
                "benchmark": initial,
                "quantity": str(quantities[index]) if quantities else "0",
                "gross_notional": "0",
            }
            for index, value in enumerate(values)
        ],
        "round_trips": [],
        "fills": [],
        "assumptions": {"quantity_unit": "BTC"},
    }


def trip(pnl, entry=0, exit_ts=HOUR, **extra):
    return {
        "entry_ts": entry,
        "exit_ts": exit_ts,
        "direction": "long",
        "exit_reason": "signal_exit",
        "net_pnl": str(pnl),
        **extra,
    }


def test_drawdown_peak_trough_recovery_and_censored_open_episode():
    result = recorded([100, 80, 90, 100, 120, 60, 70], interval=HOUR)
    report = compute_backtest_diagnostics(result, HOUR)
    dd = report["drawdowns"]
    assert dd["episode_count"] == 2
    assert dd["recovered_count"] == 1
    assert dd["max_depth_pct"] == 50
    assert dd["max_depth_amount"] == "60"
    assert dd["underwater_observation_pct"] == pytest.approx(4 / 7 * 100)
    finished = dd["episodes"][1]
    assert (finished["peak_ts"], finished["trough_ts"], finished["recovery_ts"]) == (HOUR, 2 * HOUR, 4 * HOUR)
    assert finished["duration_ms"] == 3 * HOUR
    assert finished["decline_duration_ms"] == HOUR
    assert finished["recovery_duration_ms"] == 2 * HOUR
    assert finished["depth_amount"] == "20"
    assert finished["recovery_required_pct"] == 25
    assert finished["right_censored"] is False
    unfinished = dd["open_episode"]
    assert (unfinished["peak_ts"], unfinished["trough_ts"], unfinished["end_ts"]) == (
        5 * HOUR,
        6 * HOUR,
        7 * HOUR,
    )
    assert unfinished["recovered"] is False
    assert unfinished["right_censored"] is True
    assert unfinished["recovery_ts"] is None
    assert unfinished["recovery_duration_ms"] is None
    assert unfinished["observed_recovery_duration_ms"] == HOUR
    assert unfinished["recovery_required_pct"] == 100
    assert dd["longest_duration_ms"] == 3 * HOUR


def test_initial_baseline_and_latest_equal_peak_are_observed():
    report = compute_backtest_diagnostics(recorded([90, 80, 100, 100, 90], interval=HOUR), HOUR)
    episodes = report["drawdowns"]["episodes"]
    assert episodes[0]["peak_ts"] == 0
    assert episodes[0]["depth_pct"] == 20
    assert episodes[0]["recovery_ts"] == 3 * HOUR
    assert report["drawdowns"]["open_episode"]["peak_ts"] == 4 * HOUR


def test_daily_monthly_utc_boundaries_and_partial_periods():
    start = utc(2024, 1, 31, 12)
    values = [100] * 48
    values[11:35] = [110] * 24
    values[35:47] = [121] * 12
    values[47] = D("133.1")
    report = compute_backtest_diagnostics(recorded(values, interval=HOUR, start=start), HOUR)
    calendar = report["calendar"]
    assert [row["period"] for row in calendar["daily"]] == ["2024-01-31", "2024-02-01", "2024-02-02"]
    assert [row["complete"] for row in calendar["daily"]] == [False, True, False]
    assert [row["return_pct"] for row in calendar["daily"]] == [10, 10, 10]
    assert calendar["daily"][0]["observed_start_ts"] == start
    assert calendar["daily"][0]["reason"] == "partial_calendar_period"
    assert calendar["daily"][1]["benchmark_return_pct"] == 0
    assert calendar["daily"][1]["active_return_pct"] == 10
    assert report["sample"]["complete_utc_days"] == 1
    assert [row["return_pct"] for row in calendar["monthly"]] == [10, 21]
    assert all(row["complete"] is False for row in calendar["monthly"])


def test_leap_months_use_actual_calendar_not_fixed_30_days():
    values = [100] * 60
    values[30:59] = [110] * 29
    values[59] = 121
    report = compute_backtest_diagnostics(recorded(values, start=utc(2024, 1, 1)), DAY_MS)
    months = report["calendar"]["monthly"]
    assert [(row["period"], row["return_pct"], row["complete"]) for row in months] == [
        ("2024-01", 10, True),
        ("2024-02", 10, True),
    ]
    assert months[1]["end_ts"] - months[1]["start_ts"] == 29 * DAY_MS
    assert report["sample"]["complete_months"] == 2
    assert report["calendar"]["yearly"][0]["return_pct"] == 21
    assert report["calendar"]["yearly"][0]["complete"] is False


def test_missing_interior_observation_invalidates_day_and_breaks_rolling_window():
    result = recorded([100] * (31 * 24), interval=HOUR)
    result["equity"] = [row for row in result["equity"] if row["ts"] != 15 * DAY_MS + HOUR]
    report = compute_backtest_diagnostics(result, HOUR)
    day = report["calendar"]["daily"][15]
    assert day["complete"] is False
    assert day["return_pct"] is None
    assert day["reason"] == "missing_or_irregular_bar_observations"
    assert report["sample"]["missing_bar_intervals"] == 1
    assert report["sample"]["complete_utc_days"] == 30
    assert report["rolling_risk"]["points"] == []
    assert report["rolling_risk"]["reason"] == "insufficient_consecutive_complete_utc_days"


def test_no_forward_fill_of_missing_calendar_boundary():
    result = recorded([100, 110, 121], interval=2 * DAY_MS)
    report = compute_backtest_diagnostics(result, 2 * DAY_MS)
    assert report["sample"]["complete_utc_days"] == 0
    assert all(row["return_pct"] is None for row in report["calendar"]["daily"])
    assert all(row["reason"] == "missing_boundary_observation" for row in report["calendar"]["daily"])


def test_rolling_risk_independent_alternating_return_expectations():
    with localcontext(ACCOUNTING_CONTEXT):
        equity, values = D(100), []
        for index in range(30):
            equity *= D("1.1") if index % 2 == 0 else D(".9")
            values.append(equity)
    report = compute_backtest_diagnostics(recorded(values), DAY_MS)
    assert report["rolling_risk"]["eligible_window_count"] == 1
    risk = report["rolling_risk"]["points"][0]
    assert risk["annualized_volatility_pct"] == pytest.approx(10 * sqrt(365 * 30 / 29))
    assert risk["sharpe"] == pytest.approx(0, abs=1e-40)
    assert risk["sortino"] == pytest.approx(0, abs=1e-40)
    assert risk["return_pct"] == pytest.approx((0.99**15 - 1) * 100)
    distribution = report["daily_distribution"]
    assert distribution["p05_return_pct"] == pytest.approx(-10)
    assert distribution["p95_return_pct"] == pytest.approx(10)
    assert distribution["median_return_pct"] == pytest.approx(0, abs=1e-40)
    assert distribution["tail_reason"] == "fewer_than_100_complete_daily_returns"
    assert sum(bin_["count"] for bin_ in distribution["histogram"]) == 30


def test_flat_equity_zero_variance_has_null_ratios_and_no_drawdowns():
    report = compute_backtest_diagnostics(recorded([100] * 30), DAY_MS)
    risk = report["rolling_risk"]["points"][0]
    assert risk["annualized_volatility_pct"] == 0
    assert risk["sharpe"] is None
    assert risk["sortino"] is None
    assert risk["sharpe_reason"] == "zero_or_negligible_variance"
    assert report["drawdowns"]["episodes"] == []
    assert report["drawdowns"]["max_depth_pct"] == 0
    assert report["daily_distribution"]["histogram"] == [
        {"lower": 0, "upper": 0, "count": 30, "upper_inclusive": True}
    ]


def test_decimal_rounding_dust_does_not_turn_constant_returns_into_huge_sharpe():
    with localcontext(ACCOUNTING_CONTEXT):
        value, values = D(100), []
        for _ in range(30):
            value *= D("1.01")
            values.append(value)
    risk = compute_backtest_diagnostics(recorded(values), DAY_MS)["rolling_risk"]["points"][0]
    assert risk["sharpe"] is None
    assert risk["sharpe_reason"] == "zero_or_negligible_variance"


def test_empirical_lower_tail_has_independent_five_observation_average():
    returns = [D("-.10"), D("-.08"), D("-.06"), D("-.04"), D("-.02")] + [D(0)] * 95
    with localcontext(ACCOUNTING_CONTEXT):
        value, values = D(100), []
        for daily_return in returns:
            value *= 1 + daily_return
            values.append(value)
    distribution = compute_backtest_diagnostics(recorded(values), DAY_MS)["daily_distribution"]
    assert distribution["lower_tail_count"] == 5
    assert distribution["empirical_lower_tail_mean_pct"] == -6
    assert distribution["p05_return_pct"] == pytest.approx(-0.1)
    assert distribution["p95_return_pct"] == 0
    assert distribution["tail_reason"] is None


@pytest.mark.parametrize("equity,depth,last_return", [(0, 100, -100), (-25, 125, -125)])
def test_nonpositive_equity_remains_visible_without_invalid_risk_ratios(equity, depth, last_return):
    report = compute_backtest_diagnostics(recorded([100] * 30 + [equity, 50]), DAY_MS)
    assert report["drawdowns"]["max_depth_pct"] == depth
    assert report["drawdowns"]["open_episode"]["recovery_required_pct"] is None
    assert report["calendar"]["daily"][-2]["return_pct"] == last_return
    assert report["calendar"]["daily"][-1]["return_pct"] is None
    assert report["calendar"]["daily"][-1]["reason"] == "nonpositive_opening_equity"
    assert report["rolling_risk"]["reason"] == "nonpositive_equity"
    assert report["rolling_risk"]["points"] == []
    assert "nonpositive_equity" in {row["code"] for row in report["limitations"]}
    json.dumps(report, allow_nan=False)


def test_closed_trade_outcomes_holding_times_and_concentration_have_exact_totals():
    result = recorded([100] * 20, interval=HOUR)
    result["round_trips"] = [
        trip(value, index * HOUR, (index + 1) * HOUR, direction="short" if index < 2 else "long")
        for index, value in enumerate([-20, -10, 0, 10, 100])
    ]
    report = compute_backtest_diagnostics(result, HOUR)
    trades = report["trades"]
    assert (trades["count"], trades["wins"], trades["losses"], trades["breakeven"]) == (5, 2, 2, 1)
    assert trades["net_pnl"] == "80"
    assert trades["gross_profit"] == "110"
    assert trades["gross_loss"] == "30"
    assert trades["expectancy"] == "16"
    assert trades["median_pnl"] == "0"
    assert trades["mean_winner"] == "55"
    assert trades["mean_loser"] == "-15"
    assert trades["win_rate_pct"] == 40
    assert trades["profit_factor"] == pytest.approx(11 / 3)
    assert trades["payoff_ratio"] == pytest.approx(11 / 3)
    assert trades["largest_winner_share_of_gross_profit_pct"] == pytest.approx(1000 / 11)
    assert trades["top_five_winner_share_of_gross_profit_pct"] == 100
    assert trades["mean_holding_ms"] == HOUR
    assert trades["median_holding_ms"] == HOUR
    assert trades["p05_pnl"] is None
    assert trades["by_direction"] == [
        {"label": "long", "count": 3, "net_pnl": "110"},
        {"label": "short", "count": 2, "net_pnl": "-30"},
    ]
    assert sum(bin_["count"] for bin_ in trades["histogram"]) == 5
    assert report["contribution"]["unclosed_pnl_residual"] == "-80"


def test_no_losing_trades_does_not_fabricate_infinite_profit_factor():
    result = recorded([110], interval=HOUR)
    result["round_trips"] = [trip(10)]
    report = compute_backtest_diagnostics(result, HOUR)
    assert report["trades"]["profit_factor"] is None
    assert report["trades"]["profit_factor_reason"] == "no_completed_losing_trades"
    assert report["trades"]["payoff_ratio"] is None


def test_trade_quantiles_have_known_interpolation_and_holding_time_units():
    result = recorded([100] * 30, interval=HOUR)
    result["round_trips"] = [trip(index, exit_ts=(index + 1) * HOUR) for index in range(30)]
    trades = compute_backtest_diagnostics(result, HOUR)["trades"]
    assert trades["net_pnl"] == "435"
    assert trades["expectancy"] == "14.5"
    assert trades["median_pnl"] == "14.5"
    assert trades["p05_pnl"] == "1.45"
    assert trades["p95_pnl"] == "27.55"
    assert trades["mean_holding_ms"] == 15.5 * HOUR
    assert trades["median_holding_ms"] == 15.5 * HOUR


def test_absent_sources_remain_unavailable_and_are_not_filled_with_zero_costs():
    result = recorded([100, 110], interval=HOUR)
    result.pop("round_trips")
    result.pop("fills")
    result["metrics"].pop("fees_paid")
    result["metrics"].pop("funding_paid")
    for row in result["equity"]:
        row.pop("benchmark")
        row.pop("quantity")
        row.pop("gross_notional")
    report = compute_backtest_diagnostics(result, HOUR)
    assert report["trades"]["source"] == "unavailable"
    assert report["contribution"]["closed_net_pnl"] is None
    assert report["contribution"]["fees_paid"] is None
    assert report["contribution"]["funding_paid"] is None
    assert report["contribution"]["unclosed_pnl_residual"] is None
    assert report["calendar"]["daily"][0]["benchmark_return_pct"] is None
    assert report["exposure"]["available"] is False


def test_extreme_derived_ratios_are_null_json_numbers_with_exact_money_retained():
    result = recorded(["-1e29"], initial="1e-300")
    report = compute_backtest_diagnostics(result, DAY_MS)
    assert report["sample"]["initial_cash"] == "1E-300"
    assert D(report["sample"]["final_equity"]) == D("-1e29")
    assert report["calendar"]["daily"][0]["return_pct"] is None
    assert report["calendar"]["daily"][0]["reason"] == "numeric_range"
    assert report["drawdowns"]["max_depth_pct"] is None
    assert report["drawdowns"]["max_depth_reason"] == "numeric_range"
    assert "numeric_range" in {item["code"] for item in report["limitations"]}
    json.dumps(report, allow_nan=False)


def test_completed_trade_cannot_extend_past_the_supplied_equity_path():
    result = recorded([100], interval=HOUR)
    result["round_trips"] = [trip(10, exit_ts=2 * HOUR)]
    with pytest.raises(EngineError, match="outside"):
        compute_backtest_diagnostics(result, HOUR)


def test_cost_accounting_signed_funding_and_nonunit_contract_price_impact():
    result = recorded([D("100.26")], interval=HOUR)
    result["metrics"].update(fees_paid=".04", funding_paid="-.5")
    result["assumptions"]["quantity_unit"] = "contracts"
    result["input_snapshot"] = {"instrument": {"ct_val": ".1", "ct_mult": "1"}}
    result["round_trips"] = [trip(".26", gross_pnl="-.2", fees=".04", funding_payment="-.5")]
    result["fills"] = [
        {"side": "buy", "quantity": "2", "price": "101", "reference_price": "100"},
        {"side": "sell", "quantity": "2", "price": "100", "reference_price": "101"},
    ]
    contribution = compute_backtest_diagnostics(result, HOUR)["contribution"]
    assert contribution["fees_paid"] == "0.04"
    assert contribution["funding_paid"] == "-0.5"
    assert contribution["closed_gross_pnl"] == "-0.2"
    assert contribution["closed_net_pnl"] == "0.26"
    assert contribution["total_equity_pnl"] == "0.26"
    assert contribution["unclosed_pnl_residual"] == "0"
    assert contribution["execution_price_impact"] == "0.4"
    assert contribution["slippage_paid"] is None
    del result["input_snapshot"]
    assert compute_backtest_diagnostics(result, HOUR)["contribution"]["execution_price_impact"] is None


def test_exposure_is_inventory_at_closes_and_skips_nonpositive_leverage_denominators():
    result = recorded([100, 100, 50, 0], interval=HOUR, quantities=[0, 2, -1, 0])
    for row, notional in zip(result["equity"], [0, 200, 150, 0], strict=True):
        row["gross_notional"] = str(notional)
    exposure = compute_backtest_diagnostics(result, HOUR)["exposure"]
    assert (exposure["long_pct"], exposure["short_pct"], exposure["flat_pct"]) == (25, 25, 50)
    assert exposure["invested_pct"] == 50
    assert exposure["mean_gross_notional"] == "87.5"
    assert exposure["max_gross_notional"] == "200"
    assert exposure["mean_gross_leverage"] == pytest.approx(5 / 3)
    assert exposure["max_gross_leverage"] == 3
    assert exposure["excluded_nonpositive_equity_closes"] == 1


def test_financial_strings_are_not_converted_to_binary_float_and_inputs_are_immutable():
    initial = "9007199254740993.1234567890123456789"
    final = "9007199254740993.1234567890123456790"
    result = recorded([final], interval=HOUR, initial=initial)
    before = copy.deepcopy(result)
    report = compute_backtest_diagnostics(result, HOUR)
    assert report["sample"]["initial_cash"] == initial
    assert report["contribution"]["total_equity_pnl"] == "0.0000000000000000001"
    assert result == before
    assert json.loads(json.dumps(report, allow_nan=False)) == report


def test_hostile_caller_decimal_context_does_not_change_output_or_mutate_context():
    result = recorded([100, 90, 99], interval=HOUR)
    expected = compute_backtest_diagnostics(result, HOUR)
    with localcontext() as context:
        context.prec, context.rounding, context.Emax, context.Emin = 6, ROUND_DOWN, 5, -5
        context.traps[Inexact] = True
        before = context.copy()
        assert compute_backtest_diagnostics(result, HOUR) == expected
        assert getcontext().prec == before.prec
        assert getcontext().rounding == before.rounding
        assert getcontext().flags == before.flags
        assert getcontext().traps == before.traps


def test_report_is_bounded_but_retains_all_sample_counts_and_latest_rolling_point():
    report = compute_backtest_diagnostics(recorded([100] * 1100), DAY_MS)
    assert report["diagnostics_version"] == DIAGNOSTICS_VERSION
    assert len(report["calendar"]["daily"]) == MAX_CALENDAR_ROWS
    assert report["calendar"]["daily_omitted_count"] == 700
    assert report["sample"]["complete_utc_days"] == 1100
    assert len(report["rolling_risk"]["points"]) == MAX_ROLLING_POINTS
    assert report["rolling_risk"]["eligible_window_count"] == 1071
    assert report["rolling_risk"]["points"][0]["ts"] == 30 * DAY_MS
    assert report["rolling_risk"]["points"][-1]["ts"] == 1100 * DAY_MS
    assert report["daily_distribution"]["lower_tail_count"] == 55
    assert report["daily_distribution"]["empirical_lower_tail_mean_pct"] == 0
    assert len(json.dumps(report, allow_nan=False)) < 400_000


def test_only_deepest_ten_episodes_are_retained_and_open_episode_is_always_visible():
    values = [value for decline in range(1, 13) for value in (100 - decline, 100)] + [99]
    report = compute_backtest_diagnostics(recorded(values, interval=HOUR), HOUR)
    assert report["drawdowns"]["episode_count"] == 13
    assert len(report["drawdowns"]["episodes"]) == 10
    assert report["drawdowns"]["episodes"][0]["depth_pct"] == 12
    assert report["drawdowns"]["omitted_episode_count"] == 3
    assert report["drawdowns"]["open_episode"]["depth_pct"] == 1
    assert report["drawdowns"]["open_episode"]["right_censored"] is True


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda result: result["equity"][0].update(equity="NaN"), "finite"),
        (lambda result: result["equity"][0].update(equity=float("inf")), "exact"),
        (lambda result: result["equity"][0].update(equity=100.0), "exact"),
        (lambda result: result["metrics"].update(initial_cash="0"), "positive"),
        (lambda result: result["equity"][1].update(ts=DAY_MS), "strictly increasing"),
        (lambda result: result["equity"][0].update(ts=True), "timestamp"),
    ],
)
def test_malformed_sources_fail_clearly(change, match):
    result = recorded([100, 110])
    change(result)
    with pytest.raises(EngineError, match=match):
        compute_backtest_diagnostics(result, DAY_MS)


def test_boundary_projection_cannot_masquerade_as_full_equity_path():
    result = recorded([100, 110])
    with pytest.raises(EngineError, match="boundaries"):
        compute_backtest_diagnostics(result, DAY_MS, start_ts=1)
    with pytest.raises(EngineError, match="boundaries"):
        compute_backtest_diagnostics(result, DAY_MS, end_ts=3 * DAY_MS)
    for interval in (0, -1, True):
        with pytest.raises(EngineError, match="positive integer"):
            compute_backtest_diagnostics(result, interval)


def test_actual_classic_engine_full_exit_is_paired_without_invented_funding_or_slippage():
    instrument = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".01"), D(".01"))
    candles = [
        Candle(index * HOUR, D(opening), D(max(opening, close)), D(min(opening, close)), D(close), D(10))
        for index, (opening, close) in enumerate(zip([3, 2, 4, 2, 1], [3, 2, 4, 1, 1], strict=True))
    ]
    config = BacktestConfig(
        initial_cash=D(100),
        fee_bps=D(100),
        slippage_bps=D(0),
        strategy=StrategyConfig(fast=1, slow=2, allocation=D(1)),
    )
    result = run_backtest(candles, HOUR, instrument, config)
    report = compute_backtest_diagnostics(result, HOUR)
    # 49.50 base buys at 2 and sells at 1; fees 0.99 + 0.495.
    assert report["trades"]["source"] == "paired_classic_full_spot_exits"
    assert report["trades"]["count"] == 1
    assert report["trades"]["net_pnl"] == "-50.985"
    assert report["contribution"]["fees_paid"] == "1.485"
    assert report["contribution"]["funding_paid"] is None
    assert report["contribution"]["execution_price_impact"] is None
    assert report["exposure"]["available"] is False


def test_actual_professional_perp_open_costs_are_residual_not_completed_trade_pnl():
    contract = LinearContract("BTC-USDT-SWAP", "BTC", "USDT", "USDT", D(1), D(1), "BTC", D(".01"), D(1), D(1))
    tiers = [MarginTier(1, D(0), D(100000), D(".01"), D(".005"), D(100))]
    candles = [Candle(index * HOUR, D(100), D(100), D(100), D(100), D(10)) for index in range(4)]
    config = ResearchConfig(
        initial_cash=D(1000),
        leverage=D(2),
        fee_bps=D(100),
        slippage_bps=D(0),
        strategy=StrategyConfig(kind="buy_hold", allocation=D(1)),
    )
    result = run_professional_backtest(
        candles,
        HOUR,
        contract,
        config,
        mark_candles=candles,
        funding_events=[FundingEvent(2 * HOUR, D(".01"))],
        margin_tiers=tiers,
    )
    report = compute_backtest_diagnostics(result, HOUR)
    assert report["trades"]["count"] == 0
    assert report["contribution"]["fees_paid"] == "19"
    assert report["contribution"]["funding_paid"] == "19"
    assert report["contribution"]["total_equity_pnl"] == "-38"
    assert report["contribution"]["closed_net_pnl"] == "0"
    assert report["contribution"]["unclosed_pnl_residual"] == "-38"
    assert report["exposure"]["invested_pct"] == 75
    assert report["drawdowns"]["max_depth_pct"] == pytest.approx(3.8)
    assert report["sample"]["initial_cash"] == "1000"
    assert report["sample"]["start_ts"] == 0
