"""Portfolio risk properties against native spot and isolated swap accounting."""

import copy
import json
from decimal import ROUND_DOWN, Decimal, Inexact, getcontext, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tidebench.engine import ACCOUNTING_CONTEXT
from tidebench.portfolio_analytics import (
    MAX_POSITIONS,
    MAX_SCENARIOS,
    PortfolioAnalyticsError,
    PriceShock,
    analyze_portfolio,
    replay_portfolio_snapshot,
)
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store

D = Decimal
NOW = 2_000_000
SPOT = "BTC-USDT"
SWAP = "BTC-USDT-SWAP"


def snapshot(symbol=SWAP, *, source="example", mark="100", ts=NOW, base="BTC", **overrides):
    swap = symbol.endswith("-SWAP")
    instrument = {
        "inst_id": symbol,
        "inst_type": "SWAP" if swap else "SPOT",
        "base": base,
        "quote": "USDT",
        "settle_ccy": "USDT" if swap else "",
        "ct_type": "linear" if swap else None,
        "ct_val": ".01" if swap else None,
        "ct_mult": "1" if swap else None,
        "ct_val_ccy": base if swap else None,
        "tick_size": ".01",
        "lot_size": ".01",
        "min_size": ".01",
        "state": "live",
    }
    return {
        "source": source,
        "inst_id": symbol,
        "instrument": instrument,
        "ts": ts,
        "mark_ts": ts,
        "mark": mark,
        "last": mark,
        "bid": mark,
        "ask": mark,
        "funding_time": NOW + 1_000_000,
        "next_funding_time": NOW + 2_000_000,
        "margin_tiers": [
            {
                "tier": 1,
                "min_size": "0",
                "max_size": "1000",
                "mmr": ".004",
                "imr": ".01",
                "max_leverage": "100",
            },
            {
                "tier": 2,
                "min_size": "1000",
                "max_size": "100000",
                "mmr": ".01",
                "imr": ".02",
                "max_leverage": "50",
            },
        ],
        **overrides,
    }


def holding(symbol=SWAP, *, quantity="100", entry="100", margin="10", **overrides):
    snap = snapshot(symbol)
    return {
        "inst_id": symbol,
        "inst_type": snap["instrument"]["inst_type"],
        "quantity": quantity,
        "entry_price": entry,
        "margin": margin if symbol.endswith("-SWAP") else "0",
        "instrument": snap["instrument"],
        **overrides,
    }


def account(positions=None, *, source="example", cash="1000", debt="0", reserved="0", **overrides):
    return {
        "source": source,
        "cash": cash,
        "insurance_debt": debt,
        "reserved_cash": reserved,
        "positions": positions or [],
        **overrides,
    }


def analyze(acct=None, snapshots=None, **kwargs):
    return analyze_portfolio(
        acct or account([holding()]),
        snapshots if snapshots is not None else {SWAP: snapshot()},
        as_of_ms=NOW,
        scenarios=[PriceShock("Unchanged")],
        **kwargs,
    )


@pytest.fixture
def book(tmp_path):
    result = SimulationBook(Store(tmp_path / "portfolio.sqlite"))
    result.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "test")
    return result


def submit(book, symbol, side, quantity, *, leverage=10, key="open-position-01"):
    command = {
        "source": "example",
        "inst_id": symbol,
        "side": side,
        "quantity": quantity,
        "leverage": leverage if symbol.endswith("-SWAP") else 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
    }
    return book.submit(command, key, {SPOT: snapshot(SPOT), SWAP: snapshot(SWAP)})


