"""Runtime recovery and interleaving tests with real SQLite and economic book."""

import asyncio
import json
from dataclasses import asdict
from decimal import Decimal

import httpx
import pytest
from tidebench.config import Settings
from tidebench.engine import Candle, StrategyConfig
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_service import ProfessionalRuntime
from tidebench.provenance import research_identity, serialized_result
from tidebench.store import Store, dumps

D = Decimal
HOUR = 3_600_000
END = 1767225600000
SYMBOL = "BTC-USDT-SWAP"


def quote(ts=END):
    return {
        "source": "example",
        "inst_id": SYMBOL,
        "ts": ts,
        "mark_ts": ts,
        "bid": "100",
        "ask": "100",
        "last": "100",
        "mark": "100",
        "funding_time": END + 60_000,
        "next_funding_time": END + 120_000,
        "margin_tiers": [
            {
                "tier": 1,
                "min_size": "0",
                "max_size": "100000",
                "imr": ".01",
                "mmr": ".004",
                "max_leverage": "100",
            }
        ],
        "instrument": {
            "inst_id": SYMBOL,
            "inst_type": "SWAP",
            "base": "BTC",
            "quote": "USDT",
            "settle_ccy": "USDT",
            "ct_type": "linear",
            "ct_val": ".01",
            "ct_mult": "1",
            "ct_val_ccy": "BTC",
            "tick_size": ".01",
            "lot_size": ".01",
            "min_size": ".01",
            "state": "live",
        },
    }


def command(side="buy", quantity="100", **changes):
    return {
        "source": "example",
        "inst_id": SYMBOL,
        "side": side,
        "quantity": quantity,
        "leverage": 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
        **changes,
    }


class MemoryCatalog:
    """Only transport/catalog edges are replaced; book/storage remain real."""

    def __init__(self):
        self.quote = quote()
        self.events = []
        self.funding_calls = []
        self.resumes = 0
        self.candles = [
            Candle(END - (2000 - index) * HOUR, D(100), D(100), D(100), D(100), D(1)) for index in range(2000)
        ]
        self.dataset = {
            "id": "trade-version",
            "source": "example",
            "inst_id": SYMBOL,
            "kind": "trade",
            "bar": "1H",
            "start": self.candles[0].ts,
            "end": END,
            "content_hash": "immutable-test-hash",
            "metadata": self.quote["instrument"],
            "quality": {"complete": True, "coverage_start": self.candles[0].ts, "coverage_end": END},
        }
        self.job_counter = 0

    def create_job(self, *args):
        self.job_counter += 1
        return {"id": f"transport-job-{self.job_counter}"}

    async def run_job(self, identifier):
        return {"id": identifier, "status": "completed", "dataset_id": "trade-version"}

    def load_candles(self, identifier):
        return list(self.candles)

    def get_dataset(self, identifier):
        return self.dataset

    async def get_market_snapshot(self, symbol, source):
        assert symbol == SYMBOL and source == "example"
        return self.quote

    async def funding_history(self, symbol, start, end, source):
        self.funding_calls.append((symbol, start, end, source))
        return [event for event in self.events if start <= event["ts"] < end]

    def resume_pending(self):
        self.resumes += 1

    async def stop_polling(self):
        pass

    def list_jobs(self):
        return []


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected network"))
    )
    market = MarketService(client=client)
    result = ProfessionalRuntime(Store(settings.database), market, settings)
    result.catalog = MemoryCatalog()
    result.book.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test")
    yield result
    await result.stop()
    await client.aclose()


def initial_long(runtime, deployed=None):
    actor = f"strategy:{deployed['id']}" if deployed else "manual"
    key = f"{actor}:open:{END - 2 * HOUR}" if deployed else "initial-position"
    return runtime.book.submit(command(), key, {SYMBOL: runtime.catalog.quote}, actor)


def deployment(runtime, direction="short_only"):
    return runtime.deploy(
        {
            "source": "example",
            "inst_id": SYMBOL,
            "bar": "1H",
            "direction": direction,
            "leverage": 1,
            "allocation": ".1",
            "strategy": asdict(StrategyConfig(kind="buy_hold", allocation=D(".1"))),
        },
        "test",
    )


