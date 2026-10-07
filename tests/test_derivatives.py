from decimal import ROUND_DOWN, Decimal, Inexact, getcontext, localcontext

import pytest
from tidebench.derivatives import (
    FundingEvent,
    LinearContract,
    MarginTier,
    contract_base_quantity,
    contract_notional,
    funding_payment,
    isolated_equity,
    linear_pnl,
    liquidation_condition,
    liquidation_price,
    maintenance_margin,
    select_margin_tier,
    validate_margin_tiers,
)
from tidebench.engine import EngineError

D = Decimal


@pytest.fixture
def contract():
    return LinearContract(
        "BTC-USDT-SWAP", "BTC", "USDT", "USDT", D(".01"), D(1), "BTC", D(".1"), D(".01"), D(".01")
    )


@pytest.fixture
def tiers():
    return [
        MarginTier(1, D(0), D(1000), D(".01"), D(".004"), D(100)),
        MarginTier(2, D(1000), D(2000), D(".02"), D(".005"), D(50)),
    ]


def test_contract_units_and_signed_pnl(contract):
    assert contract_base_quantity(D("12.5"), contract) == D(".125")
    assert contract_notional(D("-12.5"), D(60000), contract) == D(7500)
    assert linear_pnl(D("12.5"), D(60000), D(61000), contract) == D(125)
    assert linear_pnl(D("-12.5"), D(60000), D(61000), contract) == D(-125)
    assert isolated_equity(D(1000), D("-12.5"), D(60000), D(61000), contract) == D(875)


@pytest.mark.parametrize("size,expected", [("0", 1), ("1000", 1), ("1000.01", 2), ("-2000", 2)])
def test_explicit_shared_tier_boundaries(tiers, size, expected):
    assert select_margin_tier(D(size), tiers).tier == expected


def test_unknown_tier_coverage_rejected(tiers):
    with pytest.raises(EngineError, match="coverage"):
        select_margin_tier(D("2000.01"), tiers)


@pytest.mark.parametrize(
    "invalid",
    [
        [MarginTier(1, D(1), D(1000), D(".01"), D(".004"), D(100))],
        [
            MarginTier(1, D(0), D(1000), D(".01"), D(".004"), D(100)),
            MarginTier(2, D(1001), D(2000), D(".02"), D(".005"), D(50)),
        ],
    ],
)
def test_tier_gaps_rejected(invalid):
    with pytest.raises(EngineError, match="contiguous"):
        validate_margin_tiers(invalid)


def test_maintenance_uses_mark_not_entry(contract, tiers):
    assert maintenance_margin(D(100), D(50000), contract, tiers[0]) == D(200)
    assert maintenance_margin(D(100), D(60000), contract, tiers[0]) == D(240)


def test_positive_funding_charges_long_credits_short(contract):
    assert funding_payment(D(100), D(60000), D(".001"), contract) == D(60)
    assert funding_payment(D(-100), D(60000), D(".001"), contract) == D(-60)
    assert funding_payment(D(100), D(60000), D("-.001"), contract) == D(-60)


@pytest.mark.parametrize("side", [1, -1])
def test_liquidation_threshold_solves_equity_requirement(contract, tiers, side):
    quantity, entry, margin, fee = D(side * 100), D(60000), D(6000), D(".0005")
    threshold = liquidation_price(margin, quantity, entry, contract, tiers[0], fee)
    # Finite context rounding can put the algebraic root a few ulps either side;
    # directional probes establish the economically meaningful boundary.
    adverse = threshold - D(".01") if side == 1 else threshold + D(".01")
    safe = threshold + D(".01") if side == 1 else threshold - D(".01")
    assert liquidation_condition(margin, quantity, entry, adverse, contract, tiers[0], fee)
    assert not liquidation_condition(margin, quantity, entry, safe, contract, tiers[0], fee)


def test_zero_position_no_liquidation(contract, tiers):
    assert liquidation_price(D(0), D(0), D(1), contract, tiers[0]) is None
    assert not liquidation_condition(D(0), D(0), D(1), D(1), contract, tiers[0])


def test_helpers_do_not_inherit_or_mutate_caller_context(contract, tiers):
    expected = liquidation_price(D(1000), D(100), D(60000), contract, tiers[0], D(".0005"))
    with localcontext() as context:
        context.prec, context.rounding, context.Emax, context.Emin = 6, ROUND_DOWN, 5, -5
        context.traps[Inexact] = True
        before = context.copy()
        assert liquidation_price(D(1000), D(100), D(60000), contract, tiers[0], D(".0005")) == expected
        assert getcontext().prec == before.prec
        assert getcontext().rounding == before.rounding
        assert getcontext().flags == before.flags
        assert getcontext().traps == before.traps
        assert getcontext().Emax == before.Emax


def test_catalog_and_funding_adapters(contract):
    data = {
        "inst_id": contract.inst_id,
        "base": "BTC",
        "quote": "USDT",
        "settle_ccy": "USDT",
        "ct_type": "linear",
        "inst_type": "SWAP",
        "ct_val": D(".01"),
        "ct_mult": D(1),
        "ct_val_ccy": "BTC",
        "tick_size": D(".1"),
        "lot_size": D(".01"),
        "min_size": D(".01"),
    }
    assert LinearContract.from_catalog(data) == contract
    assert FundingEvent.from_record({"ts": 123, "rate": D("-.001"), "mark_price": D(100)}) == FundingEvent(
        123, D("-.001"), D(100)
    )


@pytest.mark.parametrize(
    "field,value", [("ct_mult", D(2)), ("settle", "BTC"), ("ct_val_ccy", "USD"), ("tick_size", D(0))]
)
def test_unsupported_contract_semantics_rejected(contract, field, value):
    from dataclasses import asdict

    data = asdict(contract) | {field: value}
    with pytest.raises(EngineError):
        LinearContract(**data)


def test_numeric_domain_fails_closed(contract):
    with pytest.raises(EngineError, match="numeric domain"):
        contract_notional(D("1e29"), D("1e29"), contract)