def test_real_book_hedge_has_zero_net_but_positive_gross_and_isolated_bankruptcy(book):
    submit(book, SPOT, "buy", "4", key="spot-long-0001")
    submit(book, SWAP, "sell", "400", key="perp-short-001")
    snaps = {SPOT: snapshot(SPOT), SWAP: snapshot(SWAP)}
    acct = book.account("example", snaps)
    result = analyze_portfolio(
        acct,
        snaps,
        as_of_ms=NOW,
        fee_bps=D(10),
        liquidation_fee_bps=D(50),
        scenarios=[PriceShock("Up 20%", D(20))],
    )
    summary = result["summary"]
    assert summary["equity_reconciliation"] == "matched"
    assert D(summary["equity"]) == 10000
    assert D(summary["cash"]) == 9560
    assert D(summary["spot_value"]) == 400
    assert D(summary["used_margin"]) == 40
    assert D(summary["gross_notional"]) == 800
    assert D(summary["net_notional"]) == 0
    assert D(summary["gross_leverage"]) == D(".08")
    assert D(result["assets"][0]["long_notional"]) == 400
    assert D(result["assets"][0]["short_notional"]) == 400
    assert D(summary["concentration_hhi"]) == 1
    stress = result["scenarios"][0]
    assert D(stress["pre_liquidation_equity"]) == 10000
    assert stress["liquidations"] == 1 and stress["bankruptcies"] == 1
    assert D(stress["post_cash"]) == 9560  # A losing isolated margin does not consume free cash.
    assert D(stress["incremental_liability"]) == D("42.88")
    assert D(stress["liquidation_costs"]) == D("2.88")
    assert D(stress["post_full_liquidation_equity"]) == D("9997.12")
    after = book.account("example", snaps)
    assert {key: value for key, value in after.items() if key != "as_of"} == {
        key: value for key, value in acct.items() if key != "as_of"
    }  # Risk analysis is strictly read-only; only the accessor clock advances.


def test_existing_insurance_liability_and_cash_reservations_are_not_double_counted():
    result = analyze(account([holding()], cash="100", debt="7", reserved="30"))
    summary = result["summary"]
    assert D(summary["equity"]) == 103
    assert D(summary["available_cash"]) == 70
    assert D(summary["insurance_liability"]) == 7
    assert D(result["scenarios"][0]["post_full_liquidation_equity"]) == 103


def test_post_liquidation_preserves_old_debt_and_charges_adverse_tick_slippage():
    acct = account([holding(quantity="-100", margin="10")], cash="1000", debt="7")
    result = analyze_portfolio(
        acct,
        {SWAP: snapshot()},
        as_of_ms=NOW,
        fee_bps=D(10),
        liquidation_fee_bps=D(50),
        slippage_bps=D(25),
        scenarios=[PriceShock("Up 20%", D(20))],
    )
    scenario = result["scenarios"][0]
    row = scenario["positions"][0]
    assert D(row["execution_price"]) == D("120.30")  # Buy to close a short.
    assert D(row["liquidation_fee"]) == D(".7218")
    assert D(scenario["incremental_liability"]) == D("11.0218")
    assert D(scenario["post_insurance_liability"]) == D("18.0218")
    assert D(scenario["post_cash"]) == 1000
    assert D(scenario["post_full_liquidation_equity"]) == D("981.9782")


@pytest.mark.parametrize(
    "quantity,change,expected_pnl,side",
    [("100", "10", "10", "long"), ("-100", "10", "-10", "short"), ("-100", "-10", "10", "short")],
)
def test_contract_quantity_is_not_base_currency(quantity, change, expected_pnl, side):
    result = analyze_portfolio(
        account([holding(quantity=quantity, margin="100")]),
        {SWAP: snapshot()},
        as_of_ms=NOW,
        scenarios=[PriceShock("Change", D(change))],
    )
    row = result["positions"][0]
    assert D(row["base_quantity"]) == (1 if quantity == "100" else -1)
    assert D(row["gross_notional"]) == 100
    assert row["side"] == side
    assert D(result["scenarios"][0]["positions"][0]["pnl_change"]) == D(expected_pnl)


def test_market_overrides_asset_overrides_parallel_without_addition():
    eth = "ETH-USDT"
    eth_snap = snapshot(eth, base="ETH")
    positions = [
        holding(SPOT, quantity="1"),
        holding(),
        {**holding(eth, quantity="1"), "instrument": eth_snap["instrument"]},
    ]
    result = analyze_portfolio(
        account(positions),
        {SPOT: snapshot(SPOT), SWAP: snapshot(), eth: eth_snap},
        as_of_ms=NOW,
        scenarios=[PriceShock("Custom", D(-5), {"BTC": D(-10)}, {SWAP: D(15)})],
    )
    rows = {row["inst_id"]: row for row in result["scenarios"][0]["positions"]}
    assert rows[SPOT]["shock_origin"] == "asset" and D(rows[SPOT]["shocked_mark"]) == 90
    assert rows[SWAP]["shock_origin"] == "market" and D(rows[SWAP]["shocked_mark"]) == 115
    assert rows[eth]["shock_origin"] == "parallel" and D(rows[eth]["shocked_mark"]) == 95
    shares = {row["asset"]: D(row["gross_share_pct"]) for row in result["assets"]}
    with localcontext(ACCOUNTING_CONTEXT):
        assert shares["BTC"] == D(2) / 3 * 100
        assert D(result["summary"]["concentration_hhi"]) == (D(2) / 3) ** 2 + (D(1) / 3) ** 2


