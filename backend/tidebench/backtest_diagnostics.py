"""Versioned, descriptive diagnostics of one recorded backtest path.

This module reads actual UTC close valuations and completed position ledgers. It
does not infer out-of-sample validity, significance, factor alpha or capacity.
Money stays in Decimal/string form; chart ratios are finite floats or null.
The fixed engine context makes output independent of the caller's context.

Methodology references (no claim of GIPS compliance):
https://www.gipsstandards.org/standards/gips-standards-for-asset-owners/gips-standards-handbook-for-asset-owners/
https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios
https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
from math import ceil, isfinite
from typing import Any

from .engine import ACCOUNTING_CONTEXT, DAY_MS, ONE, ZERO, EngineError, _accounting_value, _decimal

DIAGNOSTICS_VERSION = "backtest-diagnostics-1"
MAX_CALENDAR_ROWS = 400
MAX_ROLLING_POINTS = 400
MAX_DRAWDOWN_EPISODES = 10
MIN_DAILY_RETURNS = 30
ROLLING_WINDOW_DAYS = 30
MIN_TAIL_RETURNS = 100
RISK_DEVIATION_FLOOR = Decimal("1e-12")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _amount(value: Any, name: str) -> Decimal:
    # Floats are deliberately excluded: financial precision cannot be recovered.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise EngineError(f"diagnostics {name} requires an exact decimal string or Decimal")
    try:
        amount = Decimal(value)
    except (ValueError, DecimalException) as exc:
        raise EngineError(f"diagnostics {name} must be finite") from exc
    _decimal(amount, name)
    _accounting_value(amount, name)
    if amount and amount.as_tuple().exponent < ACCOUNTING_CONTEXT.Emin:
        raise EngineError(f"diagnostics {name} exceeds the supported decimal exponent")
    return amount


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == ZERO:
        return "0"
    # Avoid allocating a million-character fractional string for a tiny amount.
    if value.adjusted() < -100:
        return str(value)
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _finite(value: Decimal | int) -> float | None:
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _percent(numerator: Decimal | int, denominator: Decimal | int) -> float | None:
    return _finite(Decimal(numerator) / Decimal(denominator) * 100) if denominator > 0 else None


def _timestamp(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EngineError(f"diagnostics {name} must be a nonnegative integer timestamp")
    try:
        EPOCH + timedelta(milliseconds=value)
    except OverflowError as exc:
        raise EngineError(f"diagnostics {name} is outside the supported UTC calendar") from exc
    return value


def _records(value: Any, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EngineError(f"diagnostics {name} must be a sequence")
    if any(not isinstance(row, Mapping) for row in value):
        raise EngineError(f"diagnostics {name} must contain records")
    return list(value)


def _quantile(ordered: list[Decimal], probability: Decimal) -> Decimal | None:
    """Linear interpolation at (n-1)*p (Hyndman-Fan type 7)."""
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _histogram(values: list[Decimal], *, money: bool) -> list[dict[str, Any]]:
    if not values:
        return []
    low, high = min(values), max(values)
    convert = _money if money else lambda value: _finite(value * 100)
    if low == high:
        return [
            {"lower": convert(low), "upper": convert(high), "count": len(values), "upper_inclusive": True}
        ]
    count = min(10, len(values))
    width = (high - low) / count
    counts = [0] * count
    for value in values:
        counts[min(int((value - low) / width), count - 1)] += 1
    return [
        {
            "lower": convert(low + width * index),
            "upper": convert(high if index == count - 1 else low + width * (index + 1)),
            "count": frequency,
            "upper_inclusive": index == count - 1,
        }
        for index, frequency in enumerate(counts)
    ]


def _period_boundaries(timestamp: int, frequency: str) -> tuple[int, int, str]:
    date = EPOCH + timedelta(milliseconds=timestamp)
    if frequency == "daily":
        begin = date.replace(hour=0, minute=0, second=0, microsecond=0)
        finish = begin + timedelta(days=1)
        label = begin.strftime("%Y-%m-%d")
    elif frequency == "monthly":
        begin = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        finish = (
            begin.replace(year=begin.year + 1, month=1)
            if begin.month == 12
            else begin.replace(month=begin.month + 1)
        )
        label = begin.strftime("%Y-%m")
    else:
        begin = date.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        finish = begin.replace(year=begin.year + 1)
        label = str(begin.year)
    return (
        int((begin - EPOCH) // timedelta(milliseconds=1)),
        int((finish - EPOCH) // timedelta(milliseconds=1)),
        label,
    )


def _calendar(
    observations: list[tuple[int, Decimal, Decimal | None]], interval_ms: int, start: int, end: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamps = [row[0] for row in observations]
    at = {ts: (equity, benchmark) for ts, equity, benchmark in observations}
    gap_prefix, nonpositive_prefix = [0], [0]
    for index, (ts, equity, _) in enumerate(observations):
        gap_prefix.append(gap_prefix[-1] + int(index > 0 and ts - timestamps[index - 1] != interval_ms))
        nonpositive_prefix.append(nonpositive_prefix[-1] + int(equity <= ZERO))
    report: dict[str, Any] = {"timezone": "UTC", "maximum_rows_per_frequency": MAX_CALENDAR_ROWS}
    daily: list[dict[str, Any]] = []
    for frequency in ("daily", "monthly", "yearly"):
        rows: deque[dict[str, Any]] = deque(maxlen=MAX_CALENDAR_ROWS)
        count = complete_count = 0
        cursor = start
        while cursor < end:
            begin, finish, label = _period_boundaries(cursor, frequency)
            observed_begin, observed_end = max(begin, start), min(finish, end)
            left, right = bisect_left(timestamps, observed_begin), bisect_right(timestamps, observed_end)
            opening, closing = at.get(observed_begin), at.get(observed_end)
            boundary_present = opening is not None and closing is not None
            contiguous = boundary_present and gap_prefix[right] - gap_prefix[left + 1] == 0
            complete = observed_begin == begin and observed_end == finish and bool(contiguous)
            reason = None
            raw_return = benchmark_return = None
            if not boundary_present:
                reason = "missing_boundary_observation"
            elif not contiguous:
                reason = "missing_or_irregular_bar_observations"
            elif opening[0] <= ZERO:
                reason = "nonpositive_opening_equity"
            else:
                raw_return = closing[0] / opening[0] - ONE
                if closing[0] <= ZERO:
                    reason = "nonpositive_closing_equity"
                elif not complete:
                    reason = "partial_calendar_period"
            if (
                boundary_present
                and contiguous
                and opening[1] is not None
                and closing[1] is not None
                and opening[1] > ZERO
            ):
                benchmark_return = closing[1] / opening[1] - ONE
            row = {
                "period": label,
                "start_ts": begin,
                "end_ts": finish,
                "observed_start_ts": observed_begin,
                "observed_end_ts": observed_end,
                "complete": complete,
                "return_pct": _finite(raw_return * 100) if raw_return is not None else None,
                "benchmark_return_pct": _finite(benchmark_return * 100)
                if benchmark_return is not None
                else None,
                "active_return_pct": _finite((raw_return - benchmark_return) * 100)
                if raw_return is not None and benchmark_return is not None
                else None,
                "reason": reason,
            }
            if raw_return is not None and row["return_pct"] is None:
                row["reason"] = "numeric_range"
            rows.append(row)
            count += 1
            complete_count += int(complete)
            if frequency == "daily":
                daily.append(
                    {
                        "period": label,
                        "complete": complete,
                        "observed_start_ts": observed_begin,
                        "observed_end_ts": observed_end,
                        "raw_return": raw_return,
                        "opening": opening[0] if opening else None,
                        "closing": closing[0] if closing else None,
                        "positive_path": nonpositive_prefix[right] - nonpositive_prefix[left] == 0,
                    }
                )
            cursor = finish
        report[frequency] = list(rows)
        report[f"{frequency}_count"] = count
        report[f"{frequency}_complete_count"] = complete_count
        report[f"{frequency}_omitted_count"] = max(0, count - MAX_CALENDAR_ROWS)
    report["definition"] = (
        "Endpoint equity ratios without external cash flows; exact UTC boundaries; incomplete or missing periods are labeled. Active return is strategy minus benchmark return, not alpha. Retained rows are the most recent periods."
    )
    return report, daily


def _drawdowns(observations: list[tuple[int, Decimal, Decimal | None]]) -> dict[str, Any]:
    peak_ts, peak, _ = observations[0]
    episode = None
    deepest: list[dict[str, Any]] = []
    longest = open_episode = None
    count = recovered = underwater = 0
    maximum_depth, maximum_amount = ZERO, ZERO

    def finish(current, end_ts, recovery_ts):
        nonlocal longest, maximum_depth, maximum_amount
        depth = (current["peak"] - current["trough"]) / current["peak"] * 100
        amount = current["peak"] - current["trough"]
        item = {
            "id": current["id"],
            "peak_ts": current["peak_ts"],
            "trough_ts": current["trough_ts"],
            "end_ts": end_ts,
            "recovery_ts": recovery_ts,
            "recovered": recovery_ts is not None,
            "right_censored": recovery_ts is None,
            "peak_equity": _money(current["peak"]),
            "trough_equity": _money(current["trough"]),
            "depth_pct": _finite(depth),
            "depth_amount": _money(amount),
            "depth_reason": "numeric_range" if _finite(depth) is None else None,
            "duration_ms": end_ts - current["peak_ts"],
            "decline_duration_ms": current["trough_ts"] - current["peak_ts"],
            "recovery_duration_ms": recovery_ts - current["trough_ts"] if recovery_ts is not None else None,
            "observed_recovery_duration_ms": end_ts - current["trough_ts"],
            "recovery_required_pct": _percent(current["peak"] - current["trough"], current["trough"]),
        }
        maximum_depth, maximum_amount = max(maximum_depth, depth), max(maximum_amount, amount)
        if longest is None or item["duration_ms"] > longest["duration_ms"]:
            longest = item
        deepest.append(item)
        # Compare exact monetary ratios instead of potentially saturated floats.
        deepest.sort(
            key=lambda row: (-Decimal(row["depth_amount"]) / Decimal(row["peak_equity"]), row["peak_ts"])
        )
        del deepest[MAX_DRAWDOWN_EPISODES:]
        return item

    for ts, value, _ in observations[1:]:
        if value >= peak:
            if episode is not None:
                finish(episode, ts, ts)
                recovered += 1
                episode = None
            peak_ts, peak = ts, value
        else:
            underwater += 1
            if episode is None:
                count += 1
                episode = {
                    "id": f"drawdown-{count}",
                    "peak_ts": peak_ts,
                    "peak": peak,
                    "trough_ts": ts,
                    "trough": value,
                }
            elif value < episode["trough"]:
                episode["trough_ts"], episode["trough"] = ts, value
    if episode is not None:
        open_episode = finish(episode, observations[-1][0], None)
    return {
        "max_depth_pct": _finite(maximum_depth),
        "max_depth_amount": _money(maximum_amount),
        "max_depth_reason": "numeric_range" if _finite(maximum_depth) is None else None,
        "max_depth_amount_definition": "Largest absolute peak-to-trough equity loss; it may occur in a different episode from the largest percentage drawdown.",
        "episode_count": count,
        "recovered_count": recovered,
        "open_episode": open_episode,
        "longest_duration_ms": longest["duration_ms"] if longest else 0,
        "longest_episode": longest,
        "underwater_observation_pct": _percent(underwater, len(observations) - 1),
        "episodes": deepest,
        "omitted_episode_count": max(0, count - MAX_DRAWDOWN_EPISODES),
        "definition": "Positive depth from the latest observed high-water mark, including initial cash. Duration runs from peak to first recovery at or above that peak, or sample end for right-censored episodes. Recovery-required return is measured from trough equity and is unavailable at a nonpositive trough. Bar-close observations cannot reconstruct intrabar drawdowns.",
    }


def _rolling_risk(daily: list[dict[str, Any]], nonpositive: bool) -> dict[str, Any]:
    valid_run = 0
    eligible = []
    for index, row in enumerate(daily):
        valid = row["complete"] and row["raw_return"] is not None and row["positive_path"]
        valid_run = valid_run + 1 if valid else 0
        if valid_run >= ROLLING_WINDOW_DAYS:
            eligible.append(index)
    reason = (
        "nonpositive_equity"
        if nonpositive
        else "insufficient_consecutive_complete_utc_days"
        if not eligible
        else None
    )
    if nonpositive:
        eligible = []
    selected = eligible
    if len(eligible) > MAX_ROLLING_POINTS:
        selected = [
            eligible[index * (len(eligible) - 1) // (MAX_ROLLING_POINTS - 1)]
            for index in range(MAX_ROLLING_POINTS)
        ]
    annualizer = Decimal(365).sqrt()
    points = []
    for index in selected:
        window = daily[index - ROLLING_WINDOW_DAYS + 1 : index + 1]
        returns = [row["raw_return"] for row in window]
        average = sum(returns, ZERO) / len(returns)
        variance = sum(((value - average) ** 2 for value in returns), ZERO) / (len(returns) - 1)
        deviation = variance.sqrt()
        downside = (sum((min(value, ZERO) ** 2 for value in returns), ZERO) / len(returns)).sqrt()
        volatility = _finite(deviation * annualizer * 100)
        sharpe = _finite(average / deviation * annualizer) if deviation > RISK_DEVIATION_FLOOR else None
        sortino = _finite(average / downside * annualizer) if downside > RISK_DEVIATION_FLOOR else None
        points.append(
            {
                "ts": window[-1]["observed_end_ts"],
                "start_ts": window[0]["observed_start_ts"],
                "return_pct": _finite((window[-1]["closing"] / window[0]["opening"] - ONE) * 100),
                "annualized_volatility_pct": volatility,
                "sharpe": sharpe,
                "sortino": sortino,
                "volatility_reason": "numeric_range" if volatility is None else None,
                "sharpe_reason": "zero_or_negligible_variance"
                if deviation <= RISK_DEVIATION_FLOOR
                else "numeric_range"
                if sharpe is None
                else None,
                "sortino_reason": "no_or_negligible_downside_variation"
                if downside <= RISK_DEVIATION_FLOOR
                else "numeric_range"
                if sortino is None
                else None,
            }
        )
    return {
        "window_days": ROLLING_WINDOW_DAYS,
        "minimum_returns": MIN_DAILY_RETURNS,
        "eligible_window_count": len(eligible),
        "omitted_point_count": len(eligible) - len(points),
        "maximum_points": MAX_ROLLING_POINTS,
        "points": points,
        "reason": reason,
        "deviation_floor": _money(RISK_DEVIATION_FLOOR),
        "definition": "30 consecutive complete positive-equity UTC daily returns; sample standard deviation (n−1), 365-day square-root annualization, zero risk-free and downside target. Sortino downside deviation uses all 30 returns. Overlapping windows are descriptive, not independent evidence. Points are evenly sampled across eligible windows.",
    }


def _daily_distribution(daily: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in daily if row["complete"] and row["raw_return"] is not None]
    ordered = sorted(row["raw_return"] for row in rows)
    count = len(ordered)
    tail_count = ceil(count / 20) if count >= MIN_TAIL_RETURNS else 0
    best = max(rows, key=lambda row: row["raw_return"]) if rows else None
    worst = min(rows, key=lambda row: row["raw_return"]) if rows else None
    enough = count >= MIN_DAILY_RETURNS
    return {
        "count": count,
        "best_day": best["period"] if best else None,
        "worst_day": worst["period"] if worst else None,
        "best_return_pct": _finite(ordered[-1] * 100) if ordered else None,
        "worst_return_pct": _finite(ordered[0] * 100) if ordered else None,
        "median_return_pct": _finite(_quantile(ordered, Decimal(".5")) * 100) if ordered else None,
        "p05_return_pct": _finite(_quantile(ordered, Decimal(".05")) * 100) if enough else None,
        "p95_return_pct": _finite(_quantile(ordered, Decimal(".95")) * 100) if enough else None,
        "quantile_reason": None if enough else "fewer_than_30_complete_daily_returns",
        "empirical_lower_tail_mean_pct": _finite(sum(ordered[:tail_count], ZERO) / tail_count * 100)
        if tail_count
        else None,
        "lower_tail_count": tail_count,
        "tail_reason": None if tail_count else "fewer_than_100_complete_daily_returns",
        "histogram": _histogram(ordered, money=False),
        "definition": "Observed complete UTC daily equity returns. Percentiles use linear interpolation at (n−1)×p. Lower-tail mean uses the worst ceil(5%×n) observations with at least 100 days; it is historical description, not a forecast of VaR or expected shortfall.",
    }


def _closed_trades(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if "round_trips" in result:
        rows = _records(result["round_trips"], "round_trips")
        return [dict(row) for row in rows], "recorded_round_trips"
    assumptions = result.get("assumptions", {})
    if not isinstance(assumptions, Mapping) or assumptions.get("market") != "long_only_spot":
        return [], "unavailable"
    # The classic engine holds one spot lot and sells its entire quantity.
    closed, entry = [], None
    previous = -1
    for fill in _records(result.get("trades", []), "trades"):
        ts = _timestamp(fill.get("ts"), "trade timestamp")
        if ts < previous:
            raise EngineError("diagnostics spot fills must be in recorded chronological order")
        previous = ts
        quantity = _amount(fill.get("quantity"), "fill quantity")
        price, fee = _amount(fill.get("price"), "fill price"), _amount(fill.get("fee"), "fill fee")
        if quantity <= ZERO or price <= ZERO or fee < ZERO:
            raise EngineError("diagnostics spot fill quantity/price/fee violates the ledger contract")
        if fill.get("side") == "buy" and entry is None:
            entry = (ts, quantity, price, fee)
        elif fill.get("side") == "sell" and entry is not None and quantity == entry[1]:
            gross = quantity * (price - entry[2])
            closed.append(
                {
                    "entry_ts": entry[0],
                    "exit_ts": ts,
                    "direction": "long",
                    "exit_reason": "signal_exit",
                    "net_pnl": _money(quantity * price - fee - (entry[1] * entry[2] + entry[3])),
                    "gross_pnl": _money(gross),
                    "fees": _money(entry[3] + fee),
                    "funding_payment": "0",
                }
            )
            entry = None
        else:
            raise EngineError("diagnostics cannot reconstruct closed trades from a non-classic spot ledger")
    return closed, "paired_classic_full_spot_exits"


def _trade_statistics(
    rows: list[dict[str, Any]], source: str, start: int, end: int
) -> tuple[dict[str, Any], Decimal]:
    pnl, holding = [], []
    direction: dict[str, list[Decimal]] = defaultdict(list)
    exits: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        value = _amount(row.get("net_pnl"), "closed trade net PnL")
        entry, exit_ts = (
            _timestamp(row.get("entry_ts"), "entry timestamp"),
            _timestamp(row.get("exit_ts"), "exit timestamp"),
        )
        if not start <= entry <= exit_ts <= end:
            raise EngineError("diagnostics completed trade falls outside the observed window")
        pnl.append(value)
        holding.append(Decimal(exit_ts - entry))
        direction[str(row.get("direction", "unknown"))].append(value)
        exits[str(row.get("exit_reason", "unknown"))].append(value)
    ordered, winners, losers = (
        sorted(pnl),
        sorted((value for value in pnl if value > ZERO), reverse=True),
        [value for value in pnl if value < ZERO],
    )
    profit, loss = sum(winners, ZERO), -sum(losers, ZERO)
    total = sum(pnl, ZERO)
    average_winner = profit / len(winners) if winners else None
    average_loser = -loss / len(losers) if losers else None

    def breakdown(values):
        return [
            {"label": key, "count": len(items), "net_pnl": _money(sum(items, ZERO))}
            for key, items in sorted(values.items())
        ]

    return {
        "available": source != "unavailable",
        "source": source,
        "count": len(pnl),
        "wins": len(winners),
        "losses": len(losers),
        "breakeven": len(pnl) - len(winners) - len(losers),
        "net_pnl": _money(total) if source != "unavailable" else None,
        "gross_profit": _money(profit) if source != "unavailable" else None,
        "gross_loss": _money(loss) if source != "unavailable" else None,
        "win_rate_pct": _percent(len(winners), len(pnl)),
        "profit_factor": _finite(profit / loss) if loss > ZERO else None,
        "profit_factor_reason": "no_completed_losing_trades" if loss == ZERO else None,
        "expectancy": _money(total / len(pnl)) if pnl else None,
        "mean_winner": _money(average_winner),
        "mean_loser": _money(average_loser),
        "payoff_ratio": _finite(average_winner / -average_loser)
        if average_winner is not None and average_loser is not None
        else None,
        "median_pnl": _money(_quantile(ordered, Decimal(".5"))),
        "p05_pnl": _money(_quantile(ordered, Decimal(".05"))) if len(pnl) >= 30 else None,
        "p95_pnl": _money(_quantile(ordered, Decimal(".95"))) if len(pnl) >= 30 else None,
        "largest_winner": _money(winners[0]) if winners else None,
        "largest_loser": _money(min(losers)) if losers else None,
        "largest_winner_share_of_gross_profit_pct": _percent(winners[0], profit) if winners else None,
        "top_five_winner_share_of_gross_profit_pct": _percent(sum(winners[:5], ZERO), profit)
        if winners
        else None,
        "mean_holding_ms": _finite(sum(holding, ZERO) / len(holding)) if holding else None,
        "median_holding_ms": _finite(_quantile(sorted(holding), Decimal(".5"))) if holding else None,
        "min_holding_ms": int(min(holding)) if holding else None,
        "max_holding_ms": int(max(holding)) if holding else None,
        "by_direction": breakdown(direction),
        "by_exit_reason": breakdown(exits),
        "histogram": _histogram(pnl, money=True),
        "sample_warning": "closed_trade_source_unavailable"
        if source == "unavailable"
        else "fewer_than_30_completed_trades"
        if len(pnl) < 30
        else None,
        "definition": "Completed position net PnL only, including both-side fees and signed funding where recorded. Open positions are excluded; expectancy is the arithmetic mean of realized trade PnL, not an investment forecast. Profit concentration divides winning-trade PnL by total positive trade PnL.",
    }, total


def _contribution(
    result: Mapping[str, Any],
    rows: list[dict[str, Any]],
    initial: Decimal,
    final: Decimal,
    closed_net: Decimal,
    source: str,
) -> dict[str, Any]:
    metrics = result.get("metrics", {})
    fees = _amount(metrics["fees_paid"], "recorded fees") if "fees_paid" in metrics else None
    if fees is not None and fees < ZERO:
        raise EngineError("diagnostics recorded fees must be nonnegative")
    funding = _amount(metrics["funding_paid"], "recorded funding") if "funding_paid" in metrics else None
    gross = (
        sum((_amount(row["gross_pnl"], "closed gross PnL") for row in rows), ZERO)
        if all("gross_pnl" in row for row in rows) and source != "unavailable"
        else None
    )
    fills = _records(result.get("fills", result.get("trades", [])), "fills")
    assumptions, snapshot = result.get("assumptions", {}), result.get("input_snapshot", {})
    quantity_unit = assumptions.get("quantity_unit") if isinstance(assumptions, Mapping) else None
    instrument = snapshot.get("instrument", {}) if isinstance(snapshot, Mapping) else {}
    base_multiplier = None
    if (
        quantity_unit == "contracts"
        and isinstance(instrument, Mapping)
        and "ct_val" in instrument
        and "ct_mult" in instrument
    ):
        base_multiplier = _amount(instrument["ct_val"], "contract value") * _amount(
            instrument["ct_mult"], "contract multiplier"
        )
    elif quantity_unit is not None and quantity_unit != "contracts":
        base_multiplier = ONE
    impact = None
    impact_reason = "fill_reference_prices_or_base_quantity_unavailable"
    if base_multiplier is not None and all(
        "reference_price" in fill and "quantity" in fill and fill.get("side") in {"buy", "sell"}
        for fill in fills
    ):
        impact = ZERO
        for fill in fills:
            quantity = _amount(fill["quantity"], "impact quantity") * base_multiplier
            price, reference = (
                _amount(fill["price"], "fill price"),
                _amount(fill["reference_price"], "fill reference price"),
            )
            impact += quantity * (price - reference) * (ONE if fill["side"] == "buy" else -ONE)
        impact_reason = None
    return {
        "total_equity_pnl": _money(final - initial),
        "closed_net_pnl": _money(closed_net) if source != "unavailable" else None,
        "closed_gross_pnl": _money(gross),
        "unclosed_pnl_residual": _money(final - initial - closed_net) if source != "unavailable" else None,
        "fees_paid": _money(fees),
        "funding_paid": _money(funding),
        "slippage_paid": None,
        "slippage_reason": "slippage_and_adverse_tick_rounding_not_separately_recorded",
        "execution_price_impact": _money(impact),
        "execution_price_impact_reason": impact_reason,
        "fees_definition": "Recorded charges across every fill, including entry fees on open positions and any combined liquidation allowance. Trading fees and liquidation allowance are not separated.",
        "funding_definition": "Recorded signed settlements across open and closed positions: positive is a payment, negative is a receipt; unavailable values remain null.",
        "execution_price_impact_definition": "Sum of base quantity × adverse difference between recorded fill and reference price, including tick rounding. This is a local modeled fill-price comparison, not the causal return effect of removing slippage; sizing, fees and strategy paths can change.",
        "residual_definition": "Total equity change less summed completed-trade net PnL. Includes open-position mark-to-market and its paid costs; finite-precision regrouping can leave a rounding residual. It is not pure unrealized PnL. Gross trade PnL already uses cost-adjusted execution prices.",
    }


def _exposure(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not all("quantity" in row for row in rows):
        return {"available": False, "reason": "bar_close_inventory_unavailable", "sampled_closes": len(rows)}
    quantities = [_amount(row["quantity"], "bar-close inventory") for row in rows]
    long_count, short_count = (
        sum(value > ZERO for value in quantities),
        sum(value < ZERO for value in quantities),
    )
    notionals, leverage = [], []
    excluded = 0
    if all("gross_notional" in row for row in rows):
        for row in rows:
            notional, equity = (
                _amount(row["gross_notional"], "gross notional"),
                _amount(row["equity"], "equity"),
            )
            if notional < ZERO:
                raise EngineError("diagnostics gross notional must be nonnegative")
            notionals.append(notional)
            if equity > ZERO:
                leverage.append(notional / equity)
            else:
                excluded += 1
    return {
        "available": True,
        "reason": None,
        "sampled_closes": len(rows),
        "long_pct": _percent(long_count, len(rows)),
        "short_pct": _percent(short_count, len(rows)),
        "flat_pct": _percent(len(rows) - long_count - short_count, len(rows)),
        "invested_pct": _percent(long_count + short_count, len(rows)),
        "mean_gross_notional": _money(sum(notionals, ZERO) / len(notionals)) if notionals else None,
        "max_gross_notional": _money(max(notionals)) if notionals else None,
        "mean_gross_leverage": _finite(sum(leverage, ZERO) / len(leverage)) if leverage else None,
        "max_gross_leverage": _finite(max(leverage)) if leverage else None,
        "excluded_nonpositive_equity_closes": excluded,
        "definition": "Fraction of observed bar closes with positive, negative or zero inventory, including flat closes. Gross leverage is recorded absolute notional / positive net equity. These snapshots do not measure intrabar exposure, portfolio diversification or capacity.",
    }


def compute_backtest_diagnostics(
    result: Mapping[str, Any],
    interval_ms: int,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    initial_cash: Decimal | str | int | None = None,
) -> dict[str, Any]:
    """Derive a bounded JSON-safe report without modifying a saved engine result.

    Supply the complete raw candidate result, not a chart/downsample projection.
    ``equity`` needs ordered ``ts``/``equity`` records; ``benchmark`` is optional.
    Recorded ``round_trips`` supply net PnL and entry/exit timestamps. Optional
    quantity/gross_notional, metric cost totals and fill reference prices enable
    the corresponding sections. Legacy classic spot fills can be paired.

    Explicit boundaries must equal the engine's flat-start/last-close window;
    defaults are first close minus interval, last close, metrics.initial_cash.
    Full perpetual input_snapshot.instrument is needed for base-unit price
    impact; missing contract metadata leaves that amount unavailable.
    """
    if not isinstance(result, Mapping) or not isinstance(result.get("metrics"), Mapping):
        raise EngineError("diagnostics requires a raw backtest result and metrics")
    if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
        raise EngineError("diagnostics interval_ms must be a positive integer")
    rows = _records(result.get("equity"), "equity")
    if not rows:
        raise EngineError("diagnostics requires at least one equity observation")
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            initial = _amount(
                initial_cash if initial_cash is not None else result["metrics"].get("initial_cash"),
                "initial cash",
            )
            if initial <= ZERO:
                raise EngineError("diagnostics initial cash must be positive")
            raw = [
                (
                    _timestamp(row.get("ts"), "equity timestamp"),
                    _amount(row.get("equity"), "equity"),
                    _amount(row["benchmark"], "benchmark") if "benchmark" in row else None,
                )
                for row in rows
            ]
            start = _timestamp(raw[0][0] - interval_ms if start_ts is None else start_ts, "start timestamp")
            end = _timestamp(raw[-1][0] if end_ts is None else end_ts, "end timestamp")
            if start != raw[0][0] - interval_ms or end != raw[-1][0] or start >= end:
                raise EngineError("diagnostics boundaries must match the complete raw engine equity window")
            if any(left[0] >= right[0] for left, right in zip(raw, raw[1:], strict=False)):
                raise EngineError("diagnostics equity timestamps must be strictly increasing")
            observations = [(start, initial, initial if raw[0][2] is not None else None), *raw]
            gaps = [right[0] - left[0] for left, right in zip(observations, observations[1:], strict=False)]
            missing = sum(max(0, (gap + interval_ms - 1) // interval_ms - 1) for gap in gaps)
            irregular = sum(gap != interval_ms for gap in gaps)
            nonpositive = sum(equity <= ZERO for _, equity, _ in raw)
            calendar, daily = _calendar(observations, interval_ms, start, end)
            closed, source = _closed_trades(result)
            trades, closed_net = _trade_statistics(closed, source, start, end)
            limitations = [
                {
                    "code": "historical_simulation",
                    "message": "One simulated path describes historical outcomes; it does not establish future profitability, capacity or live execution quality.",
                },
                {
                    "code": "selection_not_evaluated",
                    "message": "These diagnostics do not infer independent out-of-sample validity or adjust for parameter search and multiple testing.",
                },
                {
                    "code": "bar_close_observations",
                    "message": "Drawdown and exposure use observed bar closes and cannot reconstruct unobserved intrabar paths.",
                },
                {
                    "code": "dependent_samples",
                    "message": "Returns, trades and overlapping rolling windows may be dependent. Annualized ratios use descriptive square-root scaling; no p-values or confidence intervals are inferred.",
                },
                {
                    "code": "single_path_scope",
                    "message": "No factor alpha, diversified portfolio risk, liquidity capacity or GIPS compliance is established by this report.",
                },
            ]
            if calendar["daily_complete_count"] < MIN_DAILY_RETURNS:
                limitations.append(
                    {
                        "code": "small_daily_sample",
                        "message": "Fewer than 30 complete UTC days; rolling risk is unavailable. Thirty days is a display policy, not proof of statistical reliability.",
                    }
                )
            if len(closed) < 30:
                limitations.append(
                    {
                        "code": "small_trade_sample",
                        "message": "Fewer than 30 completed positions; win rate, expectancy and profit concentration are descriptive and can be dominated by a few trades.",
                    }
                )
            if nonpositive:
                limitations.append(
                    {
                        "code": "nonpositive_equity",
                        "message": "Net equity reaches zero or becomes negative. Returns with nonpositive opening equity and annualized risk ratios are unavailable; observed drawdowns may exceed 100%.",
                    }
                )
            if irregular:
                limitations.append(
                    {
                        "code": "incomplete_equity_path",
                        "message": "Missing or irregular bar-close observations prevent returns from being treated as complete calendar samples.",
                    }
                )
            if source == "unavailable":
                limitations.append(
                    {
                        "code": "closed_trade_source_unavailable",
                        "message": "No supported completed-position ledger is supplied; closed-trade contribution is unavailable.",
                    }
                )
            if any(
                row["reason"] == "numeric_range"
                for frequency in ("daily", "monthly", "yearly")
                for row in calendar[frequency]
            ):
                limitations.append(
                    {
                        "code": "numeric_range",
                        "message": "Some derived ratios exceed the finite chart number range and are unavailable. Exact financial amounts remain decimal strings.",
                    }
                )
            return {
                "diagnostics_version": DIAGNOSTICS_VERSION,
                "schema_version": 1,
                "sample": {
                    "start_ts": start,
                    "end_ts": end,
                    "interval_ms": interval_ms,
                    "equity_observations": len(raw),
                    "observed_span_days": _finite(Decimal(end - start) / DAY_MS),
                    "initial_cash": _money(initial),
                    "final_equity": _money(raw[-1][1]),
                    "complete_utc_days": calendar["daily_complete_count"],
                    "valid_daily_returns": sum(
                        row["complete"] and row["raw_return"] is not None for row in daily
                    ),
                    "complete_months": calendar["monthly_complete_count"],
                    "closed_trades": len(closed),
                    "missing_bar_intervals": missing,
                    "irregular_bar_gaps": irregular,
                    "nonpositive_equity_observations": nonpositive,
                },
                "calendar": calendar,
                "drawdowns": _drawdowns(observations),
                "rolling_risk": _rolling_risk(daily, bool(nonpositive)),
                "daily_distribution": _daily_distribution(daily),
                "trades": trades,
                "contribution": _contribution(result, closed, initial, raw[-1][1], closed_net, source),
                "exposure": _exposure(rows),
                "limitations": limitations,
            }
    except DecimalException as exc:
        raise EngineError("diagnostics arithmetic exceeds the supported decimal domain") from exc
    except (OverflowError, ValueError) as exc:
        if isinstance(exc, EngineError):
            raise
        raise EngineError("diagnostics UTC calendar exceeds the supported domain") from exc
