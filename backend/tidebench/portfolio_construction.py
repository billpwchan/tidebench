"""Causal portfolio weights shared by historical and forward controllers."""

from decimal import Decimal, localcontext

from .engine import ACCOUNTING_CONTEXT

D = Decimal


def construction_weights(
    config, legs, signals, positions, momentum, past_funding_rate, *, risk_evidence=None
):
    """Inputs are already cut at the confirmed decision bar by the caller.

    ``None`` signals retain the economic position direction, never an invented
    previous signal. Funding is the most recent *strictly prior* settlement.
    Momentum tie-breaking is stable by symbol, as in historical research.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        weights = {leg["inst_id"]: D(str(leg["weight"])) for leg in legs}
        mode = config["mode"]
        if mode == "risk_momentum":
            if risk_evidence is None:
                from .engine import EngineError

                raise EngineError("Risk momentum requires captured causal risk evidence.")
            return {s: D(str(w)) for s, w in risk_evidence["weights"].items()}
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


def funding_carry_evidence(config, history, decision_ts, fee_bps, slippage_bps):
    """Admission hurdle, not expected profit: constant-rate settlement scenario.

    All rates are realized strictly before the decision bar's opening boundary.
    Four fills per paired unit cover entry and exit on spot plus perpetual.
    Basis, borrowing and opportunity costs are not inferred from funding rates.
    Legacy zero-settlement definitions retain the explicitly ungated policy.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        window = config.get("carry_window", 1)
        past = sorted((e for e in history if int(e["ts"]) < decision_ts), key=lambda e: int(e["ts"]))
        selected = past[-window:]
        output = {
            "sample_count": len(selected),
            "required_samples": window,
            "settlement_times": [int(e["ts"]) for e in selected],
            "cost_gate_enabled": bool(config.get("carry_cost_settlements", 0)),
            "projection": "constant_lagged_mean_per_settlement_not_APR_or_profit_forecast",
        }
        if len(selected) < window:
            return output | {"allowed": False, "reason": "insufficient_settlements", "mean_rate": None}
        rate = sum((D(str(e["rate"])) for e in selected), D(0)) / window
        age = decision_ts - int(selected[-1]["ts"])
        cost = 4 * (D(str(fee_bps)) + D(str(slippage_bps)))
        gross = rate * config.get("carry_cost_settlements", 0) * 10000
        net = gross - cost - D(str(config.get("carry_buffer_bps", 0)))
        maximum_age = config.get("carry_max_age_hours", 0) * 3600000
        reason = (
            "stale_settlement"
            if maximum_age and age > maximum_age
            else "below_rate_threshold"
            if rate < D(str(config["carry_threshold"]))
            else "cost_hurdle"
            if output["cost_gate_enabled"] and net <= 0
            else "admitted_scenario"
            if output["cost_gate_enabled"]
            else "legacy_rate_only"
        )
        return output | {
            "allowed": reason in {"admitted_scenario", "legacy_rate_only"},
            "reason": reason,
            "mean_rate": str(rate),
            "age_ms": age,
            "round_trip_cost_bps": str(cost),
            "projected_funding_bps": str(gross),
            "net_hurdle_bps": str(net),
        }
