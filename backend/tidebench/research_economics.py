"""Reconciled monetary explanations and declared causal research references.

These are arithmetic contributions and descriptive comparisons, not a causal
alpha estimate or a second counterfactual execution/financing simulation.
"""

from decimal import Decimal, localcontext

from .catalog import CATALOG_BARS
from .contributions import exact_sum
from .engine import ACCOUNTING_CONTEXT, Instrument, _buy_quantity, _fill_price
from .pro_execution import base_size
from .store import encode

D = Decimal


def bar_value(bar, key):
    return bar[key] if isinstance(bar, dict) else getattr(bar, key)


def passive_reference(result, config, legs, manifest):
    if config.get("universe_mode", "static") != "static" or any(
        leg["config"].get("rule_events") for leg in legs
    ):
        return {
            "status": "unavailable",
            "reason": "A changing lifecycle requires a matching temporal passive holdings model; no static reference is substituted.",
        }
    equity = result.get("equity", [])
    if len(equity) < 2:
        return {"status": "unavailable", "reason": "Fewer than two valued research boundaries."}
    # Start at the first next-open boundary, independently of signal warmup.
    entry_ts = equity[0]["ts"]
    chosen, weights = {}, {}
    for leg in legs:
        meta, policy = leg["instrument"], leg["config"]
        asset = meta["base"]
        allocation = D(str(policy.get("strategy", {}).get("allocation", 1)))
        weight = min(abs(D(str(policy["weight"]))), allocation)
        weights[asset] = weights.get(asset, D(0)) + weight
        if asset not in chosen or meta["inst_type"] == "SPOT":
            chosen[asset] = leg
    total = sum(weights.values(), D(0))
    scale = min(D(1), 1 / total) if total else D(0)
    initial = D(config["initial_cash"])
    capital = initial * D(str(config.get("capital_pct", 100))) / 100
    fee, slip = D(str(config["fee_bps"])) / 10000, D(str(config["slippage_bps"])) / 10000
    cash, holdings, entries = initial, {}, []
    by_asset = {}
    for asset, leg in sorted(chosen.items()):
        meta = leg["instrument"]
        bars = {bar_value(c, "ts"): c for c in leg["candles"]}
        if entry_ts not in bars:
            return {"status": "unavailable", "reason": "A market has no exact next-open reference quote."}
        unit = base_size(meta)
        instrument = Instrument(
            meta["inst_id"],
            asset,
            "USDT",
            D(str(meta["tick_size"])),
            D(str(meta["lot_size"])) * unit,
            D(str(meta["min_size"])) * unit,
        )
        price = _fill_price(D(str(bar_value(bars[entry_ts], "open"))), "buy", instrument, slip)
        budget = capital * weights[asset] * scale
        quantity = _buy_quantity(budget, price, fee, instrument)
        if budget and not quantity:
            return {
                "status": "unavailable",
                "reason": "Declared passive budget leaves a market below its captured minimum size.",
            }
        paid_fee = quantity * price * fee
        cash -= quantity * price + paid_fee
        holdings[asset], by_asset[asset] = quantity, bars
        entries.append(
            {
                "base": asset,
                "price_market": meta["inst_id"],
                "price_source": "captured_spot"
                if meta["inst_type"] == "SPOT"
                else "perpetual_trade_price_proxy",
                "quantity_base": quantity,
                "entry_price": price,
                "entry_fee": paid_fee,
                "allocated_budget": budget,
                "weight_of_initial_account": budget / initial,
            }
        )
    interval = int(manifest.get("interval_ms") or CATALOG_BARS[manifest["bar"]])
    curve = []
    for point in equity:
        bar_ts = int(point["ts"]) - interval
        if bar_ts < entry_ts:
            curve.append({"ts": point["ts"], "equity": initial, "gross_notional": D(0)})
            continue
        if any(bar_ts not in bars for bars in by_asset.values()):
            return {"status": "unavailable", "reason": "No missing passive marks are forward-filled."}
        value = sum((holdings[a] * D(str(bar_value(by_asset[a][bar_ts], "close"))) for a in holdings), D(0))
        curve.append({"ts": point["ts"], "equity": cash + value, "gross_notional": value})
    final = curve[-1]["equity"]
    return encode(
        {
            "status": "available",
            "name": "Declared unlevered passive underlying reference",
            "entry_ts": entry_ts,
            "entries": entries,
            "cash": cash,
            "equity": curve,
            "final_equity": final,
            "return_pct": (final / initial - 1) * 100,
            "mean_gross_exposure_pct": sum(
                (p["gross_notional"] / p["equity"] * 100 for p in curve if p["equity"] > 0), D(0)
            )
            / len(curve),
            "execution": "One next-open buy per base asset, fee/slippage/tick/lot/minimum adjusted; constant quantity then captured trade-close marks, no terminal forced sale.",
            "scope": "Underlying-price reference, not risk-matched alpha. No funding, borrowing, yield or financing. A perpetual trade-price proxy is not independently observed spot or an executable funded swap. Child-order admission, impact and liquidation are not inferred for this reference.",
        }
    )


