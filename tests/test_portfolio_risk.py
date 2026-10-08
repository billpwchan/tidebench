"""Independent covariance oracle, causal timing, ceilings and economic replay."""

import copy
from decimal import Decimal as D
from decimal import localcontext

import pytest
from pydantic import ValidationError
from tidebench.engine import ACCOUNTING_CONTEXT, EngineError
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.portfolio_risk import constrain_risk_weights, risk_momentum_weights

HOUR = 3600000
SYMBOLS = ["BTC-USDT", "ETH-USDT"]


def fixture(**changes):
    definition = PortfolioDefinition.model_validate(
        dict(
            mode="risk_momentum",
            risk_window=10,
            lookback=10,
            top_k=2,
            vol_target_pct="10",
            vol_floor_pct="1",
            legs=[dict(inst_id=s, weight=".9") for s in SYMBOLS],
        )
        | changes
    ).record()
    bars = {}
    with localcontext(ACCOUNTING_CONTEXT):
        for symbol, scale in zip(SYMBOLS, [D(1), D(2)], strict=True):
            price = D(100)
            records = [dict(ts=0, close=str(price))]
            for i in range(10):
                price *= 1 + scale * (D(".01") if i % 2 else D("-.005"))
                records.append(dict(ts=(i + 1) * HOUR, close=str(price)))
            bars[symbol] = records
    return definition, bars


def test_independent_covariance_and_inverse_volatility_oracle():
    config, bars = fixture(covariance_shrinkage="0", correlation_stress="0", vol_target_pct="100")
    e = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    # Alternating -0.5%, +1% has sample variance 10 * .0075^2 / 9.
    with localcontext(ACCOUNTING_CONTEXT):
        v = D(10) * D(".0075") ** 2 / 9
        assert abs(D(e["covariance"][0][0]) - v) < D("1e-45")
        assert abs(D(e["covariance"][0][1]) - 2 * v) < D("1e-45")
        assert abs(D(e["weights"]["BTC-USDT"]) - D(2) / 3) < D("1e-45")
        assert abs(D(e["weights"]["ETH-USDT"]) - D(1) / 3) < D("1e-45")
        assert abs(
            sum((D(v) for v in e["risk_contribution_pct"].values()), D(0)) - D(e["modeled_vol_pct"])
        ) < D("1e-40")


def test_stress_governor_caps_both_risk_scenarios_and_preserves_cash():
    config, bars = fixture(covariance_shrinkage="1", correlation_stress="1")
    e = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    assert D(e["stressed_vol_pct"]) <= D(10) + D("1e-40")
    assert D(e["modeled_vol_pct"]) < D(e["stressed_vol_pct"])
    assert D(e["cash_weight"]) > 0 and D(e["initial_governor_scale"]) < 1


def test_ceiling_does_not_redistribute_or_implicitly_lever():
    config, bars = fixture(vol_target_pct="100")
    config["legs"][0]["weight"] = ".1"
    config["legs"][1]["weight"] = ".2"
    e = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    assert D(e["weights"][SYMBOLS[0]]) == D(".1")
    assert D(e["weights"][SYMBOLS[1]]) == D(".2")
    assert D(e["cash_weight"]) == D(".7")


def test_future_perturbations_do_not_change_weights_and_caller_precision_does_not_leak():
    config, bars = fixture()
    expected = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    for symbol in SYMBOLS:
        bars[symbol].append(dict(ts=11 * HOUR, close="0"))
    with localcontext() as ctx:
        ctx.prec = 6
        assert risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR) == expected


@pytest.mark.parametrize("change", ["missing", "duplicate", "unconfirmed", "negative", "nan"])
def test_bad_price_or_temporal_history_never_creates_risk(change):
    config, bars = fixture()
    row = bars[SYMBOLS[0]][3]
    if change == "missing":
        row["ts"] += HOUR // 2
    elif change == "duplicate":
        row["ts"] += HOUR
    elif change == "unconfirmed":
        row["confirmed"] = False
    else:
        row["close"] = "-1" if change == "negative" else "NaN"
    with pytest.raises(EngineError):
        risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)


def test_insufficient_history_and_zero_variance_remain_cash():
    config, bars = fixture()
    e = risk_momentum_weights(config, config["legs"], {s: b[1:] for s, b in bars.items()}, 10 * HOUR, HOUR)
    assert e["reason"] == "insufficient_history" and set(e["weights"].values()) == {"0"}
    for records in bars.values():
        for row in records:
            row["close"] = "100"
    e = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    assert e["reason"] == "no_eligible_positive_momentum"
    assert set(e["excluded"].values()) == {"zero_variance"}


def test_removing_a_hedge_reapplies_governor():
    config, bars = fixture(vol_target_pct="10")
    e = risk_momentum_weights(config, config["legs"], bars, 10 * HOUR, HOUR)
    e = copy.deepcopy(e)
    # An artificial strongly negative covariance portfolio loses a hedge.
    e["covariance"] = [[".0001", "-.000099"], ["-.000099", ".0001"]]
    e["stress_covariance"] = [[".0001", "0"], ["0", ".0001"]]
    weights, new = constrain_risk_weights(config, dict(zip(SYMBOLS, [D(1), D(0)], strict=True)), e)
    assert weights[SYMBOLS[0]] < 1 and weights[SYMBOLS[1]] == 0
    assert D(new["modeled_vol_pct"]) <= 10 + D("1e-40")


@pytest.mark.parametrize(
    "field,value",
    [
        ("risk_window", 9),
        ("vol_target_pct", 0),
        ("vol_floor_pct", 0),
        ("covariance_shrinkage", "1.01"),
        ("correlation_stress", "-.1"),
    ],
)
def test_invalid_risk_controls_reject(field, value):
    with pytest.raises(ValidationError):
        fixture(**{field: value})


@pytest.mark.parametrize(
    "leg_change",
    [
        {"weight": "1.1"},
        {"weight": "-.1"},
        {"inst_id": "BTC-USDT-SWAP", "leverage": 2},
        {"inst_id": "BTC-USDT-SWAP", "direction": "long_short"},
    ],
)
def test_risk_rotation_cannot_expand_into_short_or_levered_risk(leg_change):
    config, _ = fixture()
    config["legs"][0].update(leg_change)
    with pytest.raises(ValidationError):
        PortfolioDefinition.model_validate(config)


def test_realized_metrics_use_complete_days_and_actual_notional_turnover():
    from tidebench.portfolio_risk import realized_portfolio_metrics

    points = [
        dict(
            ts=(i + 1) * 86400000,
            equity=str(D(100) * (D("1.01") if i % 2 else D(".99"))),
            positions=[dict(market_value="20")],
        )
        for i in range(32)
    ]
    orders = [dict(status="filled", notional="30"), dict(status="rejected", notional="100")]
    m = realized_portfolio_metrics(points, orders, D(100), 86400000)
    assert m["complete_utc_days"] == 32
    assert m["realized_annual_vol_pct"] > 0 and D(m["turnover"]) == D(".3")
    assert (
        realized_portfolio_metrics(points[:20], orders, D(100), 86400000)["realized_annual_vol_pct"] is None
    )
