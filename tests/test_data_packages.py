import asyncio
import hashlib
import json
import sqlite3
from decimal import Decimal

import httpx
import pytest
from tidebench.catalog import CatalogService, _example_instrument
from tidebench.data_packages import DataPackageError, DataPackageService
from tidebench.market import MarketError, MarketService
from tidebench.store import Store, dumps

START = 1_735_689_600_000
HOUR = 3_600_000
END = START + 24 * HOUR
SWAP = "BTC-USDT-SWAP"


@pytest.fixture
async def packages(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected network"))
    )
    market = MarketService(client=client)
    catalog = CatalogService(Store(tmp_path / "packages.sqlite3"), market)
    service = DataPackageService(catalog.store, catalog)
    yield service
    await client.aclose()


def create(service, inst_id=SWAP, **kwargs):
    return service.create_package(inst_id, "1H", START, END, "example", **kwargs)


async def download_components(service, package):
    for component in package["components"]:
        if component["job_id"]:
            await service.catalog.run_job(component["job_id"])
    return service.reconcile_package(package["id"])


def counts(service):
    with service.store.read() as conn:
        return tuple(
            conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("data_packages", "catalog_jobs", "data_package_components")
        )


async def imported_funding(service, *, complete=True, marks=True):
    row = {"ts": START + 4 * HOUR, "rate": "0.0001"}
    if marks:
        row.update(
            mark_price="61000", mark_ts=row["ts"], mark_price_source="Attributed archive settlement mark"
        )
    return await service.catalog.import_dataset(
        SWAP,
        "funding",
        "1H",
        START,
        END,
        "example",
        [row],
        {
            "provider": "Independent synthetic archive",
            "rate_kind": "realized",
            "coverage": {"complete": complete, "start": START, "end": END},
        },
    )


def candle(ts, kind="trade"):
    values = [str(ts), "100", "102", "99", "101"]
    return values + (["1", "100", "100", "1"] if kind == "trade" else ["1"])


async def real_instrument(inst_id, source="okx"):
    return _example_instrument(inst_id) | {"source": source, "synthetic": False}


async def test_spot_package_pins_one_dataset_and_reopens_ready_manifest(packages):
    package = create(packages, "BTC-USDT")
    assert [c["kind"] for c in package["components"]] == ["trade"]
    assert not package["ready"] and package["manifest"] is None
    ready = await packages.run_package(package["id"])
    assert ready["status"] == "ready" and ready["progress"] == 1
    assert ready["coverage"] == {"start": START, "end": END, "complete": True}
    assert ready["funding_marks"]["total"] == 0
    replacement = DataPackageService(Store(packages.store.path), packages.catalog)
    inputs = replacement.research_inputs(ready["id"])
    assert set(inputs) == {
        "dataset_id",
        "source",
        "start_ts",
        "end_ts",
        "package_id",
        "package_manifest_hash",
    }
    assert inputs["dataset_id"] == ready["manifest"]["datasets"]["trade"]["id"]
    assert inputs["package_manifest_hash"] == ready["manifest_hash"]
    assert replacement.get_manifest(ready["id"]) == ready["manifest"]


async def test_swap_prepares_every_realized_funding_timestamp_and_optional_index(packages):
    package = create(packages, include_index=True)
    prepared = await download_components(packages, package)
    assert prepared["status"] == "preparing" and not prepared["ready"]
    with pytest.raises(DataPackageError, match="before research"):
        packages.research_inputs(package["id"])
    ready = await packages.prepare_package(package["id"])
    assert ready["ready"] and ready["funding_marks"]["captured"] == 4
    assert set(ready["manifest"]["datasets"]) == {"trade", "mark", "funding", "index"}
    events = ready["manifest"]["funding_events"]
    assert [event["ts"] for event in events] == [START + hour * HOUR for hour in (0, 4, 8, 16)]
    assert all(
        event["mark_ts"] == event["ts"] and event["mark_price_source"] == "synthetic_absolute_time_series"
        for event in events
    )
    assert ready["manifest"]["funding_interval_assumption"] is None
    assert all(dataset["synthetic"] for dataset in ready["manifest"]["datasets"].values())
    inputs = packages.research_inputs(package["id"])
    assert all(
        inputs[kind + "_dataset_id"] == ready["manifest"]["datasets"][kind]["id"]
        for kind in ("mark", "funding", "index")
    )
    before = counts(packages)
    again = await packages.prepare_package(package["id"])
    assert again["manifest_hash"] == ready["manifest_hash"] and counts(packages) == before
    with packages.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit WHERE kind='data_package.ready'").fetchone()[0] == 1


