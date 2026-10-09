"""Three-market causal lifecycle counterexamples with actual journal mutations."""

import copy
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext

import pytest
from test_pro_execution import order, snapshot
from tidebench.engine import ACCOUNTING_CONTEXT, Candle
from tidebench.historical_lifecycle import (
    LifecycleEvent,
    apply_inventory_event,
    capture_events,
    eligibility,
    initialize,
)
from tidebench.platform import PlatformError
from tidebench.portfolio_research import PortfolioInput, simulate_portfolio
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, encode

HOUR = 3_600_000
START = 1767225600000
END = START + 12 * HOUR


def fact(symbol, kind, offset=0, **changes):
    raw = json.dumps(
        {
            "inst_id": symbol,
            "kind": kind,
            "effective_ts": START + offset * HOUR,
            "fixture": "causal lifecycle unit test",
        },
        sort_keys=True,
    )
    result = dict(
        inst_id=symbol,
        kind=kind,
        effective_ts=START + offset * HOUR,
        known_at=START,
        source=dict(
            url="https://example.test/lifecycle",
            published_at=START,
            captured_at=END,
            raw_content=raw,
            content_hash=hashlib.sha256(raw.encode()).hexdigest(),
            evidence_type="synthetic_fixture",
        ),
    )
    if kind in {"listing", "resume", "rules", "unit_conversion"}:
        quote = snapshot(symbol)
        quote["instrument"]["base"] = symbol.split("-")[0]
        if symbol.endswith("-SWAP"):
            quote["instrument"]["ct_val_ccy"] = quote["instrument"]["base"]
        result.update(
            instrument=quote["instrument"],
            margin_tiers=quote["margin_tiers"] if symbol.endswith("-SWAP") else [],
            valid_until=END,
        )
    return encode(LifecycleEvent.model_validate(result | changes).model_dump())


def fixture(**changes):
    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    body = encode(
        PortfolioInput.model_validate(
            dict(
                name="Lifecycle universe",
                hypothesis="Known listings, gaps and suspensions change eligibility without invented prices.",
                legs=[
                    dict(package_id=str(i) * 32, weight=".2", strategy={"kind": "buy_hold"})
                    for i in (1, 2, 3)
                ],
                universe_mode="historical_lifecycle",
                lifecycle_warmup_bars=2,
                fee_bps=0,
                slippage_bps=0,
                max_daily_loss_pct=50,
                rebalance_bars=1,
                max_order_notional=1000000,
                **changes,
            )
        ).model_dump()
    )
    legs = []
    for symbol, policy in zip(symbols, body["legs"], strict=True):
        meta = fact(symbol, "listing")["instrument"]
        policy["lifecycle_events"] = [fact(symbol, "listing")]
        bars = [
            Candle(
                ts=START + i * HOUR,
                open=D(100),
                high=D(100),
                low=D(100),
                close=D(100),
                volume=D(1),
                confirmed=True,
            )
            for i in range(12)
        ]
        legs.append(dict(config=policy, instrument=meta, candles=bars, marks=bars, funding=[], tiers=[]))
    return (
        body,
        dict(
            source="example",
            bar="1H",
            start=START,
            end=END,
            universe_scope="synthetic three-market lifecycle fixture",
        ),
        legs,
    )


def run(body, manifest, legs):
    with localcontext(ACCOUNTING_CONTEXT):
        return simulate_portfolio(body, manifest, legs)


def balanced(ledger):
    with localcontext() as context:
        context.prec = 200
        totals = {}
        for row in ledger:
            totals[row["asset"]] = totals.get(row["asset"], D(0)) + D(row["debit"]) - D(row["credit"])
        assert not any(totals.values())


