"""Independent arithmetic, timing, persistence and permission checks for L2 evidence."""

import hashlib
import sqlite3
from decimal import Decimal, localcontext
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from tidebench.config import Settings
from tidebench.liquidity_evidence import (
    LiquidityCalibrationInput,
    LiquidityCaptureInput,
    LiquidityEvidence,
    evaluate_depth,
)
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, dumps

D = Decimal
T = 1767225600000


def metadata(symbol="BTC-USDT", **changes):
    swap = symbol.endswith("-SWAP")
    return [
        {
            "instId": symbol,
            "instType": "SWAP" if swap else "SPOT",
            "state": "live",
            "baseCcy": "BTC",
            "quoteCcy": "USDT",
            "tickSz": "0.01",
            "lotSz": "0.01",
            "minSz": "0.01",
            "ctType": "linear",
            "settleCcy": "USDT",
            "ctValCcy": "BTC",
            "ctVal": "0.01",
            "ctMult": "1",
            **changes,
        }
    ]


def book(timestamp=T - 10, thick=False):
    return [
        {
            "ts": str(timestamp),
            "asks": [["100.01", "10000", "0", "5"]]
            if thick
            else [["100.10", "5", "0", "1"], ["100.20", "5", "0", "2"]],
            "bids": [["99.99", "10000", "0", "5"]]
            if thick
            else [["99.90", "5", "0", "1"], ["99.80", "5", "0", "2"]],
        }
    ]


class PublicMarket:
    region = "global"

    def __init__(self):
        self.rows, self.depth, self.calls = metadata(), book(thick=True), []

    async def _get(self, path, params):
        self.calls.append((path, params))
        return self.rows if path == "/api/v5/public/instruments" else self.depth


@pytest.fixture
def service(tmp_path, monkeypatch):
    clock = SimpleNamespace(at=T)
    monkeypatch.setattr("tidebench.liquidity_evidence.time.time_ns", lambda: clock.at * 1000000 + 123)
    monkeypatch.setattr("tidebench.liquidity_evidence.time.monotonic_ns", lambda: 123456)
    monkeypatch.setattr("tidebench.liquidity_evidence.now_ms", lambda: clock.at)
    subject = LiquidityEvidence(Store(tmp_path / "depth.sqlite3"), PublicMarket())
    subject.clock = clock
    return subject


async def capture(service, at=T, symbol="BTC-USDT", raw=None):
    service.clock.at = at
    service.market.rows = metadata(symbol)
    service.market.depth = raw or book(at - 10, thick=True)
    return await service.capture(LiquidityCaptureInput(inst_id=symbol), "qa")


def scenario(result, notional, side):
    return next(
        r for r in result["scenarios"] if r["requested_notional"] == str(notional) and r["side"] == side
    )


def test_two_level_vwap_matches_independent_hand_calculation_and_does_not_extend_depth():
    result = evaluate_depth(book(), metadata(), "BTC-USDT", 400, T)
    buy, sell = scenario(result, 1000, "buy"), scenario(result, 1000, "sell")
    assert D(result["mid"]) == 100 and D(result["spread_bps"]) == 20
    assert D(buy["vwap"]) == D("100.15") and D(sell["vwap"]) == D("99.85")
    assert D(buy["shortfall_bps"]) == D(sell["shortfall_bps"]) == 15
    assert D(buy["matched_notional"]) == D("1001.50")
    oversize = scenario(result, 100000, "buy")
    assert oversize["status"] == "depth_exhausted"
    assert D(oversize["filled_quantity"]) == 10 and D(oversize["unfilled_quantity"]) == 990
    assert D(oversize["fill_ratio_pct"]) == 1 and D(oversize["participation_pct"]) == 100


