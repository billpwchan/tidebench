import json
from decimal import Decimal as D

import pytest
from test_pro_execution import SYMBOL, assert_ledger, order, snapshot
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store


@pytest.fixture
def pending_book(tmp_path):
    clock = [1_000_000]
    book = SimulationBook(Store(tmp_path / "deferred.sqlite3"), clock=lambda: clock[0])
    book.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test")
    book.submit(order(), "initial-long", {SYMBOL: snapshot()})
    clock[0] = 2_000_001
    return book, clock


@pytest.mark.parametrize("liquidation", [False, True])
def test_due_unpublished_funding_allows_protection_without_erasing_liability(pending_book, liquidation):
    book, clock = pending_book
    quote = snapshot(price="80", ts=clock[0], funding_time=3_000_000, next_funding_time=4_000_000)
    book.submit(
        order(side="sell", reduce_only=True), "protective-exit", {SYMBOL: quote}, liquidation=liquidation
    )
    assert book.positions("example") == []
    account = book.account("example", {SYMBOL: quote})
    assert account["equity"] is None
    assert D(account["equity_before_pending_funding"]) == D("9980")
    assert account["economic_status"] == "funding_pending"
    assert account["pending_funding"][0]["quantity"] == "100"
    with pytest.raises(PlatformError) as exc:
        book.submit(order(), "premature-reentry", {SYMBOL: quote})
    assert exc.value.code in {"funding_pending", "execution_halted"}
    restarted = SimulationBook(Store(book.store.path), clock=lambda: clock[0])
    event = {"ts": 2_000_000, "rate": ".01", "mark_price": "100"}
    output = restarted.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})
    assert D(output[0]["quantity"]) == 100 and D(output[0]["payment"]) == 1
    assert restarted.settle_funding("example", SYMBOL, [event], {SYMBOL: quote}) == []
    account = restarted.account("example", {SYMBOL: quote})
    assert D(account["equity"]) == D("9979")
    assert account["pending_funding"] == []
    assert D(account["funding_paid"]) == 1
    report = restarted.contribution_report("example", {SYMBOL: quote})
    assert report["reconciled"] is True
    assert_ledger(restarted)


def test_partial_protection_funding_keeps_original_quantity_and_owner(pending_book):
    book, clock = pending_book
    quote = snapshot(ts=clock[0], funding_time=3_000_000, next_funding_time=4_000_000)
    book.submit(order(side="sell", quantity="50", reduce_only=True), "half-exit", {SYMBOL: quote})
    output = book.settle_funding(
        "example", SYMBOL, [{"ts": 2_000_000, "rate": ".01", "mark_price": "100"}], {SYMBOL: quote}
    )
    assert output[0]["quantity"] == "100"
    assert D(output[0]["margin_payment"]) == D(".5")
    assert D(output[0]["cash_payment"]) == D(".5")
    assert D(book.positions("example")[0]["margin"]) == D("4.5")
    assert D(book.account("example", {SYMBOL: quote})["equity"]) == 9999
    assert book.contribution_report("example", {SYMBOL: quote})["reconciled"] is True
    assert_ledger(book)


def test_late_event_uses_inventory_interval_after_flat_and_reopen(pending_book):
    book, clock = pending_book
    # Close before the initially observed settlement. An earlier realized event
    # published later must still find historical ownership, not the reopened lot.
    clock[0] = 1_800_000
    quote = snapshot(ts=clock[0])
    book.submit(order(side="sell", reduce_only=True), "close-before-due", {SYMBOL: quote})
    clock[0] = 1_900_000
    quote = snapshot(ts=clock[0])
    book.submit(order(quantity="20"), "new-generation", {SYMBOL: quote})
    event = {"ts": 1_500_000, "rate": ".01", "mark_price": "100"}
    output = book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})
    assert output[0]["quantity"] == "100"
    assert D(output[0]["margin_payment"]) == 0
    assert D(output[0]["cash_payment"]) == 1
    assert D(book.positions("example")[0]["margin"]) == 2
    assert D(book.account("example", {SYMBOL: quote})["equity"]) == 9999
    assert book.contribution_report("example", {SYMBOL: quote})["reconciled"] is True
    assert_ledger(book)


def test_each_known_due_time_retains_its_actual_pre_reduction_quantity(pending_book):
    book, clock = pending_book
    quote = snapshot(ts=clock[0], funding_time=3_000_000, next_funding_time=4_000_000)
    book.submit(order(side="sell", quantity="50", reduce_only=True), "first-half", {SYMBOL: quote})
    clock[0] = 3_000_001
    quote = snapshot(ts=clock[0], funding_time=4_000_000, next_funding_time=5_000_000)
    book.submit(order(side="sell", quantity="50", reduce_only=True), "second-half", {SYMBOL: quote})
    output = book.settle_funding(
        "example",
        SYMBOL,
        [
            {"ts": 2_000_000, "rate": ".01", "mark_price": "100"},
            {"ts": 3_000_000, "rate": ".01", "mark_price": "100"},
        ],
        {SYMBOL: quote},
    )
    assert [D(e["quantity"]) for e in output] == [100, 50]
    assert D(book.account("example", {SYMBOL: quote})["funding_paid"]) == D("1.5")
    assert book.contribution_report("example", {SYMBOL: quote})["reconciled"] is True
    assert_ledger(book)


