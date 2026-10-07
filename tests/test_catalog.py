import asyncio
import json
import sqlite3
from decimal import Decimal

import httpx
import pytest
from tidebench.catalog import CATALOG_BARS, CatalogService
from tidebench.market import EXAMPLE_ANCHOR, MarketError, MarketService
from tidebench.store import Store, now_ms

START = 1_735_689_600_000  # 2025-01-01 UTC
HOUR = 3_600_000


def raw_instrument(swap=False):
    return {
        "instId": "BTC-USDT-SWAP" if swap else "BTC-USDT",
        "instType": "SWAP" if swap else "SPOT",
        "instFamily": "BTC-USDT" if swap else "",
        "baseCcy": "" if swap else "BTC",
        "quoteCcy": "" if swap else "USDT",
        "settleCcy": "USDT" if swap else "",
        "ctType": "linear" if swap else "",
        "ctVal": "0.01" if swap else "",
        "ctMult": "1" if swap else "",
        "ctValCcy": "BTC" if swap else "",
        "tickSz": "0.1",
        "lotSz": "0.01" if swap else "0.00001",
        "minSz": "0.01" if swap else "0.00001",
        "state": "live",
        "listTime": "1609459200000",
    }


def raw_candle(ts, kind="trade", confirmed="1"):
    return (
        [str(ts), "100", "102", "99", "101", "1.23", "123", "123", confirmed]
        if kind == "trade"
        else [str(ts), "100", "102", "99", "101", confirmed]
    )


@pytest.fixture
async def service(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected network"))
    )
    market = MarketService(client=client)
    catalog = CatalogService(Store(tmp_path / "test.sqlite"), market)
    yield catalog
    await catalog.stop_polling()
    await client.aclose()


async def test_example_long_range_persists_isolated_immutable_versions(service):
    end = START + 1500 * HOUR
    first = service.create_job("BTC-USDT-SWAP", "trade", "1H", START, end, "example")
    result = await service.run_job(first["id"])
    assert result["status"] == "completed"
    assert result["rows"] == 1500
    assert result["pages"] == 5
    assert result["progress"] == 1
    manifest = service.get_dataset(result["dataset_id"])
    assert manifest["source"] == "example" and manifest["synthetic"]
    assert manifest["volume_unit"] == "contracts"
    assert manifest["quality"]["complete"]
    assert manifest["metadata"]["ct_val"] == "0.01"
    bars = service.load_candles(manifest["id"])
    assert len(bars) == 1500 and bars[0].ts == START and bars[-1].ts == end - HOUR
    second = service.create_job("BTC-USDT-SWAP", "trade", "1H", START, end, "example")
    second = await service.run_job(second["id"])
    other = service.get_dataset(second["dataset_id"])
    assert other["version"] == 2
    assert other["content_hash"] == manifest["content_hash"]
    assert not service.list_datasets("okx")
    with service.store.write() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM catalog_records WHERE job_id=?", (result["id"],))
    assert service.verify_dataset(manifest["id"])


async def test_real_cursor_and_page_commit_survive_transport_failure_and_retry(service):
    end = START + 500 * HOUR
    calls = []
    broken = True

    async def get(path, params):
        nonlocal broken
        if "instruments" in path:
            return [raw_instrument()]
        cursor = int(params["after"])
        calls.append(cursor)
        if len(calls) == 2 and broken:
            raise MarketError("upstream_unavailable", "Injected failure")
        return [raw_candle(cursor - n * HOUR) for n in range(1, 301)]

    service.market._get = get
    job = service.create_job("BTC-USDT", "trade", "1H", START, end)
    failed = await service.run_job(job["id"])
    assert failed["status"] == "failed" and failed["rows"] == 300
    assert failed["cursor"] == end - 300 * HOUR
    broken = False
    replacement = CatalogService(service.store, service.market)
    replacement.retry_job(job["id"])
    completed = await replacement.run_job(job["id"])
    assert completed["status"] == "completed" and completed["rows"] == 500
    assert calls == [end, end - 300 * HOUR, end - 300 * HOUR]


async def test_shutdown_requeues_without_losing_cursor(service):
    end = START + 500 * HOUR
    calls = 0

    async def get(path, params):
        nonlocal calls
        if "instruments" in path:
            return [raw_instrument()]
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError
        return [raw_candle(int(params["after"]) - n * HOUR) for n in range(1, 301)]

    service.market._get = get
    job = service.create_job("BTC-USDT", "trade", "1H", START, end)
    with pytest.raises(asyncio.CancelledError):
        await service.run_job(job["id"])
    queued = service.get_job(job["id"])
    assert queued["status"] == "queued" and queued["rows"] == 300
    assert service.resume_pending()[0]["cursor"] == end - 300 * HOUR
    assert (await service.run_job(job["id"]))["status"] == "completed"