def test_missing_current_mark_never_uses_account_cached_mark_or_zero():
    positions = [holding(SPOT, quantity="1"), holding(mark="123456", market_value="123456")]
    result = analyze(account(positions), {SPOT: snapshot(SPOT)})
    assert result["status"] == "partial"
    assert result["summary"]["gross_notional"] is None
    assert result["summary"]["net_notional"] is None
    assert result["summary"]["equity"] is None
    assert result["summary"]["concentration_hhi"] is None
    assert result["assets"][0]["gross_notional"] is None
    assert result["positions"][1]["mark"] is None
    assert result["scenarios"][0]["pre_liquidation_equity"] is None
    assert result["scenarios"][0]["liquidations"] is None
    assert result["summary"]["spot_value"] == "100"


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"mark": None}, "invalid_mark_or_units"),
        ({"mark": "0"}, "invalid_mark_or_units"),
        ({"mark": "NaN"}, "invalid_mark_or_units"),
        ({"source": "okx"}, "snapshot_identity"),
        ({"inst_id": "ETH-USDT-SWAP"}, "snapshot_identity"),
    ],
)
def test_missing_invalid_or_foreign_perp_mark_is_unavailable(overrides, code):
    result = analyze(snapshots={SWAP: snapshot(**overrides)})
    assert result["status"] == "unavailable"
    assert result["summary"]["equity"] is None
    assert result["positions"][0]["exposure_status"] == "unavailable"
    assert code in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("offset,code", [(-15001, "stale_mark"), (5001, "future_mark")])
def test_real_source_rejects_stale_future_independent_marks_even_with_fresh_last(offset, code):
    snap = snapshot(source="okx", mark_ts=NOW + offset)
    result = analyze(account([holding()], source="okx"), {SWAP: snap})
    assert result["summary"]["equity"] is None
    assert code in {issue["code"] for issue in result["issues"]}


def test_synthetic_time_is_disclosed_and_does_not_inherit_live_age_rules():
    result = analyze(snapshots={SWAP: snapshot(ts=1)})
    assert result["status"] == "available"
    assert result["positions"][0]["mark_ts"] == 1
    assert "Synthetic" in result["assumptions"]["example"]


@pytest.mark.parametrize(
    "bad_tiers",
    [
        [],
        None,
        [
            {
                "tier": 1,
                "min_size": "1",
                "max_size": "1000",
                "mmr": ".004",
                "imr": ".01",
                "max_leverage": "100",
            }
        ],
    ],
)
def test_missing_or_invalid_tiers_preserve_exposure_but_never_claim_safety(bad_tiers):
    result = analyze(snapshots={SWAP: snapshot(margin_tiers=bad_tiers)})
    assert result["status"] == "partial"
    assert D(result["summary"]["gross_notional"]) == 100
    assert D(result["summary"]["equity"]) == 1010
    assert result["summary"]["maintenance_margin"] is None
    assert result["positions"][0]["risk_status"] == "unavailable"
    assert result["positions"][0]["maintenance_breach"] is None
    assert result["scenarios"][0]["pre_liquidation_equity"] == "1010"
    assert result["scenarios"][0]["post_full_liquidation_equity"] is None
    assert result["scenarios"][0]["liquidations"] is None


@pytest.mark.parametrize("quantity,tier", [("1000", 1), ("1000.01", 2), ("-1000", 1)])
def test_explicit_tier_boundary_convention(quantity, tier):
    result = analyze(account([holding(quantity=quantity, margin="1000")]))
    assert result["positions"][0]["tier"] == tier
    assert "max inclusive" in result["assumptions"]["tier_boundary"]


def test_tier_snapshot_identity_or_staleness_cannot_be_silently_ignored():
    snap = snapshot(source="okx")
    snap["margin_tiers_snapshot"] = {
        "source": "example",
        "inst_id": SWAP,
        "td_mode": "isolated",
        "unit": "contracts",
        "observed_at": NOW,
        "tiers": snap["margin_tiers"],
    }
    result = analyze(account([holding()], source="okx"), {SWAP: snap})
    assert result["positions"][0]["risk_status"] == "unavailable"
    snap["margin_tiers_snapshot"]["source"] = "okx"
    snap["margin_tiers_snapshot"]["observed_at"] = NOW - 86400001
    result = analyze(account([holding()], source="okx"), {SWAP: snap})
    assert result["positions"][0]["risk_status"] == "unavailable"


