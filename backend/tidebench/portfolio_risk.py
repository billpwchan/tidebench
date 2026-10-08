"""Causal, capped inverse-volatility rotation with a covariance risk governor.

This is not an equal-risk-contribution optimizer. Weight ceilings leave cash;
no full-sample calibration, covariance inversion, borrowing or leverage grant.
"""

from decimal import Decimal, localcontext

from .engine import ACCOUNTING_CONTEXT, EngineError

D = Decimal
YEAR_MS = D(365 * 86400000)


def _record(bar):
    return bar if isinstance(bar, dict) else bar.__dict__


def _vol(weights, covariance, symbols):
    variance = sum(
        (
            weights[s] * weights[t] * covariance[i][j]
            for i, s in enumerate(symbols)
            for j, t in enumerate(symbols)
        ),
        D(0),
    )
    return max(D(0), variance).sqrt()


def constrain_risk_weights(config, weights, evidence):
    """Reapply the governor after stop/loss caps (a removed hedge can raise risk).

    Target risk is for the allocated sleeve before fills. Lot residuals, gaps,
    drift and changing covariance can exceed it; it is no realized-risk bound.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        symbols = evidence["symbols"]
        model = [[D(v) for v in row] for row in evidence.get("covariance", [])]
        stress = [[D(v) for v in row] for row in evidence.get("stress_covariance", [])]
        output = {s: D(str(weights[s])) for s in symbols}
        if not model:
            return output, evidence
        annualizer = D(evidence["annualizer"])
        model_vol = _vol(output, model, symbols) * annualizer
        stress_vol = _vol(output, stress, symbols) * annualizer
        effective = max(model_vol, stress_vol)
        target = D(str(config["vol_target_pct"])) / 100
        scale = min(D(1), target / effective) if effective else D(1)
        output = {s: w * scale for s, w in output.items()}
        portfolio_vol = _vol(output, model, symbols) * annualizer
        # Euler contributions sum to modeled volatility; negatives are retained.
        marginal = [
            sum((model[i][j] * output[t] for j, t in enumerate(symbols)), D(0)) for i in range(len(symbols))
        ]
        contributions = {
            s: str(output[s] * marginal[i] * annualizer * annualizer / portfolio_vol * 100)
            if portfolio_vol
            else "0"
            for i, s in enumerate(symbols)
        }
        return output, evidence | {
            "weights": {s: str(w) for s, w in output.items()},
            "governor_scale": str(scale),
            "modeled_vol_pct": str(portfolio_vol * 100),
            "stressed_vol_pct": str(_vol(output, stress, symbols) * annualizer * 100),
            "cash_weight": str(1 - sum(output.values(), D(0))),
            "risk_contribution_pct": contributions,
            "risk_scope": "allocated_sleeve_before_execution_not_account_or_realized_risk_bound",
        }


def risk_momentum_weights(config, legs, bars_by_symbol, decision_ts, interval_ms):
    with localcontext(ACCOUNTING_CONTEXT):
        if interval_ms <= 0:
            raise EngineError("Risk model interval must be positive")
        symbols = sorted(leg["inst_id"] for leg in legs)
        ceilings = {leg["inst_id"]: D(str(leg["weight"])) for leg in legs}
        n, lookback = config["risk_window"], config["lookback"]
        size = max(n, lookback) + 1
        evidence = {
            "schema_version": 1,
            "symbols": symbols,
            "decision_bar_ts": decision_ts,
            "available_at": decision_ts + interval_ms,
            "required_returns": n,
            "weights": {s: "0" for s in symbols},
            "reason": "insufficient_history",
            "selected": [],
            "excluded": {},
            "cash_weight": "1",
        }
        closes = {}
        for symbol in symbols:
            records = [_record(bar) for bar in bars_by_symbol.get(symbol, [])]
            past = sorted(
                (bar for bar in records if int(bar["ts"]) <= decision_ts), key=lambda b: int(b["ts"])
            )[-size:]
            if len(past) < size:
                return evidence
            if any(
                int(bar["ts"]) != decision_ts - (size - 1 - i) * interval_ms or not bar.get("confirmed", True)
                for i, bar in enumerate(past)
            ):
                raise EngineError("Risk model requires aligned, contiguous confirmed closes")
            prices = [D(str(bar["close"])) for bar in past]
            if any(not v.is_finite() or v <= 0 for v in prices):
                raise EngineError("Risk model prices must be finite and positive")
            closes[symbol] = prices
        annualizer = (YEAR_MS / interval_ms).sqrt()
        returns = {
            s: [b / a - 1 for a, b in zip(closes[s][-n - 1 : -1], closes[s][-n:], strict=True)]
            for s in symbols
        }
        means = {s: sum(returns[s], D(0)) / n for s in symbols}
        sample = [
            [
                sum(
                    ((a - means[s]) * (b - means[t]) for a, b in zip(returns[s], returns[t], strict=True)),
                    D(0),
                )
                / (n - 1)
                for t in symbols
            ]
            for s in symbols
        ]
        raw_sigma = {s: max(D(0), sample[i][i]).sqrt() for i, s in enumerate(symbols)}
        floor = D(str(config["vol_floor_pct"])) / 100 / annualizer
        sigma = {s: max(floor, raw_sigma[s]) for s in symbols}
        shrink, rho = D(str(config["covariance_shrinkage"])), D(str(config["correlation_stress"]))
        covariance, stress = [], []
        for i, s in enumerate(symbols):
            model_row, stress_row = [], []
            for j, t in enumerate(symbols):
                corr = sample[i][j] / (raw_sigma[s] * raw_sigma[t]) if raw_sigma[s] and raw_sigma[t] else D(0)
                corr = max(D(-1), min(D(1), corr))
                model_row.append(sigma[s] * sigma[t] * (1 if i == j else (1 - shrink) * corr))
                stress_row.append(sigma[s] * sigma[t] * (1 if i == j else rho))
            covariance.append(model_row)
            stress.append(stress_row)
        momentum = {s: closes[s][-1] / closes[s][-lookback - 1] - 1 for s in symbols}
        excluded = {
            s: "zero_variance"
            if not raw_sigma[s]
            else "nonpositive_momentum"
            if momentum[s] <= 0
            else "zero_weight_ceiling"
            for s in symbols
            if not raw_sigma[s] or momentum[s] <= 0 or ceilings[s] <= 0
        }
        ranks = sorted(((v, s) for s, v in momentum.items() if s not in excluded), reverse=True)
        selected = [s for _, s in ranks[: config["top_k"]]]
        inverse = {s: 1 / sigma[s] for s in selected}
        total = sum(inverse.values(), D(0))
        weights = {s: min(ceilings[s], inverse[s] / total) if s in inverse else D(0) for s in symbols}
        evidence |= {
            "reason": "allocated" if selected else "no_eligible_positive_momentum",
            "sample_start": decision_ts - n * interval_ms,
            "sample_end": decision_ts + interval_ms,
            "sample_returns": n,
            "selected": selected,
            "excluded": excluded,
            "momentum": {s: str(v) for s, v in momentum.items()},
            "asset_vol_pct": {s: str(raw_sigma[s] * annualizer * 100) for s in symbols},
            "floored_asset_vol_pct": {s: str(sigma[s] * annualizer * 100) for s in symbols},
            "annualizer": str(annualizer),
            "covariance": [[str(v) for v in row] for row in covariance],
            "stress_covariance": [[str(v) for v in row] for row in stress],
            "estimator": "sample_N_minus_1_simple_close_returns_fixed_diagonal_shrinkage_not_Ledoit_Wolf",
            "weights_before_governor": {s: str(v) for s, v in weights.items()},
            "ceiling_policy": "cap_without_redistribution_remainder_cash",
        }
        weights, evidence = constrain_risk_weights(config, weights, evidence)
        return evidence | {"initial_governor_scale": evidence["governor_scale"]}


def realized_portfolio_metrics(equity, orders, initial, interval_ms):
    """Observed complete UTC days, net of all simulated costs; no imputed days."""
    from math import sqrt
    from statistics import stdev

    day_ms = 86400000
    with localcontext(ACCOUNTING_CONTEXT):
        at = {p["ts"]: D(p["equity"]) for p in equity}
        at[equity[0]["ts"] - interval_ms] = initial
        daily = []
        if interval_ms <= day_ms and day_ms % interval_ms == 0:
            for ts, value in sorted(at.items()):
                if ts % day_ms == 0 and ts + day_ms in at and value > 0:
                    daily.append(float(at[ts + day_ms] / value - 1))
        gross = [
            sum((D(p["market_value"]) for p in point["positions"]), D(0)) / D(point["equity"]) * 100
            for point in equity
            if D(point["equity"]) > 0
        ]
        valid = all(D(p["equity"]) > 0 for p in equity)
        return {
            "complete_utc_days": len(daily),
            "realized_annual_vol_pct": stdev(daily) * sqrt(365) * 100 if len(daily) >= 30 and valid else None,
            "realized_vol_reason": None
            if len(daily) >= 30 and valid
            else "fewer_than_30_complete_utc_days_or_nonpositive_equity",
            "mean_gross_exposure_pct": str(sum(gross, D(0)) / len(gross)) if gross else None,
            "turnover": str(
                sum((D(order["notional"]) for order in orders if order["status"] == "filled"), D(0)) / initial
            ),
            "turnover_definition": "sum_absolute_executed_notional_divided_by_initial_account_equity",
        }
