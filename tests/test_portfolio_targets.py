"""Shared-capital quantities, opposite signs, spread/ticks and refusal bounds."""

from decimal import Decimal, localcontext

import pytest
from tidebench.platform import PlatformError
from tidebench.portfolio_targets import addition_plan, reduction_quantities, target_quantities

D = Decimal


def quotes():
    return {
        "BTC-USDT": {
            "last": "100",
            "bid": "99",
            "ask": "101",
            "instrument": {
                "inst_id": "BTC-USDT",
                "inst_type": "SPOT",
                "base": "BTC",
                "quote": "USDT",
                "tick_size": ".1",
                "lot_size": ".01",
                "min_size": ".01",
            },
        },
        "ETH-USDT-SWAP": {
            "last": "100",
            "bid": "99",
            "ask": "101",
            "instrument": {
                "inst_id": "ETH-USDT-SWAP",
                "inst_type": "SWAP",
                "base": "ETH",
                "quote": "USDT",
                "ct_val": ".01",
                "ct_mult": "1",
                "ct_val_ccy": "ETH",
                "settle_ccy": "USDT",
                "tick_size": ".1",
                "lot_size": ".1",
                "min_size": ".1",
            },
        },
    }


def test_targets_use_contract_units_and_reductions_preserve_unowned_markets():
    target = target_quantities({"BTC-USDT": ".5", "ETH-USDT-SWAP": "-.5"}, 10000, quotes())
    assert target == {"BTC-USDT": D(50), "ETH-USDT-SWAP": D(-5000)}
    assert reduction_quantities(target, {"BTC-USDT": 60, "ETH-USDT-SWAP": 10, "OTHER-USDT": 100}) == {
        "BTC-USDT": D(-10),
        "ETH-USDT-SWAP": D(-10),
    }
    assert reduction_quantities({"BTC-USDT": 0}, {"BTC-USDT": 5}) == {"BTC-USDT": D(-5)}


def test_one_cash_scale_is_order_independent_and_uses_adverse_bid_ask_ticks():
    q = quotes()
    weights = {"BTC-USDT": ".6", "ETH-USDT-SWAP": "-.6"}
    targets = target_quantities(weights, 10000, q)
    args = (q, D(1000), {"BTC-USDT": 1, "ETH-USDT-SWAP": 5}, 10, 5)
    plan = addition_plan(targets, {}, *args)
    # Buy ask 101, adverse rounded 101.1; sell bid 99, adverse rounded 98.9.
    required = D(60) * D("101.1") * D("1.001") + D(6000) * D(".01") * D("98.9") * D(".201")
    assert plan.required_cash == required
    assert 0 < plan.cash_scale < 1
    assert plan == addition_plan(dict(reversed(list(targets.items()))), {}, *args)
    with localcontext() as caller:
        caller.prec = 8
        assert plan == addition_plan(targets, {}, *args)


def test_small_legs_are_explained_and_opposite_inventory_must_be_reduced_first():
    q = quotes()
    plan = addition_plan({"BTC-USDT": D(".01")}, {}, q, 0, {"BTC-USDT": 1}, 10, 5)
    assert not plan.quantities and plan.skipped[0]["code"] == "minimum_size"
    assert plan.skipped[0]["scaled_quantity"] == "0.00"
    assert not addition_plan(
        {"ETH-USDT-SWAP": -10}, {"ETH-USDT-SWAP": 5}, q, 1000, {"ETH-USDT-SWAP": 5}, 10, 5
    ).requested
    with pytest.raises(PlatformError, match="captured quote"):
        target_quantities({"MISSING-USDT": 1}, 10000, q)
    with pytest.raises(PlatformError, match="negative"):
        target_quantities({"BTC-USDT": -1}, 10000, q)


@pytest.mark.parametrize("sign", [D(1), D(-1)])
def test_minimum_rebalances_defer_without_suppressing_exits_or_funding_units(sign):
    from tidebench.portfolio_targets import reduction_plan

    q = quotes()
    symbol = "ETH-USDT-SWAP"
    q[symbol]["instrument"]["min_size"] = "1"
    old = 10 * sign
    smaller, larger = D("9.5") * sign, D("10.5") * sign
    reductions = reduction_plan({symbol: smaller}, {symbol: old}, q)
    assert not reductions.quantities
    assert reductions.skipped[0]["code"] == "rebalance_minimum"
    additions = addition_plan({symbol: larger}, {symbol: old}, q, 1000, {symbol: 2}, 10, 5)
    assert not additions.quantities and not additions.requested and additions.required_cash == 0
    assert additions.skipped[0]["code"] == "rebalance_minimum"
    assert reduction_plan({symbol: 0}, {symbol: old}, q).quantities == {symbol: -old}
    assert reduction_plan({symbol: -old}, {symbol: old}, q).quantities == {symbol: -old}
    # A valid decrease must not leave dust that a later exit cannot submit.
    assert not reduction_plan({symbol: D(".5") * sign}, {symbol: old}, q).quantities
    assert reduction_plan({symbol: 9 * sign}, {symbol: old}, q).quantities == {symbol: -sign}
    # New undersized exposure remains a hard failure. Same-side cash rounding
    # can retain actual inventory; the group independently enforces residual limits.
    initial = addition_plan({symbol: D(".5") * sign}, {}, q, 1000, {symbol: 2}, 10, 5)
    assert initial.skipped[0]["code"] == "minimum_size"
    shortfall = addition_plan({symbol: 12 * sign}, {symbol: old}, q, 0, {symbol: 2}, 10, 5)
    assert shortfall.skipped[0]["code"] == "rebalance_cash_rounding"
