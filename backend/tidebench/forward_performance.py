"""Full-window observed account evidence, frozen inputs and wall-clock acceptance.

Returns are observation-based, never reconstructed across economic/coverage
gaps. Boundary cash-flow adjustment is disclosed as an estimate unless no
external flow occurs. Operational coverage is not HTTP availability or a SLA.
"""

import hashlib
import json
from decimal import Decimal, DecimalException, localcontext

from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .store import dumps, encode, new_id, now_ms
from .strategy_registry import digest

D = Decimal
DAY = 86400000
DEFAULT_GAP_MS = 60000
METHOD = "observed-full-window-v1"
TARGETS = {
    "min_wall_ms": 14 * DAY,
    "min_observations": 500,
    "min_coverage_pct": "99",
    "min_economic_coverage_pct": "99",
    "min_recoveries": 1,
}


def decimal(value):
    try:
        number = D(str(value))
        if not number.is_finite() or abs(number.as_tuple().exponent) > 1000:
            raise ValueError
        return number
    except (ValueError, DecimalException):
        raise PlatformError(
            "performance_integrity", "Forward observation has an invalid amount.", 409
        ) from None


def observation(row):
    try:
        item = dict(row)
        body = json.loads(item["body"])
        if not isinstance(body, dict) or digest(body) != item["state_hash"]:
            raise ValueError
        required = {
            "equity",
            "valuation_status",
            "positions",
            "external_capital",
            "fees_paid",
            "funding_paid",
            "realized_pnl",
        }
        if not required.issubset(body) or not isinstance(body["positions"], list):
            raise ValueError
        for key in ("external_capital", "fees_paid", "funding_paid", "realized_pnl"):
            decimal(body[key])
        if body["equity"] is not None:
            decimal(body["equity"])
        if any(
            not isinstance(p, dict) or not isinstance(p.get("instrument"), dict) for p in body["positions"]
        ):
            raise ValueError
        identity = body.get("observation_identity")
        if identity is not None and identity != {
            k: item[k] for k in ("source", "market_ts", "ledger_sequence", "observed_at")
        }:
            raise ValueError
        return item | {"body": body}
    except (ValueError, TypeError, KeyError):
        raise PlatformError(
            "performance_integrity",
            "Forward observation failed its content check or clock identity check.",
            409,
        ) from None


def checked_snapshot(row):
    try:
        body = json.loads(row["body"])
        if not isinstance(body, dict) or digest(body) != row["content_hash"]:
            raise ValueError
        if any(body[k] != row[k] for k in ("id", "source", "created_at")):
            raise ValueError
        window, summary, assessment = body["window"], body["summary"], body["acceptance"]
        if (
            window["method"] != METHOD
            or window["source"] != row["source"]
            or type(window["count"]) is not int
            or window["count"] < 0
            or type(window["range_start_id"]) is not int
            or window["range_start_id"] < 1
            or type(window["range_end_id"]) is not int
            or window["range_end_id"] < 0
            or summary["observations"] != window["count"]
            or summary["scope"] != "entire_frozen_observation_window"
            or assessment["as_of"] != body["created_at"]
        ):
            raise ValueError
        for key in ("ordered_observation_hash", "recovery_evidence_hash"):
            if (
                not isinstance(window[key], str)
                or len(window[key]) != 64
                or any(c not in "0123456789abcdef" for c in window[key])
            ):
                raise ValueError
        requested = window.get("requested_observed_range")
        if requested is not None and (
            not isinstance(requested, dict)
            or set(requested) != {"start", "end"}
            or type(requested["start"]) is not int
            or type(requested["end"]) is not int
            or not 0 <= requested["start"] < requested["end"] < 2**63
        ):
            raise ValueError
        return body
    except (ValueError, KeyError, TypeError):
        raise PlatformError(
            "performance_snapshot_integrity", "Frozen performance evidence failed its identity check.", 409
        ) from None


def economical(item):
    body = item["body"]
    return (
        body.get("equity") is not None
        and body.get("valuation_status") in {"fresh", "example"}
        and not body.get("pending_funding")
        and body.get("economic_status", "complete") == "complete"
    )