def durable_intent(runtime, identifier):
    with runtime.store.read() as conn:
        row = conn.execute(
            "SELECT * FROM pro_strategy_intents WHERE deployment_id=? ORDER BY bar DESC", (identifier,)
        ).fetchone()
        return dict(row) if row else None


def restart(runtime):
    replacement = ProfessionalRuntime(Store(runtime.store.path), runtime.market, runtime.settings)
    replacement.catalog = runtime.catalog
    return replacement


async def test_reverse_closes_then_opens_once_and_cursor_matches_intent(runtime):
    deployed = deployment(runtime)
    initial = initial_long(runtime, deployed)
    await runtime.evaluate(deployed)
    orders = runtime.book.orders("example")
    closes = [row for row in orders if row.get("reduce_only")]
    opens = [
        row
        for row in orders
        if row["actor"].startswith("strategy:") and not row.get("reduce_only") and row["id"] != initial["id"]
    ]
    assert len(closes) == len(opens) == 1
    assert closes[0]["side"] == "sell" and opens[0]["side"] == "sell"
    assert D(runtime.book.positions("example")[0]["quantity"]) < 0
    assert durable_intent(runtime, deployed["id"])["status"] == "completed"
    assert runtime.deployments()[0]["last_bar"] == END - HOUR
    await runtime.evaluate(runtime.deployments()[0])
    assert len(runtime.book.orders("example")) == 3


@pytest.mark.parametrize("phase", ["close", "open"])
async def test_crash_after_committed_phase_recovers_without_duplicate_fill(runtime, monkeypatch, phase):
    deployed = deployment(runtime)
    initial_long(runtime, deployed)
    original = runtime.book.submit
    tripped = False

    def crash(order, key, *args, **kwargs):
        nonlocal tripped
        result = original(order, key, *args, **kwargs)
        if f":{phase}:" in key and not tripped:
            tripped = True
            raise RuntimeError("simulated process loss after commit")
        return result

    monkeypatch.setattr(runtime.book, "submit", crash)
    with pytest.raises(RuntimeError, match="after commit"):
        await runtime.evaluate(deployed)
    assert durable_intent(runtime, deployed["id"])["status"] == "pending"
    recovered = restart(runtime)
    await recovered.evaluate(recovered.deployments()[0])
    assert durable_intent(recovered, deployed["id"])["status"] == "completed"
    assert len(recovered.book.orders("example")) == 3
    assert D(recovered.book.positions("example")[0]["quantity"]) < 0
    await recovered.stop()


async def test_stop_after_close_prevents_restart_opening_pending_intent(runtime, monkeypatch):
    deployed = deployment(runtime)
    initial_long(runtime, deployed)
    original = runtime.book.submit

    def crash(order, key, *args, **kwargs):
        result = original(order, key, *args, **kwargs)
        if ":close:" in key:
            raise RuntimeError("crash between phases")
        return result

    monkeypatch.setattr(runtime.book, "submit", crash)
    with pytest.raises(RuntimeError, match="between phases"):
        await runtime.evaluate(deployed)
    runtime.stop_deployment(deployed["id"], "test")
    recovered = restart(runtime)
    await recovered.evaluate(recovered.deployments()[0])
    assert not recovered.book.positions("example")
    assert len(recovered.book.orders("example")) == 2
    assert durable_intent(recovered, deployed["id"])["status"] == "pending"
    await recovered.stop()


async def test_stop_during_history_await_blocks_every_economic_mutation(runtime, monkeypatch):
    deployed = deployment(runtime)
    initial_long(runtime, deployed)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.catalog.run_job

    async def paused(identifier):
        entered.set()
        await release.wait()
        return await original(identifier)

    monkeypatch.setattr(runtime.catalog, "run_job", paused)
    task = asyncio.create_task(runtime.evaluate(deployed))
    await asyncio.wait_for(entered.wait(), 2)
    runtime.stop_deployment(deployed["id"], "test")
    release.set()
    with pytest.raises(PlatformError, match="no longer owns"):
        await task
    assert len(runtime.book.orders("example")) == 1
    assert D(runtime.book.positions("example")[0]["quantity"]) == 100