def test_three_markets_listing_warmup_suspend_resume_and_absent_unknown():
    body, manifest, legs = fixture()
    legs[1]["config"]["lifecycle_events"] = [fact("ETH-USDT", "listing", 3)]
    legs[1]["candles"] = legs[1]["candles"][3:]
    legs[1]["marks"] = legs[1]["marks"][3:]
    legs[0]["config"]["lifecycle_events"] += [fact("BTC-USDT", "suspend", 5), fact("BTC-USDT", "resume", 7)]
    legs[2]["config"]["lifecycle_events"] = []
    result = run(body, manifest, legs)
    assert result["lifecycle"]["status"] == "complete_within_supplied_scope"
    orders = result["orders"]
    assert min(o["quote_ts"] for o in orders if o["inst_id"] == "BTC-USDT") == START + 3 * HOUR
    assert min(o["quote_ts"] for o in orders if o["inst_id"] == "ETH-USDT") == START + 6 * HOUR
    assert not any(o["inst_id"] == "SOL-USDT" for o in orders)
    assert not any(
        START + 5 * HOUR <= o["quote_ts"] < START + 10 * HOUR for o in orders if o["inst_id"] == "BTC-USDT"
    )
    states = result["lifecycle"]["eligibility"]
    assert states[0]["markets"]["ETH-USDT"]["state"] == "unknown"
    assert states[5]["markets"]["BTC-USDT"]["reason"] == "suspended"
    assert states[7]["markets"]["BTC-USDT"]["reason"] == "warming_up"
    assert all(s["markets"]["SOL-USDT"]["tradable"] is False for s in states)
    balanced(result["ledger"])


def test_future_known_listing_and_expired_coverage_never_default_to_live():
    event = fact("BTC-USDT", "listing", known_at=START + 3 * HOUR, valid_until=START + 7 * HOUR)
    bars = {START + i * HOUR for i in range(12)}
    assert eligibility([event], START + 2 * HOUR, bars, HOUR, 2)["state"] == "unknown"
    assert not eligibility([event], START + 4 * HOUR, bars, HOUR, 2)["tradable"]
    assert eligibility([event], START + 5 * HOUR, bars, HOUR, 2)["tradable"]
    assert eligibility([event], START + 7 * HOUR, bars, HOUR, 2)["state"] == "unknown"


@pytest.mark.parametrize(
    "obstacle", ["delist", "missing_mark", "expired_rules", "unsupported_spot_conversion"]
)
def test_held_inventory_without_supported_economics_preserves_inventory_and_null_equity(obstacle):
    body, manifest, legs = fixture()
    if obstacle == "delist":
        legs[0]["config"]["lifecycle_events"].append(fact("BTC-USDT", "delist", 5))
    elif obstacle == "missing_mark":
        legs[0]["marks"].pop(5)
    elif obstacle == "expired_rules":
        legs[0]["config"]["lifecycle_events"][0]["valid_until"] = START + 5 * HOUR
    else:
        legs[0]["config"]["lifecycle_events"].append(
            fact("BTC-USDT", "unit_conversion", 5, quantity_ratio="2")
        )
    result = run(body, manifest, legs)
    assert result["economic_state"] == "incomplete_lifecycle"
    assert result["metrics"]["final_equity"] is None and result["metrics"]["total_return_pct"] is None
    assert result["final_account"]["equity"] is None
    position = next(p for p in result["final_account"]["positions"] if p["inst_id"] == "BTC-USDT")
    assert D(position["quantity"]) == 20
    assert position["market_value"] is None
    assert not any(o["quote_ts"] >= START + 5 * HOUR for o in result["orders"])
    assert result["lifecycle"]["issues"]
    assert D(result["final_account"]["cash"]) == 4000
    balanced(result["ledger"])


def test_typed_spot_cash_settlement_is_not_a_fake_market_fill():
    body, manifest, legs = fixture()
    legs[0]["config"]["lifecycle_events"] += [
        fact("BTC-USDT", "delist", 5),
        fact("BTC-USDT", "cash_settlement", 5, settlement_price="120", settlement_fee="2"),
    ]
    result = run(body, manifest, legs)
    assert result["lifecycle"]["status"] == "complete_within_supplied_scope"
    application = result["lifecycle"]["applications"][0]
    assert D(application["quantity_before"]) == 20 and application["quantity_after"] == "0"
    assert D(application["cash_delta"]) == 2398 and D(application["realized_delta"]) == 398
    assert D(result["metrics"]["final_equity"]) == 10398
    assert result["economics"]["status"] == "reconciled"
    assert D(result["economics"]["net_pnl"]) == 398
    assert result["economics"]["market_contributions"]["status"] == "unavailable"
    assert result["economics"]["passive_reference"]["status"] == "unavailable"
    assert sum(o["inst_id"] == "BTC-USDT" for o in result["orders"]) == 1
    assert D(result["metrics"]["fees_paid"]) == 2
    assert "BTC-USDT" not in {p["inst_id"] for p in result["final_account"]["positions"]}
    balanced(result["ledger"])