def test_derivative_contract_multiplier_and_minimum_quantity_are_applied():
    raw = book(thick=True)
    result = evaluate_depth(
        raw, metadata("BTC-USDT-SWAP", ctVal="0.1", ctMult="2", lotSz="1", minSz="1"), "BTC-USDT-SWAP", 400, T
    )
    buy = scenario(result, 1000, "buy")
    assert result["metadata"]["quantity_unit"] == "contracts"
    assert D(result["metadata"]["base_per_unit"]) == D("0.2")
    assert D(buy["requested_quantity"]) == 50 and D(buy["requested_base_quantity"]) == 10
    minimum = evaluate_depth(raw, metadata(minSz="100", lotSz="3"), "BTC-USDT", 400, T)
    # The displayed test book is not lot-aligned, so it cannot certify this venue.
    assert minimum["status"] == "unsupported"
    raw[0]["asks"][0][1] = raw[0]["bids"][0][1] = "9999"
    minimum = evaluate_depth(raw, metadata(minSz="100", lotSz="3"), "BTC-USDT", 400, T)
    assert D(scenario(minimum, 1000, "buy")["requested_quantity"]) == 102


def test_result_does_not_depend_on_caller_decimal_context():
    expected = evaluate_depth(book(), metadata(), "BTC-USDT", 400, T)
    with localcontext() as ctx:
        ctx.prec = 4
        assert evaluate_depth(book(), metadata(), "BTC-USDT", 400, T) == expected


@pytest.mark.parametrize(
    "kind",
    ["crossed", "locked", "duplicate", "unsorted", "negative", "nan", "misaligned", "timestamp", "empty"],
)
def test_invalid_depth_is_explicitly_unsupported(kind):
    raw = book()
    if kind in {"crossed", "locked"}:
        raw[0]["bids"][0][0] = "100.30" if kind == "crossed" else "100.10"
    elif kind in {"duplicate", "unsorted"}:
        raw[0]["asks"][1][0] = "100.10" if kind == "duplicate" else "100.00"
    elif kind in {"negative", "nan"}:
        raw[0]["asks"][0][1] = "-1" if kind == "negative" else "NaN"
    elif kind == "misaligned":
        raw[0]["asks"][0][0] = "100.101"
    elif kind == "timestamp":
        raw[0]["ts"] = "tomorrow"
    else:
        raw[0]["asks"] = []
    result = evaluate_depth(raw, metadata(), "BTC-USDT", 400, T)
    assert result["status"] == "unsupported" and result["reason"] and not result["scenarios"]


@pytest.mark.parametrize(
    "changes",
    [
        {"ctType": "inverse"},
        {"ctValCcy": "USDT"},
        {"ctMult": ""},
        {"state": "suspend"},
        {"instId": "ETH-USDT-SWAP"},
    ],
)
def test_unsupported_derivative_metadata_never_uses_spot_size(changes):
    result = evaluate_depth(book(), metadata("BTC-USDT-SWAP", **changes), "BTC-USDT-SWAP", 400, T)
    assert result["status"] == "unsupported" and not result["scenarios"]