async def test_cancellation_discards_inflight_page(service):
    entered, release = asyncio.Event(), asyncio.Event()

    async def get(path, params):
        if "instruments" in path:
            return [raw_instrument()]
        entered.set()
        await release.wait()
        return [raw_candle(START)]

    service.market._get = get
    job = service.create_job("BTC-USDT", "trade", "1H", START, START + HOUR)
    task = asyncio.create_task(service.run_job(job["id"]))
    await entered.wait()
    service.cancel_job(job["id"])
    release.set()
    result = await task
    assert result["status"] == "canceled" and result["rows"] == 0
    assert not service.list_datasets()


async def test_gap_unconfirmed_and_short_history_are_degraded(service):
    calls = 0

    async def get(path, params):
        nonlocal calls
        if "instruments" in path:
            return [raw_instrument()]
        calls += 1
        return (
            [raw_candle(START + 2 * HOUR, confirmed="0"), raw_candle(START), raw_candle(START - HOUR)]
            if calls == 1
            else []
        )

    service.market._get = get
    job = service.create_job("BTC-USDT", "trade", "1H", START, START + 3 * HOUR)
    result = await service.run_job(job["id"])
    assert result["status"] == "degraded" and result["rows"] == 1
    manifest = service.get_dataset(result["dataset_id"])
    assert not manifest["quality"]["complete"]
    assert manifest["quality"]["gaps"] == [{"start": START + HOUR, "end": START + 3 * HOUR}]


async def test_stalled_pagination_is_bounded(service):
    async def get(path, params):
        return [raw_instrument()] if "instruments" in path else [raw_candle(int(params["after"]))]

    service.market._get = get
    job = service.create_job("BTC-USDT", "trade", "1H", START, START + HOUR)
    result = await service.run_job(job["id"])
    assert result["status"] == "failed" and "pagination_stalled" in result["error"]


async def test_funding_uses_realized_signed_rates_and_retention_coverage(service):
    calls = 0

    async def get(path, params):
        nonlocal calls
        if "instruments" in path:
            return [raw_instrument(True)]
        if "history-mark-price" in path:
            return [raw_candle(int(params["after"]) - 60_000, "mark")]
        calls += 1
        return [
            {
                "instId": "BTC-USDT-SWAP",
                "fundingTime": str(START + offset),
                "fundingRate": "0.9",
                "realizedRate": "-0.0002",
                "formulaType": "withRate",
            }
            for offset in (3 * HOUR, HOUR, -HOUR)
        ]

    service.market._get = get
    events = await service.funding_history("BTC-USDT-SWAP", START, START + 4 * HOUR)
    assert [e["ts"] for e in events] == [START + HOUR, START + 3 * HOUR]
    assert all(e["rate"] == Decimal("-0.0002") and e["mark_price"] == 100 for e in events)
    assert all(
        e["mark_ts"] == e["ts"] and e["mark_price_source"] == "historical_mark_1m_open_approximation"
        for e in events
    )
    assert all(e["mark_price"] is None for e in service.load_funding(service.list_datasets()[0]["id"]))
    assert service.list_datasets()[0]["quality"]["funding_interval_assumption"] is None
    assert calls == 1
    # Both the range and its exact-time marks survive a process restart.
    replacement = CatalogService(service.store, service.market)

    async def fail(path, params):
        pytest.fail("A persisted funding range or exact mark was refetched")

    replacement.market._get = fail
    assert await replacement.funding_history("BTC-USDT-SWAP", START, START + 4 * HOUR) == events


async def test_empty_funding_range_is_valid_only_if_lower_bound_is_proven(service):
    async def get(path, params):
        return (
            [raw_instrument(True)]
            if "instruments" in path
            else [{"instId": "BTC-USDT-SWAP", "fundingTime": str(START - HOUR), "realizedRate": "0.0001"}]
        )

    service.market._get = get
    assert await service.funding_history("BTC-USDT-SWAP", START, START + HOUR) == []


async def test_missing_funding_or_forecast_only_is_not_silently_valid(service):
    async def empty(path, params):
        return [raw_instrument(True)] if "instruments" in path else []

    service.market._get = empty
    with pytest.raises(MarketError, match="coverage"):
        await service.funding_history("BTC-USDT-SWAP", START, START + HOUR)

    async def forecast(path, params):
        return (
            [raw_instrument(True)]
            if "instruments" in path
            else [{"fundingTime": str(START), "fundingRate": "0.001"}]
        )

    service.market._get = forecast
    with pytest.raises(MarketError, match="realizedRate"):
        await service.funding_history("BTC-USDT-SWAP", START, START + HOUR)