def acceptance(summary, window, targets, as_of):
    checks = {
        "public_okx_observations": window["source"] == "okx" and summary["market_updates"] >= 2,
        "market_clock_progress": summary["market_wall_ratio"] is not None
        and D(".5") <= decimal(summary["market_wall_ratio"]) <= 2,
        "bound_observation_clocks": summary["legacy_clock_observations"] == 0 and summary["observations"] > 0,
        "real_wall_duration": summary["wall_elapsed_ms"] >= targets["min_wall_ms"],
        "observation_count": summary["observations"] >= targets["min_observations"],
        "observation_coverage": summary["wall_coverage_pct"] is not None
        and decimal(summary["wall_coverage_pct"]) >= decimal(targets["min_coverage_pct"]),
        "economic_coverage": summary["economic_wall_coverage_pct"] is not None
        and decimal(summary["economic_wall_coverage_pct"]) >= decimal(targets["min_economic_coverage_pct"]),
        "fresh_final_observation": summary["last_observed_at"] is not None
        and 0 <= as_of - summary["last_observed_at"] <= window["max_gap_ms"],
        "no_pending_funding": summary["pending_funding_observations"] == 0,
        "observed_recovery": sum(summary["recovery_counts"].values()) >= targets["min_recoveries"],
        "no_clock_regression": summary["clock_regressions"] == 0,
        "no_financial_discontinuity": summary["financial_discontinuities"] == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "targets": targets,
        "as_of": as_of,
        "scope": "observed_account_and_recovery_coverage_only_not_exchange_capacity_HTTP_SLO_or_profitable_edge",
    }