async def test_capture_freezes_public_request_units_ns_raw_and_hashes(service):
    item = await capture(service)
    assert service.market.calls == [
        ("/api/v5/public/instruments", {"instType": "SPOT", "instId": "BTC-USDT"}),
        ("/api/v5/market/books", {"instId": "BTC-USDT", "sz": "400"}),
    ]
    assert item["known_at"] == item["received_at"] == T
    assert item["received_ns"] == str(T * 1000000 + 123)
    assert item["payload_encoding"] == "canonical_decoded_data_array_not_http_wire_bytes"
    assert item["raw_depth"] == service.market.depth and item["raw_metadata"] == metadata()
    assert service.get_capture(item["id"]) == item
    assert {"raw_depth", "raw_metadata"}.isdisjoint(service.captures()[0])
    for operation in ("UPDATE liquidity_captures SET inst_id='ETH-USDT'", "DELETE FROM liquidity_captures"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"), service.store.write() as conn:
            conn.execute(operation)


async def samples(service, count=12, interval=30000, **values):
    return [await capture(service, T + i * interval, **values) for i in range(count)]


async def test_calibration_conservative_all_samples_frozen_input_and_dynamic_stale(service):
    observations = await samples(service)
    report = service.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[r["id"] for r in observations])
    )
    assert report["status"] == "observational_pass" and all(report["conditions"].values())
    assert report["approved_observed_notional"] == "100000"
    assert report["child_observed_pass"] and report["sleeve_observed_pass"]
    assert (
        report["historical_capacity"] == "unknown" and report["live_execution_calibration"] == "not_measured"
    )
    assert report["independent_book_count"] == 12 and report["independent_window"]["elapsed_ms"] == 330000
    assert report["scenarios"][0]["sides"]["buy"]["shortfall_bps"]["worst"] == "1.0000"
    frozen = report["content_hash"]
    service.clock.at += 300001
    checked = service.get_calibration(report["id"])
    assert checked["current_review_status"] == "stale" and checked["content_hash"] == frozen
    for operation in ("UPDATE liquidity_calibrations SET created_at=1", "DELETE FROM liquidity_calibrations"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"), service.store.write() as conn:
            conn.execute(operation)


async def test_repeated_cached_books_do_not_create_sample_count_or_elapsed(service):
    observations = await samples(service, count=20, raw=book(T - 10, thick=True))
    report = service.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT", capture_ids=[r["id"] for r in observations], max_book_age_ms=60000
        )
    )
    assert report["independent_book_count"] == 1 and report["repeated_or_missing_timestamp_count"] == 19
    assert report["independent_window"]["elapsed_ms"] == 0
    assert report["status"] == "insufficient_evidence" and report["approved_observed_notional"] is None


@pytest.mark.parametrize(
    "kind",
    [
        "few_samples",
        "short_elapsed",
        "long_gap",
        "stale_book",
        "clock_ahead",
        "regressed_cache",
        "one_bad_side",
    ],
)
async def test_no_approval_for_temporal_or_single_sample_failure(service, kind):
    observations = await samples(
        service,
        count=2 if kind == "few_samples" else 12,
        interval=1000 if kind == "short_elapsed" else 90000 if kind == "long_gap" else 30000,
    )
    if kind in {"stale_book", "clock_ahead", "regressed_cache", "one_bad_side"}:
        at = T + 12 * 30000
        raw = book(
            at - 10000
            if kind == "stale_book"
            else at + 1000
            if kind == "clock_ahead"
            else T - 1000
            if kind == "regressed_cache"
            else at - 10,
            thick=True,
        )
        if kind == "one_bad_side":
            raw[0]["bids"][0][1] = "1"
        observations.append(await capture(service, at, raw=raw))
    report = service.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[r["id"] for r in observations])
    )
    assert report["approved_observed_notional"] is None
    assert report["status"] != "observational_pass"


async def test_oversize_or_one_side_liquidity_is_not_approved_and_empty_is_honest(service):
    observations = await samples(service, raw=book())
    # Distinct receipts of one cached thin snapshot cannot create observations.
    result = service.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[r["id"] for r in observations])
    )
    assert result["approved_observed_notional"] is None
    assert not result["scenarios"][-1]["all_samples_pass"]
    empty = service.calibrate(LiquidityCalibrationInput(inst_id="BTC-USDT"))
    assert empty["status"] == "no_samples" and empty["approved_observed_notional"] is None


async def test_calibration_rejects_market_window_and_duplicate_identity(service):
    row = await capture(service)
    with pytest.raises(PlatformError, match="one market"):
        service.calibrate(LiquidityCalibrationInput(inst_id="ETH-USDT", capture_ids=[row["id"]]))
    with pytest.raises(PlatformError, match="declared window"):
        service.calibrate(
            LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[row["id"]], window_start=T + 1)
        )
    with pytest.raises(ValueError, match="unique"):
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[row["id"], row["id"]])


