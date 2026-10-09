"""Independent full-window oracles; synthetic timestamps are not soak evidence."""

import json
from decimal import Decimal as D

import pytest
import tidebench.forward_performance as module
from test_pro_api import client as client_fixture
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, dumps
from tidebench.strategy_registry import digest


@pytest.fixture
def book(tmp_path):
    return SimulationBook(Store(tmp_path / "performance.sqlite3"))


@pytest.fixture
def client(tmp_path):
    yield from client_fixture.__wrapped__(tmp_path)


def record(book, monkeypatch, wall, market, *, equity="10000", source="example", **changes):
    # Direct observation oracles deliberately avoid attributing synthetic dates
    # to a live public feed or real operational acceptance.
    monkeypatch.setattr(module, "now_ms", lambda: wall)
    account = book.account(source, {}) | {"equity": equity, **changes}
    quotes = {"BTC-USDT": {"source": source, "ts": market}} if market else {}
    with book.store.write() as conn:
        module.ForwardPerformance.capture(conn, source, quotes, account)
    with book.store.read() as conn:
        return conn.execute("SELECT MAX(id) FROM forward_equity WHERE source=?", (source,)).fetchone()[0]


def test_complete_window_cannot_hide_early_drawdown_beyond_five_hundred_rows(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    record(book, monkeypatch, 2000, 2000, equity="9000")
    for index in range(2, 702):
        record(book, monkeypatch, 1000 + index * 1000, 1000 + index * 1000, equity="9500")
    report = book.performance.report("example", limit=10)
    assert len(report["items"]) == 10
    assert report["summary"]["observations"] == 702
    assert abs(D(report["summary"]["return"]) - D("-.05")) < D("1e-45")
    assert D(report["summary"]["max_drawdown"]) == D(".1")
    assert report["window"]["count"] == 702
    previous_page = book.performance.report(
        "example", limit=10, before=report["next_before"], window_end=report["window"]["range_end_id"]
    )
    assert previous_page["summary"] == report["summary"]
    assert previous_page["window"] == report["window"]


def test_old_economic_gap_outside_page_blocks_return_and_never_links_across_gap(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    gap = record(
        book,
        monkeypatch,
        2000,
        2000,
        equity=None,
        valuation_status="unavailable",
        economic_status="valuation_incomplete",
    )
    for index in range(2, 505):
        record(book, monkeypatch, 1000 + index * 1000, 1000 + index * 1000, equity="9500")
    report = book.performance.report("example", limit=5)
    assert all(item["body"]["equity"] is not None for item in report["items"])
    assert report["summary"]["return"] is None
    assert report["summary"]["boundary_adjusted_return"] is None
    assert report["summary"]["valuation_gap_observations"] == 1
    assert report["summary"]["total_segments"] == 2
    after_gap = book.performance.report("example", window_start=gap + 1)
    assert D(after_gap["summary"]["return"]) == 0
    assert after_gap["summary"]["observations"] == 503


def test_wall_coverage_gap_breaks_financial_link_even_when_marks_are_complete(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    record(book, monkeypatch, 121000, 121000, equity="11000")
    report = book.performance.report("example", max_gap_ms=60000)
    assert report["summary"]["coverage_gap_intervals"] == 1
    assert report["summary"]["return"] is None
    assert D(report["summary"]["wall_coverage_pct"]) == 0
    assert report["summary"]["wall_elapsed_ms"] == 120000


def test_accelerated_example_clock_does_not_create_multiple_wall_weeks(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    record(book, monkeypatch, 2000, 1000 + 30 * module.DAY)
    report = book.performance.report("example")
    assert report["summary"]["market_elapsed_ms"] == 30 * module.DAY
    assert report["summary"]["wall_elapsed_ms"] == 1000
    assert not report["acceptance"]["passed"]
    assert not report["acceptance"]["checks"]["real_wall_duration"]
    assert not report["acceptance"]["checks"]["public_okx_observations"]


def test_pending_funding_is_an_economic_gap_including_when_account_is_flat(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    record(
        book,
        monkeypatch,
        2000,
        2000,
        equity=None,
        economic_status="funding_pending",
        pending_funding=[{"inst_id": "BTC-USDT-SWAP", "ts": 1500}],
    )
    record(book, monkeypatch, 3000, 3000)
    report = book.performance.report("example")
    assert report["summary"]["pending_funding_observations"] == 1
    assert report["summary"]["return"] is None
    assert not report["acceptance"]["checks"]["no_pending_funding"]


def test_external_flows_without_flow_time_valuation_are_labeled_estimate(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_ledger(tx_id,source,ts,asset,account,debit,credit,memo,reference) VALUES('synthetic-flow','example',1500,'USDT','contributed_capital','0','1000','Independent flow oracle','test-flow')"
        )
    record(book, monkeypatch, 2000, 2000, equity="11000")
    report = book.performance.report("example")
    assert report["summary"]["complete_valuation_chain"]
    assert report["summary"]["return"] is None
    assert D(report["summary"]["boundary_adjusted_return"]) == 0
    assert D(report["summary"]["net_pnl"]) == 0
    assert report["summary"]["external_flow_periods"] == 1
    assert "not_true_TWR" in report["summary"]["return_method"]


def test_spot_and_swap_same_underlying_gross_and_actual_costs_are_observed(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    positions = [
        {"inst_id": "BTC-USDT", "market_value": "5000", "instrument": {"base": "BTC"}},
        {"inst_id": "BTC-USDT-SWAP", "market_value": "4500", "instrument": {"base": "BTC"}},
    ]
    record(
        book,
        monkeypatch,
        2000,
        2000,
        positions=positions,
        fees_paid="12",
        funding_paid="3",
        realized_pnl="-4",
    )
    summary = book.performance.report("example")["summary"]
    assert D(summary["max_observed_gross_pct"]) == 95
    assert D(summary["max_observed_base_asset_gross_pct"]) == 95
    assert D(summary["fees_paid_change"]) == 12
    assert D(summary["funding_paid_change"]) == 3


def test_snapshot_append_replay_is_identical_and_changed_old_evidence_is_detected(book, monkeypatch):
    first = record(book, monkeypatch, 1000, 1000)
    record(book, monkeypatch, 2000, 2000, equity="9500")
    frozen = book.performance.freeze("example", "reviewer")
    record(book, monkeypatch, 3000, 3000, equity="9000")
    assert book.performance.verify(frozen["id"])["verified"]
    with book.store.write() as conn:
        row = conn.execute("SELECT body FROM forward_equity WHERE id=?", (first,)).fetchone()
        body = json.loads(row[0]) | {"equity": "9900"}
        conn.execute(
            "UPDATE forward_equity SET body=?,state_hash=? WHERE id=?", (dumps(body), digest(body), first)
        )
    assert not book.performance.verify(frozen["id"])["verified"]


def test_clock_metadata_tampering_and_body_corruption_fail_integrity(book, monkeypatch):
    first = record(book, monkeypatch, 1000, 1000)
    with book.store.write() as conn:
        conn.execute("UPDATE forward_equity SET observed_at=999999 WHERE id=?", (first,))
    with pytest.raises(PlatformError) as exc:
        book.performance.report("example")
    assert exc.value.code == "performance_integrity"


def test_missing_quotes_are_recorded_instead_of_silently_dropped(book, monkeypatch):
    record(
        book,
        monkeypatch,
        1000,
        0,
        equity=None,
        valuation_status="unavailable",
        economic_status="valuation_incomplete",
    )
    report = book.performance.report("example")
    assert report["window"]["count"] == 1
    assert report["summary"]["valuation_gap_observations"] == 1
    assert report["summary"]["market_elapsed_ms"] is None


def test_actual_recovery_audit_counts_are_frozen_in_the_wall_window(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO audit(source,ts,kind,summary,details) VALUES('example',1500,'portfolio.batch_compensated','Synthetic recovery oracle','{}')"
        )
        conn.execute(
            "INSERT INTO audit(source,ts,kind,summary,details) VALUES('example',500,'ops.incident_resolved','Outside declared window','{}')"
        )
    record(book, monkeypatch, 2000, 2000)
    frozen = book.performance.freeze("example", "reviewer")
    assert frozen["body"]["summary"]["recovery_counts"] == {
        "portfolio_compensations_completed": 1,
        "operational_conditions_resolved": 0,
        "workspace_restores_completed": 0,
    }
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO audit(source,ts,kind,summary,details) VALUES('example',1600,'ops.incident_resolved','Late append excluded by audit id','{}')"
        )
    assert book.performance.verify(frozen["id"])["verified"]


def test_legacy_unbound_clock_record_is_disclosed_and_does_not_pass_acceptance(book, monkeypatch):
    first = record(book, monkeypatch, 1000, 1000)
    with book.store.write() as conn:
        body = json.loads(conn.execute("SELECT body FROM forward_equity WHERE id=?", (first,)).fetchone()[0])
        body.pop("observation_identity")
        conn.execute(
            "UPDATE forward_equity SET body=?,state_hash=? WHERE id=?", (dumps(body), digest(body), first)
        )
    report = book.performance.report("example")
    assert report["summary"]["legacy_clock_observations"] == 1
    assert not report["acceptance"]["checks"]["bound_observation_clocks"]


def test_performance_snapshot_api_freeze_read_verify_and_window_validation(client):
    initial = client.get("/api/v1/pro/execution/performance", params={"source": "example"})
    assert initial.status_code == 200
    assert initial.json()["summary"]["scope"] == "entire_frozen_observation_window"
    frozen = client.post("/api/v1/pro/execution/performance/snapshots", json={"source": "example"})
    assert frozen.status_code == 201, frozen.text
    identifier = frozen.json()["id"]
    assert (
        client.get(f"/api/v1/pro/execution/performance/snapshots/{identifier}").json()["content_hash"]
        == frozen.json()["content_hash"]
    )
    verified = client.get(f"/api/v1/pro/execution/performance/snapshots/{identifier}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"]
    assert client.get("/api/v1/pro/execution/performance", params={"max_gap_ms": 1}).status_code == 422


def test_financial_restore_breaks_return_segments_and_never_creates_negative_cost(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000, equity="11000", fees_paid="10")
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO audit(source,ts,kind,summary,details) VALUES('system',1500,'backup.restored','Financial replacement oracle','{}')"
        )
    record(book, monkeypatch, 2000, 2000, equity="10000", fees_paid="3")
    report = book.performance.report("example")
    summary = report["summary"]
    assert summary["restore_events"] == 1
    assert summary["financial_counter_regressions"] == 1
    assert summary["return"] is None and summary["net_pnl"] is None
    assert summary["fees_paid_change"] is None
    assert D(summary["fees_paid_endpoint_counter_change"]) == -7
    assert summary["total_segments"] == 2
    assert not summary["complete_valuation_chain"]
    assert not report["acceptance"]["checks"]["no_financial_discontinuity"]


def test_fee_counter_regression_without_restore_audit_still_breaks_account_evidence(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000, fees_paid="10")
    record(book, monkeypatch, 2000, 2000, fees_paid="0")
    report = book.performance.report("example")
    assert report["summary"]["financial_counter_regressions"] == 1
    assert report["summary"]["return"] is None
    assert report["summary"]["fees_paid_change"] is None


def test_incomplete_final_endpoint_does_not_mislabel_partial_profit_and_keeps_actual_fees(book, monkeypatch):
    record(book, monkeypatch, 1000, 1000)
    record(book, monkeypatch, 2000, 2000, equity="10100", fees_paid="2")
    record(
        book,
        monkeypatch,
        3000,
        3000,
        equity=None,
        valuation_status="unavailable",
        economic_status="valuation_incomplete",
        fees_paid="5",
    )
    summary = book.performance.report("example")["summary"]
    assert summary["net_pnl"] is None
    assert D(summary["nearest_complete_endpoint_delta"]["value"]) == 100
    assert D(summary["fees_paid_change"]) == 5
    assert summary["net_pnl_basis"] == "window_boundary_valuation_incomplete"


def test_ledger_sequence_rollback_without_restore_audit_is_financial_discontinuity(book, monkeypatch):
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_ledger(tx_id,source,ts,asset,account,debit,credit,memo,reference) VALUES('oracle-ledger','example',500,'USDT','cash','0','0','Independent ledger sequence oracle','counter-oracle')"
        )
    record(book, monkeypatch, 1000, 1000, equity="11000")
    with book.store.write() as conn:
        conn.execute("DELETE FROM pro_ledger WHERE tx_id='oracle-ledger'")
    record(book, monkeypatch, 2000, 2000, equity="10000")
    report = book.performance.report("example")
    summary = report["summary"]
    assert summary["ledger_sequence_regressions"] == 1
    assert summary["financial_counter_regressions"] == 1
    assert summary["clock_regressions"] == 1
    assert summary["return"] is None and summary["net_pnl"] is None
    assert summary["fees_paid_change"] is None
    assert D(summary["endpoint_equity_minus_flow_delta"]) == -1000
    assert not report["acceptance"]["checks"]["no_financial_discontinuity"]
