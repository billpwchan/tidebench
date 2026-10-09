"""Independent funding clocks, durable protective reductions, and financial previews."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
from test_catalog import raw_instrument
from test_pro_execution import SYMBOL, assert_ledger, order, snapshot
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_execution import FUNDING_SCHEDULE_POLICY, PENDING_REDUCTION_POLICY, SimulationBook
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, dumps, now_ms

D = Decimal
NOW = 1_791_547_000_000


@pytest.fixture
def book(tmp_path):
    book = SimulationBook(Store(tmp_path / "book.sqlite"), clock=lambda: NOW)
    for source in ("example", "okx"):
        book.set_risk(source, {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test")
    return book


def real_quote(**changes):
    return snapshot(source="okx", ts=NOW, **changes)


def protective(book, side="buy", quantity="100", **changes):
    q = snapshot()
    book.submit(order(side=side, quantity=quantity), "protect-entry", {SYMBOL: q})
    command = order(
        side="sell" if side == "buy" else "buy",
        quantity=quantity,
        reduce_only=True,
        order_type="stop_market",
        stop_price="95" if side == "buy" else "105",
        **changes,
    )
    pending = book.submit(command, "protect-stop", {SYMBOL: q})
    return command, pending, q


@pytest.mark.parametrize(
    "change",
    [
        {"funding_ts": NOW - 180_001},
        {"funding_ts": NOW + 5_001},
        {"funding_ts": None},
        {"funding_ts": True},
        {"funding_inst_id": "ETH-USDT-SWAP"},
        {"funding_source": "example"},
        {"funding_time": NOW - 10_000, "next_funding_time": NOW - 1},
        {"funding_time": NOW + 20_000, "next_funding_time": NOW + 10_000},
        {"next_funding_time": None},
    ],
)
def test_new_swap_risk_requires_usable_independent_funding_schedule(book, change):
    with pytest.raises(PlatformError) as error:
        book.submit(order(source="okx"), "funding-reject", {SYMBOL: real_quote(**change)})
    assert error.value.code == "funding_schedule_unavailable"
    assert not book.orders("okx") and not book.positions("okx")


@pytest.mark.parametrize("age", [180_000, -5_000])
def test_funding_local_model_clock_boundary_and_stale_index_are_accepted(book, age):
    q = real_quote(funding_ts=NOW - age, index_ts=NOW - 2 * 86_400_000)
    fill = book.submit(order(source="okx"), "funding-border", {SYMBOL: q})
    assert fill["status"] == "filled"
    assert book.account("okx", {SYMBOL: q})["economic_status"] == "complete"
    assert FUNDING_SCHEDULE_POLICY["scope"] == "local_model_admission_not_venue_acceptance"


def test_just_elapsed_boundary_can_use_observed_next_future_boundary(book):
    q = real_quote(funding_time=NOW - 1, next_funding_time=NOW + 60_000)
    book.submit(order(source="okx"), "funding-rolling", {SYMBOL: q})
    assert json.loads(book.positions("okx")[0]["metadata"])["expected_funding_time"] == NOW + 60_000


def test_schedule_gap_breaks_economic_chain_but_keeps_mark_margin_and_protection(book):
    q = real_quote()
    book.submit(order(source="okx"), "funding-initial", {SYMBOL: q})
    gap = q | {"funding_ts": NOW - 180_001}
    account = book.observe("okx", {SYMBOL: gap})
    assert account["valuation_status"] == "fresh"
    assert account["economic_status"] == "funding_schedule_unavailable"
    assert account["equity"] is None and D(account["equity_before_pending_funding"]) == 10000
    assert account["positions"][0]["maintenance_margin"] is not None
    assert not book.performance.report("okx")["summary"]["complete_valuation_chain"]
    close = book.submit(order(source="okx", side="sell", reduce_only=True), "funding-protect", {SYMBOL: gap})
    assert close["status"] == "filled" and not book.positions("okx")
    assert_ledger(book, "okx")


def test_pending_liability_has_priority_and_late_realized_settlement_still_books(book):
    q = real_quote(funding_time=NOW + 10_000, next_funding_time=NOW + 20_000)
    book.submit(order(source="okx"), "pending-original", {SYMBOL: q})
    book.now = lambda: NOW + 30_000
    later = q | {"ts": NOW + 30_000, "mark_ts": NOW + 30_000, "funding_ts": NOW - 180_001}
    account = book.observe("okx", {SYMBOL: later})
    assert account["economic_status"] == "funding_pending" and account["unavailable_funding_schedules"]
    event = {
        "inst_id": SYMBOL,
        "ts": NOW + 10_000,
        "rate": ".01",
        "mark_price": "100",
        "mark_ts": NOW + 10_000,
    }
    assert len(book.settle_funding("okx", SYMBOL, [event], {SYMBOL: later})) == 1
    assert book.settle_funding("okx", SYMBOL, [event], {SYMBOL: later}) == []
    book.submit(order(source="okx", side="sell", reduce_only=True), "pending-protect", {SYMBOL: later})
    assert_ledger(book, "okx")


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_pending_protection_clips_current_same_generation_and_preserves_command(book, side):
    command, pending, q = protective(book, side)
    opposite = "sell" if side == "buy" else "buy"
    book.submit(order(side=opposite, quantity="50", reduce_only=True), "partial-reduce", {SYMBOL: q})
    before = book.ledger("example")
    fill = book.submit(
        command,
        "protect-stop",
        {SYMBOL: snapshot(price="94" if side == "buy" else "106")},
        pending_id=pending["id"],
    )
    assert fill["status"] == "filled" and fill["id"] == pending["id"]
    assert fill["quantity"] == fill["filled_quantity"] == "50"
    assert fill["requested_quantity"] == "100" and fill["canceled_quantity"] == "50"
    assert fill["pending_reduction_policy"] == PENDING_REDUCTION_POLICY
    with book.store.read() as conn:
        original = conn.execute("SELECT payload,key FROM pro_orders WHERE id=?", (pending["id"],)).fetchone()
        assert original["payload"] == dumps(command) and original["key"] == "protect-stop"
    restart = SimulationBook(book.store, clock=lambda: NOW)
    assert restart.submit(command, "protect-stop", {SYMBOL: q}) == fill
    assert restart.ledger("example") == book.ledger("example") and len(book.ledger("example")) > len(before)
    assert not book.positions("example")
    assert book.contribution_report("example", {SYMBOL: q})["reconciled"] is True
    assert_ledger(book)


def test_flat_cancels_old_protection_before_reopen_without_a_trigger(book):
    command, pending, q = protective(book)
    book.submit(order(side="sell", reduce_only=True), "flat-position", {SYMBOL: q})
    canceled = book.existing("example", "protect-stop", dumps(command))
    assert canceled["status"] == "canceled" and canceled["cancellation_reason"] == "position_closed"
    assert canceled["filled_quantity"] == "0" and canceled["canceled_quantity"] == "100"
    assert not book.pending()
    book.submit(order(), "new-generation", {SYMBOL: q})
    book.reconcile_pending_reductions("example", SYMBOL)
    assert book.positions("example")[0]["quantity"] == "100"
    assert book.existing("example", "protect-stop", dumps(command)) == canceled
    assert_ledger(book)


@pytest.mark.parametrize(
    "defect,expected",
    [
        ("legacy", "position_identity_unavailable"),
        ("generation", "position_generation_changed"),
        ("direction", "position_direction_changed"),
    ],
)
def test_unprovable_or_changed_protection_cancels_without_fill(book, defect, expected):
    command, pending, q = protective(book)
    before = book.ledger("example")
    with book.store.write() as conn:
        body = json.loads(
            conn.execute("SELECT body FROM pro_orders WHERE id=?", (pending["id"],)).fetchone()[0]
        )
        if defect == "legacy":
            body.pop("protected_position_generation")
            conn.execute("UPDATE pro_orders SET body=? WHERE id=?", (dumps(body), pending["id"]))
        elif defect == "generation":
            body["protected_position_generation"] = "another-position"
            conn.execute("UPDATE pro_orders SET body=? WHERE id=?", (dumps(body), pending["id"]))
        else:
            conn.execute(
                "UPDATE pro_positions SET quantity='-100' WHERE source='example' AND inst_id=?", (SYMBOL,)
            )
    book.reconcile_pending_reductions("example", SYMBOL)
    canceled = book.existing("example", "protect-stop", dumps(command))
    assert canceled["status"] == "canceled" and canceled["cancellation_reason"] == expected
    assert canceled["filled_quantity"] == "0"
    assert book.ledger("example") == before


def test_instant_oversize_reduction_and_pending_payload_mutation_remain_rejected(book):
    command, pending, q = protective(book)
    book.submit(order(side="sell", quantity="50", reduce_only=True), "partial-reduce", {SYMBOL: q})
    with pytest.raises(PlatformError) as error:
        book.submit(order(side="sell", quantity="100", reduce_only=True), "instant-oversize", {SYMBOL: q})
    assert error.value.code == "reduce_only"
    with pytest.raises(PlatformError) as error:
        book.submit(command | {"quantity": "50"}, "protect-stop", {SYMBOL: q}, pending_id=pending["id"])
    assert error.value.code == "idempotency_conflict"


def test_concurrent_protection_trigger_cannot_duplicate_actual_fill(book):
    command, pending, q = protective(book)
    book.submit(order(side="sell", quantity="50", reduce_only=True), "partial-reduce", {SYMBOL: q})

    def run():
        try:
            return book.submit(command, "protect-stop", {SYMBOL: q}, pending_id=pending["id"])["status"]
        except PlatformError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))
    assert sorted(outcomes) == ["filled", "order_not_pending"]
    assert not book.positions("example")
    assert_ledger(book)


def test_old_working_order_survives_terminal_history_page_and_can_cancel(book):
    q = snapshot()
    pending = book.submit(order(order_type="limit", limit_price="90"), "old-pending", {SYMBOL: q})
    quotes = {SYMBOL: q, "BTC-USDT": snapshot("BTC-USDT")}
    for index in range(202):
        book.submit(
            order(
                "BTC-USDT",
                side="buy" if index % 2 == 0 else "sell",
                quantity=".01",
                reduce_only=bool(index % 2),
            ),
            f"history-{index:04d}",
            quotes,
        )
    rows = book.orders("example")
    assert len(rows) == 201 and len({row["id"] for row in rows}) == 201
    assert pending["id"] in {row["id"] for row in rows}
    assert len(book.orders("example", limit=2)) == 3
    assert book.cancel(pending["id"], "operator")["status"] == "canceled"
    assert not book.pending()


@pytest.mark.parametrize(
    "symbol,side,close_qty,entry_price,exit_price,leverage",
    [
        ("BTC-USDT", "buy", "1", "50", "110", 1),
        ("BTC-USDT", "buy", "2", "100", "90", 1),
        (SYMBOL, "buy", "50", "100", "110", 10),
        (SYMBOL, "sell", "50", "100", "90", 10),
        (SYMBOL, "buy", "100", "100", "110", 10),
        (SYMBOL, "sell", "100", "100", "90", 10),
        (SYMBOL, "buy", "100", "100", "1", 10),
        (SYMBOL, "sell", "100", "100", "1000", 10),
    ],
)
def test_preview_cash_margin_and_debt_match_fill_without_mutation(
    book, symbol, side, close_qty, entry_price, exit_price, leverage
):
    initial_qty = "100" if symbol.endswith("SWAP") else "2"
    book.set_risk("example", {"fee_bps": "10", "slippage_bps": "5"}, "test")
    book.submit(
        order(symbol, side=side, quantity=initial_qty, leverage=leverage),
        "preview-entry",
        {symbol: snapshot(symbol, price=entry_price)},
    )
    command = order(
        symbol,
        side="sell" if side == "buy" else "buy",
        quantity=close_qty,
        reduce_only=True,
        leverage=leverage,
    )
    q = snapshot(symbol, price=exit_price)
    before = book.ledger("example"), book.risk("example"), book.positions("example")
    preview = book.preview(command, {symbol: q})
    assert before == (book.ledger("example"), book.risk("example"), book.positions("example"))
    fill = book.submit(command, "preview-close", {symbol: q})
    assert preview["estimated_cash_after"] == fill["cash_after"]
    assert preview["estimated_insurance_debt_after"] == fill["insurance_debt"]
    assert preview["estimated_margin_after"] == fill["margin_after"]
    assert preview["estimated_position_after"] == fill["position_after"]
    assert D(preview["estimated_liability_created"]) == D(fill["insurance_debt"])
    assert_ledger(book)


async def test_real_catalog_binds_funding_provenance_and_runtime_rejects_stale_schedule(tmp_path):
    def handler(request):
        current = now_ms()
        path = request.url.path
        if path.endswith("/instruments"):
            rows = [raw_instrument(swap=True)]
        elif path.endswith("/mark-price"):
            rows = [{"instId": SYMBOL, "markPx": "100", "ts": str(current)}]
        elif path.endswith("/index-tickers"):
            rows = [{"instId": "BTC-USDT", "idxPx": "100", "ts": str(current - 2 * 86_400_000)}]
        elif path.endswith("/funding-rate"):
            rows = [
                {
                    "instId": SYMBOL,
                    "fundingRate": ".01",
                    "fundingTime": str(current - 10_000),
                    "nextFundingTime": str(current - 1),
                    "ts": str(current - 2 * 86_400_000),
                }
            ]
        elif path.endswith("/ticker"):
            rows = [{"instId": SYMBOL, "last": "100", "bidPx": "100", "askPx": "100", "ts": str(current)}]
        elif path.endswith("/position-tiers"):
            rows = [
                {"tier": "1", "minSz": "0", "maxSz": "100000", "mmr": ".004", "imr": ".01", "maxLever": "100"}
            ]
        else:
            raise AssertionError("Unexpected endpoint " + path)
        return httpx.Response(200, json={"code": "0", "data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(data_dir=tmp_path, _env_file=None)
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    try:
        with pytest.raises(PlatformError) as error:
            await runtime.submit(order(source="okx"), "actual-stale-funding", "operator")
        assert error.value.code == "funding_schedule_unavailable"
        q = runtime.snapshots[("okx", SYMBOL)]
        assert q["funding_inst_id"] == SYMBOL and q["funding_source"] == "okx"
        assert not runtime.book.orders("okx")
    finally:
        await runtime.stop()
        await client.aclose()


async def test_runtime_liquidation_works_with_fresh_mark_and_stale_funding(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected network")))
    settings = Settings(data_dir=tmp_path, _env_file=None)
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    current = now_ms()
    try:
        runtime.book.set_risk(
            "okx", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test"
        )
        q = snapshot(source="okx", ts=current)
        runtime.book.submit(order(source="okx"), "liquidation-entry", {SYMBOL: q})
        severe = snapshot(source="okx", ts=current + 1, price="90", funding_ts=current - 180_001)

        async def reconcile(*args, **kwargs):
            return None

        runtime.sync_funding = reconcile
        await runtime.poll_market("okx", SYMBOL, {SYMBOL: severe})
        assert not runtime.book.positions("okx")
        assert runtime.book.orders("okx")[0]["liquidation"]
        assert_ledger(runtime.book, "okx")
    finally:
        await runtime.stop()
        await client.aclose()
