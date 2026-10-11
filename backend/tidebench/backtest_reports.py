"""Versioned read-only views of verified, immutable research evidence."""

from statistics import median

from .platform import PlatformError


def report_candidate(plan, selected=""):
    candidates = []
    if plan.get("result"):
        candidates.append(("single", "development", plan["result"]))
    for experiment in plan.get("experiments", []):
        scope = "cost_sensitivity" if plan.get("mode") == "cost_stress" else "development"
        candidates.append((experiment["id"], scope, experiment["result"]))
    for fold in plan.get("folds", []):
        candidates.append((fold["id"] + ":test", "out_of_sample_window", fold["test_result"]))
        for experiment in fold.get("training_experiments", []):
            candidates.append((fold["id"] + ":" + experiment["id"], "training", experiment["result"]))
    if not candidates and plan.get("metrics"):
        candidates.append(("single", "development", plan))
    candidate = (
        next((item for item in candidates if item[0] == selected), None)
        if selected
        else next(iter(candidates), None)
    )
    if candidate is None:
        raise PlatformError(
            "research_variant_not_found", "Choose an existing result candidate or test fold.", 404
        )
    return candidate


def validation_view(plan):
    """Fold statistics are descriptive: separate accounts are never compounded."""
    rows = []
    for fold in plan.get("folds", []):
        metrics = fold["test_result"]["metrics"]
        strategy_return, benchmark = metrics.get("total_return_pct"), metrics.get("benchmark_return_pct")
        rows.append(
            {
                "id": fold["id"],
                "train_start_ts": fold["train_start_ts"],
                "train_end_ts": fold["train_end_ts"],
                "test_start_ts": fold["test_start_ts"],
                "test_end_ts": fold["test_end_ts"],
                "return_pct": strategy_return,
                "benchmark_return_pct": benchmark,
                "return_difference_pp": strategy_return - benchmark
                if strategy_return is not None and benchmark is not None
                else None,
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "complete_utc_days": metrics.get("complete_utc_days"),
                "round_trips": metrics.get("round_trips"),
                "training_candidates": len(fold.get("training_experiments", [])),
                "selected_strategy": fold.get("selected_strategy"),
            }
        )
    returns = [row["return_pct"] for row in rows if row["return_pct"] is not None]
    differences = [row["return_difference_pp"] for row in rows if row["return_difference_pp"] is not None]
    return {
        "mode": plan.get("mode"),
        "folds": rows,
        "test_fold_count": len(rows),
        "positive_test_folds": sum(value > 0 for value in returns),
        "benchmark_outperforming_folds": sum(value > 0 for value in differences),
        "median_return_pct": median(returns) if returns else None,
        "median_return_difference_pp": median(differences) if differences else None,
        "candidate_evaluations": sum(row["training_candidates"] for row in rows)
        + len(plan.get("experiments", [])),
        "unused_tail_bars": plan.get("unused_tail_bars"),
        "definition": "Each test fold starts flat with its own account. Fold returns are descriptive and are not a continuous investable equity curve. Out-of-sample windows do not prove untouched holdout independence; review the project's recorded holdout usage.",
    }