def populated_book(tmp_path, *, side="buy", quantity="100"):
    clock = [START]
    book = SimulationBook(
        Store(tmp_path / "lifecycle.sqlite3"), clock=lambda: clock[0], capture_contributions=False
    )
    initialize(book.store)
    quote = snapshot("BTC-USDT-SWAP", ts=START)
    quote.pop("funding_time")
    quote.pop("next_funding_time")
    book.set_risk("example", dict(fee_bps=0, slippage_bps=0, max_order_notional=1000000), "fixture")
    book.submit(
        order("BTC-USDT-SWAP", side, quantity, leverage=2),
        "fixture-open",
        {"BTC-USDT-SWAP": quote},
        "fixture",
    )
    clock[0] = START + 5 * HOUR
    return book, clock, quote


def test_contract_units_convert_exact_exposure_basis_margin_and_idempotent_journal(tmp_path):
    book, clock, quote = populated_book(tmp_path)
    event = fact("BTC-USDT-SWAP", "unit_conversion", 5, quantity_ratio="2")
    event["instrument"]["ct_val"] = ".005"
    before = book.positions("example")[0]
    first = apply_inventory_event(book, "example", event)
    after = book.positions("example")[0]
    assert D(after["quantity"]) == D(before["quantity"]) * 2
    assert D(first["base_before"]) == D(first["base_after"])
    assert (
        after["entry_price"] == before["entry_price"]
        and after["margin"] == before["margin"]
        and after["basis"] == before["basis"]
    )
    ledger = book.ledger("example", limit=10000)
    assert apply_inventory_event(book, "example", event) == first
    assert book.ledger("example", limit=10000) == ledger
    assert len(book.orders("example")) == 1 and book.account("example", {})["fees_paid"] == "0.00"
    balanced(ledger)


@pytest.mark.parametrize(
    "side,price,expected_cash,expected_debt",
    [("buy", "120", "10020", "0"), ("sell", "120", "9980", "0"), ("buy", "0", "9950", "50")],
)
def test_swap_cash_settlement_actual_profit_margin_fee_and_insurance(
    tmp_path, side, price, expected_cash, expected_debt
):
    book, clock, quote = populated_book(tmp_path, side=side)
    # 100 contracts * .01 BTC * 100 / leverage2 = 50 posted margin.
    event = fact("BTC-USDT-SWAP", "cash_settlement", 5, settlement_price=price)
    applied = apply_inventory_event(book, "example", event)
    account = book.account("example", {})
    assert D(account["cash"]) == D(expected_cash)
    assert D(account["insurance_debt"]) == D(expected_debt)
    assert not book.positions("example") and applied["quantity_after"] == "0"
    balanced(book.ledger("example", limit=10000))


def test_bad_conversion_rolls_back_without_application_or_ledger_change(tmp_path):
    book, clock, quote = populated_book(tmp_path)
    event = fact("BTC-USDT-SWAP", "unit_conversion", 5, quantity_ratio="3")
    event["instrument"]["ct_val"] = ".005"
    before = book.positions("example"), book.ledger("example", limit=10000)
    with pytest.raises(PlatformError, match="exactly conserve"):
        apply_inventory_event(book, "example", event)
    assert (book.positions("example"), book.ledger("example", limit=10000)) == before
    with book.store.read() as conn:
        assert not conn.execute("SELECT * FROM historical_lifecycle_applications").fetchall()