class ForwardPerformance:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS forward_equity(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,market_ts INTEGER NOT NULL,ledger_sequence INTEGER NOT NULL,observed_at INTEGER NOT NULL,body TEXT NOT NULL,state_hash TEXT NOT NULL,UNIQUE(source,market_ts,ledger_sequence,state_hash));
                CREATE INDEX IF NOT EXISTS forward_equity_history ON forward_equity(source,id DESC);
                CREATE TABLE IF NOT EXISTS forward_performance_snapshots(id TEXT PRIMARY KEY,source TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS forward_performance_snapshots_history ON forward_performance_snapshots(source,created_at DESC,id DESC);
            """)

    @staticmethod
    def capture(conn, source, snapshots, account):
        with localcontext(ACCOUNTING_CONTEXT):
            source_quotes = {key: s for key, s in snapshots.items() if s.get("source") == source}
            market_ts = max((int(s["ts"]) for s in source_quotes.values()), default=0)
            sequence = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM pro_ledger WHERE source=?", (source,)
            ).fetchone()[0]
            flow = sum(
                (
                    D(r["credit"]) - D(r["debit"])
                    for r in conn.execute(
                        "SELECT debit,credit FROM pro_ledger WHERE source=? AND asset='USDT' AND account='contributed_capital'",
                        (source,),
                    )
                ),
                D(0),
            )
            observed_at = now_ms()
            body = {
                key: account[key]
                for key in (
                    "equity",
                    "cash",
                    "available_cash",
                    "used_margin",
                    "unrealized_pnl",
                    "realized_pnl",
                    "fees_paid",
                    "funding_paid",
                    "insurance_debt",
                    "valuation_status",
                    "positions",
                )
            }
            body.update(
                external_capital=str(flow),
                quote_timestamps={key: s["ts"] for key, s in snapshots.items()},
                economic_status=account.get(
                    "economic_status",
                    "complete"
                    if account["valuation_status"] in {"fresh", "example"}
                    else "valuation_incomplete",
                ),
                pending_funding=account.get("pending_funding", []),
                equity_before_pending_funding=account.get("equity_before_pending_funding"),
                market_time_known=bool(source_quotes),
                observation_identity={
                    "source": source,
                    "market_ts": market_ts,
                    "ledger_sequence": sequence,
                    "observed_at": observed_at,
                },
            )
            conn.execute(
                "INSERT OR IGNORE INTO forward_equity(source,market_ts,ledger_sequence,observed_at,body,state_hash) VALUES(?,?,?,?,?,?)",
                (source, market_ts, sequence, observed_at, dumps(body), digest(body)),
            )

    def _report(
        self,
        conn,
        source,
        *,
        limit=500,
        before=2**63 - 1,
        window_start=None,
        window_end=None,
        observed_start=None,
        observed_end=None,
        max_gap_ms=DEFAULT_GAP_MS,
        as_of=None,
        audit_end=None,
        _requested_observed_range=None,
    ):
        requested = _requested_observed_range
        if observed_start is not None or observed_end is not None:
            if (
                type(observed_start) is not int
                or type(observed_end) is not int
                or not 0 <= observed_start < observed_end < 2**63
            ):
                raise PlatformError(
                    "performance_observed_range", "Supply an ordered pair of UTC wall-time boundaries.", 422
                )
            if window_start is not None or window_end is not None:
                raise PlatformError(
                    "performance_window_mixed",
                    "Use either wall-time boundaries or resolved observation IDs.",
                    422,
                )
            if before != 2**63 - 1:
                raise PlatformError(
                    "performance_time_page_requires_ids",
                    "Resolve wall time once, then paginate with the returned fixed observation IDs.",
                    422,
                )
            first_id, last_id = conn.execute(
                "SELECT MIN(id),MAX(id) FROM forward_equity WHERE source=? AND observed_at>=? AND observed_at<?",
                (source, observed_start, observed_end),
            ).fetchone()
            # Include all source rows between these IDs. Filtering every row by
            # wall time would hide intervening clock regressions/economic gaps.
            window_start, window_end = (first_id, last_id) if first_id is not None else (1, 0)
            requested = {"start": observed_start, "end": observed_end}
        window_start = 1 if window_start is None else window_start
        if (
            source not in {"okx", "example"}
            or not 1 <= limit <= 5000
            or not 1 <= window_start < 2**63
            or not 1000 <= max_gap_ms <= DAY
        ):
            raise PlatformError(
                "performance_window", "Invalid performance source, page, window or coverage interval.", 422
            )
        maximum = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM forward_equity WHERE source=?", (source,)
        ).fetchone()[0]
        end = min(maximum, window_end) if window_end is not None else maximum
        if end < 0 or not 1 <= before < 2**63 or window_end is not None and not 0 <= window_end < 2**63:
            raise PlatformError("performance_window", "Invalid observation window boundary.", 422)
        total = conn.execute("SELECT COUNT(*) FROM forward_equity WHERE source=?", (source,)).fetchone()[0]
        page = conn.execute(
            "SELECT * FROM forward_equity WHERE source=? AND id>=? AND id<=? AND id<? ORDER BY id DESC LIMIT ?",
            (source, window_start, end, before, limit),
        ).fetchall()
        items = [observation(row) for row in reversed(page)]
        audit_end = (
            audit_end
            if audit_end is not None
            else conn.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]
        )
        bound_rows = conn.execute(
            "SELECT id,observed_at FROM forward_equity WHERE source=? AND id>=? AND id<=? AND (id=(SELECT MIN(id) FROM forward_equity WHERE source=? AND id>=? AND id<=?) OR id=(SELECT MAX(id) FROM forward_equity WHERE source=? AND id>=? AND id<=?)) ORDER BY id",
            (source, window_start, end, source, window_start, end, source, window_start, end),
        ).fetchall()
        restore_times = (
            [
                row[0]
                for row in conn.execute(
                    "SELECT ts FROM audit WHERE source IN (?, 'system') AND id<=? AND kind='backup.restored' AND ts>=? AND ts<=? ORDER BY ts",
                    (source, audit_end, bound_rows[0]["observed_at"], bound_rows[-1]["observed_at"]),
                )
            ]
            if bound_rows
            else []
        )
        financial_counter_regressions = ledger_sequence_regressions = 0
        row_hash = hashlib.sha256()
        n = valid_count = legacy = pending = gaps = regressions = updates = flow_periods = 0
        covered = economic_covered = 0
        first = last = first_valid = last_valid = previous = None
        twr = peak = D(1)
        drawdown = D(0)
        chain = True
        max_gross = max_base = D(0)
        gross_samples = 0
        segments, segment, segment_count = [], None, 0
        with localcontext(ACCOUNTING_CONTEXT):
            for raw in conn.execute(
                "SELECT * FROM forward_equity WHERE source=? AND id>=? AND id<=? ORDER BY id",
                (source, window_start, end),
            ):
                item = observation(raw)
                row_hash.update(
                    (
                        dumps(
                            {
                                k: item[k]
                                for k in (
                                    "id",
                                    "source",
                                    "market_ts",
                                    "ledger_sequence",
                                    "observed_at",
                                    "state_hash",
                                )
                            }
                        )
                        + "\n"
                    ).encode()
                )
                body = item["body"]
                n += 1
                legacy += body.get("observation_identity") is None
                pending += (
                    bool(body.get("pending_funding")) or body.get("economic_status") == "funding_pending"
                )
                good = economical(item)
                if good:
                    decimal(body["equity"])
                    valid_count += 1
                    first_valid = first_valid or item
                    last_valid = item
                    if decimal(body["equity"]) > 0:
                        assets = {}
                        for position in body["positions"]:
                            value = decimal(position["market_value"])
                            base = position["instrument"]["base"]
                            assets[base] = assets.get(base, D(0)) + value
                        gross_samples += 1
                        max_gross = max(max_gross, sum(assets.values(), D(0)) / decimal(body["equity"]) * 100)
                        max_base = max(
                            [max_base] + [v / decimal(body["equity"]) * 100 for v in assets.values()]
                        )
                else:
                    chain = False
                first = first or item
                last = item
                contiguous = False
                if previous:
                    wall_delta = item["observed_at"] - previous["observed_at"]
                    market_delta = item["market_ts"] - previous["market_ts"]
                    known = body.get("market_time_known", bool(body.get("quote_timestamps"))) and previous[
                        "body"
                    ].get("market_time_known", bool(previous["body"].get("quote_timestamps")))
                    updates += known and market_delta > 0
                    reset = any(previous["observed_at"] < ts <= item["observed_at"] for ts in restore_times)
                    fee_reset = decimal(body["fees_paid"]) < decimal(previous["body"]["fees_paid"])
                    ledger_reset = item["ledger_sequence"] < previous["ledger_sequence"]
                    ledger_sequence_regressions += ledger_reset
                    financial_counter_regressions += fee_reset or ledger_reset
                    clock_regression = wall_delta < 0 or known and market_delta < 0 or ledger_reset
                    coverage_gap = wall_delta > max_gap_ms
                    regressions += clock_regression
                    gaps += coverage_gap
                    if not clock_regression and not coverage_gap:
                        covered += wall_delta
                    if reset or fee_reset or ledger_reset or clock_regression or coverage_gap:
                        chain = False
                    else:
                        if good and economical(previous):
                            economic_covered += wall_delta
                            p = previous["body"]
                            if decimal(p["equity"]) > 0:
                                flow = decimal(body["external_capital"]) - decimal(p["external_capital"])
                                flow_periods += flow != 0
                                multiplier = (decimal(body["equity"]) - flow) / decimal(p["equity"])
                                if multiplier >= 0:
                                    contiguous = True
                                    twr *= multiplier
                                    peak = max(peak, twr)
                                    drawdown = max(drawdown, (peak - twr) / peak)
                                    if segment:
                                        segment["last_id"] = item["id"]
                                        segment["return"] = str(
                                            (decimal(segment["return"]) + 1) * multiplier - 1
                                        )
                                        segment["observations"] += 1
                                else:
                                    chain = False
                            else:
                                chain = False
                if not contiguous:
                    if segment:
                        segments.append(segment)
                        segments = segments[-100:]
                        segment_count += 1
                    segment = (
                        {"first_id": item["id"], "last_id": item["id"], "observations": 1, "return": "0"}
                        if good
                        else None
                    )
                previous = item
            if segment:
                segments.append(segment)
                segments = segments[-100:]
                segment_count += 1
            wall_span = max(0, last["observed_at"] - first["observed_at"]) if first else 0
            known_bounds = conn.execute(
                "SELECT MIN(market_ts),MAX(market_ts) FROM forward_equity WHERE source=? AND id>=? AND id<=? AND market_ts>0",
                (source, window_start, end),
            ).fetchone()
            market_span = known_bounds[1] - known_bounds[0] if known_bounds[0] is not None else None
            audit_end = (
                audit_end
                if audit_end is not None
                else conn.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]
            )
            audit_hash = hashlib.sha256()
            recovery_counts = {
                "portfolio_compensations_completed": 0,
                "operational_conditions_resolved": 0,
                "workspace_restores_completed": 0,
            }
            kinds = {
                "portfolio.batch_compensated": "portfolio_compensations_completed",
                "ops.incident_resolved": "operational_conditions_resolved",
                "backup.restored": "workspace_restores_completed",
            }
            if first:
                for row in conn.execute(
                    "SELECT id,source,ts,kind,details FROM audit WHERE source IN (?, 'system') AND id<=? AND ts>=? AND ts<=? AND kind IN ('portfolio.batch_compensated','ops.incident_resolved','backup.restored') ORDER BY id",
                    (source, audit_end, first["observed_at"], last["observed_at"]),
                ):
                    audit_hash.update((dumps(dict(row)) + "\n").encode())
                    recovery_counts[kinds[row["kind"]]] += 1
            complete = (
                bool(n)
                and chain
                and valid_count == n
                and not restore_times
                and not financial_counter_regressions
            )
            summary = {
                "observations": n,
                "total_observations": total,
                "scope": "entire_frozen_observation_window",
                "complete_valuation_chain": complete,
                "return": str(twr - 1) if n >= 2 and complete and not flow_periods else None,
                "boundary_adjusted_return": str(twr - 1) if n >= 2 and complete else None,
                "max_drawdown": str(drawdown) if n >= 2 and complete and not flow_periods else None,
                "return_method": "geometrically_linked_observed_returns_no_flows"
                if not flow_periods
                else "boundary_cash_flow_estimate_not_true_TWR_without_flow_time_valuation",
                "external_flow_periods": flow_periods,
                "valuation_gap_observations": n - valid_count,
                "coverage_gap_intervals": gaps,
                "clock_regressions": regressions,
                "legacy_clock_observations": legacy,
                "pending_funding_observations": pending,
                "financial_discontinuities": len(restore_times) + financial_counter_regressions,
                "restore_events": len(restore_times),
                "financial_counter_regressions": financial_counter_regressions,
                "ledger_sequence_regressions": ledger_sequence_regressions,
                "wall_elapsed_ms": wall_span,
                "market_elapsed_ms": market_span,
                "market_updates": updates,
                "market_wall_ratio": str(D(market_span) / wall_span)
                if market_span is not None and wall_span
                else None,
                "wall_covered_ms": covered,
                "economic_wall_covered_ms": economic_covered,
                "wall_coverage_pct": str(D(covered) / wall_span * 100) if wall_span else None,
                "economic_wall_coverage_pct": str(D(economic_covered) / wall_span * 100)
                if wall_span
                else None,
                "first_observed_at": first["observed_at"] if first else None,
                "last_observed_at": last["observed_at"] if last else None,
                "first_market_ts": known_bounds[0],
                "last_market_ts": known_bounds[1],
                "max_observed_gross_pct": str(max_gross) if gross_samples else None,
                "max_observed_base_asset_gross_pct": str(max_base) if gross_samples else None,
                "gross_valuation_samples": gross_samples,
                "segments": segments[-100:],
                "total_segments": segment_count,
                "recovery_counts": recovery_counts,
                "warning": "Full frozen observation window; page controls only the displayed rows. Gaps/funding obligations suppress linked return. Cash-flow timing without matching valuation is an estimate. Observed drawdown misses unobserved intraperiod extremes. Wall-clock account coverage is not service availability, venue execution capacity or profitable strategy evidence.",
            }
            if first:
                for key in ("fees_paid", "funding_paid", "realized_pnl"):
                    summary[key + "_change"] = str(decimal(last["body"][key]) - decimal(first["body"][key]))
                summary["recorded_cost_basis"] = (
                    "actual_posted_account_counters_pending_obligations_are_not_estimated_costs"
                )
            if first_valid and last_valid:
                p, c = first_valid["body"], last_valid["body"]
                endpoint_delta = str(
                    decimal(c["equity"])
                    - decimal(p["equity"])
                    - (decimal(c["external_capital"]) - decimal(p["external_capital"]))
                )
                if first_valid["id"] == first["id"] and last_valid["id"] == last["id"]:
                    summary["net_pnl"] = endpoint_delta
                    summary["net_pnl_basis"] = (
                        "complete_window_endpoint_equity_minus_external_flows_not_interpolated_path"
                    )
                else:
                    summary["net_pnl"] = None
                    summary["net_pnl_basis"] = "window_boundary_valuation_incomplete"
                    summary["nearest_complete_endpoint_delta"] = {
                        "first_id": first_valid["id"],
                        "last_id": last_valid["id"],
                        "value": endpoint_delta,
                        "scope": "partial_complete_endpoints_not_full_window_profit",
                    }
                if restore_times or financial_counter_regressions:
                    summary["endpoint_equity_minus_flow_delta"] = endpoint_delta
                    summary["net_pnl"] = None
                    summary["net_pnl_basis"] = "financial_replacement_endpoint_difference_not_strategy_PnL"
            if restore_times or financial_counter_regressions:
                summary["return"] = summary["boundary_adjusted_return"] = summary["max_drawdown"] = None
                if first:
                    for key in ("fees_paid", "funding_paid", "realized_pnl"):
                        summary[key + "_endpoint_counter_change"] = summary[key + "_change"]
                        summary[key + "_change"] = None
            window = {
                "method": METHOD,
                "source": source,
                "range_start_id": window_start,
                "range_end_id": end,
                "min_id": first["id"] if first else None,
                "max_id": last["id"] if last else None,
                "count": n,
                "ordered_observation_hash": row_hash.hexdigest(),
                "audit_end_id": audit_end,
                "recovery_evidence_hash": audit_hash.hexdigest(),
                "max_gap_ms": max_gap_ms,
            }
            if requested is not None:
                window["requested_observed_range"] = requested
            assessment = acceptance(summary, window, TARGETS, now_ms() if as_of is None else as_of)
            return encode(
                {
                    "items": items,
                    "summary": summary,
                    "window": window,
                    "acceptance": assessment,
                    "next_before": items[0]["id"] if len(items) == limit else None,
                }
            )

    def report(self, source, **options):
        with self.store.read() as conn:
            return self._report(conn, source, **options)

    def freeze(
        self,
        source,
        actor,
        *,
        window_start=None,
        window_end=None,
        observed_start=None,
        observed_end=None,
        max_gap_ms=DEFAULT_GAP_MS,
    ):
        identifier, created_at = new_id(), now_ms()
        with self.store.write() as conn:
            report = self._report(
                conn,
                source,
                window_start=window_start,
                window_end=window_end,
                observed_start=observed_start,
                observed_end=observed_end,
                max_gap_ms=max_gap_ms,
                as_of=created_at,
            )
            if report["window"]["count"] == 0:
                raise PlatformError(
                    "performance_window_empty",
                    "No observations belong to this window; nothing can be frozen.",
                    422,
                )
            body = {
                "id": identifier,
                "source": source,
                "created_by": actor,
                "created_at": created_at,
                **{k: report[k] for k in ("window", "summary", "acceptance")},
            }
            conn.execute(
                "INSERT INTO forward_performance_snapshots VALUES(?,?,?,?,?)",
                (identifier, source, dumps(body), digest(body), created_at),
            )
            self.store.audit(
                conn,
                source,
                "performance.snapshot_frozen",
                "Complete account observation window frozen",
                {"snapshot_id": identifier, "window": body["window"], "actor": actor},
            )
        return self.snapshot(identifier)

    def snapshots(self, source, *, limit=20, before=None):
        """Bounded saved summaries. Content validity is not numerical replay."""
        if source not in {"okx", "example"} or type(limit) is not int or not 1 <= limit <= 100:
            raise PlatformError("performance_snapshot_page", "Choose a source and 1–100 snapshots.", 422)
        boundary, boundary_id = 2**63 - 1, "z"
        if before is not None:
            try:
                timestamp, boundary_id = before.split(":", 1)
                boundary = int(timestamp)
                if (
                    not timestamp.isdecimal()
                    or not 0 <= boundary < 2**63
                    or len(boundary_id) != 32
                    or any(c not in "0123456789abcdef" for c in boundary_id)
                ):
                    raise ValueError
            except (ValueError, AttributeError):
                raise PlatformError(
                    "performance_snapshot_cursor", "Invalid snapshot page cursor.", 422
                ) from None
        fields = (
            "observations",
            "return",
            "max_drawdown",
            "net_pnl",
            "net_pnl_basis",
            "wall_elapsed_ms",
            "wall_coverage_pct",
            "economic_wall_coverage_pct",
            "first_observed_at",
            "last_observed_at",
            "clock_regressions",
            "pending_funding_observations",
        )
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM forward_performance_snapshots WHERE source=? AND (created_at<? OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC LIMIT ?",
                (source, boundary, boundary, boundary_id, limit + 1),
            ).fetchall()
        items = []
        for row in rows[:limit]:
            item = {k: row[k] for k in ("id", "source", "created_at", "content_hash")}
            try:
                body = checked_snapshot(row)
                item.update(
                    status="available",
                    integrity_status="stored_content_valid",
                    verification_status="not_recomputed",
                    window=body["window"],
                    summary={k: body["summary"].get(k) for k in fields},
                    acceptance=body["acceptance"],
                )
            except PlatformError as exc:
                item.update(
                    status="unavailable",
                    integrity_status="unavailable",
                    verification_status="unavailable",
                    error={"code": exc.code, "message": exc.message},
                )
            items.append(item)
        return {
            "source": source,
            "items": items,
            "next_before": f"{rows[limit - 1]['created_at']}:{rows[limit - 1]['id']}"
            if len(rows) > limit
            else None,
            "scope": "Bounded stored-content summaries; content hashes do not establish recomputed verification. Use the separate verification endpoint.",
        }

    def snapshot(self, identifier):
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM forward_performance_snapshots WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise PlatformError(
                    "performance_snapshot_missing", "Performance evidence snapshot not found.", 404
                )
            body = checked_snapshot(row)
            return dict(row) | {"body": body}

    def verify(self, identifier):
        frozen = self.snapshot(identifier)
        body = frozen["body"]
        window = body["window"]
        with self.store.read() as conn:
            result = self._report(
                conn,
                body["source"],
                window_start=window["range_start_id"],
                window_end=window["range_end_id"],
                max_gap_ms=window["max_gap_ms"],
                as_of=body["created_at"],
                audit_end=window["audit_end_id"],
                _requested_observed_range=window.get("requested_observed_range"),
            )
        # total_observations is a live account count, not part of the frozen
        # measurement. New observations after the boundary never alter evidence.
        summary = result["summary"] | {"total_observations": body["summary"]["total_observations"]}
        equal = (
            result["window"] == window
            and summary == body["summary"]
            and result["acceptance"] == body["acceptance"]
        )
        return {
            "snapshot_id": identifier,
            "verified": equal,
            "original_hash": frozen["content_hash"],
            "recomputed_window": result["window"],
            "recomputed_summary": summary,
            "recomputed_acceptance": result["acceptance"],
        }