async def test_identical_concurrent_requests_allocate_one_owned_job_set(packages):
    results = await asyncio.gather(*(asyncio.to_thread(create, packages) for _ in range(8)))
    assert len({result["id"] for result in results}) == 1
    assert counts(packages) == (1, 3, 3)
    assert create(packages)["id"] == results[0]["id"]
    explicit = create(packages, idempotency_key="another-version")
    assert explicit["id"] != results[0]["id"] and counts(packages) == (2, 6, 6)
    assert create(packages, idempotency_key="another-version")["id"] == explicit["id"]
    with pytest.raises(DataPackageError) as exc:
        create(packages, "ETH-USDT-SWAP", idempotency_key="another-version")
    assert exc.value.code == "idempotency_conflict" and exc.value.status == 409


def test_child_insert_failure_rolls_back_package_jobs_and_links(packages):
    with packages.store.write() as conn:
        conn.execute(
            "CREATE TRIGGER injected_job_failure BEFORE INSERT ON catalog_jobs WHEN NEW.kind='mark' BEGIN SELECT RAISE(ABORT,'injected allocation fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected allocation"):
        create(packages)
    assert counts(packages) == (0, 0, 0)


def test_active_package_capacity_and_catalog_capacity_are_atomic(packages):
    for index in range(5):
        create(packages, "BTC-USDT", idempotency_key="package-" + str(index))
    with pytest.raises(DataPackageError) as exc:
        create(packages, idempotency_key="sixth")
    assert exc.value.code == "package_queue_full" and exc.value.status == 429
    assert counts(packages) == (5, 5, 5)
    for package in packages.list_packages():
        packages.cancel_package(package["id"])
    for _ in range(18):
        packages.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
    before = counts(packages)
    with pytest.raises(DataPackageError) as exc:
        create(packages, idempotency_key="over-catalog-capacity")
    assert exc.value.code == "catalog_queue_full" and counts(packages) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"bar": []},
        {"source": "ambiguous"},
        {"start": True},
        {"start": START + 1},
        {"end": START + HOUR},
        {"end": START + 98001 * HOUR},
        {"include_index": "yes"},
        {"idempotency_key": " " * 1000},
        {"dataset_ids": {"mark": "not-a-spot-dataset"}},
        {"dataset_ids": []},
    ],
)
def test_input_limits_do_not_create_orphan_downloads(packages, changes):
    kwargs = {"inst_id": "BTC-USDT", "bar": "1H", "start": START, "end": END, "source": "example"} | changes
    with pytest.raises(MarketError):
        packages.create_package(**kwargs)
    assert counts(packages) == (0, 0, 0)


async def test_explicit_published_version_binding_never_allocates_an_extra_download(packages):
    first = await packages.run_package(create(packages, "BTC-USDT")["id"])
    dataset_id = first["manifest"]["datasets"]["trade"]["id"]
    bound = create(packages, "BTC-USDT", dataset_ids={"trade": dataset_id})
    assert bound["status"] == "preparing" and bound["components"][0]["job_id"] is None
    assert counts(packages) == (2, 1, 2)
    ready = await packages.prepare_package(bound["id"])
    assert ready["manifest"]["datasets"]["trade"]["id"] == dataset_id
    newer = await packages.run_package(create(packages, "BTC-USDT", idempotency_key="fresh")["id"])
    assert newer["manifest"]["datasets"]["trade"]["id"] != dataset_id
    assert packages.research_inputs(bound["id"])["dataset_id"] == dataset_id


@pytest.mark.parametrize(
    "changes", [{"source": "okx"}, {"inst_id": "ETH-USDT"}, {"bar": "4H"}, {"end": END + 4 * HOUR}]
)
async def test_bound_dataset_must_match_exact_identity(packages, changes):
    ready = await packages.run_package(create(packages, "BTC-USDT")["id"])
    identifier = ready["manifest"]["datasets"]["trade"]["id"]
    before = counts(packages)
    kwargs = {
        "inst_id": "BTC-USDT",
        "bar": "1H",
        "start": START,
        "end": END,
        "source": "example",
        "dataset_ids": {"trade": identifier},
    } | changes
    with pytest.raises(DataPackageError) as exc:
        packages.create_package(**kwargs)
    assert exc.value.code == "package_dataset_mismatch" and counts(packages) == before


