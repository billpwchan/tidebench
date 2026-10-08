"""Economic contribution must reconcile mixed entries, costs, funding and exits."""

import json
from decimal import Decimal as D

import pytest
from test_pro_execution import SYMBOL, order, snapshot, submit
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, dumps


@pytest.fixture
def book(tmp_path):
    b = SimulationBook(Store(tmp_path / "book.sqlite3"))
    b.set_risk("example", {"fee_bps": "10", "slippage_bps": "0", "liquidation_fee_bps": "50"}, "trader")
    return b


def report(book, symbol=SYMBOL, price="110"):
    result = book.contribution_report("example", {symbol: snapshot(symbol, price=price)})
    assert result["reconciled"], result
    assert abs(D(result["reconciliation_delta"])) <= D("1e-40")
    return result, {r["owner"]: r for r in result["owners"]}


@pytest.mark.parametrize("symbol,leverage", [("BTC-USDT", 1), (SYMBOL, 10)])
def test_mixed_entry_costs_and_proportional_partial_reduction(book, symbol, leverage):
    qty1, qty2 = ("1", "2") if leverage == 1 else ("100", "200")
    submit(
        book,
        order(symbol, quantity=qty1, leverage=leverage),
        snapshot(symbol, price="100"),
        key="alice-entry-001",
        actor="alice",
    )
    submit(
        book,
        order(symbol, quantity=qty2, leverage=leverage),
        snapshot(symbol, price="120"),
        key="bob-entry-0001",
        actor="bob",
    )
    submit(
        book,
        order(
            symbol,
            side="sell",
            quantity="1.5" if leverage == 1 else "150",
            leverage=leverage,
            reduce_only=True,
        ),
        snapshot(symbol, price="110"),
        key="operator-exit-01",
        actor="risk-operator",
    )
    result, owners = report(book, symbol)
    assert set(owners) == {"manual:alice", "manual:bob"}
    assert D(owners["manual:alice"]["markets"][0]["quantity"]) == (D(".5") if leverage == 1 else D(50))
    assert D(owners["manual:bob"]["markets"][0]["quantity"]) == (D(1) if leverage == 1 else D(100))
    assert D(owners["manual:alice"]["net_pnl"]) > 0 > D(owners["manual:bob"]["net_pnl"])
    assert D(result["totals"]["fees_paid"]) == D(".505")
    submit(
        book,
        order(
            symbol,
            side="sell",
            quantity="1.5" if leverage == 1 else "150",
            leverage=leverage,
            reduce_only=True,
        ),
        snapshot(symbol, price="110"),
        key="operator-exit-02",
        actor="risk-operator",
    )
    result, owners = report(book, symbol)
    assert all(D(r["unrealized_pnl"]) == 0 for r in owners.values())
    assert abs(D(result["totals"]["net_pnl"]) - D("-10.67")) < D("1e-40")


