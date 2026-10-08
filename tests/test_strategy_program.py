"""Bounded programs, causal indicators and checkpoint differential checks."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from tidebench.engine import ACCOUNTING_CONTEXT, Candle, Instrument, StrategyConfig
from tidebench.pro_research import ResearchConfig, _DecisionState, directional_signal, run_research_plan
from tidebench.store import encode
from tidebench.strategy_program import ProStrategyInput

D = Decimal
HOUR = 3_600_000


def rule(feature, op, other, signal, tag):
    return {
        "conditions": [{"left": {"feature": feature}, "op": op, "right": other}],
        "signal": signal,
        "tag": tag,
    }


def candles():
    values = [100, 101, 102, 99, 95, 94, 97, 104, 108, 103, 98, 91, 96, 101, 107, 102, 96, 99, 104, 109] * 4
    return [Candle(i * HOUR, D(v), D(v + 2), D(v - 2), D(v), D(1)) for i, v in enumerate(values)]


def strategy():
    model = ProStrategyInput(
        kind="program",
        fast=2,
        slow=3,
        window=5,
        atr_period=3,
        rules=[
            rule("fast_sma", "gt", {"feature": "slow_sma"}, 1, "trend-long"),
            rule("fast_sma", "le", {"feature": "slow_sma"}, 0, "trend-exit"),
        ],
    )
    return StrategyConfig(**model.model_dump())


def test_ordered_rules_never_match_unavailable_features_and_have_explicit_exit():
    state = _DecisionState(ResearchConfig(strategy=strategy()))
    with localcontext(ACCOUNTING_CONTEXT):
        assert state.on_bar(candles()[0])[0] is None
        assert state.on_bar(candles()[1])[0] is None
        signal, features = state.on_bar(candles()[2])
        assert signal == 1 and features["tag"] == "trend-long"
        signal, features = state.on_bar(candles()[3])
        assert signal == 0 and features["tag"] == "trend-exit"
        assert features["atr"] and features["volume"] == "1"


@pytest.mark.parametrize(
    "kind", ["sma_cross", "rsi_reversion", "close_breakout", "zscore_reversion", "program"]
)
def test_chunk_and_restart_match_one_continuous_indicator_path(kind):
    config = ResearchConfig(strategy=replace(strategy(), kind=kind), direction="long_short")
    full, part = _DecisionState(config), _DecisionState(config)
    data = candles()
    with localcontext(ACCOUNTING_CONTEXT):
        expected = [full.on_bar(c) for c in data]
        observed = [part.on_bar(c) for c in data[:31]]
        restored = _DecisionState(config)
        restored.restore(encode(part.snapshot()))
        observed.extend(restored.on_bar(c) for c in data[31:])
    assert observed == expected


@pytest.mark.parametrize("kind", ["close_breakout", "zscore_reversion", "program"])
def test_future_price_perturbation_cannot_change_prior_signals(kind):
    data = candles()
    changed = data[:40] + [
        replace(c, open=c.open * 2, high=c.high * 2, low=c.low * 2, close=c.close * 2) for c in data[40:]
    ]
    config = ResearchConfig(strategy=replace(strategy(), kind=kind))
    a, b = _DecisionState(config), _DecisionState(config)
    with localcontext(ACCOUNTING_CONTEXT):
        first = [a.on_bar(c) for c in data]
        second = [b.on_bar(c) for c in changed]
    assert first[:40] == second[:40]
    assert directional_signal(data[:40], config.strategy) == first[39][0]


def test_malformed_or_unbounded_programs_fail_before_execution():
    valid = strategy().__dict__
    for replacement in (
        {"rules": []},
        {"rules": [rule("future_close", "gt", {"constant": 0}, 1, "oracle")]},
        {"rules": [rule("close", "eval", {"constant": 0}, 1, "unsafe")]},
        {"rules": [rule("close", "gt", {"feature": "rsi", "constant": 1}, 1, "ambiguous")]},
    ):
        with pytest.raises(ValueError):
            ProStrategyInput.model_validate(valid | replacement)


def test_program_next_open_orders_and_work_budget():
    data = candles()
    spot = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".00001"), D(".00001"))
    config = ResearchConfig(strategy=strategy())
    result = run_research_plan(data, HOUR, spot, config)["result"]
    assert result["fills"]
    signals = {s["id"]: s for s in result["signals"]}
    for fill in result["fills"]:
        assert fill["ts"] >= signals[fill["signal_id"]]["ts"]
        assert fill["phase"] == "next_open"


def test_shipped_recipes_validate_with_explicit_risk_and_failure_hypotheses():
    import json
    from pathlib import Path

    from tidebench.strategy_registry import StrategyDefinition

    recipes = json.loads((Path(__file__).parents[1] / "examples/strategies.json").read_text())
    assert len({r["id"] for r in recipes}) == len(recipes) == 7
    for recipe in recipes:
        definition = StrategyDefinition.model_validate(recipe["definition"])
        assert definition.strategy.stop_loss_pct > 0
        assert definition.strategy.risk_per_trade_pct == Decimal(".5")
        assert "Reject" in recipe["hypothesis"] or "reject" in recipe["hypothesis"]