async def test_imported_funding_marks_are_captured_with_attribution_without_refetch(packages, monkeypatch):
    imported = await imported_funding(packages)
    package = create(packages, dataset_ids={"funding": imported["id"]})

    async def no_refetch(*args):
        pytest.fail("Attributed imported marks must not be replaced by exchange approximations")

    monkeypatch.setattr(packages.catalog, "_settlement_mark", no_refetch)
    ready = await packages.run_package(package["id"])
    assert ready["ready"] and ready["funding_marks"]["captured"] == 1
    assert ready["manifest"]["datasets"]["funding"]["transport"] == "user_import"
    event = ready["manifest"]["funding_events"][0]
    assert (
        event["mark_price"] == "61000" and event["mark_price_source"] == "Attributed archive settlement mark"
    )
    assert counts(packages) == (1, 3, 3)


async def test_incomplete_imported_funding_explicitly_blocks_readiness(packages):
    imported = await imported_funding(packages, complete=False)
    package = create(packages, dataset_ids={"funding": imported["id"]})
    result = await packages.run_package(package["id"])
    assert result["status"] == "blocked" and not result["ready"] and result["manifest"] is None
    assert any(blocker["code"] == "funding_history_incomplete" for blocker in result["blockers"])
    with pytest.raises(DataPackageError) as exc:
        packages.retry_package(package["id"])
    assert exc.value.code == "package_state"
    assert packages.cancel_package(package["id"])["status"] == "canceled"


async def test_real_retention_empty_history_cannot_be_mistaken_for_zero_funding(packages, monkeypatch):
    monkeypatch.setattr(packages.catalog, "get_instrument", real_instrument)

    async def pages(path, params):
        if "funding-rate-history" in path:
            return []
        kind = "mark" if "mark-price" in path else "trade"
        return [candle(END - index * HOUR, kind) for index in range(1, 26)]

    monkeypatch.setattr(packages.catalog.market, "_get", pages)
    package = packages.create_package(SWAP, "1H", START, END, "okx")
    result = await packages.run_package(package["id"])
    assert result["status"] == "blocked" and not result["ready"]
    funding = next(c for c in result["components"] if c["kind"] == "funding")
    assert funding["status"] == "degraded" and funding["rows"] == 0
    assert result["funding_marks"]["captured"] == 0
    assert any(b["code"] == "funding_history_incomplete" for b in result["blockers"])
    assert not packages.list_packages("example")


async def test_gapped_confirmed_candles_block_research(packages):
    dataset = await packages.catalog.import_dataset(
        "BTC-USDT",
        "trade",
        "1H",
        START,
        END,
        "example",
        [
            {
                "ts": START,
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "1",
                "confirmed": True,
            }
        ],
        {"provider": "Incomplete synthetic fixture"},
    )
    package = create(packages, "BTC-USDT", dataset_ids={"trade": dataset["id"]})
    assert package["status"] == "blocked" and not package["ready"]
    assert package["blockers"][0]["code"] == "dataset_coverage_incomplete"


async def test_instrument_rules_drift_between_components_blocks_bundle(packages, monkeypatch):
    package = create(packages)
    trade = next(c for c in package["components"] if c["kind"] == "trade")
    await packages.catalog.run_job(trade["job_id"])
    original = packages.catalog.get_instrument

    async def changed_rules(inst_id, source):
        metadata = await original(inst_id, source)
        metadata["tick_size"] = Decimal("0.001")
        metadata["instrument_version"] = hashlib.sha256(dumps(metadata).encode()).hexdigest()
        return metadata

    monkeypatch.setattr(packages.catalog, "get_instrument", changed_rules)
    result = await packages.run_package(package["id"])
    assert result["status"] == "blocked"
    assert any(b["code"] == "instrument_rules_mismatch" for b in result["blockers"])