def test_known_due_unreconciled_funding_invalidates_margin_safety_only():
    p = holding()
    p["instrument"]["expected_funding_time"] = NOW
    result = analyze(account([p]))
    assert D(result["summary"]["gross_notional"]) == 100
    assert D(result["summary"]["equity"]) == 1010
    assert result["positions"][0]["risk_status"] == "unavailable"
    assert result["positions"][0]["bankrupt"] is None
    assert result["scenarios"][0]["post_full_liquidation_equity"] is None
    assert "funding_due_unreconciled" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize(
    "key,new_value",
    [
        ("ct_val", ".1"),
        ("ct_mult", "2"),
        ("ct_val_ccy", "ETH"),
        ("settle_ccy", "USDC"),
        ("base", "ETH"),
        ("ct_type", "inverse"),
    ],
)
def test_held_contract_unit_changes_fail_closed(key, new_value):
    snap = snapshot()
    snap["instrument"][key] = new_value
    result = analyze(snapshots={SWAP: snap})
    assert result["summary"]["gross_notional"] is None
    assert result["positions"][0]["mark"] is None


def test_untradeable_but_unit_stable_instrument_retains_exposure():
    snap = snapshot()
    snap["instrument"]["state"] = "suspend"
    result = analyze(snapshots={SWAP: snap})
    assert result["positions"][0]["tradable"] is False
    assert D(result["positions"][0]["gross_notional"]) == 100


def test_nonpositive_equity_has_no_infinite_or_negative_leverage_ratio():
    result = analyze(account([holding(quantity="-100", margin="1")], cash="0", debt="10"))
    assert D(result["summary"]["equity"]) == -9
    assert result["summary"]["gross_leverage"] is None
    assert result["summary"]["net_leverage"] is None
    assert result["summary"]["leverage_reason"] == "positive_complete_equity_required"


def test_maintenance_fee_reserve_equality_triggers_full_liquidation():
    result = analyze(account([holding(margin="1")]), fee_bps=D(10), liquidation_fee_bps=D(50))
    p = result["positions"][0]
    assert D(p["maintenance_margin"]) == D(".4")
    assert D(p["close_fee_reserve"]) == D(".6")
    assert p["maintenance_breach"] is True
    assert result["scenarios"][0]["liquidations"] == 1
    assert D(result["scenarios"][0]["post_cash"]) == D("1000.4")


def test_missing_liability_or_cash_makes_complete_equity_unavailable():
    acct = account([holding()])
    del acct["insurance_debt"]
    result = analyze(acct)
    assert result["summary"]["insurance_liability"] is None
    assert result["summary"]["equity"] is None
    assert result["scenarios"][0]["post_full_liquidation_equity"] is None


def test_empty_portfolio_and_reserved_cash_have_true_zero_exposure():
    result = analyze(account([], cash="100", reserved="110"), {})
    assert result["status"] == "available"
    assert result["summary"]["gross_notional"] == "0"
    assert result["summary"]["equity"] == "100"
    assert result["summary"]["available_cash"] == "-10"
    assert result["summary"]["concentration_hhi"] is None
    assert result["scenarios"][0]["post_full_liquidation_equity"] == "100"


def test_duplicate_positions_are_not_double_counted():
    result = analyze(account([holding(), holding()]))
    assert result["summary"]["gross_notional"] is None
    assert result["summary"]["equity"] is None
    assert "duplicate_position" in {item["code"] for item in result["issues"]}


def test_snapshot_capture_replay_hash_and_no_mutation():
    acct, snaps = account([holding()]), {SWAP: snapshot()}
    before = copy.deepcopy((acct, snaps))
    result = analyze(acct, snaps)
    assert replay_portfolio_snapshot(result["input_snapshot"]) == result
    assert (acct, snaps) == before
    json.dumps(result, allow_nan=False)
    modified = copy.deepcopy(result["input_snapshot"])
    modified["snapshots"][SWAP]["mark"] = "101"
    assert (
        replay_portfolio_snapshot(modified)["captured_scenario"]["input_hash"]
        != result["captured_scenario"]["input_hash"]
    )


def test_caller_precision_rounding_traps_and_flags_are_unchanged():
    reference = analyze()
    caller = getcontext().copy()
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.Emin = -9
        context.Emax = 9
        context.flags[Inexact] = True
        expected = context.copy()
        actual = analyze()
        assert actual == reference
        assert context.prec == expected.prec and context.rounding == expected.rounding
        assert context.traps == expected.traps and context.flags == expected.flags
        assert context.Emin == -9 and context.Emax == 9
    assert getcontext().prec == caller.prec and getcontext().flags == caller.flags