async def test_stop_between_phases_is_checked_inside_open_transaction(runtime, monkeypatch):
    deployed = deployment(runtime)
    initial_long(runtime, deployed)
    original = runtime.offload

    async def stop_before_open(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if function.__name__ == "account":
            runtime.stop_deployment(deployed["id"], "test")
        return result

    monkeypatch.setattr(runtime, "offload", stop_before_open)
    with pytest.raises(PlatformError, match="no longer owns"):
        await runtime.evaluate(deployed)
    assert not runtime.book.positions("example")
    assert len(runtime.book.orders("example")) == 2


def test_deployment_rejects_unacknowledged_inventory_without_mutation(runtime):
    initial_long(runtime)
    with pytest.raises(PlatformError, match="Close the existing position") as error:
        deployment(runtime)
    assert error.value.code == "deployment_inventory"
    assert not runtime.deployments()
    assert D(runtime.book.positions("example")[0]["quantity"]) == 100
    assert len(runtime.book.orders("example")) == 1


def test_deployment_rejects_existing_pending_and_preserves_reservation(runtime):
    pending = runtime.book.submit(
        command(order_type="limit", limit_price="95"), "manual-before-deploy", {SYMBOL: quote()}
    )
    before = runtime.book.account("example", {SYMBOL: quote()})["reserved_cash"]
    with pytest.raises(PlatformError, match="Cancel this market's pending"):
        deployment(runtime)
    assert not runtime.deployments()
    assert runtime.book.pending()[0]["id"] == pending["id"]
    assert runtime.book.account("example", {SYMBOL: quote()})["reserved_cash"] == before
    runtime.book.cancel(pending["id"], "test")
    assert deployment(runtime)["status"] == "running"


@pytest.mark.parametrize("actor", ["pending-order", "risk-engine"])
def test_username_does_not_grant_internal_execution_authority(runtime, actor):
    deployment(runtime)
    with pytest.raises(PlatformError, match="Stop the strategy before adding manual risk"):
        runtime.book.submit(command(), f"manual-{actor}", {SYMBOL: quote()}, actor)
    assert not runtime.book.positions("example")
    assert not runtime.book.orders("example")


async def test_hanging_history_does_not_block_same_market_protective_stop(runtime, monkeypatch):
    deployed = deployment(runtime)
    initial_long(runtime, deployed)
    pending = runtime.book.submit(
        command(side="sell", reduce_only=True, order_type="stop_market", stop_price="95"),
        "protective-stop-test",
        {SYMBOL: quote()},
    )
    entered, release, filled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_history, original_submit = runtime.catalog.run_job, runtime.book.submit

    async def hung(identifier):
        entered.set()
        await release.wait()
        return await original_history(identifier)

    def observe_fill(*args, **kwargs):
        result = original_submit(*args, **kwargs)
        if result["id"] == pending["id"] and result["status"] == "filled":
            loop.call_soon_threadsafe(filled.set)
        return result

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(runtime.catalog, "run_job", hung)
    monkeypatch.setattr(runtime.book, "submit", observe_fill)
    risk_task, strategy_task = (
        asyncio.create_task(runtime.execution_loop()),
        asyncio.create_task(runtime.strategy_loop()),
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        runtime.catalog.quote = quote() | {"bid": "90", "ask": "90", "mark": "90", "last": "90"}
        await asyncio.wait_for(filled.wait(), 7)
        assert not release.is_set()
        assert not runtime.book.positions("example")
        assert not risk_task.done() and not strategy_task.done()
    finally:
        risk_task.cancel()
        strategy_task.cancel()
        await asyncio.gather(risk_task, strategy_task, return_exceptions=True)


async def test_concurrent_evaluations_prepare_and_fill_a_signal_once(runtime):
    deployed = deployment(runtime)
    await asyncio.gather(runtime.evaluate(deployed), runtime.evaluate(deployed))
    assert runtime.catalog.job_counter == 1
    assert len(runtime.book.orders("example")) == 1
    assert durable_intent(runtime, deployed["id"])["status"] == "completed"


async def test_stopping_a_waiting_strategy_keeps_scheduler_alive(runtime, monkeypatch):
    deployed = deployment(runtime)
    entered, canceled = asyncio.Event(), asyncio.Event()

    async def hang(identifier):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    monkeypatch.setattr(runtime.catalog, "run_job", hang)
    task = asyncio.create_task(runtime.strategy_loop())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        runtime.stop_deployment(deployed["id"], "test")
        await asyncio.wait_for(canceled.wait(), 2)
        await asyncio.sleep(1.1)
        assert not task.done()
        assert not runtime.book.orders("example")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_funding_boundary_bypasses_periodic_ttl_and_manual_commands_force_reconcile(runtime):
    initial_long(runtime)
    runtime.catalog.quote = quote(END + 10_000)
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
    assert len(runtime.catalog.funding_calls) == 1
    runtime.catalog.quote = quote(END + 60_000)
    runtime.catalog.events = [
        {"ts": END + 60_000, "rate": D(".001"), "mark_price": D(100), "mark_ts": END + 60_000}
    ]
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
    assert len(runtime.catalog.funding_calls) == 2
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=False)
    assert len(runtime.catalog.funding_calls) == 2  # cursor now equals observed end
    runtime.catalog.quote = quote(END + 60_001)
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=False)
    assert len(runtime.catalog.funding_calls) == 3
    assert D(runtime.book.account("example", {SYMBOL: runtime.catalog.quote})["funding_paid"]) == D(".1")