async def test_failed_download_retry_reuses_the_same_committed_cursor_and_ids(packages, monkeypatch):
    monkeypatch.setattr(packages.catalog, "get_instrument", real_instrument)
    end = START + 500 * HOUR
    calls, broken = [], True

    async def pages(path, params):
        cursor = int(params["after"])
        calls.append(cursor)
        if len(calls) == 2 and broken:
            raise MarketError("upstream_unavailable", "Injected second-page failure")
        return [candle(cursor - index * HOUR) for index in range(1, 301)]

    monkeypatch.setattr(packages.catalog.market, "_get", pages)
    package = packages.create_package("BTC-USDT", "1H", START, end, "okx")
    failed = await packages.run_package(package["id"])
    assert failed["status"] == "failed"
    job_id = failed["components"][0]["job_id"]
    assert packages.catalog.get_job(job_id)["cursor"] == end - 300 * HOUR
    broken = False
    recovered = DataPackageService(
        Store(packages.store.path), CatalogService(packages.store, packages.catalog.market)
    )
    monkeypatch.setattr(recovered.catalog, "get_instrument", real_instrument)
    recovered.retry_package(package["id"])
    ready = await recovered.run_package(package["id"])
    assert ready["ready"] and ready["components"][0]["job_id"] == job_id
    assert counts(packages) == (1, 1, 1)
    assert calls == [end, end - 300 * HOUR, end - 300 * HOUR]