def test_short_funding_liquidation_and_debt_are_not_counted_twice(book):
    submit(
        book, order(side="sell", quantity="100"), snapshot(price="100"), key="alice-short-01", actor="alice"
    )
    submit(book, order(side="sell", quantity="200"), snapshot(price="120"), key="bob-short-0001", actor="bob")
    quote = snapshot(price="110", ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
    events = [{"ts": 2_000_000, "rate": ".01", "mark_price": "110", "mark_ts": 2_000_000}]
    book.settle_funding("example", SYMBOL, events, {SYMBOL: quote})
    _, owners = report(book)
    assert D(owners["manual:alice"]["funding_paid"]) == D("-1.1")
    assert D(owners["manual:bob"]["funding_paid"]) == D("-2.2")
    quote = snapshot(price="500", ts=2_000_001, funding_time=3_000_000, next_funding_time=4_000_000)
    result = submit(
        book,
        order(side="buy", quantity="300", reduce_only=True),
        quote,
        key="liquidation-001",
        actor="risk-engine",
        liquidation=True,
    )
    assert D(result["insurance_debt"]) > 0
    report_result, owners = report(book, price="500")
    account = book.account("example", {})
    assert D(report_result["totals"]["net_pnl"]) == D(account["equity"]) - D(account["initial_cash"])
    assert set(owners) == {"manual:alice", "manual:bob"}


def test_pending_fill_preserves_original_owner_and_idempotent_events(book):
    command = order(quantity="100", order_type="limit", limit_price="100")
    pending = submit(book, command, snapshot(price="101"), key="alice-pending-01", actor="alice")
    assert pending["status"] == "pending"
    filled = book.submit(
        command,
        "alice-pending-01",
        {SYMBOL: snapshot(price="100")},
        "pending-order",
        pending_id=pending["id"],
    )
    assert filled["submitted_by"] == "alice"
    book.submit(command, "alice-pending-01", {SYMBOL: snapshot(price="100")}, "alice")
    _, owners = report(book)
    assert set(owners) == {"manual:alice"}
    with book.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contribution_events").fetchone()[0] == 1


def test_pre_upgrade_balances_are_legacy_and_future_entries_are_distinct(tmp_path):
    store = Store(tmp_path / "book.sqlite3")
    old = SimulationBook(store, capture_contributions=False)
    old.set_risk("example", {"slippage_bps": "0"}, "trader")
    submit(old, order(quantity="100"), snapshot(), key="prior-entry-0001", actor="old-strategy")
    current = SimulationBook(store)
    result, owners = report(current)
    assert set(owners) == {"legacy"}
    submit(current, order(quantity="100"), snapshot(price="110"), key="future-entry-01", actor="alice")
    result, owners = report(current)
    assert set(owners) == {"legacy", "manual:alice"}
    restarted = SimulationBook(Store(store.path))
    assert report(restarted)[0]["owners"] == result["owners"]


def test_tampered_sleeve_blocks_economic_mutation_and_rolls_back(book):
    submit(book, key="alice-entry-001", actor="alice")
    orders = book.orders("example")
    with book.store.write() as conn:
        row = conn.execute("SELECT body FROM contribution_sleeves").fetchone()
        body = json.loads(row[0]) | {"quantity": "0"}
        conn.execute("UPDATE contribution_sleeves SET body=?", (dumps(body),))
    with pytest.raises(PlatformError, match="integrity"):
        submit(book, key="alice-entry-002", actor="alice")
    assert book.orders("example") == orders


@pytest.mark.parametrize(
    "symbol,side,leverage", [("BTC-USDT", "buy", 1), (SYMBOL, "buy", 3), (SYMBOL, "sell", 3)]
)
def test_many_interleaved_owner_entries_and_exits_reconcile_finite_decimals(book, symbol, side, leverage):
    from random import Random

    rng = Random(17)
    lot = D(".01")
    for i in range(35):
        price = str(D(100) + D(i) / 10)
        held = book.positions("example")
        reducing = bool(held) and i % 3 == 2
        if reducing:
            qty = (abs(D(held[0]["quantity"])) * D(rng.randint(1, 9)) / 10 / lot).to_integral_value(
                rounding="ROUND_FLOOR"
            ) * lot
        else:
            qty = D(rng.randint(3, 19)) / 10 if leverage == 1 else D(rng.randint(30, 190))
        if not qty:
            continue
        action = ("sell" if side == "buy" else "buy") if reducing else side
        submit(
            book,
            order(symbol, side=action, quantity=str(qty), leverage=leverage, reduce_only=reducing),
            snapshot(symbol, price=price),
            key=f"interleaved-owner-{i:03}",
            actor=f"owner-{i % 3}",
        )
        report(book, symbol, price)
    held = book.positions("example")
    if held:
        submit(
            book,
            order(
                symbol,
                side="sell" if side == "buy" else "buy",
                quantity=str(abs(D(held[0]["quantity"]))),
                leverage=leverage,
                reduce_only=True,
            ),
            snapshot(symbol, price="104"),
            key="interleaved-final-close",
            actor="operator",
        )
    result, owners = report(book, symbol, "104")
    assert all(
        D(m["quantity"]) == 0 and D(m["entry_notional"]) == 0 for o in owners.values() for m in o["markets"]
    )
    assert D(result["totals"]["unrealized_pnl"]) == 0
    events = book.contributions.events("example", owner="manual:owner-0", limit=1)
    assert len(events) == 1 and events[0]["order"]
    older = book.contributions.events("example", owner="manual:owner-0", before=events[0]["id"], limit=1)
    assert len(older) == 1 and older[0]["id"] < events[0]["id"]


def test_corrupt_attribution_never_blocks_protective_reduction_and_is_not_silently_repaired(book):
    submit(book, key="before-corruption-001", actor="alice")
    with book.store.write() as conn:
        row = conn.execute("SELECT body FROM contribution_sleeves").fetchone()
        corrupt = json.loads(row[0]) | {"fees": "999"}
        conn.execute("UPDATE contribution_sleeves SET body=?", (dumps(corrupt),))
    closed = submit(
        book,
        order(side="sell", quantity="100", reduce_only=True),
        snapshot(price="110"),
        key="protective-after-corruption",
        actor="risk-operator",
    )
    assert closed["status"] == "filled" and not book.positions("example")
    assert book.risk("example")["halted"]
    with book.store.read() as conn:
        assert json.loads(conn.execute("SELECT body FROM contribution_sleeves").fetchone()[0]) == corrupt
    assert book.contributions.statuses()[0]["source"] == "example"
    events = book.contributions.events("example")
    assert (
        events[0]["kind"] == "quarantined_fill"
        and events[0]["body"]["economic_evidence"]["order"]["id"] == closed["id"]
    )
    with pytest.raises(PlatformError, match="quarantined"):
        book.contribution_report("example", {})


def test_corrupt_attribution_preserves_funding_and_liquidation_economic_evidence(book):
    submit(book, key="funding-before-corruption", actor="alice")
    with book.store.write() as conn:
        conn.execute("UPDATE contribution_sleeves SET content_hash='corrupt'")
    quote = snapshot(ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
    event = {"ts": 2_000_000, "rate": ".01", "mark_price": "100", "mark_ts": 2_000_000}
    assert book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})
    assert D(book.account("example", {SYMBOL: quote})["funding_paid"]) == 1
    liquidation = submit(
        book,
        order(side="sell", quantity="100", reduce_only=True),
        snapshot(price="1", ts=2_000_001, funding_time=3_000_000, next_funding_time=4_000_000),
        key="corruption-safe-liquidation",
        actor="risk-engine",
        liquidation=True,
    )
    assert liquidation["status"] == "filled" and D(liquidation["insurance_debt"]) > 0
    assert not book.positions("example")
    assert {e["kind"] for e in book.contributions.events("example")} >= {
        "quarantined_funding",
        "quarantined_fill",
    }