def test_raw_source_hash_temporal_attribution_conflict_and_immutable_capture(tmp_path):
    store = Store(tmp_path / "evidence.sqlite3")
    initialize(store)
    original = fact("BTC-USDT", "listing")
    with pytest.raises(ValueError, match="SHA-256"):
        LifecycleEvent.model_validate(
            original | {"source": original["source"] | {"raw_content": "Changed captured source body"}}
        )
    with pytest.raises(ValueError, match="Known-at"):
        LifecycleEvent.model_validate(original | {"known_at": START - 1})
    with pytest.raises(PlatformError, match="same-kind"):
        capture_events(store, "example", "BTC-USDT", [original, original], HOUR, END)
    saved = capture_events(store, "example", "BTC-USDT", [original], HOUR, END)
    changed = copy.deepcopy(original)
    changed["instrument"]["min_size"] = ".02"
    capture_events(store, "example", "BTC-USDT", [changed], HOUR, END)
    assert saved == [original]  # captured body, not a pointer to today's catalog
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM historical_lifecycle_events").fetchone()[0] == 2
    with pytest.raises(Exception, match="immutable lifecycle"):
        with store.write() as conn:
            conn.execute("UPDATE historical_lifecycle_events SET body='{}'")


def test_simulated_contract_unit_change_keeps_underlying_signal_price_and_economics():
    body, manifest, legs = fixture()
    symbol = "BTC-USDT-SWAP"
    listing = fact(symbol, "listing")
    legs[0]["instrument"] = listing["instrument"]
    legs[0]["tiers"] = listing["margin_tiers"]
    legs[0]["config"]["lifecycle_events"] = [listing]
    conversion = fact(symbol, "unit_conversion", 5, quantity_ratio="2")
    conversion["instrument"]["ct_val"] = ".005"
    legs[0]["config"]["lifecycle_events"].append(conversion)
    result = run(body, manifest, legs)
    assert result["lifecycle"]["status"] == "complete_within_supplied_scope"
    application = result["lifecycle"]["applications"][0]
    assert D(application["base_before"]) == D(application["base_after"]) == 20
    assert len(result["orders"]) == 3
    assert all(D(p["equity"]) == 10000 for p in result["equity"])
    assert D(result["final_account"]["fees_paid"]) == 0
    assert application["signal_price_policy"] == "unchanged underlying base-price history"
    balanced(result["ledger"])


def test_incomplete_lifecycle_holdout_rejects_even_if_statistical_sample_is_small():
    from tidebench.research_protocol import evaluate_frozen_portfolio

    body, manifest, legs = fixture()
    legs[0]["config"]["lifecycle_events"].append(fact("BTC-USDT", "delist", 5))
    manifest.update(
        evaluation_plan=dict(
            warmup_bars=0,
            test_start=START,
            test_end=END,
            rejection_plan="Reject unavailable economics even without enough statistical observations.",
            criteria=dict(
                min_observations=100,
                min_trades=1,
                min_return_vs_cash_pct="-100",
                max_drawdown_pct="100",
                require_zero_debt=True,
            ),
        ),
        governance={},
    )
    with localcontext(ACCOUNTING_CONTEXT):
        result = evaluate_frozen_portfolio(body, manifest, legs)
    assert result["evaluation"]["rejection"]["status"] == "rejected"
    assert any(
        c["metric"] == "lifecycle_economics" and not c["passed"]
        for c in result["evaluation"]["rejection"]["checks"]
    )


def test_uncertain_delist_interval_is_unknown_until_upper_bound():
    events = [fact("BTC-USDT", "listing"), fact("BTC-USDT", "delist", 5, effective_until=START + 7 * HOUR)]
    bars = {START + i * HOUR for i in range(12)}
    assert eligibility(events, START + 6 * HOUR, bars, HOUR, 2)["state"] == "unknown"
    assert eligibility(events, START + 7 * HOUR, bars, HOUR, 2)["state"] == "delisted"


@pytest.fixture
async def runtime(tmp_path):
    import httpx
    from tidebench.config import Settings
    from tidebench.market import MarketService
    from tidebench.pro_service import ProfessionalRuntime

    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    result = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    yield result
    await result.stop()
    await client.aclose()


