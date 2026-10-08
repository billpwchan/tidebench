"""Adverse gaps, signed exits, loss budgets and forward restart parity."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from tidebench.engine import ACCOUNTING_CONTEXT, Candle, Instrument, StrategyConfig
from tidebench.pro_research import ResearchConfig, run_professional_backtest
from tidebench.strategy_risk import exit_on_close, risk_notional

D = Decimal
HOUR = 3_600_000
SPOT = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".01"), D(".01"))


def bars(closes, opens=None):
    opens = opens or closes
    return [
        Candle(i * HOUR, D(o), max(D(o), D(c)), min(D(o), D(c)), D(c), D(10))
        for i, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]


@pytest.mark.parametrize(
    "quantity,close,peak,expected",
    [
        (1, 94, 100, "close_stop_loss"),
        (-1, 106, 100, "close_stop_loss"),
        (1, 104, 110, "close_trailing_stop"),
        (-1, 96, 90, "close_trailing_stop"),
        (1, 111, 111, "close_take_profit"),
        (-1, 89, 89, "close_take_profit"),
    ],
)
def test_signed_close_exit_rules(quantity, close, peak, expected):
    strategy = StrategyConfig(stop_loss_pct=D(5), trailing_stop_pct=D(5), take_profit_pct=D(10))
    assert exit_on_close(strategy, D(quantity), D(100), D(close), D(peak), 1)[0] == expected
    assert exit_on_close(strategy, D(0), D(0), D(close), None, 1)[0] is None


def test_close_stop_cannot_fill_at_ideal_trigger_or_signal_close():
    strategy = StrategyConfig(kind="buy_hold", allocation=D(1), stop_loss_pct=D(5))
    data = bars([100, 100, 94, 80, 80], [100, 100, 100, 80, 80])
    result = run_professional_backtest(
        data,
        HOUR,
        SPOT,
        ResearchConfig(strategy=strategy, initial_cash=D(1000), fee_bps=D(0), slippage_bps=D(0)),
    )
    exit_fill = next(f for f in result["fills"] if f["reason"] == "close_stop_loss")
    assert exit_fill["ts"] == 3 * HOUR and D(exit_fill["price"]) == 80
    assert result["round_trips"][0]["net_pnl"] == "-200"
    assert next(s for s in result["signals"] if s["indicators"].get("exit_reason"))["bar_ts"] == 2 * HOUR


def test_holding_limit_counts_completed_position_closes_and_fills_next_open():
    strategy = StrategyConfig(kind="buy_hold", max_holding_bars=2)
    result = run_professional_backtest(bars([100] * 6), HOUR, SPOT, ResearchConfig(strategy=strategy))
    assert result["round_trips"][0]["entry_ts"] == HOUR
    assert result["round_trips"][0]["exit_ts"] == 3 * HOUR
    assert result["round_trips"][0]["exit_reason"] == "maximum_holding_closes"


def test_loss_budget_caps_notional_after_round_trip_costs_and_ignores_caller_precision():
    strategy = StrategyConfig(kind="buy_hold", allocation=D(1), stop_loss_pct=D(5), risk_per_trade_pct=D(1))
    with localcontext(ACCOUNTING_CONTEXT):
        expected = D(10) / D(".053")
    with localcontext() as context:
        context.prec = 6
        assert risk_notional(D(1000), strategy, D(10), D(5)) == expected
    result = run_professional_backtest(
        bars([100] * 4),
        HOUR,
        SPOT,
        ResearchConfig(strategy=strategy, initial_cash=D(1000), fee_bps=D(10), slippage_bps=D(5)),
    )
    notional = D(result["fills"][0]["notional"])
    assert D(180) < notional <= expected
    assert risk_notional(D(1000), replace(strategy, risk_per_trade_pct=D(0)), 10, 5) is None