async def test_price_only_mark_and_index_candles_have_explicit_units(service):
    for kind in ("mark", "index"):
        job = service.create_job("BTC-USDT-SWAP", kind, "1m", START, START + 120 * 60_000, "example")
        result = await service.run_job(job["id"])
        manifest = service.get_dataset(result["dataset_id"])
        assert manifest["volume_unit"] == "not_applicable"
        assert all(c.volume == 0 for c in service.load_candles(manifest["id"]))


async def test_synthetic_funding_has_variable_schedule_and_snapshot_units(service):
    events = await service.funding_history("BTC-USDT-SWAP", START, START + 2 * 86_400_000, "example")
    deltas = {b["ts"] - a["ts"] for a, b in zip(events, events[1:], strict=False)}
    assert deltas == {4 * HOUR, 8 * HOUR}
    snapshot = await service.get_market_snapshot("BTC-USDT-SWAP", "example")
    assert snapshot["instrument"]["quantity_unit"] == "contracts"
    assert snapshot["instrument"]["contract_size_base"] == Decimal("0.01")
    assert snapshot["margin_tiers"] and snapshot["synthetic"]
    spot = await service.get_market_snapshot("BTC-USDT", "example")
    assert spot["mark"] == spot["last"] and spot["index"] is None
    assert spot["funding_rate"] is None and spot["margin_tiers"] == []


async def test_metadata_preserves_swap_base_units_and_tier_snapshots(service):
    async def get(path, params):
        if "instruments" in path:
            return [raw_instrument(True)]
        return [
            {"tier": "1", "minSz": "0", "maxSz": "1000", "mmr": "0.004", "imr": "0.01", "maxLever": "100"}
        ]

    service.market._get = get
    instrument = await service.get_instrument("BTC-USDT-SWAP")
    assert instrument["base"] == "BTC" and instrument["quote"] == "USDT"
    assert instrument["lot_size"] == Decimal("0.01")
    tiers = await service.get_margin_tiers("BTC-USDT-SWAP")
    assert tiers["unit"] == "contracts" and not tiers["historical"]
    assert tiers["tiers"][0]["mmr"] == Decimal("0.004")


async def test_concurrent_claims_do_not_download_twice(service):
    job = service.create_job("BTC-USDT", "trade", "1H", START, START + 800 * HOUR, "example")
    await asyncio.gather(service.run_job(job["id"]), service.run_job(job["id"]))
    result = service.get_job(job["id"])
    assert result["rows"] == 800 and result["pages"] == 3
    assert len(service.list_datasets()) == 1


async def test_dataset_hash_detects_manifest_tampering(service):
    job = service.create_job("BTC-USDT", "trade", "1H", START, START + HOUR, "example")
    job = await service.run_job(job["id"])
    manifest = service.get_dataset(job["dataset_id"])
    manifest["content_hash"] = "f" * 64
    with service.store.write() as conn:
        conn.execute(
            "UPDATE catalog_datasets SET manifest=? WHERE id=?", (json.dumps(manifest), manifest["id"])
        )
    with pytest.raises(MarketError, match="hash"):
        service.load_candles(manifest["id"])


async def test_poll_health_persists_failure_and_source_isolation(service):
    await service.poll_once("BTC-USDT-SWAP", "example")
    assert service.health("example")["items"][0]["state"] == "synthetic"
    assert service.health("okx")["items"] == []

    async def fail(path, params):
        raise MarketError("upstream_unavailable", "Injected outage")

    service.market._get = fail
    with pytest.raises(MarketError):
        await service.poll_once("BTC-USDT-SWAP")
    item = service.health("okx")["items"][0]
    assert item["state"] == "unavailable" and item["consecutive_errors"] == 1
    restarted = CatalogService(service.store, service.market)
    assert restarted.health("okx")["items"][0]["last_error"] == item["last_error"]


async def test_poller_starts_and_stops_real_task(service):
    await service.start_polling(["BTC-USDT"], "example", 1)
    for _ in range(20):
        await asyncio.sleep(0)
        if service.health()["items"]:
            break
    assert service.health()["polling"] and service.health()["items"]
    await service.stop_polling()
    assert not service.health()["polling"]


