"""Pure shared-capital target sizing for historical and managed paper portfolios.

No account mutation or freshness authority lives here. Callers persist the plan
and submit it through the transactional book, which remains the risk authority.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .pro_execution import base_size, number

D = Decimal


def _quote(quotes, symbol):
    if symbol not in quotes:
        raise PlatformError("portfolio_quote_missing", "Every target leg requires a captured quote.", 409)
    quote = quotes[symbol]
    if number(quote["last"]) <= 0:
        raise PlatformError("portfolio_quote_invalid", "Target sizing needs positive prices.", 422)
    return quote, quote["instrument"]


def target_quantities(weights, capital, quotes):
    with localcontext(ACCOUNTING_CONTEXT):
        capital = max(D(0), number(capital))
        result = {}
        for symbol, raw_weight in sorted(weights.items()):
            quote, meta = _quote(quotes, symbol)
            weight, lot = number(raw_weight), number(meta["lot_size"])
            if meta["inst_type"] == "SPOT" and weight < 0:
                raise PlatformError("portfolio_spot_short", "Spot target weights cannot be negative.", 422)
            quantity = (
                capital * abs(weight) / (base_size(meta) * number(quote["last"])) / lot
            ).to_integral_value(rounding=ROUND_FLOOR) * lot
            result[symbol] = quantity if weight >= 0 else -quantity
        return result


def reduction_quantities(targets, positions):
    """Reduce only the declared universe, releasing cash before additions."""
    with localcontext(ACCOUNTING_CONTEXT):
        result = {}
        for symbol, desired in sorted(targets.items()):
            old = number(positions.get(symbol, 0))
            desired = number(desired)
            quantity = -old if desired * old <= 0 else desired - old if abs(desired) < abs(old) else D(0)
            if quantity:
                result[symbol] = quantity
        return result


@dataclass(frozen=True)
class AdditionPlan:
    cash_scale: Decimal
    required_cash: Decimal
    quantities: dict[str, Decimal]
    requested: dict[str, Decimal]
    skipped: list[dict]


def addition_plan(targets, positions, quotes, available_cash, leverage, fee_bps, slippage_bps):
    """One common scale using post-reduction cash and adverse rounded fills.

    Persist this immutable quantity plan before submitting any addition. A
    later risk rejection is a real leg failure, not permission to resize a
    previously committed command on retry.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        fee, slip = number(fee_bps) / 10000, number(slippage_bps) / 10000
        if not 0 <= fee <= D(".01") or not 0 <= slip <= D(".01"):
            raise PlatformError("portfolio_costs", "Costs must be within 0–100 bps.", 422)
        requested, required = {}, D(0)
        for symbol, desired in sorted(targets.items()):
            old, desired = number(positions.get(symbol, 0)), number(desired)
            if not desired or old and (old * desired < 0 or abs(desired) <= abs(old)):
                continue
            quantity = desired - old
            quote, meta = _quote(quotes, symbol)
            lev = number(leverage[symbol])
            if lev < 1 or lev > 50 or meta["inst_type"] == "SPOT" and lev != 1:
                raise PlatformError("portfolio_leverage", "Unsupported leg leverage.", 422)
            price = number(quote["ask"] if quantity > 0 else quote["bid"]) * (
                1 + slip if quantity > 0 else 1 - slip
            )
            tick = number(meta["tick_size"])
            price = (price / tick).to_integral_value(
                rounding=ROUND_CEILING if quantity > 0 else ROUND_FLOOR
            ) * tick
            if price <= 0:
                raise PlatformError(
                    "portfolio_quote_invalid", "Execution sizing needs positive rounded prices.", 422
                )
            requested[symbol] = quantity
            required += abs(quantity) * base_size(meta) * price * (1 / lev + fee)
        scale = min(D(1), max(D(0), number(available_cash) / required)) if required else D(1)
        quantities, skipped = {}, []
        for symbol, quantity in requested.items():
            _, meta = _quote(quotes, symbol)
            lot = number(meta["lot_size"])
            size = (abs(quantity) * scale / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
            if size >= number(meta["min_size"]):
                quantities[symbol] = size if quantity > 0 else -size
            else:
                skipped.append(
                    {
                        "inst_id": symbol,
                        "code": "minimum_size",
                        "requested_quantity": str(abs(quantity)),
                        "scaled_quantity": str(size),
                        "minimum_size": str(meta["min_size"]),
                        "cash_scale": str(scale),
                        "message": "Common cash scaling and lot rounding leave this leg below minimum size.",
                    }
                )
        return AdditionPlan(scale, required, quantities, requested, skipped)