def market_contributions(result, legs):
    if result.get("lifecycle") or any(leg["config"].get("rule_events") for leg in legs):
        return {
            "status": "unavailable",
            "reason": "Lifecycle holdings need event-aware market attribution; account identity remains authoritative.",
        }
    final = {p["inst_id"]: p for p in result["final_account"]["positions"]}
    rows = []
    for leg in legs:
        symbol, meta = leg["instrument"]["inst_id"], leg["instrument"]
        quantity, entry, price_pnl, fees = D(0), D(0), D(0), D(0)
        for order in result.get("orders", []):
            if order["inst_id"] != symbol or order.get("status") != "filled":
                continue
            size, price = D(order["quantity"]), D(order["price"])
            signed = size if order["side"] == "buy" else -size
            fees += D(order["fee"])
            if meta["inst_type"] == "SPOT":
                price_pnl -= signed * price
            elif quantity and quantity * signed < 0:
                price_pnl += (
                    (price - entry) * size * base_size(order["instrument"]) * (1 if quantity > 0 else -1)
                )
            else:
                entry = (abs(quantity) * entry + size * price) / abs(quantity + signed)
            quantity += signed
        position = final.get(symbol)
        if position and position.get("mark") is None:
            return {"status": "unavailable", "reason": "A market has no complete terminal valuation."}
        if quantity:
            if not position or D(position["quantity"]) != quantity:
                return {
                    "status": "unreconciled",
                    "reason": "Filled quantities do not reconcile with the final market inventory.",
                }
            mark = D(position["mark"])
            price_pnl += (
                quantity * mark
                if meta["inst_type"] == "SPOT"
                else quantity * base_size(meta) * (mark - entry)
            )
        funding = sum((D(e["payment"]) for e in result.get("funding", []) if e["inst_id"] == symbol), D(0))
        rows.append(
            {
                "inst_id": symbol,
                "price_pnl_before_fees_and_funding": price_pnl,
                "fees_pnl": -fees,
                "funding_pnl": -funding,
                "net_pnl": exact_sum([price_pnl, -fees, -funding]),
            }
        )
    return encode(
        {
            "status": "available",
            "markets": rows,
            "net_pnl": exact_sum(D(row["net_pnl"]) for row in rows),
            "scope": "Actual filled quantities, weighted-average derivative entry and terminal independent marks. Arithmetic monetary contribution, not independent strategy returns.",
        }
    )