@pytest.mark.parametrize("action", ["reduce", "funding", "liquidate"])
@pytest.mark.parametrize(
    "record,corruption",
    [
        ("sleeve", "malformed_json"),
        ("baseline", "malformed_json"),
        ("sleeve", "not_object"),
        ("baseline", "not_object"),
        ("sleeve", "missing_decimal"),
        ("baseline", "missing_decimal"),
        ("sleeve", "wrong_identity"),
        ("baseline", "wrong_identity"),
        ("baseline", "wrong_positions"),
        ("baseline", "position_metadata_json"),
        ("baseline", "position_metadata_type"),
        ("baseline", "position_decimal"),
        ("sleeve", "NaN"),
        ("sleeve", "Infinity"),
        ("sleeve", "-Infinity"),
        ("sleeve", "sNaN"),
        ("sleeve", "not-a-decimal"),
        ("sleeve", "1e99999999"),
        ("baseline", "NaN"),
        ("baseline", "not-a-decimal"),
    ],
)
def test_invalid_attribution_records_preserve_economic_protection(book, action, record, corruption):
    """Parser failures stay in the attribution boundary and preserve economic commands."""
    from tidebench.strategy_registry import digest

    submit(book, key="before-invalid-attribution", actor="alice")
    table, column, hash_column = (
        ("contribution_sleeves", "body", "content_hash")
        if record == "sleeve"
        else ("contribution_accounts", "baseline", "baseline_hash")
    )
    with book.store.write() as conn:
        raw = conn.execute(f"SELECT {column} FROM {table} WHERE source='example'").fetchone()[0]
        body = json.loads(raw)
        fields = body if record == "sleeve" else body["account"]
        if corruption == "malformed_json":
            damaged, content_hash = "{", "damaged-json"
        elif corruption == "not_object":
            damaged, content_hash = dumps([]), digest([])
        else:
            if corruption == "missing_decimal":
                fields.pop("fees")
            elif corruption == "wrong_identity":
                fields["source"] = "okx"
            elif corruption == "wrong_positions":
                body["positions"] = {}
            elif corruption.startswith("position_"):
                position = dict(conn.execute("SELECT * FROM pro_positions WHERE source='example'").fetchone())
                if corruption == "position_metadata_json":
                    position["metadata"] = "{"
                elif corruption == "position_metadata_type":
                    position["metadata"] = dumps({"inst_type": []})
                else:
                    position["quantity"] = "NaN"
                body["positions"] = [position]
            else:
                fields["fees"] = corruption
            damaged, content_hash = dumps(body), digest(body)
        conn.execute(
            f"UPDATE {table} SET {column}=?,{hash_column}=? WHERE source='example'",
            (damaged, content_hash),
        )
    with pytest.raises(PlatformError) as error:
        submit(book, key="new-risk-invalid-attribution", actor="alice")
    assert error.value.code == "contribution_integrity"
    assert len(book.orders("example")) == 1

    if action == "funding":
        quote = snapshot(ts=2_000_000, funding_time=3_000_000, next_funding_time=4_000_000)
        event = {"ts": 2_000_000, "rate": ".01", "mark_price": "100", "mark_ts": 2_000_000}
        assert len(book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote})) == 1
        assert book.settle_funding("example", SYMBOL, [event], {SYMBOL: quote}) == []
        assert D(book.account("example", {SYMBOL: quote})["funding_paid"]) == 1
        assert len(book.positions("example")) == 1
    else:
        filled = submit(
            book,
            order(side="sell", reduce_only=True),
            snapshot(price="1" if action == "liquidate" else "110"),
            key="protective-invalid-attribution",
            actor="risk-operator",
            liquidation=action == "liquidate",
        )
        assert filled["status"] == "filled" and not book.positions("example")
        if action == "liquidate":
            assert D(filled["insurance_debt"]) > 0
    assert book.risk("example")["halted"]
    assert book.contributions.statuses()[0]["source"] == "example"
    with book.store.read() as conn:
        assert conn.execute(f"SELECT {column} FROM {table} WHERE source='example'").fetchone()[0] == damaged
    with pytest.raises(PlatformError) as error:
        book.contribution_report("example", {})
    assert error.value.code == "contribution_quarantined"


def test_economic_book_failure_is_not_hidden_by_attribution_quarantine(book):
    submit(book, key="before-economic-corruption", actor="alice")
    with book.store.write() as conn:
        conn.execute("UPDATE pro_accounts SET cash='not-a-decimal' WHERE source='example'")
    with pytest.raises(PlatformError) as error:
        submit(
            book,
            order(side="sell", reduce_only=True),
            snapshot(price="110"),
            key="protective-economic-corruption",
            actor="risk-operator",
        )
    assert error.value.code == "invalid_number"
    assert len(book.positions("example")) == 1
    assert len(book.orders("example")) == 1
    assert not book.contributions.statuses()
