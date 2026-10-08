"""Real catalog/book acceptance for evolving time, incremental state and P&L."""

import asyncio
from dataclasses import asdict
from decimal import Decimal, localcontext

import httpx
import pytest
from tidebench.config import Settings
from tidebench.engine import ACCOUNTING_CONTEXT, StrategyConfig
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_research import ResearchConfig, _DecisionState
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store

D = Decimal
HOUR = 3_600_000


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    yield runtime
    await runtime.stop()
    await client.aclose()


def step(runtime, hours=1):
    return runtime.clock.change(
        step_ms=hours * HOUR, expected_revision=runtime.clock.status()["revision"], actor="test"
    )


def deploy(runtime, kind="rsi_reversion", symbol="BTC-USDT"):
    return runtime.deploy(
        {
            "source": "example",
            "inst_id": symbol,
            "bar": "1H",
            "direction": "long_only",
            "leverage": 1,
            "allocation": ".1",
            "strategy": asdict(StrategyConfig(kind=kind, allocation=D(".1"))),
        },
        "test",
    )


async def test_clock_is_forward_only_persisted_and_moves_quotes_and_funding(runtime):
    original = runtime.clock.status()
    before = await runtime.catalog.get_market_snapshot("BTC-USDT-SWAP", "example")
    after_clock = step(runtime, 4)
    after = await runtime.catalog.get_market_snapshot("BTC-USDT-SWAP", "example")
    assert after["ts"] - before["ts"] == 4 * HOUR
    assert after["last"] != before["last"]
    assert before["funding_time"] == after["ts"]
    assert after["funding_time"] == after["ts"] + 4 * HOUR
    restored = ProfessionalRuntime(Store(runtime.store.path), runtime.market, runtime.settings)
    assert restored.clock.status() == after_clock
    with pytest.raises(PlatformError, match="changed"):
        runtime.clock.change(step_ms=HOUR, expected_revision=original["revision"], actor="stale-user")
    with pytest.raises(PlatformError, match="at most one day"):
        runtime.clock.change(step_ms=-1, expected_revision=after_clock["revision"], actor="test")
    runtime.clock.change(speed=60, expected_revision=after_clock["revision"], actor="test")
    with pytest.raises(PlatformError, match="Pause"):
        step(runtime)


async def test_incremental_history_and_rsi_survive_restart_without_sliding_reset(runtime):
    deployment = deploy(runtime)
    await runtime.evaluate(deployment)
    for _ in range(4):
        step(runtime)
        await runtime.evaluate(runtime.deployments()[0])
    with runtime.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM catalog_records").fetchone()[0] == 2004
        assert conn.execute("SELECT COUNT(*) FROM forward_bars").fetchone()[0] == 2004
    decisions = runtime.history.decisions(deployment["id"])
    assert [d["new_bars"] for d in decisions] == [1, 1, 1, 1, 2000]
    restored = ProfessionalRuntime(Store(runtime.store.path), runtime.market, runtime.settings)
    step(restored)
    await restored.evaluate(restored.deployments()[0])
    latest = restored.history.decisions(deployment["id"])[0]
    with restored.store.read() as conn:
        rows = conn.execute("SELECT body FROM forward_bars ORDER BY ts").fetchall()
    import json

    with localcontext(ACCOUNTING_CONTEXT):
        state = _DecisionState(
            ResearchConfig(strategy=StrategyConfig(kind="rsi_reversion", allocation=D(".1")))
        )
        for row in rows:
            signal, indicators = state.on_close(D(json.loads(row["body"])["close"]))
    assert latest["indicators"] == indicators and latest["signal"] == signal
    assert latest["new_bars"] == 1 and len(latest["content_hash"]) == 64
    await restored.evaluate(restored.deployments()[0])
    assert len(restored.history.decisions(deployment["id"])) == 6


async def test_checkpoint_corruption_blocks_trading_and_keeps_last_decision(runtime):
    deployment = deploy(runtime, "buy_hold")
    await runtime.evaluate(deployment)
    before = runtime.book.orders("example")
    with runtime.store.write() as conn:
        conn.execute("UPDATE forward_checkpoints SET state='{}'")
    step(runtime)
    with pytest.raises(PlatformError, match="integrity"):
        await runtime.evaluate(runtime.deployments()[0])
    assert runtime.book.orders("example") == before
    assert len(runtime.history.decisions(deployment["id"])) == 1


async def test_quote_dispatch_is_linear_and_failure_does_not_lose_other_markets(runtime, monkeypatch):
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    calls = []
    original = runtime.catalog.get_market_snapshot

    async def quote(symbol, source):
        calls.append(symbol)
        if symbol == "ETH-USDT":
            raise PlatformError("outage", "injected quote failure", 502)
        return await original(symbol, source)

    monkeypatch.setattr(runtime.catalog, "get_market_snapshot", quote)
    for symbol in symbols:
        deploy(runtime, "buy_hold", symbol)
    await runtime.execution_once()
    assert sorted(calls) == sorted(symbols)
    assert ("example", "ETH-USDT") in runtime.market_errors
    assert ("example", "BTC-USDT") in runtime.snapshots and ("example", "SOL-USDT") in runtime.snapshots


