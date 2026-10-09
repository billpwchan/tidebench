"""Cost/funding identities and causal passive comparisons on real simulated fills."""

import copy
from decimal import Decimal as D

from test_portfolio_research import HOUR, START, failure_fixture
from test_pro_execution import snapshot
from tidebench.portfolio_research import simulate_portfolio
from tidebench.research_economics import explain_portfolio


def test_filled_cash_compensation_costs_remain_in_reconciled_economics():
    config, manifest, legs = failure_fixture(weight=".2", fee_bps=10, slippage_bps=5)
    result = simulate_portfolio(config, manifest, legs)
    report = explain_portfolio(result, config, legs, manifest)
    assert report["status"] == "reconciled"
    assert D(report["net_pnl"]) < 0
    assert D(report["fees_pnl"]) < 0 and D(report["modeled_quote_shortfall_pnl"]) < 0
    assert D(report["price_pnl_before_recorded_quote_shortfall"]) == 0
    assert D(report["net_pnl"]) == D(report["fees_pnl"]) + D(report["modeled_quote_shortfall_pnl"])
    assert report["market_contributions"]["reconciled"]
    assert report["edge_status"] == "not_established"
    passive = report["passive_reference"]
    assert passive["status"] == "available"
    assert passive["entry_ts"] == START + HOUR
    assert D(passive["equity"][0]["equity"]) == 10000
    assert report["exposure_comparison"]["risk_matched"] is False
    assert report["beta_diagnostic"]["status"] == "unavailable"


def test_funding_income_is_separate_from_price_and_fees_not_deducted_twice():
    config, manifest, legs = failure_fixture(weight=".2", fee_bps=0, slippage_bps=0)
    symbol = "ETH-USDT-SWAP"
    meta = snapshot(symbol)["instrument"]
    meta.update(base="ETH", ct_val_ccy="ETH")
    legs[1]["instrument"] = meta
    legs[1]["tiers"] = snapshot(symbol)["margin_tiers"]
    legs[1]["funding"] = [
        {
            "ts": START + 2 * HOUR,
            "rate": "-.01",
            "mark_price": "100",
            "mark_ts": START + 2 * HOUR,
            "inst_id": symbol,
        }
    ]
    result = simulate_portfolio(config, manifest, legs)
    report = explain_portfolio(result, config, legs, manifest)
    assert report["status"] == "reconciled"
    assert D(report["funding_pnl"]) > 0
    assert D(report["gross_price_pnl_after_modeled_execution"]) == 0
    assert D(report["net_pnl"]) == D(report["funding_pnl"])
    assert report["market_contributions"]["reconciled"]
    swap = next(r for r in report["market_contributions"]["markets"] if r["inst_id"] == symbol)
    assert D(swap["funding_pnl"]) == D(report["funding_pnl"])
    assert "perpetual_trade_price_proxy" in [
        e["price_source"] for e in report["passive_reference"]["entries"]
    ]


def test_no_quote_evidence_means_no_invented_shortfall_and_lifecycle_no_static_proxy():
    config, manifest, legs = failure_fixture(weight=".2", fee_bps=10, slippage_bps=5)
    result = simulate_portfolio(config, manifest, legs)
    result = copy.deepcopy(result)
    for fill in result["orders"]:
        fill.pop("quote_bid", None)
    report = explain_portfolio(result, config, legs, manifest)
    assert report["status"] == "reconciled"
    assert report["modeled_quote_shortfall_pnl"] is None
    assert report["price_pnl_before_recorded_quote_shortfall"] is None
    report = explain_portfolio(result, config | {"universe_mode": "historical_lifecycle"}, legs, manifest)
    assert report["passive_reference"]["status"] == "unavailable"
    assert report["excess_return_vs_passive_pct"] is None
    result["final_account"]["equity"] = None
    assert explain_portfolio(result, config, legs, manifest)["status"] == "incomplete"