@pytest.mark.parametrize("change", ["-100", "-101", "1000.01", "NaN", "Infinity"])
def test_invalid_price_shock_fails_closed(change):
    with pytest.raises(PortfolioAnalyticsError):
        PriceShock("Invalid", D(change))


def test_work_and_representation_budgets_fail_closed():
    with pytest.raises(PortfolioAnalyticsError, match="1–25"):
        analyze_portfolio(account(), {}, scenarios=[PriceShock(str(i)) for i in range(MAX_SCENARIOS + 1)])
    with pytest.raises(PortfolioAnalyticsError, match="500"):
        analyze(account([holding()] * (MAX_POSITIONS + 1)))
    with pytest.raises(PortfolioAnalyticsError, match="unique"):
        analyze_portfolio(account(), {}, scenarios=[PriceShock("Same"), PriceShock("Same")])
    result = analyze(account([holding(quantity="1e30")]))
    assert result["positions"][0]["exposure_status"] == "unavailable"
    result = analyze(snapshots={SWAP: snapshot(mark="1e-1000000")})
    assert result["positions"][0]["mark"] is None


@given(
    spot_qty=st.integers(1, 10000),
    price=st.integers(1, 100000),
    change=st.integers(-99, 300),
    cash=st.integers(0, 1000000),
)
@settings(max_examples=50, deadline=None)
def test_unlevered_spot_scenario_equity_equals_cash_plus_shocked_inventory(spot_qty, price, change, cash):
    p = holding(SPOT, quantity=str(spot_qty), entry=str(price))
    result = analyze_portfolio(
        account([p], cash=str(cash)),
        {SPOT: snapshot(SPOT, mark=str(price))},
        as_of_ms=NOW,
        scenarios=[PriceShock("Stress", D(change))],
    )
    with localcontext(ACCOUNTING_CONTEXT):
        expected = D(cash) + D(spot_qty) * D(price) * (1 + D(change) / 100)
        stress = result["scenarios"][0]
        assert D(stress["pre_liquidation_equity"]) == expected
        assert D(stress["post_full_liquidation_equity"]) == expected
        assert stress["liquidations"] == 0 and stress["bankruptcies"] == 0


@given(quantity=st.integers(1, 10000), change=st.integers(-99, 300), short=st.booleans())
@settings(max_examples=50, deadline=None)
def test_isolated_settlement_conserves_equity_less_explicit_costs(quantity, change, short):
    signed = -quantity if short else quantity
    p = holding(quantity=str(signed), margin="10")
    result = analyze_portfolio(
        account([p]),
        {SWAP: snapshot()},
        as_of_ms=NOW,
        fee_bps=D(10),
        liquidation_fee_bps=D(50),
        scenarios=[PriceShock("Stress", D(change))],
    )
    stress = result["scenarios"][0]
    with localcontext(ACCOUNTING_CONTEXT):
        assert D(stress["post_full_liquidation_equity"]) == D(stress["pre_liquidation_equity"]) - D(
            stress["liquidation_costs"]
        )
        assert D(stress["post_cash"]) >= 1000
        assert D(stress["incremental_liability"]) >= 0


def test_known_funding_boundary_between_last_snapshot_and_capture_is_not_safe():
    p = holding()
    p["instrument"]["expected_funding_time"] = NOW - 1000
    snap = snapshot(source="okx", ts=NOW - 2000)
    result = analyze(account([p], source="okx"), {SWAP: snap})
    assert result["positions"][0]["exposure_status"] == "available"
    assert result["positions"][0]["risk_status"] == "unavailable"
    assert "funding_due_unreconciled" in {item["code"] for item in result["issues"]}


def test_current_tick_is_used_for_hypothetical_liquidation_not_held_tick():
    snap = snapshot()
    snap["instrument"]["tick_size"] = ".5"
    result = analyze_portfolio(
        account([holding(quantity="-100", margin="10")]),
        {SWAP: snap},
        as_of_ms=NOW,
        slippage_bps=D(25),
        scenarios=[PriceShock("Up", D(20))],
    )
    row = result["scenarios"][0]["positions"][0]
    assert D(row["execution_price"]) == D("120.5")