async def test_content_integrity_is_checked_on_read_and_report_dependencies(service):
    row = await capture(service)
    report = service.calibrate(LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[row["id"]]))
    with service.store.write() as conn:
        conn.execute("DROP TRIGGER immutable_liquidity_captures_update")
        conn.execute("UPDATE liquidity_captures SET received_at=1")
    for getter, identifier in ((service.get_capture, row["id"]), (service.get_calibration, report["id"])):
        with pytest.raises(PlatformError, match="verification"):
            getter(identifier)


async def test_paper_comparison_cannot_use_future_or_stale_capture_and_is_only_local(service):
    SimulationBook(service.store)
    prior = await capture(service, T)
    await capture(service, T + 1000)  # Captured after the local order below.
    body = {
        "id": "paper",
        "inst_id": "BTC-USDT",
        "quantity": "1",
        "side": "buy",
        "price": "100.05",
        "updated_at": T + 100,
        "fee": "0.1",
    }
    with service.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_orders VALUES(?,?,?,?,?,?,?,?,?)",
            ("paper", "okx", "key", "{}", "filled", dumps(body), "0", T + 100, T + 100),
        )
    row = service.compare_paper("BTC-USDT")[0]
    assert row["capture_id"] == prior["id"] and row["execution_mode"] == "local-paper"
    assert D(row["paper_shortfall_bps"]) == 5 and D(row["model_minus_walk_bps"]) == 4
    assert row["displayed_walk"]["status"] == "complete"
    body["updated_at"] = T + 10000
    with service.store.write() as conn:
        conn.execute("UPDATE pro_orders SET body=?", (dumps(body),))
    assert service.compare_paper("BTC-USDT")[0]["status"] == "no_causal_capture"


def test_api_capture_calibration_csrf_researcher_write_and_viewer_read(tmp_path):
    def upstream(request):
        rows = metadata() if request.url.path == "/api/v5/public/instruments" else book(thick=True)
        return httpx.Response(200, json={"code": "0", "data": rows})

    market = MarketService(client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None), market)
    prefix = "/api/v1/pro/research/liquidity"
    with TestClient(app) as client:
        setup = client.post(
            "/api/v1/auth/setup", json={"username": "research", "password": "liquidity-test-password"}
        )
        client.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        for name, role in (("researcher", "researcher"), ("observer", "viewer")):
            assert (
                client.post(
                    "/api/v1/auth/users",
                    json={"username": name, "password": "liquidity-test-password", "role": role},
                ).status_code
                == 201
            )
        client.post("/api/v1/auth/logout")
        login = client.post(
            "/api/v1/auth/login", json={"username": "researcher", "password": "liquidity-test-password"}
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        captured = client.post(prefix + "/captures", json={"inst_id": "BTC-USDT"})
        assert captured.status_code == 201, captured.text
        row = captured.json()
        assert client.get(prefix + "/captures/" + row["id"]).json()["content_hash"] == row["content_hash"]
        report = client.post(
            prefix + "/calibrations", json={"inst_id": "BTC-USDT", "capture_ids": [row["id"]]}
        )
        assert report.status_code == 201 and report.json()["approved_observed_notional"] is None
        assert client.get(prefix + "/calibrations").json()["items"][0]["id"] == report.json()["id"]
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.post(prefix + "/captures", json={"inst_id": "BTC-USDT"}).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        client.post("/api/v1/auth/logout")
        login = client.post(
            "/api/v1/auth/login", json={"username": "observer", "password": "liquidity-test-password"}
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.get(prefix + "/captures").status_code == 200
        assert client.post(prefix + "/captures", json={"inst_id": "BTC-USDT"}).status_code == 403
        assert client.post(prefix + "/calibrations", json={"inst_id": "BTC-USDT"}).status_code == 403


async def test_recomputed_hash_cannot_turn_inconsistent_raw_arithmetic_into_evidence(service):
    row = await capture(service)
    row["evidence"]["scenarios"][0]["vwap"] = "1"
    row["evidence_hash"] = hashlib.sha256(dumps(row["evidence"]).encode()).hexdigest()
    row["content_hash"] = hashlib.sha256(
        dumps({k: v for k, v in row.items() if k != "content_hash"}).encode()
    ).hexdigest()
    with service.store.write() as conn:
        conn.execute("DROP TRIGGER immutable_liquidity_captures_update")
        conn.execute(
            "UPDATE liquidity_captures SET payload=?,content_hash=?", (dumps(row), row["content_hash"])
        )
    with pytest.raises(PlatformError, match="verification"):
        service.get_capture(row["id"])


async def test_future_receipt_does_not_become_fresh_current_evidence(service):
    observations = await samples(service)
    report = service.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[r["id"] for r in observations])
    )
    service.clock.at = T
    checked = service.get_calibration(report["id"])
    assert checked["current_review_status"] == "clock_ahead" and checked["latest_sample_age_ms"] < 0
    assert service.captures()[0]["current_age_ms"] < 0