async def test_frozen_lifecycle_sources_replay_without_catalog_or_event_store_lookups(runtime, monkeypatch):
    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    project = runtime.portfolio_registry.create_project(
        "Bounded historical universe",
        "Keep source chronology pinned through mutable catalog changes and a one-use final evaluation.",
        dict(
            rebalance_bars=1,
            capital_pct="60",
            legs=[dict(inst_id=s, weight=".2", strategy={"kind": "buy_hold"}) for s in symbols],
        ),
        "researcher",
    )
    packages = []
    for symbol in symbols:
        package = runtime.packages.create_package(symbol, "1H", START, START + 48 * HOUR, "example")
        package = await runtime.packages.run_package(package["id"])
        assert package["ready"]
        packages.append(package)
    histories = {}
    for symbol, package in zip(symbols, packages, strict=True):
        listing = fact(
            symbol, "listing", instrument=package["manifest"]["instrument"], valid_until=START + 48 * HOUR
        )
        listing["source"]["captured_at"] = START + 48 * HOUR
        histories[symbol] = [listing]
    body = dict(
        name="Frozen lifecycle final",
        portfolio_version_id=project["version"]["id"],
        package_ids=[p["id"] for p in packages],
        test_start=START + 10 * HOUR,
        test_end=START + 48 * HOUR,
        warmup_bars=4,
        universe_mode="historical_lifecycle",
        lifecycle_warmup_bars=2,
        lifecycle_events=histories,
        fee_bps=0,
        slippage_bps=0,
        rejection_plan="Reject failed lifecycle accounting or an unavailable terminal marked equity.",
        criteria=dict(
            min_return_vs_cash_pct="-100", max_drawdown_pct="100", min_trades=1, min_observations=20
        ),
    )
    preview = runtime.protocol.preview(body, "researcher")
    frozen = runtime.protocol.seal(
        dict(preview_id=preview["id"], preview_hash=preview["plan_hash"]), "researcher"
    )
    # A newly imported contradictory body does not rewrite sealed source bytes.
    changed = copy.deepcopy(histories["BTC-USDT"][0])
    changed["instrument"]["min_size"] = "100000000"
    capture_events(runtime.store, "example", "BTC-USDT", [changed], HOUR, START + 48 * HOUR)
    for method in ("load_candles", "verify_dataset", "get_margin_tiers"):
        monkeypatch.setattr(
            runtime.catalog, method, lambda *a, **k: pytest.fail("Frozen lifecycle read mutable catalog")
        )
    monkeypatch.setattr(
        runtime.packages, "research_inputs", lambda *a: pytest.fail("Frozen lifecycle read mutable package")
    )
    original = runtime.protocol.evaluate(frozen["id"], frozen["plan_hash"], "researcher")
    await runtime.offload(runtime.portfolios.compute, original["id"])
    original = runtime.portfolios.get(original["id"])
    assert original["status"] == "completed", original["error"]
    assert original["result"]["lifecycle"]["status"] == "complete_within_supplied_scope"
    replay = runtime.portfolios.replay(original["id"], "researcher")
    await runtime.offload(runtime.portfolios.compute, replay["id"])
    replay = runtime.portfolios.get(replay["id"])
    assert replay["status"] == "completed", replay["error"]
    assert replay["result"] == original["result"] and replay["manifest"]["replay_verified"]
    assert set(original["result"]["lifecycle"]["source_hashes"]) == {
        e[0]["source"]["content_hash"] for e in histories.values()
    }
    # The forward controller's lifecycle difference is a hard blocker, not a
    # cost/risk acknowledgement that could claim equivalent execution.
    monkeypatch.undo()
    release = runtime.portfolio_releases.preview(original["id"])
    assert "historical_lifecycle_forward_unsupported" in release["blockers"]
    assert any(d["field"] == "lifecycle_execution_support" for d in release["risk_differences"])
    assert not runtime.book.positions("example")