@pytest.mark.parametrize(
    "updates",
    [
        {"source": "auto"},
        {"inst_id": "../private"},
        {"bar": "1D"},
        {"start": START + 1},
        {"end": START},
        {"kind": "funding"},
    ],
)
def test_invalid_range_and_unsupported_requests_fail_before_network(service, updates):
    args = {
        "inst_id": "BTC-USDT",
        "kind": "trade",
        "bar": "1H",
        "start": START,
        "end": START + HOUR,
        "source": "example",
    } | updates
    with pytest.raises(MarketError):
        service.create_job(**args)


def test_catalog_bars_do_not_change_legacy_bar_contract():
    assert CATALOG_BARS["1m"] == 60_000


async def test_example_prices_are_consistent_across_mark_trade_bar_and_execution(service):
    datasets = []
    for symbol, kind, bar in (
        ("BTC-USDT", "trade", "1H"),
        ("BTC-USDT-SWAP", "trade", "1H"),
        ("BTC-USDT-SWAP", "mark", "1m"),
    ):
        job = service.create_job(symbol, kind, bar, EXAMPLE_ANCHOR - HOUR, EXAMPLE_ANCHOR, "example")
        result = await service.run_job(job["id"])
        datasets.append(service.load_candles(result["dataset_id"]))
    snapshot = await service.get_market_snapshot("BTC-USDT-SWAP", "example")
    assert (
        datasets[0][-1].close
        == datasets[1][-1].close
        == datasets[2][-1].close
        == snapshot["last"]
        == snapshot["mark"]
    )
    assert datasets[0][0].open == datasets[2][0].open
    assert snapshot["bid"] < snapshot["mark"] < snapshot["ask"]
    event = (
        await service.funding_history("BTC-USDT-SWAP", EXAMPLE_ANCHOR, EXAMPLE_ANCHOR + HOUR, "example")
    )[0]
    assert event["mark_price"] == snapshot["mark"] and event["mark_ts"] == EXAMPLE_ANCHOR


@pytest.mark.parametrize("offset,confirmed", [(-60_000, "1"), (0, "0")])
async def test_funding_rejects_nearest_or_unconfirmed_mark(service, offset, confirmed):
    async def get(path, params):
        if "instruments" in path:
            return [raw_instrument(True)]
        if "history-mark-price" in path:
            return [raw_candle(START + offset, "mark", confirmed)]
        return [
            {"instId": "BTC-USDT-SWAP", "fundingTime": str(ts), "realizedRate": "0.0001"}
            for ts in (START, START - HOUR)
        ]

    service.market._get = get
    with pytest.raises(MarketError, match="exact funding timestamp"):
        await service.funding_history("BTC-USDT-SWAP", START, START + HOUR)


async def test_import_preserves_attribution_canonical_hash_and_unique_versions(service):
    row = {
        "ts": START,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1.23",
        "confirmed": True,
    }
    provenance = {"provider": "Desk archive", "origin": "s3://desk/2025-01-01.json", "license": "private use"}
    first = await service.import_dataset(
        "BTC-USDT", "trade", "1H", START, START + HOUR, "example", [row, row], provenance
    )
    second = await service.import_dataset(
        "BTC-USDT", "trade", "1H", START, START + HOUR, "example", [row], provenance
    )
    assert first["transport"] == "user_import" and first["provenance"] == provenance
    assert first["quality"]["complete"] and first["quality"]["records"] == 1
    assert first["content_hash"] == second["content_hash"] and second["version"] == 2
    assert service.load_candles(first["id"])[0].close == Decimal("101")
    assert service.verify_dataset(first["id"])
    tampered = first | {"provenance": {"provider": "another provider"}}
    with service.store.write() as conn:
        conn.execute("UPDATE catalog_datasets SET manifest=? WHERE id=?", (json.dumps(tampered), first["id"]))
    with pytest.raises(MarketError, match="hash"):
        service.verify_dataset(first["id"])


async def test_import_funding_coverage_requires_provider_assertion(service):
    records = [{"ts": START, "rate": "-0.0001"}]
    provenance = {"provider": "Licensed history", "rate_kind": "realized"}
    incomplete = await service.import_dataset(
        "BTC-USDT-SWAP", "funding", "1m", START, START + HOUR, "example", records, provenance
    )
    assert not incomplete["quality"]["complete"]
    complete = await service.import_dataset(
        "BTC-USDT-SWAP",
        "funding",
        "1m",
        START,
        START + HOUR,
        "example",
        records,
        provenance | {"coverage": {"start": START, "end": START + HOUR, "complete": True}},
    )
    assert complete["quality"]["complete"] and complete["quality"]["coverage_method"] == "importer_declared"
    assert service.load_funding(complete["id"])[0]["rate"] == Decimal("-0.0001")


