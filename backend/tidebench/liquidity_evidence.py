"""Immutable public L2 observations and deliberately limited capacity evidence.

The payload is the canonical decoded OKX data array, not HTTP wire bytes. This
is an observation of displayed, cached liquidity, never an executable quote or
an estimate of historical capacity. No private exchange endpoint is involved.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Annotated

from pydantic import Field, model_validator

from .engine import ACCOUNTING_CONTEXT
from .market import MarketError, _decimal, _timestamp
from .platform import PlatformError
from .schemas import InputModel
from .store import dumps, encode, new_id, now_ms

D = Decimal
SCENARIOS = (D(1000), D(2500), D(10000), D(100000))
MARKET = r"^[A-Z0-9]{1,24}-USDT(?:-SWAP)?$"
MarketId = Annotated[str, Field(pattern=MARKET)]
CaptureId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class LiquidityCaptureInput(InputModel):
    inst_id: MarketId
    depth: int = Field(default=400, ge=1, le=400)


class LiquidityCalibrationInput(InputModel):
    inst_id: MarketId
    capture_ids: list[CaptureId] = Field(default_factory=list, max_length=512)
    window_start: int | None = Field(default=None, ge=1, lt=2**63)
    window_end: int | None = Field(default=None, ge=1, lt=2**63)
    max_shortfall_bps: Decimal = Field(default=D(10), gt=0, le=1000)
    max_participation_pct: Decimal = Field(default=D(10), gt=0, le=100)
    minimum_samples: int = Field(default=12, ge=2, le=512)
    minimum_elapsed_ms: int = Field(default=300000, ge=1000, le=2592000000)
    max_gap_ms: int = Field(default=60000, ge=1000, le=86400000)
    max_book_age_ms: int = Field(default=2000, ge=1, le=60000)
    review_max_age_ms: int = Field(default=300000, ge=1000, le=86400000)
    child_notional: Decimal = Field(default=D(2500), gt=0, le=100000)
    sleeve_notional: Decimal = Field(default=D(10000), gt=0, le=100000)

    @model_validator(mode="after")
    def valid_window(self):
        if len(set(self.capture_ids)) != len(self.capture_ids):
            raise ValueError("Capture IDs must be unique.")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("Window end must follow window start.")
        if self.sleeve_notional < self.child_notional:
            raise ValueError("Sleeve notional must be at least the child order notional.")
        return self


def _hash(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def _metadata(rows, symbol):
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("instrument_metadata_missing_or_ambiguous")
    row = rows[0]
    swap, base = symbol.endswith("-SWAP"), symbol.split("-")[0]
    if row.get("instId") != symbol or row.get("instType") != ("SWAP" if swap else "SPOT"):
        raise ValueError("instrument_identity_mismatch")
    if row.get("state") != "live":
        raise ValueError("instrument_not_live")
    tick, lot, minimum = (_decimal(row.get(k), k, positive=True) for k in ("tickSz", "lotSz", "minSz"))
    if swap:
        if row.get("ctType") != "linear" or row.get("settleCcy") != "USDT" or row.get("ctValCcy") != base:
            raise ValueError("unsupported_contract_units")
        size = _decimal(row.get("ctVal"), "ctVal", positive=True) * _decimal(
            row.get("ctMult"), "ctMult", positive=True
        )
    else:
        if row.get("baseCcy") != base or row.get("quoteCcy") != "USDT":
            raise ValueError("unsupported_spot_units")
        size = D(1)
    return {
        "product": "SWAP" if swap else "SPOT",
        "quantity_unit": "contracts" if swap else "base_asset",
        "base_asset": base,
        "base_per_unit": size,
        "tick_size": tick,
        "lot_size": lot,
        "minimum_size": minimum,
    }


def _levels(rows, side, depth, meta):
    if not isinstance(rows, list) or not 1 <= len(rows) <= depth:
        raise ValueError("missing_or_excess_depth")
    levels, previous = [], None
    for row in rows:
        if not isinstance(row, list) or not 2 <= len(row) <= 4:
            raise ValueError("invalid_depth_level")
        price = _decimal(row[0], "depth_price", positive=True)
        quantity = _decimal(row[1], "depth_quantity", positive=True)
        if price % meta["tick_size"] or quantity % meta["lot_size"]:
            raise ValueError("depth_not_aligned_to_instrument")
        if previous is not None and (price <= previous if side == "buy" else price >= previous):
            raise ValueError("depth_not_strictly_ordered")
        levels.append((price, quantity))
        previous = price
    return levels


def _walk(levels, side, notional, mid, meta):
    # Round up to a venue-valid quantity so a passing size covers the declared
    # minimum notional instead of silently rounding it to zero or a smaller size.
    lot, size = meta["lot_size"], meta["base_per_unit"]
    desired = max(notional / mid / size, meta["minimum_size"])
    requested = (desired / lot).to_integral_value(rounding=ROUND_CEILING) * lot
    remaining, filled, value, levels_used = requested, D(0), D(0), 0
    total = sum((quantity for _, quantity in levels), D(0))
    for price, quantity in levels:
        if remaining <= 0:
            break
        taken = min(remaining, quantity)
        filled += taken
        value += taken * size * price
        remaining -= taken
        levels_used += 1
    base = filled * size
    vwap = value / base if base else None
    shortfall = ((vwap - mid) if side == "buy" else (mid - vwap)) / mid * 10000 if vwap else None
    return {
        "side": side,
        "requested_notional": notional,
        "requested_quantity": requested,
        "requested_base_quantity": requested * size,
        "effective_mid_notional": requested * size * mid,
        "filled_quantity": filled,
        "filled_base_quantity": base,
        "unfilled_quantity": remaining,
        "matched_notional": value,
        "fill_ratio_pct": filled / requested * 100,
        "status": "complete" if remaining == 0 else "depth_exhausted",
        "vwap": vwap,
        "shortfall_bps": shortfall,
        "displayed_side_quantity": total,
        "displayed_side_notional": sum((price * quantity * size for price, quantity in levels), D(0)),
        "participation_pct": filled / total * 100,
        "levels_used": levels_used,
    }


def evaluate_depth(raw, metadata_rows, symbol, depth, received_at):
    """Pure, finite-depth, Decimal evaluation, independent of caller context."""
    with localcontext(ACCOUNTING_CONTEXT):
        try:
            meta = _metadata(metadata_rows, symbol)
            if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
                raise ValueError("book_missing_or_ambiguous")
            book = raw[0]
            timestamp = _timestamp(book.get("ts"))
            age = received_at - timestamp
            asks = _levels(book.get("asks"), "buy", depth, meta)
            bids = _levels(book.get("bids"), "sell", depth, meta)
            if bids[0][0] >= asks[0][0]:
                raise ValueError("crossed_or_locked_book")
            mid = (asks[0][0] + bids[0][0]) / 2
            status = "supported" if age >= 0 else "clock_ahead"
            return encode(
                {
                    "status": status,
                    "reason": None if age >= 0 else "exchange_timestamp_after_receive",
                    "metadata": meta,
                    "exchange_ts": timestamp,
                    "book_age_at_capture_ms": age,
                    "best_bid": bids[0][0],
                    "best_ask": asks[0][0],
                    "mid": mid,
                    "spread_bps": (asks[0][0] - bids[0][0]) / mid * 10000,
                    "levels": {"buy": len(asks), "sell": len(bids)},
                    "scenarios": [
                        _walk(levels, side, notional, mid, meta)
                        for notional in SCENARIOS
                        for side, levels in (("buy", asks), ("sell", bids))
                    ],
                    "basis": "Displayed organic L2; mid-sized quantity rounded up to lot/minimum; fees excluded.",
                }
            )
        except (ValueError, ArithmeticError, MarketError) as exc:
            return {"status": "unsupported", "reason": str(exc), "scenarios": []}


def _quantiles(values):
    ordered = sorted(D(str(v)) for v in values)
    if not ordered:
        return {"median": None, "p90": None, "p95": None, "worst": None}
    # Conservative nearest-rank values; small samples are not interpolated into
    # more precise estimates. The actual approval test uses the worst sample.
    return {
        name: str(ordered[max(0, math.ceil(len(ordered) * q) - 1)])
        for name, q in (("median", 0.5), ("p90", 0.9), ("p95", 0.95), ("worst", 1))
    }


def checked_evidence(row, table):
    """Verify one frozen SQLite row without IO; usable by backup validation."""
    if table not in {"liquidity_captures", "liquidity_calibrations"}:
        raise ValueError("Unsupported liquidity evidence table.")
    try:
        payload = json.loads(row["payload"])
        body = {k: v for k, v in payload.items() if k != "content_hash"}
        valid = payload["id"] == row["id"] and payload["inst_id"] == row["inst_id"]
        valid = (
            valid
            and payload["source"] == "okx"
            and payload["schema_version"] == 1
            and payload["methodology"] == "okx_displayed_l2_v1"
        )
        time_field = "received_at" if table == "liquidity_captures" else "created_at"
        valid = valid and payload[time_field] == row[time_field]
        valid = valid and payload["content_hash"] == row["content_hash"] == _hash(body)
        if table == "liquidity_captures":
            valid = valid and payload["raw_hash"] == _hash(payload["raw_depth"])
            valid = valid and payload["metadata_hash"] == _hash(payload["raw_metadata"])
            valid = valid and payload["evidence_hash"] == _hash(payload["evidence"])
            valid = valid and payload["known_at"] == payload["received_at"]
            valid = valid and int(payload["received_ns"]) // 1000000 == payload["received_at"]
            computed = evaluate_depth(
                payload["raw_depth"],
                payload["raw_metadata"],
                payload["inst_id"],
                payload["request"]["depth"],
                payload["received_at"],
            )
            if (
                int(payload["received_ns"]) < int(payload["book_request_started_ns"])
                or int(payload["metadata_received_ns"]) < int(payload["request_started_ns"])
                or int(payload["request_elapsed_ns"]) < 0
            ):
                computed = {"status": "unsupported", "reason": "capture_clock_regression", "scenarios": []}
            valid = valid and payload["evidence"] == computed
        else:
            validated = LiquidityCalibrationInput(**payload["input"])
            pins = [c["id"] for c in payload["captures"]]
            valid = valid and payload["input_hash"] == _hash(payload["input"])
            audit = payload["selection_audit"]
            selected = set(validated.capture_ids)
            all_pins = set(pins)
            omitted = audit["omitted_capture_ids"]
            valid = valid and len(pins) == len(all_pins) and selected.issubset(all_pins)
            valid = valid and audit["all_available_count"] == len(pins)
            valid = valid and audit["selected_count"] == len(selected)
            valid = valid and len(omitted) == len(set(omitted)) and set(omitted) == all_pins - selected
            valid = valid and audit["selection_policy"] == "all_available_captures_in_declared_window"
            valid = valid and audit["availability_as_of"] == payload["created_at"]
            valid = valid and validated.inst_id == payload["inst_id"]
            valid = (
                valid
                and payload["historical_capacity"] == "unknown"
                and payload["live_execution_calibration"] == "not_measured"
            )
        if not valid:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise PlatformError(
            "liquidity_evidence_integrity",
            "Liquidity evidence failed its identity/content verification.",
            409,
        ) from None
    return payload


class LiquidityEvidence:
    def __init__(self, store, market):
        self.store, self.market = store, market
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS liquidity_captures(id TEXT PRIMARY KEY,inst_id TEXT NOT NULL,received_at INTEGER NOT NULL,content_hash TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS liquidity_captures_market ON liquidity_captures(inst_id,received_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS liquidity_calibrations(id TEXT PRIMARY KEY,inst_id TEXT NOT NULL,created_at INTEGER NOT NULL,content_hash TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS liquidity_calibrations_market ON liquidity_calibrations(inst_id,created_at DESC,id DESC);
                CREATE TRIGGER IF NOT EXISTS immutable_liquidity_captures_update BEFORE UPDATE ON liquidity_captures BEGIN SELECT RAISE(ABORT,'liquidity captures are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_liquidity_captures_delete BEFORE DELETE ON liquidity_captures BEGIN SELECT RAISE(ABORT,'liquidity captures are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_liquidity_calibrations_update BEFORE UPDATE ON liquidity_calibrations BEGIN SELECT RAISE(ABORT,'liquidity calibrations are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_liquidity_calibrations_delete BEFORE DELETE ON liquidity_calibrations BEGIN SELECT RAISE(ABORT,'liquidity calibrations are immutable'); END;
            """)

    async def capture(self, body: LiquidityCaptureInput, actor="system"):
        started_ns, elapsed_start = time.time_ns(), time.monotonic_ns()
        metadata = await self.market._get(
            "/api/v5/public/instruments",
            {
                "instType": "SWAP" if body.inst_id.endswith("-SWAP") else "SPOT",
                "instId": body.inst_id,
            },
        )
        metadata_received_ns = time.time_ns()
        book_request_ns = time.time_ns()
        raw = await self.market._get("/api/v5/market/books", {"instId": body.inst_id, "sz": str(body.depth)})
        received_ns, elapsed_end = time.time_ns(), time.monotonic_ns()
        received_at = received_ns // 1000000
        result = evaluate_depth(raw, metadata, body.inst_id, body.depth, received_at)
        if received_ns < book_request_ns or metadata_received_ns < started_ns or elapsed_end < elapsed_start:
            result = {"status": "unsupported", "reason": "capture_clock_regression", "scenarios": []}
        payload = {
            "schema_version": 1,
            "methodology": "okx_displayed_l2_v1",
            "id": new_id(),
            "source": "okx",
            "inst_id": body.inst_id,
            "region": self.market.region,
            "request": {"endpoint": "/api/v5/market/books", "depth": body.depth},
            "request_started_at": started_ns // 1000000,
            "request_started_ns": str(started_ns),
            "book_request_started_at": book_request_ns // 1000000,
            "book_request_started_ns": str(book_request_ns),
            "metadata_received_at": metadata_received_ns // 1000000,
            "metadata_received_ns": str(metadata_received_ns),
            "received_at": received_at,
            "received_ns": str(received_ns),
            "request_elapsed_ns": str(elapsed_end - elapsed_start),
            "known_at": received_at,
            "created_by": actor,
            "payload_encoding": "canonical_decoded_data_array_not_http_wire_bytes",
            "raw_depth": raw,
            "raw_hash": _hash(raw),
            "raw_metadata": metadata,
            "metadata_hash": _hash(metadata),
            "evidence": result,
            "evidence_hash": _hash(result),
        }
        payload["content_hash"] = _hash(payload)
        with self.store.write() as conn:
            conn.execute(
                "INSERT INTO liquidity_captures VALUES(?,?,?,?,?)",
                (
                    payload["id"],
                    body.inst_id,
                    received_at,
                    payload["content_hash"],
                    dumps(payload),
                ),
            )
        return payload

    def _read(self, table, identifier):
        with self.store.read() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError(
                "liquidity_evidence_not_found", "This liquidity evidence record does not exist.", 404
            )
        return checked_evidence(row, table)

    def get_capture(self, identifier):
        return self._read("liquidity_captures", identifier)

    def captures(self, inst_id=None, limit=100):
        with self.store.read() as conn:
            ids = conn.execute(
                "SELECT id FROM liquidity_captures WHERE (? IS NULL OR inst_id=?) ORDER BY received_at DESC,id DESC LIMIT ?",
                (inst_id, inst_id, limit),
            ).fetchall()
        return [self._summary(self.get_capture(row[0])) for row in ids]

    @staticmethod
    def _summary(item):
        return {k: v for k, v in item.items() if k not in {"raw_depth", "raw_metadata"}} | {
            "current_age_ms": now_ms() - item["received_at"],
        }

    def calibrate(self, body: LiquidityCalibrationInput, actor="system"):
        created_at = now_ms()
        # A researcher can choose a window, never hide an already captured bad
        # snapshot inside it by submitting only favorable capture identifiers.
        # Selection and expansion use one read snapshot of available evidence.
        with self.store.read() as conn:
            selected = []
            for identifier in body.capture_ids:
                row = conn.execute("SELECT * FROM liquidity_captures WHERE id=?", (identifier,)).fetchone()
                if not row:
                    raise PlatformError(
                        "liquidity_evidence_not_found", "This liquidity evidence record does not exist.", 404
                    )
                selected.append(checked_evidence(row, "liquidity_captures"))
            if any(c["inst_id"] != body.inst_id for c in selected):
                raise PlatformError(
                    "liquidity_market_mismatch", "One calibration must use observations of one market.", 422
                )
            selected_start = min((c["known_at"] for c in selected), default=None)
            selected_end = max((c["known_at"] for c in selected), default=None)
            start, end = body.window_start or selected_start, body.window_end or selected_end
            if any(not start <= c["known_at"] <= end for c in selected):
                raise PlatformError(
                    "liquidity_window_mismatch", "Every capture must fall inside the declared window.", 422
                )
            rows = (
                conn.execute(
                    "SELECT * FROM liquidity_captures WHERE inst_id=? AND received_at BETWEEN ? AND ? ORDER BY received_at,id LIMIT 513",
                    (body.inst_id, start, end),
                ).fetchall()
                if start is not None and end is not None
                else []
            )
            if len(rows) > 512:
                raise PlatformError(
                    "liquidity_window_too_large",
                    "This window has more than 512 available captures. Choose a narrower window.",
                    422,
                )
            captures = [checked_evidence(row, "liquidity_captures") for row in rows]
        actual_start = min((c["known_at"] for c in captures), default=None)
        actual_end = max((c["known_at"] for c in captures), default=None)
        selected_ids = set(body.capture_ids)
        selection_audit = {
            "selection_policy": "all_available_captures_in_declared_window",
            "availability_as_of": created_at,
            "all_available_count": len(captures),
            "selected_count": len(selected),
            "omitted_capture_ids": [c["id"] for c in captures if c["id"] not in selected_ids],
            "omitted_policy": "automatically_included_not_excluded",
        }
        ordered = sorted(captures, key=lambda c: (c["known_at"], c["id"]))
        timestamps, independent, repeated, regressed = set(), [], 0, False
        previous = None
        for capture in ordered:
            ts = capture["evidence"].get("exchange_ts")
            if ts is not None and previous is not None and ts < previous:
                regressed = True
            if ts is not None:
                previous = max(previous or ts, ts)
            if ts in timestamps or ts is None:
                repeated += 1
            else:
                timestamps.add(ts)
                independent.append(capture)
        independent_start = min((c["known_at"] for c in independent), default=None)
        independent_end = max((c["known_at"] for c in independent), default=None)
        elapsed = (independent_end - independent_start) if independent else 0
        gaps = [b["known_at"] - a["known_at"] for a, b in zip(independent, independent[1:], strict=False)]
        uncovered_edges = {
            "start_ms": independent_start - start
            if independent_start is not None and start is not None
            else None,
            "end_ms": end - independent_end if independent_end is not None and end is not None else None,
            "max_allowed_ms": body.max_gap_ms,
        }
        conditions = {
            "boundary_coverage": bool(independent)
            and all(
                edge is not None and 0 <= edge <= body.max_gap_ms
                for name, edge in uncovered_edges.items()
                if name != "max_allowed_ms"
            ),
            "samples": len(independent) >= body.minimum_samples,
            "elapsed": elapsed >= body.minimum_elapsed_ms,
            "cadence": bool(independent) and max(gaps, default=0) <= body.max_gap_ms,
            "fresh_at_capture": bool(captures)
            and all(
                c["evidence"].get("status") == "supported"
                and 0 <= c["evidence"].get("book_age_at_capture_ms", -1) <= body.max_book_age_ms
                for c in captures
            ),
            "timestamp_order": not regressed,
            "supported": bool(captures) and all(c["evidence"]["status"] == "supported" for c in captures),
        }
        outcomes = []
        with localcontext(ACCOUNTING_CONTEXT):
            for size in SCENARIOS:
                sides = {}
                for side in ("buy", "sell"):
                    matching = [
                        s
                        for c in captures
                        for s in c["evidence"]["scenarios"]
                        if s["side"] == side and D(s["requested_notional"]) == size
                    ]
                    passes = (
                        len(matching) == len(captures)
                        and bool(captures)
                        and all(
                            s["status"] == "complete"
                            and D(s["shortfall_bps"]) <= body.max_shortfall_bps
                            and D(s["participation_pct"]) <= body.max_participation_pct
                            for s in matching
                        )
                    )
                    sides[side] = {
                        "all_samples_pass": passes,
                        "complete_samples": sum(s["status"] == "complete" for s in matching),
                        "shortfall_bps": _quantiles(
                            s["shortfall_bps"] for s in matching if s["shortfall_bps"] is not None
                        ),
                        "participation_pct": _quantiles(s["participation_pct"] for s in matching),
                    }
                outcomes.append(
                    {
                        "notional": str(size),
                        "sides": sides,
                        "all_samples_pass": all(s["all_samples_pass"] for s in sides.values()),
                    }
                )
        data_sufficient = all(conditions.values())
        approved = max(
            (D(o["notional"]) for o in outcomes if data_sufficient and o["all_samples_pass"]), default=None
        )
        if not captures:
            status = "no_samples"
        elif not conditions["supported"]:
            status = "unsupported"
        elif not data_sufficient:
            status = "insufficient_evidence"
        else:
            status = "observational_pass" if approved is not None else "exceeds_limits"
        config = encode(body.model_dump())
        report = {
            "schema_version": 1,
            "methodology": "okx_displayed_l2_v1",
            "id": new_id(),
            "source": "okx",
            "inst_id": body.inst_id,
            "created_at": created_at,
            "created_by": actor,
            "input": config,
            "input_hash": _hash(config),
            "captures": [
                {"id": c["id"], "content_hash": c["content_hash"], "known_at": c["known_at"]} for c in ordered
            ],
            "declared_window": {"start": start, "end": end},
            "observed_window": {"start": actual_start, "end": actual_end},
            "independent_window": {"start": independent_start, "end": independent_end, "elapsed_ms": elapsed},
            "selection_audit": selection_audit,
            "uncovered_edges": uncovered_edges,
            "sample_count": len(captures),
            "independent_book_count": len(independent),
            "repeated_or_missing_timestamp_count": repeated,
            "maximum_observed_gap_ms": max(gaps, default=None),
            "conditions": conditions,
            "spread_bps": _quantiles(
                c["evidence"]["spread_bps"] for c in captures if "spread_bps" in c["evidence"]
            ),
            "status": status,
            "scenarios": outcomes,
            "approved_observed_notional": str(approved) if approved is not None else None,
            "declared_child_notional": str(body.child_notional),
            "declared_sleeve_notional": str(body.sleeve_notional),
            "child_observed_pass": approved is not None and body.child_notional <= approved,
            "sleeve_observed_pass": approved is not None and body.sleeve_notional <= approved,
            "historical_capacity": "unknown",
            "live_execution_calibration": "not_measured",
            "scope": "Observed cached organic L2 window only; no queue position, hidden liquidity, impact/replenishment, fees or executable guarantee. Both sides and every selected sample must pass; quantiles are descriptive. Historical backtest cost assumptions remain scenarios.",
        }
        report["content_hash"] = _hash(report)
        with self.store.write() as conn:
            conn.execute(
                "INSERT INTO liquidity_calibrations VALUES(?,?,?,?,?)",
                (
                    report["id"],
                    body.inst_id,
                    report["created_at"],
                    report["content_hash"],
                    dumps(report),
                ),
            )
        return report

    def get_calibration(self, identifier):
        # Verify report and its pinned captures in one read snapshot, including
        # during a concurrent verified restore of this workspace.
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM liquidity_calibrations WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError(
                    "liquidity_evidence_not_found", "This liquidity evidence record does not exist.", 404
                )
            report = checked_evidence(row, "liquidity_calibrations")
            for c in report["captures"]:
                pinned = conn.execute("SELECT * FROM liquidity_captures WHERE id=?", (c["id"],)).fetchone()
                if (
                    not pinned
                    or checked_evidence(pinned, "liquidity_captures")["content_hash"] != c["content_hash"]
                ):
                    raise PlatformError(
                        "liquidity_evidence_integrity", "A frozen calibration capture hash changed.", 409
                    )
        latest = report["independent_window"]["end"]
        age = now_ms() - latest if latest is not None else None
        return report | {
            "latest_sample_age_ms": age,
            "current_review_status": "clock_ahead"
            if age is not None and age < 0
            else "stale"
            if age is not None and age > report["input"]["review_max_age_ms"]
            else report["status"],
        }

    def calibrations(self, inst_id=None, limit=20):
        with self.store.read() as conn:
            ids = conn.execute(
                "SELECT id FROM liquidity_calibrations WHERE (? IS NULL OR inst_id=?) ORDER BY created_at DESC,id DESC LIMIT ?",
                (inst_id, inst_id, limit),
            ).fetchall()
        return [self.get_calibration(row[0]) for row in ids]

    def compare_paper(self, inst_id, limit=50, max_age_ms=2000):
        """Compare only local filled orders with evidence already known at fill.

        The local account's paper fill is synthetic. Its shortfall against an
        earlier displayed book is a model comparison, not venue fill validation.
        """
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT body FROM pro_orders WHERE source='okx' AND status='filled' ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
            books = conn.execute(
                "SELECT id,received_at FROM liquidity_captures WHERE inst_id=? ORDER BY received_at DESC LIMIT 1000",
                (inst_id,),
            ).fetchall()
        captures = [self.get_capture(row["id"]) for row in books]
        output = []
        with localcontext(ACCOUNTING_CONTEXT):
            for row in rows:
                order = json.loads(row[0])
                if order.get("inst_id") != inst_id:
                    continue
                filled_at = order["updated_at"]
                candidates = [
                    c
                    for c in captures
                    if c["evidence"]["status"] == "supported"
                    and 0 <= filled_at - c["known_at"] <= max_age_ms
                    and 0 <= filled_at - c["evidence"]["exchange_ts"] <= max_age_ms
                ]
                c = candidates[0] if candidates else None
                item = {
                    "order_id": order["id"],
                    "inst_id": inst_id,
                    "filled_at": filled_at,
                    "execution_mode": "local-paper",
                    "status": "matched" if c else "no_causal_capture",
                    "capture_id": c["id"] if c else None,
                }
                if c:
                    meta, ev = _metadata(c["raw_metadata"], inst_id), c["evidence"]
                    price, qty, mid = D(order["price"]), D(order["quantity"]), D(ev["mid"])
                    side = order["side"]
                    shortfall = ((price - mid) if side == "buy" else (mid - price)) / mid * 10000
                    walk = _walk(
                        _levels(
                            c["raw_depth"][0]["asks" if side == "buy" else "bids"],
                            side,
                            c["request"]["depth"],
                            meta,
                        ),
                        side,
                        qty * meta["base_per_unit"] * mid,
                        mid,
                        meta,
                    )
                    item |= {
                        "displayed_walk": encode(walk),
                        "model_minus_walk_bps": str(shortfall - walk["shortfall_bps"])
                        if walk["status"] == "complete"
                        else None,
                        "capture_known_at": c["known_at"],
                        "capture_age_at_fill_ms": filled_at - c["known_at"],
                        "paper_shortfall_bps": str(shortfall),
                        "paper_quantity": str(qty),
                        "quantity_unit": meta["quantity_unit"],
                        "mid": str(mid),
                        "paper_price": str(price),
                        "fee": order.get("fee"),
                        "basis": "Synthetic local paper fill against causally prior displayed mid; not live execution calibration.",
                    }
                output.append(item)
                if len(output) >= limit:
                    break
        return output