def test_official_three_market_sample_retains_source_interval_and_missing_settlement():
    from pathlib import Path

    sample = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples/lifecycle/okx-spot-removals-2025-03-20.json"
        ).read_text()
    )
    assert set(sample["lifecycle_events"]) == {"XR-USDT", "GOAL-USDT", "KP3R-USDT"}
    for events in sample["lifecycle_events"].values():
        event = LifecycleEvent.model_validate(events[0])
        assert event.kind == "delist" and event.settlement_price is None
        assert event.effective_until - event.effective_ts == HOUR
        assert event.source.evidence_type == "announcement_extract"
        assert event.known_at < event.effective_ts
        assert "not archived original HTML" in event.source.raw_content
        assert (
            eligibility(encode([event.model_dump()]), event.effective_ts + HOUR // 2, set(), HOUR, 2)["state"]
            == "unknown"
        )


def test_conversion_rejects_precision_loss_before_inventory_and_journal_mutation(tmp_path):
    book, clock, quote = populated_book(tmp_path, quantity="100.01")
    # A tradable .01-lot quantity and an individually supported 50-digit ratio
    # produce a product outside the supported 50-digit accounting domain.
    event = fact(
        "BTC-USDT-SWAP",
        "unit_conversion",
        5,
        quantity_ratio="1.0000000000000000000000000000000000000000000000001",
    )
    before = book.positions("example"), book.ledger("example", limit=10000)
    with pytest.raises(PlatformError):
        apply_inventory_event(book, "example", event)
    assert (book.positions("example"), book.ledger("example", limit=10000)) == before


async def test_real_package_windows_can_start_after_listing_without_fabricated_prehistory(runtime):
    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    starts = (START, START + 4 * HOUR, START + 8 * HOUR)
    packages = []
    for symbol, start in zip(symbols, starts, strict=True):
        p = runtime.packages.create_package(symbol, "1H", start, START + 24 * HOUR, "example")
        p = await runtime.packages.run_package(p["id"])
        assert p["ready"]
        packages.append(p)
    body = encode(
        PortfolioInput.model_validate(
            dict(
                name="Staggered actual packages",
                hypothesis="Each ready package starts at its own listing boundary; missing prehistory cannot grant warmup.",
                universe_mode="historical_lifecycle",
                lifecycle_warmup_bars=2,
                fee_bps=0,
                slippage_bps=0,
                max_daily_loss_pct=50,
                rebalance_bars=1,
                legs=[
                    dict(
                        package_id=p["id"],
                        weight=".2",
                        strategy={"kind": "buy_hold"},
                        lifecycle_events=[
                            fact(
                                symbol,
                                "listing",
                                (start - START) // HOUR,
                                instrument=p["manifest"]["instrument"],
                                valid_until=START + 24 * HOUR,
                            )
                        ],
                    )
                    for symbol, start, p in zip(symbols, starts, packages, strict=True)
                ],
            )
        ).model_dump()
    )
    queued = runtime.portfolios.create(body, "researcher")
    assert queued["manifest"]["start"] == START and queued["manifest"]["end"] == START + 24 * HOUR
    await runtime.offload(runtime.portfolios.compute, queued["id"])
    result = runtime.portfolios.get(queued["id"])
    assert result["status"] == "completed", result["error"]
    assert result["result"]["lifecycle"]["status"] == "complete_within_supplied_scope"
    for symbol, start in zip(symbols, starts, strict=True):
        orders = [o for o in result["result"]["orders"] if o["inst_id"] == symbol]
        assert orders and min(o["quote_ts"] for o in orders) >= start + 3 * HOUR
    assert not runtime.book.positions("example")


async def test_actual_incomplete_inventory_study_cannot_be_released_even_with_all_acknowledgements(runtime):
    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    project = runtime.portfolio_registry.create_project(
        "Unresolved custody release test",
        "A delisted custody balance without a settlement must not be promoted as an economically complete strategy.",
        dict(
            capital_pct="60",
            rebalance_bars=1,
            legs=[dict(inst_id=s, weight=".2", strategy={"kind": "buy_hold"}) for s in symbols],
        ),
        "researcher",
    )
    inputs = []
    for symbol in symbols:
        p = runtime.packages.create_package(symbol, "1H", START, START + 24 * HOUR, "example")
        p = await runtime.packages.run_package(p["id"])
        assert p["ready"]
        events = [
            fact(symbol, "listing", instrument=p["manifest"]["instrument"], valid_until=START + 24 * HOUR)
        ]
        if symbol == "BTC-USDT":
            events.append(fact(symbol, "delist", 10))
        inputs.append(
            dict(package_id=p["id"], weight=".2", strategy={"kind": "buy_hold"}, lifecycle_events=events)
        )
    body = dict(
        name="Unresolved custody study",
        hypothesis=project["version"]["hypothesis"],
        portfolio_version_id=project["version"]["id"],
        capital_pct="60",
        rebalance_bars=1,
        legs=inputs,
        universe_mode="historical_lifecycle",
        lifecycle_warmup_bars=2,
        fee_bps=0,
        slippage_bps=0,
        max_daily_loss_pct=50,
    )
    queued = runtime.portfolios.create(body, "researcher")
    await runtime.offload(runtime.portfolios.compute, queued["id"])
    result = runtime.portfolios.get(queued["id"])
    assert result["status"] == "completed", result["error"]
    assert result["result"]["metrics"]["final_equity"] is None
    assert result["result"]["economics"]["status"] == "incomplete"
    preview = runtime.portfolio_releases.preview(result["id"])
    assert "research_economics_incomplete" in preview["blockers"]
    with pytest.raises(PlatformError, match="blocked"):
        runtime.portfolio_releases.approve(
            dict(
                run_id=result["id"],
                preview_hash=preview["preview_hash"],
                review="Acknowledge every displayed condition to test the mandatory economic block.",
                acknowledgements=preview["required_acknowledgements"],
            ),
            "researcher",
        )
    assert not runtime.portfolio_releases.list("example")
    assert not runtime.book.positions("example")


def test_rules_and_unit_conversion_do_not_resume_suspension_or_cover_unknown_membership():
    symbol = "BTC-USDT-SWAP"
    events = [fact(symbol, "listing"), fact(symbol, "suspend", 5), fact(symbol, "rules", 6)]
    bars = {START + i * HOUR for i in range(12)}
    assert eligibility(events, START + 7 * HOUR, bars, HOUR, 2)["state"] == "suspended"
    assert eligibility([fact(symbol, "rules")], START + 4 * HOUR, bars, HOUR, 2)["state"] == "unknown"
    expired = [fact(symbol, "listing", valid_until=START + 5 * HOUR), fact(symbol, "rules", 7)]
    assert eligibility(expired, START + 8 * HOUR, bars, HOUR, 2)["state"] == "unknown"


def test_checked_lifecycle_sources_and_accounting_applications_detect_tampered_identities(tmp_path):
    from tidebench.historical_lifecycle import checked_application, checked_event

    book, clock, quote = populated_book(tmp_path)
    event = fact("BTC-USDT-SWAP", "cash_settlement", 5, settlement_price="120", settlement_fee="1")
    capture_events(book.store, "example", "BTC-USDT-SWAP", [event], HOUR, END)
    apply_inventory_event(book, "example", event)
    with book.store.read() as conn:
        source = dict(conn.execute("SELECT * FROM historical_lifecycle_events").fetchone())
        application = dict(conn.execute("SELECT * FROM historical_lifecycle_applications").fetchone())
    assert checked_event(source) == event
    assert D(checked_application(application)["realized_delta"]) == 19
    corrupt = copy.deepcopy(source)
    corrupt["source_hash"] = "0" * 64
    with pytest.raises(PlatformError, match="does not verify"):
        checked_event(corrupt)
    corrupt = copy.deepcopy(application)
    body = json.loads(corrupt["body"])
    body["cash_delta"] = "999999"
    corrupt["body"] = json.dumps(body)
    with pytest.raises(PlatformError, match="does not verify"):
        checked_application(corrupt)
    with pytest.raises(PlatformError, match="does not verify"):
        checked_application(application | {"source": "okx"})


def test_late_inventory_fact_and_managed_book_mutation_are_explicitly_unsupported(tmp_path):
    book, clock, quote = populated_book(tmp_path)
    event = fact("BTC-USDT-SWAP", "cash_settlement", 4, settlement_price="120", known_at=START + 5 * HOUR)
    before = book.positions("example"), book.ledger("example", limit=10000)
    with pytest.raises(PlatformError, match="Late inventory"):
        apply_inventory_event(book, "example", event)
    assert (book.positions("example"), book.ledger("example", limit=10000)) == before
    managed = SimulationBook(Store(tmp_path / "managed.sqlite3"), clock=lambda: clock[0])
    with pytest.raises(PlatformError, match="isolated research"):
        apply_inventory_event(
            managed, "example", fact("BTC-USDT-SWAP", "cash_settlement", 5, settlement_price="120")
        )