@pytest.mark.parametrize(
    "record_update",
    [
        {"open": "NaN"},
        {"volume": "-1"},
        {"confirmed": False},
        {"ts": START + 1},
        {"high": "1"},
    ],
)
async def test_import_rejects_noncanonical_records_atomically(service, record_update):
    row = {
        "ts": START,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1",
        "confirmed": True,
    } | record_update
    with pytest.raises(MarketError):
        await service.import_dataset(
            "BTC-USDT", "trade", "1H", START, START + HOUR, "example", [row], {"provider": "Desk"}
        )
    assert not service.list_datasets() and not service.list_jobs()


async def test_import_rejects_conflicting_duplicates_and_missing_provider(service):
    row = {
        "ts": START,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1",
        "confirmed": True,
    }
    with pytest.raises(MarketError, match="disagree"):
        await service.import_dataset(
            "BTC-USDT",
            "trade",
            "1H",
            START,
            START + HOUR,
            "example",
            [row, row | {"close": "100"}],
            {"provider": "Desk"},
        )
    with pytest.raises(MarketError, match="provider"):
        await service.import_dataset("BTC-USDT", "trade", "1H", START, START + HOUR, "example", [row], {})


async def test_catalog_watch_health_is_region_isolated(service):
    await service.poll_once("BTC-USDT", "example")
    service.market.region = "us"
    assert service.health("example")["items"] == []
    await service.poll_once("BTC-USDT", "example")
    assert len(service.health("example")["items"]) == 1


async def test_health_uses_separate_price_and_funding_freshness_budgets(service):
    ts = now_ms()
    snapshot = {
        "source": "okx",
        "inst_id": "BTC-USDT-SWAP",
        "mark_price": Decimal(100),
        "mark_ts": ts,
        "index_ts": ts,
        "funding_ts": ts - 60_000,
        "transport": "rest",
    }

    async def get(*args):
        return snapshot.copy()

    service.get_derivative_snapshot = get
    await service.poll_once("BTC-USDT-SWAP")
    item = service.health("okx")["items"][0]
    assert item["state"] == "healthy" and item["field_ages_ms"]["funding_ts"] >= 60_000
    assert item["freshness_budgets_ms"]["funding_ts"] == 180_000
    # Direct health check of persisted data avoids monotonicity rejection of an older poll.
    with service.store.write() as conn:
        snapshot["mark_ts"] = ts - 60_000
        conn.execute(
            "UPDATE catalog_watches SET body=? WHERE source='okx'", (json.dumps(snapshot, default=str),)
        )
    item = service.health("okx")["items"][0]
    assert item["state"] == "stale" and item["stale_fields"] == ["mark_ts"]


async def test_import_crash_after_atomic_record_commit_recovers_without_provider_access(service):
    def crash(*args):
        raise RuntimeError("Injected power loss before manifest publication")

    service._finalize = crash
    row = {
        "ts": START,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1",
        "confirmed": True,
    }
    with pytest.raises(RuntimeError, match="power loss"):
        await service.import_dataset(
            "BTC-USDT", "trade", "1H", START, START + HOUR, "example", [row], {"provider": "Desk archive"}
        )
    interrupted = service.list_jobs()[0]
    assert interrupted["transport"] == "user_import" and interrupted["status"] == "running"
    assert interrupted["rows"] == 1
    recovered = CatalogService(service.store, service.market)
    pending = recovered.resume_pending()
    completed = await recovered.run_job(pending[0]["id"])
    manifest = recovered.get_dataset(completed["dataset_id"])
    assert completed["status"] == "completed" and manifest["transport"] == "user_import"
    assert manifest["provenance"]["provider"] == "Desk archive"
    assert recovered.verify_dataset(manifest["id"])


@pytest.mark.parametrize("mutation", ["quality", "metadata"])
async def test_dataset_hash_binds_quality_and_instrument_metadata(service, mutation):
    job = service.create_job("BTC-USDT-SWAP", "mark", "1H", START, START + HOUR, "example")
    completed = await service.run_job(job["id"])
    manifest = service.get_dataset(completed["dataset_id"])
    if mutation == "quality":
        manifest["quality"]["complete"] = False
    else:
        manifest["metadata"]["ct_val"] = "1000"
    with service.store.write() as conn:
        conn.execute(
            "UPDATE catalog_datasets SET manifest=? WHERE id=?", (json.dumps(manifest), manifest["id"])
        )
    with pytest.raises(MarketError, match="hash"):
        service.verify_dataset(manifest["id"])