async def test_late_funding_publication_is_not_cached_as_no_event_or_advanced_cursor(runtime):
    initial_long(runtime)
    runtime.catalog.quote = quote(END + 10_000)
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
    runtime.catalog.quote = quote(END + 60_001)
    for _ in range(2):
        await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
        assert (
            runtime.book.account("example", {SYMBOL: runtime.catalog.quote})["economic_status"]
            == "funding_pending"
        )
    assert len(runtime.catalog.funding_calls) == 3
    assert runtime.book.positions("example")[0]["funding_cursor"] == END
    runtime.catalog.events = [
        {"ts": END + 60_000, "rate": D(".001"), "mark_price": D(100), "mark_ts": END + 60_000}
    ]
    await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
    assert runtime.book.positions("example")[0]["funding_cursor"] == END + 60_000
    assert len(runtime.catalog.funding_calls) == 4


def research_config(dataset="trade-version"):
    return {
        "dataset_id": dataset,
        "mark_dataset_id": None,
        "funding_dataset_id": None,
        "strategy": asdict(StrategyConfig(kind="buy_hold")),
        "direction": "long_only",
        "initial_cash": "10000",
        "leverage": 1,
        "fee_bps": "10",
        "slippage_bps": "5",
        "liquidation_fee_bps": "5",
        "mode": "single",
        "options": {},
    }


