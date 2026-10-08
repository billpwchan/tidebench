"""Independent formula checks, causal boundaries, restart and replay contracts."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from tidebench.engine import ACCOUNTING_CONTEXT, Candle, EngineError, Instrument, StrategyConfig
from tidebench.pro_research import (
    ResearchConfig,
    ResearchPlanConfig,
    _DecisionState,
    replay_research_snapshot,
    run_research_plan,
)
from tidebench.strategy_models import CausalStrategyModel
from tidebench.strategy_program import ProStrategyInput

D = Decimal
HOUR = 3_600_000


def config(kind, **kwargs):
    return StrategyConfig(
        **ProStrategyInput(
            kind=kind,
            momentum_horizons=[2, 3],
            vol_window=2,
            window=4,
            reversion_trend_window=4,
            max_bar_vol_pct=0,
            **kwargs,
        ).model_dump()
    )


def bars():
    # Alternating noise, persistent repricing and a crash; no profitable fixture assumption.
    prices = (
        [100 + i % 3 for i in range(40)]
        + [100 + i * 2 + i % 3 for i in range(40)]
        + [180 - i * 3 + i % 4 for i in range(40)]
    )
    return [Candle(i * HOUR, D(p), D(p + 2), D(p - 2), D(p), D(1)) for i, p in enumerate(prices)]


def test_momentum_scores_use_exact_return_lags_and_population_return_volatility():
    with localcontext(ACCOUNTING_CONTEXT):
        state = CausalStrategyModel(config("ts_momentum", momentum_entry=0))
        values = [D(100), D(110), D(100), D(110), D(90), D(100)]
        for p in values:
            signal, features = state.on_close(p)
        returns = [D(90) / 110 - 1, D(100) / 90 - 1]
        mean = sum(returns) / 2
        sigma = (sum((x - mean) ** 2 for x in returns) / 2).sqrt()
        assert D(features["bar_vol_pct"]) == sigma * 100
        assert D(features["momentum_2"]) == (D(100) / 110 - 1) / (sigma * D(2).sqrt())
        assert D(features["momentum_3"]) == 0
        assert signal == 0  # One negative and one zero horizon cannot imply consensus.


def test_reversion_center_excludes_current_close_and_guard_flattens_structural_moves():
    with localcontext(ACCOUNTING_CONTEXT):
        state = CausalStrategyModel(config("regime_reversion", efficiency_max=1))
        for p in [100, 101, 99, 100, 101, 95]:
            signal, features = state.on_close(D(p))
        prior = [D(101), D(99), D(100), D(101)]
        center = sum(prior) / 4
        deviation = (sum((p - center) ** 2 for p in prior) / 4).sqrt()
        assert D(features["prior_mean"]) == center
        assert D(features["prior_zscore"]) == (95 - center) / deviation
        assert signal == 1
        guarded = CausalStrategyModel(config("regime_reversion", efficiency_max=D(".3")))
        for p in [100, 101, 99, 100, 101, 95]:
            signal, features = guarded.on_close(D(p))
        assert signal == 0 and features["reason"] == "trend_guard"


@pytest.mark.parametrize("kind", ["ts_momentum", "regime_reversion"])
def test_future_perturbation_chunk_restart_and_direction_are_consistent(kind):
    strategy = config(kind)
    full, part = (
        _DecisionState(ResearchConfig(strategy=strategy, direction="long_short")),
        _DecisionState(ResearchConfig(strategy=strategy, direction="long_short")),
    )
    with localcontext(ACCOUNTING_CONTEXT):
        expected = [full.on_bar(c) for c in bars()]
        observed = [part.on_bar(c) for c in bars()[:53]]
        restarted = _DecisionState(part.config)
        restarted.restore(part.snapshot())
        observed += [restarted.on_bar(c) for c in bars()[53:]]
        assert observed == expected
        altered = bars()[:70] + [
            replace(c, open=c.open * 2, high=c.high * 2, low=c.low * 2, close=c.close * 2)
            for c in bars()[70:]
        ]
        state = _DecisionState(part.config)
        assert [state.on_bar(c) for c in altered][:70] == expected[:70]
        long = _DecisionState(ResearchConfig(strategy=strategy))
        assert all(long.on_bar(c)[0] != -1 for c in bars())
    with pytest.raises(EngineError):
        restarted.restore(
            {
                "schema_version": 2,
                "fast": [],
                "slow": [],
                "window": [],
                "fast_sum": "0",
                "slow_sum": "0",
                "gain": "0",
                "loss": "0",
                "atr": "0",
                "previous": None,
                "changes": 0,
                "atr_count": 0,
            }
        )


def test_degenerate_and_shock_windows_do_not_create_infinite_forecasts():
    with localcontext(ACCOUNTING_CONTEXT):
        s = CausalStrategyModel(config("ts_momentum"))
        for _ in range(5):
            signal, features = s.on_close(D(100))
        assert signal == 0 and features["reason"] == "degenerate_volatility"
        s = CausalStrategyModel(replace(config("ts_momentum"), max_bar_vol_pct=D(1)))
        for p in [100, 101, 100, 102, 140]:
            signal, features = s.on_close(D(p))
        assert signal == 0 and features["reason"] == "volatility_guard"


@pytest.mark.parametrize("kind", ["ts_momentum", "regime_reversion"])
def test_models_trade_only_after_signal_and_snapshot_replay_is_identical(kind):
    strategy = config(kind)
    instrument = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".00001"), D(".00001"))
    result = run_research_plan(bars(), HOUR, instrument, ResearchConfig(strategy=strategy))["result"]
    assert result["fills"]
    signals = {s["id"]: s for s in result["signals"]}
    assert all(
        fill["phase"] == "next_open" and fill["ts"] >= signals[fill["signal_id"]]["ts"]
        for fill in result["fills"]
    )
    replay = replay_research_snapshot(result["input_snapshot"])
    assert replay["input_hash"] == result["input_hash"]
    assert replay["metrics"] == result["metrics"]
    # Decimal strings in a sensitivity grid must not leak into signal arithmetic.
    key = "momentum_entry" if kind == "ts_momentum" else "efficiency_max"
    study = run_research_plan(
        bars(),
        HOUR,
        instrument,
        ResearchConfig(strategy=strategy),
        mode="train_test",
        options=ResearchPlanConfig(grid={key: ["0.2", "0.5"]}),
    )
    assert study["folds"]


@pytest.mark.parametrize("horizons", [[3, 2], [2, 2], [2, 401], [2], [2, 3, 4, 5, 6]])
def test_horizon_admission_is_bounded_and_unambiguous(horizons):
    with pytest.raises(ValueError):
        ProStrategyInput(kind="ts_momentum", momentum_horizons=horizons)
