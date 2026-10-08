"""Causal research hypotheses; original model adaptations, not paper replications.

Every observation is a confirmed close. Windows are bar counts, volatility is
unannualized population standard deviation of simple close returns. No fitted
parameter, future price, or current-window reversion mean is used.
"""

from collections import deque
from decimal import Decimal

from .engine import ZERO, EngineError, _string

D = Decimal


class CausalStrategyModel:
    def __init__(self, strategy):
        self.strategy = strategy
        self.limit = (
            max(
                [strategy.vol_window, strategy.window, strategy.reversion_trend_window]
                + strategy.momentum_horizons
            )
            + 1
        )
        self.prices = deque(maxlen=self.limit)

    def snapshot(self):
        return {"schema_version": 1, "prices": [_string(p) for p in self.prices]}

    def restore(self, body):
        if body.get("schema_version") != 1 or not isinstance(body.get("prices"), list):
            raise EngineError("Missing or unsupported research model checkpoint")
        prices = [D(p) for p in body["prices"]]
        if len(prices) > self.limit or any(not p.is_finite() or p <= 0 for p in prices):
            raise EngineError("Invalid research model checkpoint prices")
        self.prices = deque(prices, maxlen=self.limit)

    def on_close(self, close):
        self.prices.append(close)
        s = self.strategy
        needed = (
            max(
                s.vol_window,
                max(s.momentum_horizons)
                if s.kind == "ts_momentum"
                else max(s.window, s.reversion_trend_window),
            )
            + 1
        )
        if len(self.prices) < needed:
            return None, {"reason": "warmup", "required_closes": needed, "available_closes": len(self.prices)}
        prices = list(self.prices)
        returns = [
            b / a - 1 for a, b in zip(prices[-s.vol_window - 1 : -1], prices[-s.vol_window :], strict=True)
        ]
        average = sum(returns, ZERO) / s.vol_window
        variance = sum(((r - average) ** 2 for r in returns), ZERO) / s.vol_window
        sigma = variance.sqrt()
        features = {"bar_vol_pct": _string(100 * sigma)}
        if sigma == 0:
            return 0, features | {"reason": "degenerate_volatility"}
        if s.max_bar_vol_pct and sigma * 100 > s.max_bar_vol_pct:
            return 0, features | {"reason": "volatility_guard"}
        if s.kind == "ts_momentum":
            scores = [(close / prices[-h - 1] - 1) / (sigma * D(h).sqrt()) for h in s.momentum_horizons]
            features.update(
                {f"momentum_{h}": _string(v) for h, v in zip(s.momentum_horizons, scores, strict=True)}
            )
            raw = (
                1
                if all(v > s.momentum_entry for v in scores)
                else -1
                if all(v < -s.momentum_entry for v in scores)
                else 0
            )
            return raw, features | {"reason": "horizon_consensus" if raw else "horizon_disagreement"}
        prior = prices[-s.window - 1 : -1]
        center = sum(prior, ZERO) / s.window
        deviation = (sum(((p - center) ** 2 for p in prior), ZERO) / s.window).sqrt()
        path = prices[-s.reversion_trend_window - 1 :]
        distance = sum((abs(b - a) for a, b in zip(path[:-1], path[1:], strict=True)), ZERO)
        efficiency = abs(path[-1] - path[0]) / distance if distance else ZERO
        features.update(
            {
                "efficiency_ratio": _string(efficiency),
                "prior_mean": _string(center),
                "prior_std": _string(deviation),
            }
        )
        if efficiency > s.efficiency_max:
            return 0, features | {"reason": "trend_guard"}
        if not deviation:
            return 0, features | {"reason": "degenerate_price_window"}
        z = (close - center) / deviation
        raw = 1 if z < -s.z_entry else -1 if z > s.z_entry else 0 if abs(z) <= s.z_exit else None
        return raw, features | {
            "prior_zscore": _string(z),
            "reason": "reversion_entry" if raw else "reversion_exit" if raw == 0 else "hold_regime",
        }