async def test_running_research_is_requeued_after_restart_and_saved_snapshot_reused(runtime, monkeypatch):
    # Use a spot metadata version for this job; no derivatives transport needed.
    runtime.catalog.dataset = dict(runtime.catalog.dataset)
    runtime.catalog.dataset["inst_id"] = "BTC-USDT"
    runtime.catalog.dataset["metadata"] = {
        key: value
        for key, value in runtime.catalog.quote["instrument"].items()
        if key in {"base", "quote", "tick_size", "lot_size", "min_size", "state"}
    } | {"inst_id": "BTC-USDT", "inst_type": "SPOT"}
    run = runtime.create_run(research_config())
    captured = {
        "trade": runtime.catalog.dataset,
        "mark": None,
        "funding": None,
        "margin_tiers": None,
        "instrument": runtime.catalog.dataset["metadata"],
        "captured_at": 123,
    }
    with runtime.store.write() as conn:
        conn.execute(
            "UPDATE pro_runs SET status='running',snapshot=? WHERE id=?", (json.dumps(captured), run["id"])
        )
    recovered = restart(runtime)
    blocker = asyncio.Event()

    async def idle():
        await blocker.wait()

    for method in ("catalog_loop", "execution_loop", "strategy_loop", "backup_loop"):
        monkeypatch.setattr(recovered, method, idle)
    await recovered.start()
    try:
        for _ in range(200):
            current = recovered.run(run["id"], include_snapshot=True)
            if current["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed", current.get("error")
        assert current["snapshot"]["captured_at"] == 123
        assert recovered.catalog.resumes == 1
        assert current["result"]["result"]["metrics"]["initial_cash"] == "10000"
    finally:
        await recovered.stop()


def test_compute_crops_declared_warmup_and_reuses_captured_funding_without_reload(runtime, monkeypatch):
    catalog = runtime.catalog
    catalog.candles = [
        Candle(END - (3000 - index) * HOUR, D(100), D(100), D(100), D(100), D(1)) for index in range(3000)
    ]
    start = catalog.candles[2500].ts
    end = catalog.candles[2510].ts
    config = research_config() | {
        "mark_dataset_id": "mark-version",
        "funding_dataset_id": "funding-version",
        "start_ts": start,
        "end_ts": end,
    }
    event = {
        "ts": start + 2 * HOUR,
        "rate": ".001",
        "mark_price": "100",
        "mark_ts": start + 2 * HOUR,
        "mark_price_source": "historical_mark_1m_open_approximation",
    }
    dataset = catalog.dataset | {"start": catalog.candles[0].ts, "end": END}
    captured = {
        "trade": dataset,
        "mark": dataset | {"kind": "mark"},
        "funding": dataset
        | {
            "kind": "funding",
            "quality": {"complete": True, "coverage_start": catalog.candles[0].ts, "coverage_end": END},
        },
        "margin_tiers": {"source": "example", "tiers": catalog.quote["margin_tiers"]},
        "instrument": catalog.quote["instrument"],
        "funding_events": [event],
    }

    def forbidden(identifier):
        pytest.fail("Captured funding replay tried to reload mutable transport inputs")

    monkeypatch.setattr(catalog, "load_funding", forbidden, raising=False)
    result = runtime.compute(config, captured, {})["result"]
    assert result["assumptions"]["warmup_bars"] == 2000
    assert len(result["input_snapshot"]["trade_candles"]) == 2010
    assert len(result["equity"]) == 10
    assert result["funding"][0]["ts"] == start + 2 * HOUR
    assert result["funding"][0]["mark_price_source"] == "historical_mark_1m_open_approximation"
    assert (
        result["input_snapshot"]["funding_events"][0]["rate"] == ".001"
        or result["input_snapshot"]["funding_events"][0]["rate"] == "0.001"
    )


async def test_old_pending_intent_is_superseded_by_latest_closed_bar(runtime):
    deployed = deployment(runtime)
    old_bar = END - 2 * HOUR
    with runtime.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_strategy_intents VALUES(?,?,?,'pending',?)", (deployed["id"], old_bar, ".1", END)
        )
        conn.execute("UPDATE pro_deployments SET last_bar=? WHERE id=?", (old_bar, deployed["id"]))
    await runtime.evaluate(runtime.deployments()[0])
    with runtime.store.read() as conn:
        old = conn.execute(
            "SELECT status FROM pro_strategy_intents WHERE deployment_id=? AND bar=?",
            (deployed["id"], old_bar),
        ).fetchone()[0]
    assert old == "superseded"
    assert durable_intent(runtime, deployed["id"])["status"] == "completed"
    assert len(runtime.book.orders("example")) == 1
    assert runtime.book.orders("example")[0]["side"] == "sell"


async def test_simultaneous_research_claim_executes_only_one_compute(runtime, monkeypatch):
    dataset = dict(runtime.catalog.dataset)
    dataset["inst_id"] = "BTC-USDT"
    dataset["metadata"] = {
        key: value
        for key, value in runtime.catalog.quote["instrument"].items()
        if key in {"base", "quote", "tick_size", "lot_size", "min_size", "state"}
    } | {"inst_id": "BTC-USDT", "inst_type": "SPOT"}
    runtime.catalog.dataset = dataset
    queued = runtime.create_run(research_config())
    calls = []

    def compute(config, snapshot, manifest):
        calls.append(snapshot["trade"]["id"])
        return {"mode": "single", "result": {"metrics": {"initial_cash": "10000"}}}

    monkeypatch.setattr(runtime, "compute", compute)
    await asyncio.gather(runtime.perform_run(queued["id"]), runtime.perform_run(queued["id"]))
    assert calls == ["trade-version"]
    assert runtime.run(queued["id"])["status"] == "completed"


async def test_replay_rejects_recomputed_results_that_differ_from_original(runtime, monkeypatch):
    original_result = {"mode": "single", "result": {"metrics": {"final_equity": "10000"}}}
    identifier = "e" * 32
    config = research_config()
    with runtime.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_runs(id,source,status,config,snapshot,manifest,result,created_at,updated_at) VALUES(?,?,'queued',?,?,?,?,?,?)",
            (
                identifier,
                "example",
                dumps(config),
                dumps({"trade": runtime.catalog.dataset, "instrument": runtime.catalog.quote["instrument"]}),
                dumps(
                    {"replay_of": "original", "expected_result_hash": serialized_result(original_result)[1]}
                ),
                None,
                END,
                END,
            ),
        )
    monkeypatch.setattr(
        runtime, "compute", lambda *args: {"mode": "single", "result": {"metrics": {"final_equity": "10001"}}}
    )
    await runtime.perform_run(identifier)
    result = runtime.run(identifier)
    assert result["status"] == "failed" and result["result"] is None
    assert result["manifest"]["replay_verified"] is False
    assert "differ" in result["error"]


