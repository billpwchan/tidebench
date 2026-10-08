"""Causal portfolio weights shared by historical and forward controllers."""

from decimal import Decimal, localcontext

from .engine import ACCOUNTING_CONTEXT

D = Decimal


def construction_weights(config, legs, signals, positions, momentum, past_funding_rate):
    """Inputs are already cut at the confirmed decision bar by the caller.

    ``None`` signals retain the economic position direction, never an invented
    previous signal. Funding is the most recent *strictly prior* settlement.
    Momentum tie-breaking is stable by symbol, as in historical research.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        weights = {leg["inst_id"]: D(str(leg["weight"])) for leg in legs}
        mode = config["mode"]
        if mode == "independent_signals":
            weights = {
                symbol: abs(weight)
                * (
                    D(str(signals[symbol]))
                    if signals[symbol] is not None
                    else (
                        D(1)
                        if D(str(positions.get(symbol, 0))) > 0
                        else D(-1)
                        if D(str(positions.get(symbol, 0))) < 0
                        else D(0)
                    )
                )
                for symbol, weight in weights.items()
            }
        elif mode == "momentum":
            ranks = sorted(
                ((D(str(value)), symbol) for symbol, value in momentum.items() if value is not None),
                reverse=True,
            )
            chosen = {symbol for value, symbol in ranks[: config["top_k"]] if value > 0}
            weights = {symbol: weight if symbol in chosen else D(0) for symbol, weight in weights.items()}
        elif mode == "funding_carry":
            if past_funding_rate is None or D(str(past_funding_rate)) < D(str(config["carry_threshold"])):
                weights = dict.fromkeys(weights, D(0))
        return weights


def apply_weight_caps(mode, weights, caps, capital, exits):
    """Keep matched carry notionals coupled under an asymmetric leg loss cap.

    Caps are amounts under the caller's declared account loss budget. They
    never grant capital. Carry shares its tightest scale across both legs;
    unrelated signal sleeves keep their own caps. Lot rounding comes later.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        if capital <= 0 or mode == "funding_carry" and exits:
            return dict.fromkeys(weights, D(0))
        if mode == "funding_carry":
            scale = min(
                [D(1)]
                + [caps[s] / (abs(w) * capital) for s, w in weights.items() if w and caps.get(s) is not None]
            )
            return {s: w * scale for s, w in weights.items()}
        return {
            s: D(0)
            if s in exits
            else (1 if w >= 0 else -1) * min(abs(w), caps[s] / capital)
            if caps.get(s) is not None
            else w
            for s, w in weights.items()
        }