def explain_portfolio(result, config, legs, manifest):
    with localcontext(ACCOUNTING_CONTEXT):
        final, initial = result["final_account"], D(config["initial_cash"])
        if final.get("equity") is None:
            return {
                "status": "incomplete",
                "reason": "Unresolved lifecycle, funding or valuation prevents a complete economic explanation.",
                "cash_reference": {"return_pct": "0", "yield_assumption": "zero interest"},
                "edge_status": "not_established",
            }
        net = D(final["equity"]) - initial
        fees, funding = D(final["fees_paid"]), D(final["funding_paid"])
        gross_price = exact_sum([net, fees, funding])
        fills = [o for o in result.get("orders", []) if o.get("status") == "filled"]
        shortfall_complete = all(
            o.get("quote_bid") is not None and o.get("quote_ask") is not None for o in fills
        )
        shortfall = (
            sum(
                (
                    D(o["quantity"])
                    * base_size(o["instrument"])
                    * (D(o["price"]) - (D(str(o["quote_bid"])) + D(str(o["quote_ask"]))) / 2)
                    * (1 if o["side"] == "buy" else -1)
                    for o in fills
                ),
                D(0),
            )
            if shortfall_complete
            else None
        )
        before_quote_cost = exact_sum([gross_price, shortfall]) if shortfall is not None else None
        reconstructed = (
            exact_sum([before_quote_cost, -shortfall, -fees, -funding])
            if before_quote_cost is not None
            else exact_sum([gross_price, -fees, -funding])
        )
        delta = exact_sum([reconstructed, -net])
        reference = passive_reference(result, config, legs, manifest)
        descriptive = {
            "status": "unavailable",
            "reason": "At least 30 positive valued paired boundaries are required.",
        }
        if reference["status"] == "available":
            pairs = [
                (D(p["equity"]), D(q["equity"]))
                for p, q in zip(result["equity"], reference["equity"], strict=True)
                if p["equity"] is not None
            ]
            if (
                len(pairs) == len(result["equity"])
                and len(pairs) >= 31
                and all(a > 0 and b > 0 for a, b in pairs)
            ):
                returns = [
                    (a1 / a0 - 1, b1 / b0 - 1) for (a0, b0), (a1, b1) in zip(pairs, pairs[1:], strict=False)
                ]
                n = D(len(returns))
                ybar, xbar = sum((y for y, _ in returns), D(0)) / n, sum((x for _, x in returns), D(0)) / n
                variance = sum(((x - xbar) ** 2 for _, x in returns), D(0))
                if variance:
                    beta = sum(((x - xbar) * (y - ybar) for y, x in returns), D(0)) / variance
                    descriptive = encode(
                        {
                            "status": "descriptive_only",
                            "paired_returns": len(returns),
                            "sample_beta": beta,
                            "sample_intercept_per_bar": ybar - beta * xbar,
                            "scope": "Full-window ex-post OLS diagnostic, never used to size the reference or select parameters. No alpha significance, independent trials, risk matching or causal edge is established.",
                        }
                    )
        markets = market_contributions(result, legs)
        if markets["status"] == "available":
            markets["reconciliation_delta"] = str(exact_sum([D(markets["net_pnl"]), -net]))
            markets["reconciled"] = abs(D(markets["reconciliation_delta"])) <= max(abs(net), initial) * D(
                "1e-46"
            )
        return encode(
            {
                "schema_version": 1,
                "market_contributions": markets,
                "status": "reconciled"
                if abs(delta) <= max(abs(net), initial) * D("1e-46")
                else "unreconciled",
                "net_pnl": net,
                "gross_price_pnl_after_modeled_execution": gross_price,
                "price_pnl_before_recorded_quote_shortfall": before_quote_cost,
                "modeled_quote_shortfall_pnl": -shortfall if shortfall is not None else None,
                "fees_pnl": -fees,
                "funding_pnl": -funding,
                "reconciliation_delta": delta,
                "identity": "net = price before recorded quote shortfall − recorded quote shortfall − fees − funding paid; insurance liabilities are already reflected in equity and are not deducted twice",
                "attribution_scope": "Arithmetic for actual filled quantities, not a re-execution at alternative prices. Cost-driven sizing, stops and liquidation feedback are not an independent counterfactual.",
                "cash_reference": {"return_pct": "0", "yield_assumption": "zero interest"},
                "passive_reference": reference,
                "excess_return_vs_passive_pct": (D(final["equity"]) - D(reference["final_equity"]))
                / initial
                * 100
                if reference["status"] == "available"
                else None,
                "exposure_comparison": {
                    "strategy_mean_gross_pct": result["metrics"].get("mean_gross_exposure_pct"),
                    "passive_mean_gross_pct": reference.get("mean_gross_exposure_pct"),
                    "risk_matched": False,
                },
                "beta_diagnostic": descriptive,
                "edge_status": "not_established",
            }
        )