async def test_performance_records_costs_real_marks_and_explicit_valuation_gaps(runtime):
    symbol = "BTC-USDT"
    quote = await runtime.catalog.get_market_snapshot(symbol, "example")
    runtime.book.observe("example", {symbol: quote})
    order = {
        "source": "example",
        "inst_id": symbol,
        "side": "buy",
        "quantity": ".01",
        "leverage": 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
    }
    fill = await runtime.submit(order, "performance-order", "test")
    initial = runtime.book.performance.report("example")
    assert D(initial["summary"]["net_pnl"]) < -D(fill["fee"])
    assert D(initial["summary"]["fees_paid_change"]) == D(fill["fee"])
    step(runtime)
    current = await runtime.catalog.get_market_snapshot(symbol, "example")
    runtime.book.observe("example", {symbol: current})
    report = runtime.book.performance.report("example")
    assert report["summary"]["total_observations"] == 3
    assert report["summary"]["complete_valuation_chain"]
    # A partial valuation is recorded as a gap, never interpolated into returns.
    runtime.book.observe(
        "example", {"ETH-USDT": await runtime.catalog.get_market_snapshot("ETH-USDT", "example")}
    )
    report = runtime.book.performance.report("example")
    assert report["items"][-1]["body"]["equity"] is None
    assert report["summary"]["return"] is None
    assert not report["summary"]["complete_valuation_chain"]


async def test_fast_market_stop_fills_before_another_quote_returns(runtime, monkeypatch):
    symbol = "BTC-USDT"
    quotes = {s: await runtime.catalog.get_market_snapshot(s, "example") for s in (symbol, "ETH-USDT")}
    command = {
        "source": "example",
        "inst_id": symbol,
        "side": "buy",
        "quantity": ".01",
        "leverage": 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
    }
    runtime.book.submit(command, "latency-open", quotes, "test")
    tick = D(quotes[symbol]["instrument"]["tick_size"])
    trigger = (D(quotes[symbol]["bid"]) // tick + 1000) * tick
    pending = runtime.book.submit(
        command
        | {"side": "sell", "reduce_only": True, "order_type": "stop_market", "stop_price": str(trigger)},
        "latency-stop",
        quotes,
        "test",
    )
    deploy(runtime, "buy_hold", "ETH-USDT")
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.catalog.get_market_snapshot

    async def quote(symbol, source):
        if symbol == "ETH-USDT":
            entered.set()
            await release.wait()
        return await original(symbol, source)

    monkeypatch.setattr(runtime.catalog, "get_market_snapshot", quote)
    task = asyncio.create_task(runtime.execution_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        async with asyncio.timeout(1):
            while (
                next(o for o in runtime.book.orders("example") if o["id"] == pending["id"])["status"]
                != "filled"
            ):
                await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
        await task


async def test_holding_exit_and_decision_order_link_survive_restart(runtime):
    body = {
        "source": "example",
        "inst_id": "BTC-USDT",
        "bar": "1H",
        "direction": "long_only",
        "leverage": 1,
        "allocation": ".1",
        "strategy": asdict(StrategyConfig(kind="buy_hold", allocation=D(".1"), max_holding_bars=2)),
    }
    deployment = runtime.deploy(body, "test")
    await runtime.evaluate(deployment)
    initial = runtime.history.decisions(deployment["id"])[0]
    assert len(initial["orders"]) == 1 and initial["orders"][0]["side"] == "buy"
    step(runtime)
    await runtime.evaluate(runtime.deployments()[0])
    restored = ProfessionalRuntime(Store(runtime.store.path), runtime.market, runtime.settings)
    step(restored)
    await restored.evaluate(restored.deployments()[0])
    latest = restored.history.decisions(deployment["id"])[0]
    assert latest["exit_state"]["holding_closes"] == 2
    assert latest["indicators"]["exit_reason"] == "maximum_holding_closes"
    assert latest["orders"][0]["reduce_only"] and latest["orders"][0]["side"] == "sell"
    assert not restored.book.positions("example")
    restored.stop_deployment(deployment["id"], "test")
    replacement = restored.deploy(body, "test")
    await restored.evaluate(replacement)
    assert len(restored.book.positions("example")) == 1
    with restored.store.write() as conn:
        conn.execute("UPDATE forward_equity SET body='{}' WHERE id=(SELECT MAX(id) FROM forward_equity)")
    with pytest.raises(PlatformError, match="content check"):
        restored.book.performance.report("example")


@pytest.mark.parametrize("kind", ["ts_momentum", "regime_reversion"])
async def test_new_models_resume_from_durable_forward_history_and_match_full_path(runtime, kind):
    deployment = deploy(runtime, kind=kind, symbol="ETH-USDT")
    await runtime.evaluate(deployment)
    step(runtime)
    await runtime.evaluate(runtime.deployments()[0])
    restored = ProfessionalRuntime(Store(runtime.store.path), runtime.market, runtime.settings)
    step(restored)
    await restored.evaluate(restored.deployments()[0])
    latest = restored.history.decisions(deployment["id"])[0]
    assert latest["new_bars"] == 1
    with restored.store.read() as conn:
        rows = conn.execute("SELECT body FROM forward_bars ORDER BY ts").fetchall()
    import json

    with localcontext(ACCOUNTING_CONTEXT):
        state = _DecisionState(ResearchConfig(strategy=StrategyConfig(kind=kind, allocation=D(".1"))))
        for row in rows:
            signal, indicators = state.on_close(D(json.loads(row["body"])["close"]))
    assert latest["signal"] == signal and latest["indicators"] == indicators
    assert "bar_vol_pct" in indicators