async def test_cherry_picked_good_captures_cannot_hide_bad_book_inside_window(service):
    first = await capture(service, T)
    bad = book(T + 30000 - 10, thick=True)
    bad[0]["bids"][0][1] = "1"
    middle = await capture(service, T + 30000, raw=bad)
    last = await capture(service, T + 60000)
    report = service.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT",
            capture_ids=[first["id"], last["id"]],
            minimum_samples=2,
            minimum_elapsed_ms=1000,
        )
    )
    assert report["status"] == "exceeds_limits" and report["approved_observed_notional"] is None
    assert report["input"]["capture_ids"] == [first["id"], last["id"]]
    assert len(report["captures"]) == report["selection_audit"]["all_available_count"] == 3
    assert report["selection_audit"]["omitted_capture_ids"] == [middle["id"]]
    assert report["selection_audit"]["omitted_policy"] == "automatically_included_not_excluded"
    assert service.get_calibration(report["id"])["content_hash"] == report["content_hash"]


async def test_explicit_window_without_selected_ids_includes_all_available_books(service):
    first = await capture(service, T)
    last = await capture(service, T + 30000)
    report = service.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT",
            window_start=T,
            window_end=T + 30000,
            minimum_samples=2,
            minimum_elapsed_ms=1000,
        )
    )
    assert report["status"] == "observational_pass"
    assert {r["id"] for r in report["captures"]} == {first["id"], last["id"]}
    assert (
        report["selection_audit"]["selected_count"] == 0
        and len(report["selection_audit"]["omitted_capture_ids"]) == 2
    )
    assert service.get_calibration(report["id"])["content_hash"] == report["content_hash"]


async def test_five_minute_observations_cannot_establish_an_entire_day_window(service):
    observations = await samples(service)
    report = service.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT",
            capture_ids=[r["id"] for r in observations],
            window_start=T - 12 * 3600000,
            window_end=T + 12 * 3600000,
        )
    )
    assert (
        report["conditions"]["samples"]
        and report["conditions"]["elapsed"]
        and report["conditions"]["cadence"]
    )
    assert not report["conditions"]["boundary_coverage"]
    assert report["status"] == "insufficient_evidence" and report["approved_observed_notional"] is None
    assert report["uncovered_edges"]["start_ms"] == 12 * 3600000
    assert report["uncovered_edges"]["end_ms"] > 11 * 3600000


async def test_full_available_window_bound_requires_narrower_window_instead_of_truncation(service):
    original = await capture(service)
    with service.store.write() as conn:
        for number in range(1, 513):
            row = {**original, "id": f"{number:032x}"}
            row["content_hash"] = hashlib.sha256(
                dumps({k: v for k, v in row.items() if k != "content_hash"}).encode()
            ).hexdigest()
            conn.execute(
                "INSERT INTO liquidity_captures VALUES(?,?,?,?,?)",
                (row["id"], row["inst_id"], row["received_at"], row["content_hash"], dumps(row)),
            )
    with pytest.raises(PlatformError, match="narrower window"):
        service.calibrate(LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[original["id"]]))