async def test_history_pages_never_decode_materialized_result_or_input_tables(runtime, monkeypatch):
    config = {"mode": "single"}
    with runtime.store.write() as conn:
        for index in range(5):
            conn.execute(
                "INSERT INTO pro_runs(id,source,status,config,snapshot,result,summary,created_at,updated_at) VALUES(?,?,'completed',?,?,?,?,?,?)",
                (
                    f"{index:032x}",
                    "example",
                    dumps(config),
                    '{"large_snapshot": "must_not_be_read"}',
                    '{"large_financial_tables": "must_not_be_read"}',
                    '{"final_equity":"10000"}',
                    END,
                    END,
                ),
            )
    decode = json.loads

    def guarded(value):
        assert "must_not_be_read" not in value
        return decode(value)

    monkeypatch.setattr("tidebench.pro_service.json.loads", guarded)
    first = runtime.runs("example", limit=2)
    second = runtime.runs("example", limit=2, before=f"{first[-1]['created_at']}:{first[-1]['id']}")
    third = runtime.runs("example", limit=2, before=f"{second[-1]['created_at']}:{second[-1]['id']}")
    assert [row["id"] for row in first + second + third] == [f"{index:032x}" for index in reversed(range(5))]
    assert all(row["summary"]["final_equity"] == "10000" for row in first + second + third)
    assert runtime.runs("okx") == []


def test_installed_research_identity_is_path_independent_and_content_sensitive(monkeypatch):
    from pathlib import Path

    identity = research_identity()
    read = Path.read_bytes

    def changed(path):
        content = read(path)
        return content + b"\n# changed implementation\n" if path.name == "pro_research.py" else content

    monkeypatch.setattr(Path, "read_bytes", changed)
    altered = research_identity()
    assert identity["code_fingerprint"] != altered["code_fingerprint"]
    assert identity["modules"]["engine.py"] == altered["modules"]["engine.py"]
    assert all("/" not in name for name in identity["modules"])


async def test_funding_transport_failure_cannot_block_liquidation_and_restart_settlement(
    runtime, monkeypatch
):
    runtime.book.set_risk("example", {"max_leverage": "10"}, "test")
    runtime.book.submit(command(leverage=10), "funding-outage-entry", {SYMBOL: runtime.catalog.quote})
    due = END + 60001
    severe = quote(due)
    severe.update(bid="80", ask="80", last="80", mark="80")
    runtime.catalog.quote = severe

    async def unavailable(*args):
        raise PlatformError("upstream_outage", "Funding history endpoint unavailable", 502)

    with monkeypatch.context() as patch:
        patch.setattr(runtime.catalog, "funding_history", unavailable)
        await runtime.poll_market("example", SYMBOL, {SYMBOL: severe})
    assert not runtime.book.positions("example")
    assert runtime.book.orders("example")[0]["liquidation"]
    assert runtime.book.account("example", {SYMBOL: severe})["equity"] is None
    assert runtime.book.deferred_funding.pending("example")[0]["quantity"] == "100"
    restored = restart(runtime)
    restored.catalog.events = [
        {"ts": END + 60000, "rate": ".001", "mark_price": "100", "mark_ts": END + 60000}
    ]
    await restored.sync_funding("example", SYMBOL, {SYMBOL: severe}, periodic=True, protective=True)
    account = restored.book.account("example", {SYMBOL: severe})
    assert not account["pending_funding"]
    assert D(account["funding_paid"]) == D(".1")
    assert D(account["equity"]) == D("9979.9")
    assert restored.book.contribution_report("example", {SYMBOL: severe})["reconciled"]
    await restored.sync_funding("example", SYMBOL, {SYMBOL: severe}, periodic=True, protective=True)
    assert D(restored.book.account("example", {SYMBOL: severe})["funding_paid"]) == D(".1")
    await restored.stop()
