"""Conservative admission estimate; a budget, not a measured RSS guarantee."""

from collections.abc import Sequence
from decimal import Decimal

from .catalog import CATALOG_BARS
from .engine import EngineError, StrategyConfig
from .pro_research import MAX_FOLDS, _bounded_work, _plan_options, _strategy_candidates
from .strategy_program import ProStrategyInput, strategy_complexity


def estimate_research_memory(config, dataset, start, end):
    interval = CATALOG_BARS[dataset["bar"]]
    selected = (end - start) // interval
    bars = selected + min(2000, (start - dataset["start"]) // interval)
    mode, plan = config["mode"], _plan_options(config.get("options"))
    if mode not in {"single", "grid", "cost_stress", "train_test", "walk_forward"}:
        raise EngineError("unsupported research mode")
    strategy = StrategyConfig(**ProStrategyInput.model_validate(config["strategy"]).model_dump())
    candidate_configs = _strategy_candidates(strategy, plan.grid)
    candidates = len(candidate_configs)
    cases = candidates
    if mode == "single" and (plan.grid or plan.fee_bps or plan.slippage_bps):
        raise EngineError("single mode does not consume grid or cost dimensions")
    if mode != "cost_stress" and (plan.fee_bps or plan.slippage_bps):
        raise EngineError("cost dimensions require cost_stress mode")
    if mode == "cost_stress":
        if plan.grid:
            raise EngineError("cost_stress evaluates a fixed strategy")
        cases = 1
        for values in (plan.fee_bps, plan.slippage_bps):
            if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
                raise EngineError("cost dimensions must be sequences")
            parsed = [Decimal(str(value)) for value in values]
            if any(not v.is_finite() or not 0 <= v <= 100 for v in parsed) or len(set(parsed)) != len(parsed):
                raise EngineError("cost dimensions require distinct finite values between 0 and 100 bps")
            cases *= max(1, len(parsed))
        if cases > 25:
            raise EngineError("cost stress is limited to 25 cells")
    if mode in {"train_test", "walk_forward"}:
        train = plan.train_bars or int(selected * plan.train_fraction)
        purge = plan.purge_bars
        test = plan.test_bars or (
            selected - train - purge
            if mode == "train_test"
            else max(2, int(selected * (1 - plan.train_fraction)))
        )
        step = plan.step_bars or test
        folds = 1 if mode == "train_test" else max(0, 1 + (selected - train - purge - test) // max(1, step))
        if train < 2 or test < 2 or train + purge + test > selected or not folds:
            raise EngineError("training, purge and test windows do not fit the selected range")
        if mode == "walk_forward" and step < test:
            raise EngineError("walk-forward test windows must not overlap")
        if folds > MAX_FOLDS:
            raise EngineError(f"walk-forward is limited to {MAX_FOLDS} folds")
        cases = (candidates + 1) * folds
    _bounded_work(cases * max(strategy_complexity(c) for c in candidate_configs), bars)
    estimate = bars * 1200 + cases * selected * 6000
    return {
        "estimated_peak_bytes": estimate,
        "bar_cases": cases * selected,
        "input_bars": bars,
        "cases": cases,
        "model": "conservative Python object + financial-table admission estimate; actual RSS must be measured independently",
    }
