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
from tidebench.store import Store

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
            Candle(END - (500 - index) * HOUR, D(100), D(100), D(100), D(100), D(1)) for index in range(500)
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


def initial_long(runtime):
    return runtime.book.submit(command(), "initial-position", {SYMBOL: runtime.catalog.quote})


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
    initial_long(runtime)
    deployed = deployment(runtime)
    await runtime.evaluate(deployed)
    orders = runtime.book.orders("example")
    closes = [row for row in orders if row.get("reduce_only")]
    opens = [row for row in orders if row["actor"].startswith("strategy:") and not row.get("reduce_only")]
    assert len(closes) == len(opens) == 1
    assert closes[0]["side"] == "sell" and opens[0]["side"] == "sell"
    assert D(runtime.book.positions("example")[0]["quantity"]) < 0
    assert durable_intent(runtime, deployed["id"])["status"] == "completed"
    assert runtime.deployments()[0]["last_bar"] == END - HOUR
    await runtime.evaluate(runtime.deployments()[0])
    assert len(runtime.book.orders("example")) == 3


@pytest.mark.parametrize("phase", ["close", "open"])
async def test_crash_after_committed_phase_recovers_without_duplicate_fill(runtime, monkeypatch, phase):
    initial_long(runtime)
    deployed = deployment(runtime)
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
    initial_long(runtime)
    deployed = deployment(runtime)
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
    initial_long(runtime)
    deployed = deployment(runtime)
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
    initial_long(runtime)
    deployed = deployment(runtime)
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
        with pytest.raises(PlatformError, match="not published"):
            await runtime.sync_funding("example", SYMBOL, {SYMBOL: runtime.catalog.quote}, periodic=True)
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

    for method in ("catalog_loop", "execution_loop", "backup_loop"):
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