def test_stale_snapshot_cannot_borrow_a_fresh_mark_identity():
    result = analyze(
        account([holding()], source="okx"), {SWAP: snapshot(source="okx", ts=NOW - 15001, mark_ts=NOW)}
    )
    assert result["positions"][0]["mark"] is None
    assert "stale_snapshot" in {issue["code"] for issue in result["issues"]}


def test_bankruptcy_remains_known_without_tiers_but_liquidation_count_is_unknown():
    result = analyze_portfolio(
        account([holding(quantity="-100", margin="10")]),
        {SWAP: snapshot(margin_tiers=[])},
        as_of_ms=NOW,
        scenarios=[PriceShock("Up", D(20))],
    )
    scenario = result["scenarios"][0]
    assert scenario["bankruptcies"] == 1
    assert scenario["liquidations"] is None
    assert scenario["pre_liquidation_equity"] == "990"
    assert scenario["post_full_liquidation_equity"] is None


def test_aggregate_numeric_domain_overflow_is_unavailable_not_invented_zero():
    symbols = ["BTC-USDT", "ETH-USDT"]
    snaps = {symbol: snapshot(symbol, base=symbol.split("-")[0], mark="9e29") for symbol in symbols}
    rows = []
    for symbol in symbols:
        rows.append({**holding(symbol, quantity="1"), "instrument": snaps[symbol]["instrument"]})
    result = analyze(account(rows), snaps)
    assert all(position["exposure_status"] == "available" for position in result["positions"])
    assert result["summary"]["gross_notional"] is None
    assert result["summary"]["spot_value"] is None
    assert result["summary"]["equity"] is None
    assert result["scenarios"][0]["pre_liquidation_equity"] is None
    assert "accounting_domain" in {issue["code"] for issue in result["issues"]}


def test_scenario_value_outside_numeric_domain_is_explicitly_unavailable():
    result = analyze_portfolio(
        account([holding(SPOT, quantity="1")]),
        {SPOT: snapshot(SPOT, mark="9e29")},
        as_of_ms=NOW,
        scenarios=[PriceShock("Beyond domain", D(100))],
    )
    assert result["summary"]["equity"] is not None
    assert result["scenarios"][0]["status"] == "unavailable"
    assert result["scenarios"][0]["pre_liquidation_equity"] is None
    assert result["scenarios"][0]["post_full_liquidation_equity"] is None


def test_shock_mappings_are_copied_and_immutable():
    assets = {"BTC": D(-10)}
    shock = PriceShock("Immutable", asset_pct=assets)
    assets["BTC"] = D(100)
    assert shock.asset_pct["BTC"] == D(-10)
    with pytest.raises(TypeError):
        shock.asset_pct["BTC"] = D(100)


def test_complete_tier_snapshot_can_supply_tiers_without_inline_list():
    snap = snapshot(source="okx")
    tiers = snap.pop("margin_tiers")
    snap["margin_tiers_snapshot"] = {
        "source": "okx",
        "inst_id": SWAP,
        "td_mode": "isolated",
        "unit": "contracts",
        "observed_at": NOW,
        "tiers": tiers,
    }
    result = analyze(account([holding()], source="okx"), {SWAP: snap})
    assert result["positions"][0]["risk_status"] == "available"


def test_fractional_tier_identifier_is_rejected_instead_of_truncated():
    snap = snapshot()
    snap["margin_tiers"][0]["tier"] = D("1.9")
    result = analyze(snapshots={SWAP: snap})
    assert result["positions"][0]["risk_status"] == "unavailable"


def test_exact_spot_basis_replaces_rounded_average_entry_for_unrealized_pnl():
    # Dividing this stored basis by three cannot be represented exactly at 50 digits.
    entry = "0." + "3" * 50
    result = analyze(
        account([holding(SPOT, quantity="3", entry=entry, basis="1")]), {SPOT: snapshot(SPOT, mark="1")}
    )
    assert result["positions"][0]["unrealized_pnl"] == "2"
    assert result["positions"][0]["unrealized_pnl_reason"] is None
    assert result["summary"]["equity"] == "1003"


def test_absent_spot_basis_does_not_invent_pnl_or_prevent_price_risk_analysis():
    result = analyze(account([holding(SPOT, quantity="3")]), {SPOT: snapshot(SPOT)})
    assert result["status"] == "available"
    assert result["positions"][0]["unrealized_pnl"] is None
    assert result["positions"][0]["unrealized_pnl_reason"] == "exact_spot_cost_basis_not_supplied"
    assert result["summary"]["equity"] == "1300"
    assert result["scenarios"][0]["pre_liquidation_equity"] == "1300"
