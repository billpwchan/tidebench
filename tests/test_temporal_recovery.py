"""Restore keeps public evidence, while financial reports remain in their epoch."""

from pathlib import Path

import httpx
import pytest
from test_historical_lifecycle import END, HOUR, fact
from test_liquidity_evidence import PublicMarket, book
from tidebench.config import Settings
from tidebench.historical_lifecycle import capture_events
from tidebench.liquidity_evidence import LiquidityCalibrationInput, LiquidityCaptureInput, _hash
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import BackupService, PlatformError
from tidebench.store import dumps


@pytest.fixture
def runtime(tmp_path):
    market = MarketService(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("External network forbidden"))
        )
    )
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None), market)
    return app.state.professional


async def public_capture(runtime, monkeypatch, timestamp):
    monkeypatch.setattr("tidebench.liquidity_evidence.time.time_ns", lambda: timestamp * 1000000)
    monkeypatch.setattr("tidebench.liquidity_evidence.now_ms", lambda: timestamp)
    provider = PublicMarket()
    provider.depth = book(timestamp - 10, thick=True)
    runtime.liquidity.market = provider
    return await runtime.liquidity.capture(LiquidityCaptureInput(inst_id="BTC-USDT"))


@pytest.mark.asyncio
async def test_restore_unions_public_source_evidence_but_not_later_financial_reports(runtime, monkeypatch):
    first = await public_capture(runtime, monkeypatch, END)
    runtime.book.observe("example", {})
    initial = runtime.book.performance.freeze("example", "audit")
    backup = runtime.backups.create("before-later-public-and-financial-facts")
    second = await public_capture(runtime, monkeypatch, END + 300000)
    calibration = runtime.liquidity.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT", capture_ids=[first["id"], second["id"]], minimum_samples=2
        )
    )
    capture_events(runtime.store, "example", "BTC-USDT", [fact("BTC-USDT", "listing")], HOUR, END)
    runtime.book.observe("example", {})
    later = runtime.book.performance.freeze("example", "audit")
    restored = runtime.backups.restore(backup["id"])
    assert restored["retained_temporal_evidence"] == {
        "historical_lifecycle_events": 1,
        "liquidity_captures": 1,
        "liquidity_calibrations": 1,
    }
    assert runtime.liquidity.get_capture(second["id"])["content_hash"] == second["content_hash"]
    assert runtime.liquidity.get_calibration(calibration["id"])["content_hash"] == calibration["content_hash"]
    assert runtime.book.performance.verify(initial["id"])["verified"]
    with pytest.raises(PlatformError, match="not found"):
        runtime.book.performance.snapshot(later["id"])
    # The complete later financial epoch remains recoverable in the safety image.
    with BackupService._readonly(
        Path(runtime.backups.directory) / (restored["safety_backup_id"] + ".sqlite3")
    ) as conn:
        assert conn.execute("SELECT COUNT(*) FROM forward_performance_snapshots").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_backup_verifies_public_payload_hash_and_pinned_capture_reference(runtime, monkeypatch):
    first = await public_capture(runtime, monkeypatch, END)
    calibration = runtime.liquidity.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[first["id"]], minimum_samples=2)
    )
    with runtime.store.write() as conn:
        conn.execute("DROP TRIGGER immutable_liquidity_calibrations_update")
        body = dict(calibration)
        body["captures"][0]["content_hash"] = "0" * 64
        body["content_hash"] = _hash({k: v for k, v in body.items() if k != "content_hash"})
        conn.execute(
            "UPDATE liquidity_calibrations SET content_hash=?,payload=? WHERE id=?",
            (body["content_hash"], dumps(body), calibration["id"]),
        )
    with runtime.store.read() as conn, pytest.raises(PlatformError, match="reference") as failed:
        BackupService._check_connection(conn)
    assert failed.value.code == "backup_integrity"


def test_backup_rejects_indexed_snapshot_identity_change(runtime):
    runtime.book.observe("example", {})
    frozen = runtime.book.performance.freeze("example", "audit")
    with runtime.store.write() as conn:
        conn.execute("UPDATE forward_performance_snapshots SET source='okx' WHERE id=?", (frozen["id"],))
    with runtime.store.read() as conn, pytest.raises(PlatformError) as failed:
        BackupService._check_connection(conn)
    assert failed.value.code == "backup_integrity"


@pytest.mark.asyncio
async def test_review_pins_exact_report_and_never_uses_public_liquidity_as_example_evidence(
    runtime, monkeypatch
):
    first = await public_capture(runtime, monkeypatch, END)
    report = runtime.liquidity.calibrate(
        LiquidityCalibrationInput(inst_id="BTC-USDT", capture_ids=[first["id"]], minimum_samples=2)
    )
    with runtime.store.read() as conn:
        review = runtime.portfolio_releases.liquidity_review("okx", ["BTC-USDT", "ETH-USDT"], conn)
        example = runtime.portfolio_releases.liquidity_review("example", ["BTC-USDT"], conn)
    assert review["reports"][0]["id"] == report["id"]
    assert review["reports"][0]["content_hash"] == report["content_hash"]
    assert review["missing_markets"] == ["ETH-USDT"]
    assert example["reports"] == []
    second = await public_capture(runtime, monkeypatch, END + 300000)
    later = runtime.liquidity.calibrate(
        LiquidityCalibrationInput(
            inst_id="BTC-USDT", capture_ids=[first["id"], second["id"]], minimum_samples=2
        )
    )
    assert later["id"] != review["reports"][0]["id"]
    assert (
        runtime.liquidity.get_calibration(review["reports"][0]["id"])["content_hash"]
        == review["reports"][0]["content_hash"]
    )
