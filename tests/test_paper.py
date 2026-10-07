"""Adversarial checks for transactional spot fills and strategy supervision."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from threading import Barrier
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
import tidebench.paper as paper_module
from tidebench.engine import Candle, Instrument
from tidebench.paper import DeskError, PaperDesk
from tidebench.schemas import OrderInput, RiskInput, StrategyInput
from tidebench.store import Store, dumps
from tidebench.worker import Supervisor

D = Decimal
HOUR = 3_600_000
DAY = 86_400_000
NOW = 1_800_000_000_000
INSTRUMENT = Instrument("BTC-USDT", "BTC", "USDT", D("0.01"), D("0.001"), D("0.001"))


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=NOW)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(value.now / 1000, tz=tz)

    monkeypatch.setattr(paper_module, "now_ms", lambda: value.now)
    monkeypatch.setattr(paper_module, "datetime", FixedDatetime)
    return value


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "paper.sqlite3")


@pytest.fixture
def desk(store, clock):
    return PaperDesk(store)


def tickers(source="example", *, bid="99.97", ask="100.03", last="100", ts=NOW, inst_id="BTC-USDT"):
    return {
        "source": source,
        "items": [
            {
                "inst_id": inst_id,
                "bid": bid,
                "ask": ask,
                "last": last,
                "ts": ts,
            }
        ],
    }


def order(side="buy", quantity="1", source="example", inst_id="BTC-USDT"):
    return OrderInput(source=source, inst_id=inst_id, side=side, quantity=D(quantity))


def limits(desk, source="example", *, notional="1000000", position=100, daily=50):
    return desk.update_risk(
        source,
        RiskInput(
            source=source, max_order_notional=D(notional), max_position_pct=position, max_daily_loss_pct=daily
        ),
    )


def account_row(store, source="example"):
    with store.read() as conn:
        return dict(conn.execute("SELECT * FROM accounts WHERE source=?", (source,)).fetchone())


def position_row(store, source="example", inst_id="BTC-USDT"):
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE source=? AND inst_id=?", (source, inst_id)
        ).fetchone()
        return dict(row) if row else None


def events(store, kind, source="example"):
    return [item for item in store.events(source, limit=200) if item["kind"] == kind]


def test_same_idempotency_key_serializes_concurrent_fills(desk, store):
    count = 8
    barrier = Barrier(count)

    def submit():
        barrier.wait(timeout=5)
        return desk.place(order(), "concurrent-command", INSTRUMENT, tickers())

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(submit) for _ in range(count)]]
    assert len({result[0]["id"] for result in results}) == 1
    assert sum(not replay for _, replay in results) == 1
    assert len(store.orders("example")) == 1
    assert len(events(store, "paper.filled")) == 1
    assert position_row(store)["quantity"] == "1"


def test_idempotency_normalizes_quantities_and_rejects_different_payload(desk, store):
    original, replay = desk.place(order(quantity="1.0"), "payload-command", INSTRUMENT, tickers())
    repeated, replayed = desk.place(order(quantity="1.000"), "payload-command", INSTRUMENT, tickers())
    assert replay is False and replayed is True and repeated == original
    with pytest.raises(DeskError) as caught:
        desk.place(order(quantity="2"), "payload-command", INSTRUMENT, tickers())
    assert (caught.value.code, caught.value.status) == ("idempotency_conflict", 409)
    assert len(store.orders("example")) == 1


def test_committed_fill_and_halt_survive_restart_and_retry_needs_no_new_quote(desk, store):
    original, _ = desk.place(order(), "restart-command", INSTRUMENT, tickers())
    before = account_row(store)
    desk.halt("example", True, "restart test")
    restarted = Store(store.path)
    restored = PaperDesk(restarted)
    assert account_row(restarted) == before
    assert restarted.risk("example")["kill_switch"] is True
    repeated, replay = restored.place(order(), "restart-command", INSTRUMENT, {"items": []})
    assert replay is True and repeated == original
    with pytest.raises(DeskError, match="halted"):
        restored.place(order(), "new-after-restart", INSTRUMENT, tickers())


def test_source_accounts_keys_orders_positions_and_risk_are_isolated(desk, store):
    desk.place(order(), "same-across-accounts", INSTRUMENT, tickers())
    assert account_row(store, "okx")["cash"] == "10000"
    assert position_row(store, "okx") is None
    desk.halt("example", True, "one source only")
    desk.place(order(source="okx"), "same-across-accounts", INSTRUMENT, tickers("okx"))
    assert len(store.orders("okx")) == len(store.orders("example")) == 1
    assert store.risk("okx")["kill_switch"] is False


def test_insufficient_cash_including_fee_and_overselling_leave_balances_unchanged(desk, store):
    limits(desk)
    with store.write() as conn:
        conn.execute("UPDATE accounts SET cash='100',initial_cash='100' WHERE source='example'")
    before = account_row(store)
    with pytest.raises(DeskError) as caught:
        desk.place(order(), "fee-must-fit", INSTRUMENT, tickers())
    assert caught.value.code == "insufficient_cash"
    assert account_row(store) == before
    assert store.orders("example") == []
    desk.place(order(quantity="0.5"), "smaller-buy", INSTRUMENT, tickers())
    after_buy = account_row(store)
    with pytest.raises(DeskError) as caught:
        desk.place(order("sell", "0.501"), "oversell-command", INSTRUMENT, tickers())
    assert caught.value.code == "insufficient_asset"
    assert account_row(store) == after_buy
    assert position_row(store)["quantity"] == "0.5"


def test_ask_bid_adverse_tick_slippage_and_two_sided_fees(desk, store):
    instrument = replace(INSTRUMENT, tick_size=D("0.05"))
    quotes = tickers(bid="100.003", ask="100.007", last="100.005")
    buy, _ = desk.place(order(), "ask-price-command", instrument, quotes)
    sell, _ = desk.place(order("sell"), "bid-price-command", instrument, quotes)
    assert (D(buy["price"]), D(sell["price"])) == (D("100.10"), D("99.95"))
    assert D(buy["fee"]) == D("0.10010") and D(sell["fee"]) == D("0.09995")
    assert D(account_row(store)["cash"]) == D("10000") - D("100.10") - D("0.10010") + D("99.95") - D(
        "0.09995"
    )
    assert D(position_row(store)["cost_basis"]) == 0


def test_partial_sells_conserve_average_cost_and_terminal_realized_pnl(desk, store):
    with localcontext() as context:
        context.prec = 50
        first, _ = desk.place(order(quantity="2"), "basis-first-buy", INSTRUMENT, tickers())
        second, _ = desk.place(
            order(quantity="1"),
            "basis-second-buy",
            INSTRUMENT,
            tickers(bid="119.97", ask="120.03", last="120"),
        )
        original_basis = D(first["notional"]) + D(first["fee"]) + D(second["notional"]) + D(second["fee"])
        partial, _ = desk.place(
            order("sell", "1"),
            "basis-partial-sell",
            INSTRUMENT,
            tickers(bid="129.97", ask="130.03", last="130"),
        )
        remaining = position_row(store)
        assert D(remaining["quantity"]) == D("2")
        assert abs(D(remaining["cost_basis"]) + original_basis / 3 - original_basis) < D("1e-40")
        final, _ = desk.place(
            order("sell", "2"), "basis-final-sell", INSTRUMENT, tickers(bid="89.97", ask="90.03", last="90")
        )
        proceeds = D(partial["notional"]) - D(partial["fee"]) + D(final["notional"]) - D(final["fee"])
        account = account_row(store)
        assert D(position_row(store)["quantity"]) == D(position_row(store)["cost_basis"]) == 0
        assert abs(D(account["realized_pnl"]) - (proceeds - original_basis)) < D("1e-40")
        assert abs(D(account["cash"]) - D("10000") - D(account["realized_pnl"])) < D("1e-40")


def test_filled_order_ledger_replays_to_account_and_inventory(desk, store):
    commands = [
        ("buy", "2", "100"),
        ("buy", "1", "120"),
        ("sell", "0.5", "110"),
        ("sell", "0.75", "130"),
        ("buy", "0.25", "90"),
        ("sell", "2", "125"),
    ]
    for index, (side, qty, price) in enumerate(commands):
        desk.place(
            order(side, qty), f"ledger-command-{index}", INSTRUMENT, tickers(bid=price, ask=price, last=price)
        )
    with store.read() as conn:
        ledger = [json.loads(row[0]) for row in conn.execute("SELECT body FROM orders ORDER BY rowid")]
    with localcontext() as context:
        context.prec = 50
        cash, quantity, basis, fees, realized = D("10000"), D(0), D(0), D(0), D(0)
        for fill in ledger:
            size, price, fee = D(fill["quantity"]), D(fill["price"]), D(fill["fee"])
            notional = size * price
            if fill["side"] == "buy":
                cash -= notional + fee
                basis += notional + fee
                quantity += size
            else:
                removed = basis if size == quantity else basis * size / quantity
                basis -= removed
                quantity -= size
                cash += notional - fee
                realized += notional - fee - removed
            fees += fee
            assert cash == D(fill["cash_after"])
            assert quantity == D(fill["position_after"])
            assert basis == D(fill["cost_basis_after"])
            assert realized == D(fill["realized_pnl_after"])
        account, position = account_row(store), position_row(store)
        assert cash == D(account["cash"]) and fees == D(account["fees_paid"])
        assert quantity == D(position["quantity"]) and basis == D(position["cost_basis"])
        assert realized == D(account["realized_pnl"])


def test_risk_limits_use_post_fee_equity_and_allow_reduction(desk, store):
    limits(desk, notional="10000", position=10, daily=5)
    with pytest.raises(DeskError) as caught:
        desk.place(order(quantity="10"), "over-position-command", INSTRUMENT, tickers())
    assert caught.value.code == "position_limit"
    desk.place(order(quantity="9"), "under-position-command", INSTRUMENT, tickers())
    limits(desk, notional="1", position=1, daily=0.1)
    # Risk reductions remain available despite tighter order/exposure/loss limits.
    desk.place(order("sell", "9"), "reduce-risk-command", INSTRUMENT, tickers())
    assert D(position_row(store)["quantity"]) == 0


def test_per_order_limit_rejects_before_mutating_account(desk, store):
    limits(desk, notional="100")
    before = account_row(store)
    with pytest.raises(DeskError) as caught:
        desk.place(order(), "order-limit-command", INSTRUMENT, tickers())
    assert caught.value.code == "order_limit"
    assert account_row(store) == before and store.orders("example") == []


def test_daily_observed_loss_blocks_new_buys_but_not_sells(desk, store):
    desk.place(order(quantity="20"), "before-loss-command", INSTRUMENT, tickers())
    before = account_row(store)
    fallen = tickers(bid="49.97", ask="50.03", last="50")
    with pytest.raises(DeskError) as caught:
        desk.place(order(quantity="0.1"), "after-loss-command", INSTRUMENT, fallen)
    assert caught.value.code == "daily_loss_limit"
    assert account_row(store) == before
    desk.place(order("sell", "1"), "loss-reduction-command", INSTRUMENT, fallen)
    assert D(position_row(store)["quantity"]) == D("19")


def test_first_accepted_command_on_new_utc_day_anchors_observed_equity(desk, store, clock):
    desk.place(order(), "first-day-buy", INSTRUMENT, tickers())
    previous = account_row(store)
    clock.now += DAY
    quotes = tickers(bid="89.97", ask="90.03", last="90")
    expected_baseline = D(previous["cash"]) + D("90")
    desk.place(order("sell", "0.5"), "new-day-sell", INSTRUMENT, quotes)
    current = account_row(store)
    assert current["day_key"] == datetime.fromtimestamp(clock.now / 1000, tz=UTC).strftime("%Y-%m-%d")
    assert D(current["day_equity"]) == expected_baseline


@pytest.mark.parametrize("timestamp", [NOW - 15_001, NOW + 5_001])
def test_stale_and_excessively_future_live_quotes_fail_closed(desk, store, timestamp):
    before = account_row(store, "okx")
    with pytest.raises(DeskError) as caught:
        desk.place(order(source="okx"), f"bad-clock-{timestamp}", INSTRUMENT, tickers("okx", ts=timestamp))
    assert caught.value.code == "stale_quote"
    assert account_row(store, "okx") == before and store.orders("okx") == []


def test_synthetic_quotes_may_be_old_but_live_sources_must_match(desk, store):
    desk.place(order(), "old-example-command", INSTRUMENT, tickers(ts=1))
    with pytest.raises(DeskError) as caught:
        desk.place(order(), "mismatch-command", INSTRUMENT, tickers("okx"))
    assert caught.value.code == "source_mismatch"


@pytest.mark.parametrize(
    "patch",
    [
        {"bid": "0"},
        {"ask": "99"},
        {"ask": "110"},
        {"bid": "NaN"},
        {"ask": "Infinity"},
        {"last": "0"},
        {"last": "-1"},
        {"last": "NaN"},
        {"last": "Infinity"},
        {"ts": True},
        {"ts": "1800000000000"},
    ],
)
def test_invalid_execution_quotes_cannot_change_ledger(desk, store, patch):
    quotes = tickers()
    quotes["items"][0].update(patch)
    with pytest.raises(DeskError) as caught:
        desk.place(order(), "invalid-quote-command", INSTRUMENT, quotes)
    assert caught.value.code == "invalid_quote"
    assert account_row(store)["cash"] == "10000" and store.orders("example") == []


def test_invalid_held_mark_cannot_be_used_to_increase_risk_or_fake_account_value(desk, store):
    desk.place(order(), "hold-btc-command", INSTRUMENT, tickers())
    eth = replace(INSTRUMENT, inst_id="ETH-USDT", base="ETH")
    quotes = tickers(inst_id="ETH-USDT")
    quotes["items"].extend(tickers(last="NaN")["items"])
    with pytest.raises(DeskError) as caught:
        desk.place(order(inst_id="ETH-USDT"), "bad-held-mark-command", eth, quotes)
    assert caught.value.code == "invalid_quote"
    account = desk.account("example", quotes)
    assert account["equity"] is None and account["valuation_status"] == "unavailable"
    assert position_row(store, inst_id="ETH-USDT") is None


def test_rounding_to_zero_execution_price_is_rejected(desk, store):
    instrument = replace(INSTRUMENT, tick_size=D("1"))
    quotes = tickers(bid="1", ask="1", last="1")
    desk.place(order(), "single-tick-buy", instrument, quotes)
    before = account_row(store)
    with pytest.raises(DeskError):
        desk.place(order("sell"), "zero-price-sell", instrument, quotes)
    assert account_row(store) == before and len(store.orders("example")) == 1


@pytest.mark.parametrize("quantity", ["0.0001", "1.0001"])
def test_minimum_size_and_lot_precision_are_enforced(desk, store, quantity):
    with pytest.raises(DeskError) as caught:
        desk.place(order(quantity=quantity), "precision-command", INSTRUMENT, tickers())
    assert caught.value.code == "invalid_quantity" and caught.value.status == 422
    assert store.orders("example") == []


def test_halt_commit_serializes_ahead_of_waiting_order_transactions(desk, store):
    count = 4
    barrier = Barrier(count + 1)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            desk.place(order(), f"halt-race-command-{index}", INSTRUMENT, tickers())
        except DeskError as exc:
            return exc.code
        return "filled"

    with ThreadPoolExecutor(max_workers=count) as pool:
        with store.write() as conn:
            conn.execute("UPDATE risk SET kill_switch=1 WHERE source='example'")
            futures = [pool.submit(submit, index) for index in range(count)]
            barrier.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]
    assert results == ["desk_halted"] * count
    assert store.orders("example") == [] and account_row(store)["cash"] == "10000"


def test_failure_after_account_writes_rolls_back_fill_and_audit_atomically(desk, store, monkeypatch):
    original_audit = store.audit
    before = account_row(store)

    def fail_fill_audit(*args, **kwargs):
        if args[2] == "paper.filled":
            raise RuntimeError("injected audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(store, "audit", fail_fill_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        desk.place(order(), "rollback-command", INSTRUMENT, tickers())
    assert account_row(store) == before
    assert position_row(store) is None and store.orders("example") == []
    assert store.events("example") == []
    monkeypatch.setattr(store, "audit", original_audit)
    _, replayed = desk.place(order(), "rollback-command", INSTRUMENT, tickers())
    assert replayed is False and len(events(store, "paper.filled")) == 1


def deployment(store, strategy=None):
    configuration = strategy or StrategyInput(kind="buy_hold")
    with store.write() as conn:
        conn.execute(
            "INSERT INTO deployments(id,source,inst_id,bar,strategy,status,created_at,updated_at) "
            "VALUES('deployment-a','example','BTC-USDT','1H',?,'running',?,?)",
            (dumps(configuration.model_dump()), NOW, NOW),
        )
    return store.deployments("example")[0]


class FakeMarket:
    def __init__(self, block=False):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = block
        self.candles = [Candle(i * HOUR, D("100"), D("100"), D("100"), D("100"), D("1")) for i in range(2)]

    async def get_candles(self, *args):
        self.entered.set()
        if self.block:
            await self.release.wait()
        return {"candles": self.candles, "fetched_at": NOW}

    async def get_tickers(self, source):
        return tickers(source)

    async def get_instruments(self, source):
        return [INSTRUMENT]


@pytest.mark.asyncio
async def test_auto_strategy_restart_does_not_repeat_a_completed_bar(desk, store):
    snapshot = deployment(store)
    first = Supervisor(store, FakeMarket(), desk, SimpleNamespace())
    await first.evaluate(snapshot)
    assert len(store.orders("example")) == 1
    restored_store = Store(store.path)
    restarted = Supervisor(restored_store, FakeMarket(), PaperDesk(restored_store), SimpleNamespace())
    await restarted.evaluate(restored_store.deployments("example")[0])
    assert len(store.orders("example")) == 1 and len(events(store, "paper.filled")) == 1


@pytest.mark.asyncio
async def test_stop_during_market_fetch_prevents_auto_fill(desk, store):
    snapshot = deployment(store)
    market = FakeMarket(block=True)
    supervisor = Supervisor(store, market, desk, SimpleNamespace())
    task = asyncio.create_task(supervisor.evaluate(snapshot))
    await asyncio.wait_for(market.entered.wait(), 2)
    with store.write() as conn:
        conn.execute("UPDATE deployments SET status='stopped' WHERE id='deployment-a'")
    market.release.set()
    with pytest.raises(DeskError) as caught:
        await asyncio.wait_for(task, 2)
    assert caught.value.code == "deployment_stopped"
    assert store.orders("example") == []
    assert store.deployments("example")[0]["last_bar"] is None


@pytest.mark.asyncio
async def test_stop_during_warmup_does_not_advance_bar_or_emit_observed_event(desk, store):
    snapshot = deployment(store, StrategyInput(kind="sma_cross"))
    market = FakeMarket(block=True)
    supervisor = Supervisor(store, market, desk, SimpleNamespace())
    task = asyncio.create_task(supervisor.evaluate(snapshot))
    await asyncio.wait_for(market.entered.wait(), 2)
    with store.write() as conn:
        conn.execute("UPDATE deployments SET status='stopped' WHERE id='deployment-a'")
    market.release.set()
    await asyncio.wait_for(task, 2)
    assert store.deployments("example")[0]["last_bar"] is None
    assert events(store, "strategy.observed") == []


@pytest.mark.asyncio
async def test_auto_entry_budget_matches_fee_inclusive_cash_allocation(desk, store):
    snapshot = deployment(store, StrategyInput(kind="buy_hold", allocation=D("0.25")))
    await Supervisor(store, FakeMarket(), desk, SimpleNamespace()).evaluate(snapshot)
    fill = store.orders("example")[0]
    assert D(fill["notional"]) + D(fill["fee"]) <= D("2500")


@pytest.mark.asyncio
async def test_supervisor_stop_waits_for_inflight_transaction_before_returning(desk, store):
    entered, release = ThreadEvent(), ThreadEvent()
    supervisor = Supervisor(store, FakeMarket(), desk, SimpleNamespace())

    def blocked_fill():
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test did not release the fill")
        return desk.place(order(), "shutdown-inflight-command", INSTRUMENT, tickers())

    waiter = asyncio.create_task(supervisor.offload(blocked_fill))
    supervisor.tasks = [waiter]
    assert await asyncio.to_thread(entered.wait, 2)
    stopping = asyncio.create_task(supervisor.stop())
    try:
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not stopping.done()
        assert store.orders("example") == []
    finally:
        release.set()
        await asyncio.wait_for(stopping, 2)
    assert len(store.orders("example")) == 1
    assert not supervisor.inflight