def test_tampered_pending_history_blocks_new_risk_but_not_economic_reduction(pending_book):
    book, clock = pending_book
    quote = snapshot(ts=clock[0], funding_time=3_000_000, next_funding_time=4_000_000)
    book.observe("example", {SYMBOL: quote})
    with book.store.write() as conn:
        row = conn.execute("SELECT body FROM pro_funding_obligations").fetchone()
        body = json.loads(row["body"])
        body["quantity"] = "200"
        conn.execute("UPDATE pro_funding_obligations SET body=?", (json.dumps(body),))
    assert book.account("example", {SYMBOL: quote})["pending_funding"][0]["integrity"] == "failed"
    book.submit(order(side="sell", reduce_only=True), "protect-against-damage", {SYMBOL: quote})
    assert book.positions("example") == []
    with pytest.raises(PlatformError) as exc:
        book.settle_funding(
            "example", SYMBOL, [{"ts": 2_000_000, "rate": ".01", "mark_price": "100"}], {SYMBOL: quote}
        )
    assert exc.value.code == "funding_inventory_integrity"
    assert_ledger(book)


def test_fresh_okx_cache_timestamp_regression_cannot_date_a_reduction_before_its_inventory(tmp_path):
    clock = [2_000_000]
    book = SimulationBook(Store(tmp_path / "quote-clock.sqlite3"), clock=lambda: clock[0])
    book.set_risk("okx", {"fee_bps": "0", "slippage_bps": "0"}, "test")
    first = snapshot(
        source="okx", ts=1_999_900, mark_ts=1_999_900, funding_time=2_050_300, next_funding_time=3_000_000
    )
    book.submit(order(source="okx"), "clock-initial-entry", {SYMBOL: first})
    clock[0] = 2_050_000
    second = snapshot(
        source="okx", ts=2_049_990, mark_ts=2_049_990, funding_time=2_050_300, next_funding_time=3_000_000
    )
    book.submit(order(source="okx"), "clock-second-entry", {SYMBOL: second})
    clock[0] = 2_050_500
    # A different REST cache can deliver an older but still fresh price snapshot.
    exit_quote = snapshot(
        source="okx", ts=2_049_980, mark_ts=2_049_980, funding_time=2_050_300, next_funding_time=3_000_000
    )
    closed = book.submit(
        order(source="okx", side="sell", quantity="200", reduce_only=True),
        "clock-protective-exit",
        {SYMBOL: exit_quote},
    )
    assert closed["status"] == "filled" and not book.positions("okx")
    assert closed["quote_ts"] == 2_049_980
    assert book.deferred_funding.pending("okx")[0]["quantity"] == "200"
    published = snapshot(
        source="okx", ts=2_050_500, mark_ts=2_050_500, funding_time=3_000_000, next_funding_time=4_000_000
    )
    settled = book.settle_funding(
        "okx", SYMBOL, [{"ts": 2_050_300, "rate": ".01", "mark_price": "100"}], {SYMBOL: published}
    )
    assert D(settled[0]["quantity"]) == 200 and D(settled[0]["payment"]) == 2
    assert D(book.account("okx", {SYMBOL: published})["equity"]) == 9998
    assert book.contribution_report("okx", {SYMBOL: published})["reconciled"]
    assert_ledger(book)


@pytest.mark.parametrize("side,expected", [("buy", "49"), ("sell", "51")])
def test_contract_unit_conversion_keeps_all_original_funding_in_isolated_margin(tmp_path, side, expected):
    from test_historical_lifecycle import HOUR, START, fact, populated_book
    from tidebench.historical_lifecycle import apply_inventory_event

    book, clock, quote = populated_book(tmp_path, side=side)
    event = fact("BTC-USDT-SWAP", "unit_conversion", 5, quantity_ratio=".5")
    event["instrument"]["ct_val"] = ".02"
    apply_inventory_event(book, "example", event)
    ts = START + 5 * HOUR
    current = dict(quote, instrument=event["instrument"], ts=ts, mark_ts=ts)
    settled = book.settle_funding(
        "example",
        "BTC-USDT-SWAP",
        [{"ts": ts, "rate": ".01", "mark_price": "100", "mark_ts": ts}],
        {"BTC-USDT-SWAP": current},
    )
    account = book.account("example", {"BTC-USDT-SWAP": current})
    assert D(account["positions"][0]["quantity"]) == (D(50) if side == "buy" else D(-50))
    assert D(account["positions"][0]["margin"]) == D(expected)
    assert D(settled[0]["margin_payment"]) == (D(1) if side == "buy" else D(-1))
    assert D(settled[0]["cash_payment"]) == 0
    assert D(account["cash"]) == D("9950")
    assert (
        book.settle_funding(
            "example",
            "BTC-USDT-SWAP",
            [{"ts": ts, "rate": ".01", "mark_price": "100", "mark_ts": ts}],
            {"BTC-USDT-SWAP": current},
        )
        == []
    )
