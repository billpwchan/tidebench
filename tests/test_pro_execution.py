"""Economic and transactional invariants for persistent professional simulation."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_DOWN, Decimal, Inexact, getcontext, localcontext
from threading import Barrier

import pytest
from tidebench.engine import ACCOUNTING_CONTEXT
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook, number, tier_for
from tidebench.store import Store, dumps, now_ms

D = Decimal
SYMBOL = "BTC-USDT-SWAP"


def snapshot(symbol=SYMBOL, source="example", price="100", ts=1_000_000, **changes):
    is_swap = symbol.endswith("-SWAP")
    meta = {
        "inst_id": symbol,
        "inst_type": "SWAP" if is_swap else "SPOT",
        "base": "BTC",
        "quote": "USDT",
        "settle_ccy": "USDT" if is_swap else "",
        "ct_type": "linear" if is_swap else None,
        "ct_val": ".01" if is_swap else None,
        "ct_mult": "1" if is_swap else None,
        "ct_val_ccy": "BTC" if is_swap else None,
        "tick_size": ".01",
        "lot_size": ".01",
        "min_size": ".01",
        "state": "live",
    }
    return {
        "source": source,
        "inst_id": symbol,
        "ts": ts,
        "mark_ts": ts,
        "bid": price,
        "ask": price,
        "last": price,
        "mark": price,
        "instrument": meta,
        "funding_time": 2_000_000,
        "next_funding_time": 3_000_000,
        "margin_tiers": [
            {
                "tier": 1,
                "min_size": "0",
                "max_size": "1000",
                "imr": ".01",
                "mmr": ".004",
                "max_leverage": "100",
            },
            {
                "tier": 2,
                "min_size": "1000",
                "max_size": "100000",
                "imr": ".02",
                "mmr": ".005",
                "max_leverage": "50",
            },
        ],
        **changes,
    }


def order(symbol=SYMBOL, side="buy", quantity="100", source="example", leverage=10, **changes):
    return {
        "source": source,
        "inst_id": symbol,
        "side": side,
        "quantity": quantity,
        "leverage": leverage if symbol.endswith("-SWAP") else 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
        **changes,
    }


@pytest.fixture
def book(tmp_path):
    result = SimulationBook(Store(tmp_path / "workspace.sqlite"))
    result.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test")
    return result


def submit(book, command=None, quote=None, key="command-0001", **kwargs):
    command = command or order()
    quote = quote or snapshot(command["inst_id"], command["source"])
    return book.submit(command, key, {command["inst_id"]: quote}, **kwargs)


def assert_ledger(book, source="example"):
    with book.store.read() as conn:
        rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM pro_ledger WHERE source=? ORDER BY id", (source,))
        ]
        cash = D(conn.execute("SELECT cash FROM pro_accounts WHERE source=?", (source,)).fetchone()[0])
        positions = list(
            conn.execute("SELECT quantity,margin,metadata FROM pro_positions WHERE source=?", (source,))
        )
    with localcontext(ACCOUNTING_CONTEXT) as context:
        context.prec = 200
        transactions = {}
        for row in rows:
            key = row["tx_id"], row["asset"]
            transactions[key] = transactions.get(key, D(0)) + D(row["debit"]) - D(row["credit"])
        assert all(value == 0 for value in transactions.values())
    with localcontext(ACCOUNTING_CONTEXT):
        replay_cash = D(0)
        replay_margin = D(0)
        inventory = {}
        for row in rows:
            amount = D(row["debit"]) - D(row["credit"])
            if row["asset"] == "USDT" and row["account"] == "cash":
                replay_cash += amount
            if row["asset"] == "USDT" and row["account"] == "margin":
                replay_margin += amount
            if row["account"] == "inventory":
                inventory[row["asset"]] = inventory.get(row["asset"], D(0)) + amount
        assert replay_cash == cash
        assert cash >= 0
        assert replay_margin == sum((D(row["margin"]) for row in positions), D(0))
        for row in positions:
            meta = json.loads(row["metadata"])
            if meta["inst_type"] == "SPOT":
                assert inventory.get(meta["base"], D(0)) == D(row["quantity"])


def test_spot_native_journal_partial_basis_and_equity_conservation(book):
    symbol = "BTC-USDT"
    book.set_risk("example", {"fee_bps": "10"}, "test")
    submit(book, order(symbol, quantity="4"), snapshot(symbol), key="spot-buy-001")
    partial = submit(
        book,
        order(symbol, side="sell", quantity="1", reduce_only=True),
        snapshot(symbol, price="110"),
        key="spot-sell-01",
    )
    assert D(partial["cash_after"]) == D("9709.49")
    with book.store.read() as conn:
        row = conn.execute("SELECT * FROM pro_positions WHERE inst_id=?", (symbol,)).fetchone()
        assert D(row["basis"]) == D("300.3")
    submit(
        book,
        order(symbol, side="sell", quantity="3", reduce_only=True),
        snapshot(symbol, price="90"),
        key="spot-sell-02",
    )
    account = book.account("example", {symbol: snapshot(symbol)})
    assert D(account["cash"]) == D("9979.22")
    assert D(account["realized_pnl"]) == D("-20.78")
    assert D(account["fees_paid"]) == D(".78")
    assert D(account["equity"]) == D(10000) + D(account["realized_pnl"])
    assert_ledger(book)


def test_swap_round_trip_net_fee_and_native_journal(book):
    book.set_risk("example", {"fee_bps": "10"}, "test")
    submit(book)
    account = book.account("example", {SYMBOL: snapshot()})
    assert D(account["cash"]) == D("9989.9")
    assert D(account["equity"]) == D("9999.9")
    assert D(account["realized_pnl"]) == D("-.1")
    submit(
        book, order(side="sell", quantity="100", reduce_only=True), snapshot(price="110"), key="swap-close-01"
    )
    account = book.account("example", {SYMBOL: snapshot(price="110")})
    assert D(account["cash"]) == D("10009.79")
    assert D(account["realized_pnl"]) == D("9.79")
    assert D(account["fees_paid"]) == D(".21")
    assert_ledger(book)


def test_fractional_margin_partial_close_has_explicit_balanced_rounding(book):
    submit(book, order(quantity="7", leverage=3), snapshot(price="101"))
    submit(
        book,
        order(side="sell", quantity="1.23", leverage=3, reduce_only=True),
        snapshot(price="92"),
        key="partial-0001",
    )
    submit(
        book,
        order(side="sell", quantity="5.77", leverage=3, reduce_only=True),
        snapshot(price="110"),
        key="partial-0002",
    )
    assert_ledger(book)
    account = book.account("example", {SYMBOL: snapshot(price="110")})
    assert D(account["used_margin"]) == 0
    assert D(account["cash"]) > 0


def test_positive_negative_realized_funding_exactly_once(book):
    submit(book)
    first_quote = snapshot(ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
    first = {
        "inst_id": SYMBOL,
        "ts": 2_000_000,
        "rate": ".01",
        "mark_price": "100",
        "mark_ts": 2_000_000,
        "mark_price_source": "historical_mark_1m_open_approximation",
    }
    assert len(book.settle_funding("example", SYMBOL, [first], {SYMBOL: first_quote})) == 1
    assert book.settle_funding("example", SYMBOL, [first], {SYMBOL: first_quote}) == []
    second_quote = snapshot(ts=3_000_000, funding_time=4_000_000, next_funding_time=5_000_000)
    second = {"ts": 3_000_000, "rate": "-.002", "mark_price": "102", "mark_ts": 3_000_000}
    book.settle_funding("example", SYMBOL, [second], {SYMBOL: second_quote})
    account = book.account("example", {SYMBOL: second_quote})
    assert D(account["funding_paid"]) == D(".796")
    assert D(account["used_margin"]) == D("9.204")
    assert D(account["equity"]) == D("9999.204")
    assert_ledger(book)


def test_funding_sign_for_short_and_conflicting_history_rollback(book):
    submit(book, order(side="sell"))
    quote = snapshot(ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
    event = {"ts": 2_000_000, "rate": ".01", "mark_price": "100"}
    book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})
    assert D(book.account("example", {SYMBOL: quote})["funding_paid"]) == D(-1)
    with pytest.raises(PlatformError, match="rewritten"):
        book.settle_funding("example", SYMBOL, [event | {"rate": ".02"}], {SYMBOL: quote})
    assert_ledger(book)


def test_concurrent_funding_posts_one_economic_event(book):
    submit(book)
    quote = snapshot(ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
    event = {"ts": 2_000_000, "rate": ".01", "mark_price": "100"}
    barrier = Barrier(4)

    def settle():
        barrier.wait()
        return book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: settle(), range(4)))
    assert sum(len(row) for row in rows) == 1
    assert_ledger(book)


def test_expected_settlement_survives_restart_and_late_publication_blocks_fill(book):
    submit(book)
    restarted = SimulationBook(Store(book.store.path))
    quote = snapshot(ts=2_000_001, funding_time=3_000_000, next_funding_time=4_000_000)
    meta = json.loads(restarted.positions("example")[0]["metadata"])
    assert meta["expected_funding_time"] == 2_000_000
    with pytest.raises(PlatformError, match="realized historical rate"):
        restarted.settle_funding("example", SYMBOL, [], {SYMBOL: quote})
    with pytest.raises(PlatformError, match="reconcile"):
        submit(restarted, order(side="sell", reduce_only=True), quote, key="late-close-01")
    event = {"ts": 2_000_000, "rate": ".001", "mark_price": "100"}
    restarted.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})
    assert json.loads(restarted.positions("example")[0]["metadata"])["expected_funding_time"] == 3_000_000
    submit(restarted, order(side="sell", reduce_only=True), quote, key="late-close-01")
    assert_ledger(restarted)


@pytest.mark.parametrize(
    "bad",
    [
        {"ts_offset": 100_000, "rate": ".001", "mark_price": "100"},
        {"ts": 2_000_000, "rate": ".001", "mark_price": "100", "mark_ts": 2_000_001},
        {"ts": 2_000_000, "rate": ".001", "mark_price": "-100"},
        {"ts": 2_000_000, "rate": "NaN", "mark_price": "100"},
        {"ts": 2_000_000, "rate": ".001", "mark_price": "100", "inst_id": "ETH-USDT-SWAP"},
    ],
)
def test_invalid_funding_cannot_mutate_ledger(book, bad):
    bad = dict(bad)
    if "ts_offset" in bad:
        bad["ts"] = now_ms() + bad.pop("ts_offset")
    submit(book)
    before = book.ledger("example")
    with pytest.raises(PlatformError):
        book.settle_funding("example", SYMBOL, [bad], {SYMBOL: snapshot(ts=2_000_000)})
    assert book.ledger("example") == before


def test_limit_and_stop_reservations_release_on_cancel(book):
    quote = snapshot()
    limit = submit(book, order(order_type="limit", limit_price="90"), quote)
    stop = submit(book, order(order_type="stop_market", stop_price="110"), quote, key="command-0002")
    account = book.account("example", {SYMBOL: quote})
    assert D(account["reserved_cash"]) == 20
    assert D(account["available_cash"]) == 9980
    assert not account["positions"]
    book.cancel(limit["id"], "test")
    book.cancel(stop["id"], "test")
    assert D(book.account("example", {SYMBOL: quote})["reserved_cash"]) == 0
    assert_ledger(book)


def test_pending_limit_fill_reprices_with_tick_and_obeys_limit(book):
    pending = submit(book, order(order_type="limit", limit_price="90"))
    with pytest.raises(PlatformError, match="limit"):
        submit(
            book,
            order(order_type="limit", limit_price="90"),
            snapshot(price="95"),
            pending_id=pending["id"],
            actor="pending-order",
        )
    fill = submit(
        book,
        order(order_type="limit", limit_price="90"),
        snapshot(price="89"),
        pending_id=pending["id"],
        actor="pending-order",
    )
    assert fill["id"] == pending["id"]
    assert fill["status"] == "filled"
    assert D(fill["price"]) == 89
    assert D(book.account("example", {SYMBOL: snapshot(price="89")})["reserved_cash"]) == 0
    assert_ledger(book)


def test_tier_boundary_is_explicit_and_unknown_coverage_rejected():
    quote = snapshot()
    assert tier_for(quote["instrument"], D(1000), quote["margin_tiers"])["mmr"] == "0.004"
    assert tier_for(quote["instrument"], D("1000.01"), quote["margin_tiers"])["mmr"] == "0.005"
    with pytest.raises(ValueError, match="coverage"):
        tier_for(quote["instrument"], D(100001), quote["margin_tiers"])


def test_halt_blocks_new_risk_but_allows_reduction(book):
    submit(book)
    book.halt("example", True, "Risk review", "test")
    with pytest.raises(PlatformError, match="halted"):
        submit(book, key="blocked-0001")
    submit(book, order(side="sell", reduce_only=True), key="allowed-exit1")
    assert book.risk("example")["halted"]
    assert not book.positions("example")
    assert_ledger(book)


def test_concurrent_idempotency_single_fill_and_conflict(book):
    barrier = Barrier(6)

    def fill():
        barrier.wait()
        return submit(book)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: fill(), range(6)))
    assert len({result["id"] for result in results}) == 1
    assert len(book.orders("example")) == 1
    assert D(book.positions("example")[0]["quantity"]) == 100
    with pytest.raises(PlatformError, match="different order"):
        submit(book, order(quantity="101"))
    assert_ledger(book)


def deploy(book, identifier="strategy-test", status="running"):
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_deployments VALUES(?,?,?,?,?,?,?,?,?)",
            (identifier, "example", SYMBOL, dumps({}), status, None, None, now_ms(), now_ms()),
        )
    return identifier


def test_stopped_strategy_cannot_fill_and_active_cursor_commits_atomically(book):
    identifier = deploy(book)
    result = submit(book, key=f"strategy:{identifier}:123", actor=f"strategy:{identifier}")
    assert result["status"] == "filled"
    with book.store.read() as conn:
        assert (
            conn.execute("SELECT last_bar FROM pro_deployments WHERE id=?", (identifier,)).fetchone()[0]
            == 123
        )
    with book.store.write() as conn:
        conn.execute("UPDATE pro_deployments SET status='stopped' WHERE id=?", (identifier,))
    before = book.ledger("example")
    with pytest.raises(PlatformError, match="no longer owns"):
        submit(book, key=f"strategy:{identifier}:124", actor=f"strategy:{identifier}")
    assert book.ledger("example") == before


def test_audit_failure_rolls_back_fill_ledger_and_strategy_cursor(book, monkeypatch):
    identifier = deploy(book)
    before = book.ledger("example")

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(book.store, "audit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        submit(book, key=f"strategy:{identifier}:123", actor=f"strategy:{identifier}")
    assert book.ledger("example") == before
    assert not book.positions("example")
    with book.store.read() as conn:
        assert (
            conn.execute("SELECT last_bar FROM pro_deployments WHERE id=?", (identifier,)).fetchone()[0]
            is None
        )
        assert conn.execute("SELECT COUNT(*) FROM pro_orders").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change,code",
    [
        ({"mark_ts_offset": -20000}, "stale_mark"),
        ({"mark": None}, "missing_mark"),
        ({"mark": "0"}, "invalid_market"),
        ({"mark_ts_offset": 20000}, "stale_mark"),
        ({"ts_offset": -20000}, "stale_market"),
    ],
)
def test_stale_missing_zero_and_future_independent_marks_rejected(book, change, code):
    quote = snapshot(source="okx", ts=now_ms(), mark_ts=now_ms(), funding_time=now_ms() + 10000)
    change = dict(change)
    for field in ("ts", "mark_ts"):
        if f"{field}_offset" in change:
            change[field] = now_ms() + change.pop(f"{field}_offset")
    quote.update(change)
    with pytest.raises(PlatformError) as failure:
        submit(book, order(source="okx"), quote)
    assert failure.value.code == code
    assert not book.positions("okx")


def test_other_source_mark_cannot_value_held_position(book):
    submit(book)
    account = book.account("example", {SYMBOL: snapshot(source="okx", ts=now_ms())})
    assert account["valuation_status"] == "unavailable"
    assert account["equity"] is None


def test_missing_or_unknown_tiers_return_unavailable_and_block_new_risk(book):
    submit(book)
    bad_quote = snapshot(margin_tiers=[])
    assert book.account("example", {SYMBOL: bad_quote})["valuation_status"] == "unavailable"
    with pytest.raises(PlatformError, match="fresh"):
        submit(book, quote=bad_quote, key="bad-tiers-001")


def test_gap_deficit_preserves_free_cash_records_liability_and_halts(book):
    submit(book, order(quantity="1000", leverage=10))
    account = book.account("example", {SYMBOL: snapshot()})
    free_cash = D(account["cash"])
    result = submit(
        book,
        order(side="sell", quantity="1000", leverage=10, reduce_only=True),
        snapshot(price="1"),
        key="liquidation-01",
        actor="risk-engine",
        liquidation=True,
    )
    account = book.account("example", {SYMBOL: snapshot(price="1")})
    assert D(account["cash"]) == free_cash == 9900
    assert D(account["insurance_debt"]) == 890
    assert D(account["equity"]) == 9010
    assert D(account["realized_pnl"]) == -990
    assert result["liquidation"]
    assert book.risk("example")["halted"]
    assert_ledger(book)


def test_buy_ask_sell_bid_adverse_slippage_and_tick(book):
    symbol = "BTC-USDT"
    book.set_risk("example", {"fee_bps": "10", "slippage_bps": "10"}, "test")
    quote = snapshot(symbol, bid="99.97", ask="100.03")
    quote["instrument"]["tick_size"] = ".1"
    bought = submit(book, order(symbol, quantity="1"), quote)
    sold = submit(
        book, order(symbol, side="sell", quantity="1", reduce_only=True), quote, key="ask-bid-sell1"
    )
    assert D(bought["price"]) == D("100.2")
    assert D(sold["price"]) == D("99.8")
    assert D(book.account("example", {symbol: quote})["cash"]) == D("9999.4")
    assert_ledger(book)


def test_simulation_math_independent_of_caller_context(book):
    with localcontext() as context:
        context.prec, context.rounding, context.Emin, context.Emax = 6, ROUND_DOWN, -5, 5
        context.traps[Inexact] = True
        before = context.copy()
        submit(book, order(quantity="7", leverage=3), snapshot(price="101"))
        submit(
            book,
            order(side="sell", quantity="1.23", leverage=3, reduce_only=True),
            snapshot(price="92"),
            key="context-exit1",
        )
        account = book.account("example", {SYMBOL: snapshot(price="92")})
        assert account["equity"] is not None
        assert getcontext().prec == before.prec
        assert getcontext().rounding == before.rounding
        assert getcontext().flags == before.flags
        assert getcontext().traps == before.traps
        assert getcontext().Emax == before.Emax
    assert_ledger(book)


@pytest.mark.parametrize("bad", ["1e30", "NaN", "Infinity", "1e-99999", "1." + "1" * 51])
def test_numbers_fail_closed_without_huge_serialization(bad):
    with pytest.raises(PlatformError, match="finite supported"):
        number(bad)


def test_contract_units_cannot_change_under_an_open_position(book):
    submit(book)
    quote = snapshot()
    quote["instrument"]["ct_val"] = ".1"
    with pytest.raises(PlatformError, match="units changed"):
        submit(book, quote=quote, key="bad-contract1")
    assert_ledger(book)


def test_new_position_already_below_mark_maintenance_is_rejected(book):
    book.set_risk("example", {"max_leverage": 50}, "test")
    with pytest.raises(PlatformError, match="already violate"):
        submit(book, order(leverage=50), snapshot(mark="95"))
    assert not book.positions("example")


def test_material_ledger_imbalance_rolls_back(book):
    before = book.ledger("example")
    with pytest.raises(RuntimeError, match="unbalanced"), book.store.write() as conn:
        book.post(
            conn,
            "example",
            "bad-ledger",
            "injected",
            "test",
            [("USDT", "cash", D(100)), ("USDT", "pnl", D(-99))],
        )
    assert book.ledger("example") == before


def test_concurrent_limit_update_cannot_unhalt_risk(book):
    barrier = Barrier(2)

    def halt():
        barrier.wait()
        book.halt("example", True, "Concurrent risk stop", "test")

    def limits():
        barrier.wait()
        book.set_risk("example", {"max_order_notional": "2000"}, "test")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(halt), pool.submit(limits)]
        for future in futures:
            future.result()
    assert book.risk("example")["halted"] is True
    assert book.risk("example")["max_order_notional"] == "2000"


def test_short_realized_pnl_is_signed_correctly(book):
    submit(book, order(side="sell"))
    submit(book, order(side="buy", reduce_only=True), snapshot(price="90"), key="short-close1")
    account = book.account("example", {SYMBOL: snapshot(price="90")})
    assert D(account["realized_pnl"]) == 10
    assert D(account["equity"]) == 10010
    assert_ledger(book)


def test_overdraw_oversell_and_cross_margin_fail_closed(book):
    for command in [order(quantity="100000", leverage=1), order(margin_mode="cross"), order(leverage=0)]:
        with pytest.raises(PlatformError):
            submit(book, command)
    with pytest.raises(PlatformError, match="existing inventory"):
        submit(book, order("BTC-USDT", side="sell", quantity="1"))
    assert not book.orders("example")
    assert_ledger(book)


def test_pending_admission_cap_and_cancelled_order_cannot_fill(book):
    for index in range(100):
        pending = submit(book, order(order_type="limit", limit_price="90"), key=f"pending-{index:04}")
    with pytest.raises(PlatformError, match="100 pending"):
        submit(book, order(order_type="limit", limit_price="90"), key="pending-overflow")
    book.cancel(pending["id"], "test")
    with pytest.raises(PlatformError, match="no longer pending"):
        submit(
            book,
            order(order_type="limit", limit_price="90"),
            snapshot(price="89"),
            key="pending-0099",
            pending_id=pending["id"],
            actor="pending-order",
        )
    assert not book.positions("example")
    assert_ledger(book)