async def test_cancel_during_late_catalog_page_prevents_rows_and_publication(packages, monkeypatch):
    monkeypatch.setattr(packages.catalog, "get_instrument", real_instrument)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_page(path, params):
        entered.set()
        await release.wait()
        return [candle(END - index * HOUR) for index in range(1, 26)]

    monkeypatch.setattr(packages.catalog.market, "_get", held_page)
    package = packages.create_package("BTC-USDT", "1H", START, END, "okx")
    task = asyncio.create_task(packages.run_package(package["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(DataPackageError):
            packages.resume_pending()
        canceled = packages.cancel_package(package["id"])
        assert canceled["status"] == "canceled" and canceled["components"][0]["status"] == "canceled"
        with pytest.raises(DataPackageError):
            packages.retry_package(package["id"])
        release.set()
        result = await asyncio.wait_for(task, 2)
        assert result["status"] == "canceled" and result["components"][0]["rows"] == 0
        assert result["manifest"] is None
        assert create(packages, "BTC-USDT", idempotency_key="new-after-cancel")["id"] != package["id"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_competing_preparers_do_not_duplicate_funding_observations(packages, monkeypatch):
    package = await download_components(packages, create(packages))
    entered, release = asyncio.Event(), asyncio.Event()
    original = packages.catalog._settlement_mark
    calls = []

    async def held_mark(inst_id, ts, source):
        calls.append(ts)
        entered.set()
        await release.wait()
        return await original(inst_id, ts, source)

    monkeypatch.setattr(packages.catalog, "_settlement_mark", held_mark)
    task = asyncio.create_task(packages.prepare_package(package["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        competitor = DataPackageService(packages.store, packages.catalog)
        second = await competitor.prepare_package(package["id"])
        assert second["status"] == "preparing" and len(calls) == 1
        release.set()
        ready = await asyncio.wait_for(task, 2)
        assert ready["ready"] and len(calls) == 4 and len(set(calls)) == 4
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancel_during_funding_mark_fetch_cannot_late_publish(packages, monkeypatch):
    package = await download_components(packages, create(packages))
    entered, release = asyncio.Event(), asyncio.Event()
    original = packages.catalog._settlement_mark

    async def held_mark(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(packages.catalog, "_settlement_mark", held_mark)
    task = asyncio.create_task(packages.prepare_package(package["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        packages.cancel_package(package["id"])
        release.set()
        result = await asyncio.wait_for(task, 2)
        assert result["status"] == "canceled" and result["funding_marks"]["captured"] == 0
        assert result["manifest"] is None
        assert packages.cancel_package(package["id"])["status"] == "canceled"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_preparation_failure_requires_explicit_retry_and_reuses_captured_marks(packages, monkeypatch):
    package = await download_components(packages, create(packages))
    original = packages.catalog._settlement_mark
    calls, broken = [], True

    async def mark(inst_id, ts, source):
        calls.append(ts)
        if len(calls) == 2 and broken:
            raise MarketError("missing_settlement_mark", "Injected missing exact historical minute")
        return await original(inst_id, ts, source)

    monkeypatch.setattr(packages.catalog, "_settlement_mark", mark)
    failed = await packages.prepare_package(package["id"])
    assert failed["status"] == "failed" and failed["funding_marks"]["captured"] == 1
    assert failed["blockers"][0]["code"] == "missing_settlement_mark"
    before = counts(packages)
    await packages.advance_pending()
    assert len(calls) == 2 and packages.get_package(package["id"])["status"] == "failed"
    broken = False
    packages.retry_package(package["id"])
    ready = await packages.prepare_package(package["id"])
    assert ready["ready"] and counts(packages) == before
    assert calls.count(START) == 1 and calls.count(START + 4 * HOUR) == 2


async def test_shutdown_preserves_partial_funding_preparation_for_restart(packages, monkeypatch):
    package = await download_components(packages, create(packages))
    original = packages.catalog._settlement_mark
    entered = asyncio.Event()
    calls = []

    async def mark(inst_id, ts, source):
        calls.append(ts)
        if len(calls) == 2:
            entered.set()
            await asyncio.Event().wait()
        return await original(inst_id, ts, source)

    monkeypatch.setattr(packages.catalog, "_settlement_mark", mark)
    task = asyncio.create_task(packages.prepare_package(package["id"]))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert packages.get_package(package["id"])["funding_marks"]["captured"] == 1
    recovered = DataPackageService(
        Store(packages.store.path), CatalogService(packages.store, packages.catalog.market)
    )
    recovered.resume_pending()
    ready = await recovered.prepare_package(package["id"])
    assert ready["ready"] and len(ready["manifest"]["funding_events"]) == 4


async def test_budgeted_preparation_releases_claim_and_resumes_without_duplicate_marks(packages):
    package = await download_components(packages, create(packages))
    for expected in range(1, 5):
        result = await packages.prepare_package(package["id"], max_observations=1)
        assert result["funding_marks"]["captured"] == expected
        assert result["ready"] is (expected == 4)
        with packages.store.read() as conn:
            assert (
                conn.execute(
                    "SELECT prepare_token FROM data_packages WHERE id=?", (package["id"],)
                ).fetchone()[0]
                is None
            )
    assert packages.get_manifest(package["id"])["funding_events_hash"]
    assert counts(packages) == (1, 3, 3)


async def test_scheduler_tick_captures_at_most_twenty_new_observations(packages):
    package = packages.create_package(SWAP, "1H", START, START + 10 * 24 * HOUR, "example")
    await download_components(packages, package)
    await packages.advance_pending()
    partial = packages.get_package(package["id"])
    assert partial["status"] == "preparing" and partial["funding_marks"]["captured"] == 20
    assert partial["funding_marks"]["total"] == 40 and not partial["ready"]
    await packages.advance_pending()
    assert packages.get_package(package["id"])["ready"]


async def test_scheduler_rotates_preparation_batches_between_active_packages(packages):
    first = packages.create_package(SWAP, "1H", START, START + 10 * 24 * HOUR, "example")
    second = packages.create_package("ETH-USDT-SWAP", "1H", START, START + 10 * 24 * HOUR, "example")
    await download_components(packages, first)
    await download_components(packages, second)
    await packages.advance_pending()
    await packages.advance_pending()
    assert packages.get_package(first["id"])["funding_marks"]["captured"] == 20
    assert packages.get_package(second["id"])["funding_marks"]["captured"] == 20
    assert not packages.get_package(first["id"])["ready"] and not packages.get_package(second["id"])["ready"]
    await packages.advance_pending()
    await packages.advance_pending()
    assert packages.get_package(first["id"])["ready"] and packages.get_package(second["id"])["ready"]


@pytest.mark.parametrize("budget", [0, 1001, True, "20"])
async def test_preparation_budget_is_bounded(packages, budget):
    package = create(packages)
    with pytest.raises(DataPackageError):
        await packages.prepare_package(package["id"], max_observations=budget)


def test_resume_only_releases_package_claim_not_catalog_worker_token(packages):
    package = create(packages)
    job_id = package["components"][0]["job_id"]
    with packages.store.write() as conn:
        conn.execute(
            "UPDATE data_packages SET status='preparing',prepare_token='abandoned-preparer' WHERE id=?",
            (package["id"],),
        )
        conn.execute(
            "UPDATE catalog_jobs SET status='running',worker_token='existing-download-owner' WHERE id=?",
            (job_id,),
        )
    packages.resume_pending()
    with packages.store.read() as conn:
        assert (
            conn.execute("SELECT prepare_token FROM data_packages WHERE id=?", (package["id"],)).fetchone()[0]
            is None
        )
        assert (
            conn.execute("SELECT worker_token FROM catalog_jobs WHERE id=?", (job_id,)).fetchone()[0]
            == "existing-download-owner"
        )


def test_retry_does_not_reset_sibling_running_worker_tokens(packages):
    package = create(packages)
    trade = next(c["job_id"] for c in package["components"] if c["kind"] == "trade")
    mark = next(c["job_id"] for c in package["components"] if c["kind"] == "mark")
    with packages.store.write() as conn:
        conn.execute("UPDATE catalog_jobs SET status='failed',error='injected' WHERE id=?", (trade,))
        conn.execute(
            "UPDATE catalog_jobs SET status='running',worker_token='active-owner' WHERE id=?", (mark,)
        )
    assert packages.reconcile_package(package["id"])["status"] == "failed"
    packages.retry_package(package["id"])
    with packages.store.read() as conn:
        assert (
            conn.execute("SELECT worker_token FROM catalog_jobs WHERE id=?", (mark,)).fetchone()[0]
            == "active-owner"
        )
    assert packages.catalog.get_job(trade)["status"] == "queued"


@pytest.mark.parametrize(
    "changes", [{"mark_ts": START + 1}, {"mark_price": "NaN"}, {"mark_price": "0"}, {"mark_price_source": ""}]
)
async def test_invalid_or_misattributed_settlement_mark_blocks_ready(packages, monkeypatch, changes):
    package = await download_components(packages, create(packages))

    async def malformed_mark(inst_id, ts, source):
        return {"mark_price": "100", "mark_ts": ts, "mark_price_source": "test historical minute"} | changes

    monkeypatch.setattr(packages.catalog, "_settlement_mark", malformed_mark)
    result = await packages.prepare_package(package["id"])
    assert result["status"] == "failed" and not result["ready"] and result["manifest"] is None


async def test_dataset_integrity_is_verified_before_ready(packages):
    package = create(packages, "BTC-USDT")
    completed = await packages.catalog.run_job(package["components"][0]["job_id"])
    with packages.store.write() as conn:
        row = conn.execute(
            "SELECT manifest FROM catalog_datasets WHERE id=?", (completed["dataset_id"],)
        ).fetchone()
        manifest = json.loads(row[0])
        manifest["quality"]["records"] += 1
        conn.execute(
            "UPDATE catalog_datasets SET manifest=? WHERE id=?", (dumps(manifest), completed["dataset_id"])
        )
    blocked = await packages.prepare_package(package["id"])
    assert blocked["status"] == "blocked" and not blocked["ready"]
    assert blocked["blockers"][0]["code"] == "dataset_integrity_error"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE data_packages SET manifest='{}' WHERE id=?",
        "DELETE FROM data_package_components WHERE package_id=?",
        "DELETE FROM data_package_funding_marks WHERE package_id=?",
    ],
)
async def test_published_package_inputs_are_immutable(packages, statement):
    ready = await packages.run_package(create(packages)["id"])
    with packages.store.write() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(statement, (ready["id"],))
    with pytest.raises(DataPackageError):
        packages.cancel_package(ready["id"])
    with pytest.raises(DataPackageError):
        packages.retry_package(ready["id"])
    assert packages.get_manifest(ready["id"])["package_id"] == ready["id"]


async def test_manifest_hash_detects_tampering_even_if_sql_guard_is_removed(packages):
    ready = await packages.run_package(create(packages, "BTC-USDT")["id"])
    with packages.store.write() as conn:
        conn.execute("DROP TRIGGER data_package_manifest_immutable")
        manifest = ready["manifest"] | {"source": "okx"}
        conn.execute("UPDATE data_packages SET manifest=? WHERE id=?", (dumps(manifest), ready["id"]))
    with pytest.raises(DataPackageError) as exc:
        packages.research_inputs(ready["id"])
    assert exc.value.code == "package_integrity"


def test_source_region_list_and_detail_boundaries(packages):
    first = create(packages, "BTC-USDT")
    other = packages.create_package("BTC-USDT", "1H", START, END, "okx")
    assert [p["id"] for p in packages.list_packages("example")] == [first["id"]]
    assert [p["id"] for p in packages.list_packages("okx")] == [other["id"]]
    assert len(packages.list_packages(limit=1)) == 1
    assert all("manifest" not in p for p in packages.list_packages())
    packages.catalog.market.region = "us"
    assert packages.list_packages() == []
    with pytest.raises(DataPackageError) as exc:
        packages.get_package(first["id"])
    assert exc.value.code == "region_mismatch"


@pytest.mark.parametrize("query", [{"limit": True}, {"limit": 0}, {"limit": 101}, {"source": "all"}])
def test_list_limits_are_enforced(packages, query):
    with pytest.raises(DataPackageError):
        packages.list_packages(**query)
