from decimal import Decimal

from tidebench.portfolio_construction import funding_carry_evidence

D = Decimal
HOUR = 3600000


def test_four_fill_cost_hurdle_rejects_positive_funding_and_excludes_current_event():
    config = dict(
        carry_threshold="0",
        carry_window=2,
        carry_cost_settlements=21,
        carry_buffer_bps=20,
        carry_max_age_hours=16,
    )
    history = [dict(ts=i * HOUR, rate=".0001") for i in range(5)] + [dict(ts=5 * HOUR, rate="1")]
    result = funding_carry_evidence(config, history, 5 * HOUR, 10, 5)
    assert result["sample_count"] == 2 and result["settlement_times"] == [3 * HOUR, 4 * HOUR]
    assert result["mean_rate"] == ".0001" or D(result["mean_rate"]) == D(".0001")
    assert D(result["round_trip_cost_bps"]) == 60  # Four fills, not one round trip.
    assert D(result["net_hurdle_bps"]) == -59
    assert result["allowed"] is False and result["reason"] == "cost_hurdle"
    cheaper = funding_carry_evidence(config | {"carry_buffer_bps": 0}, history, 5 * HOUR, 1, 0)
    assert cheaper["allowed"] and D(cheaper["net_hurdle_bps"]) == 17


def test_insufficient_stale_negative_and_legacy_inputs_are_distinct():
    config = dict(carry_threshold="0", carry_window=2, carry_cost_settlements=21, carry_max_age_hours=16)
    history = [dict(ts=HOUR, rate=".001"), dict(ts=2 * HOUR, rate=".001")]
    assert (
        funding_carry_evidence(config, history[:1], 3 * HOUR, 10, 5)["reason"] == "insufficient_settlements"
    )
    assert funding_carry_evidence(config, history, 19 * HOUR, 10, 5)["reason"] == "stale_settlement"
    assert (
        funding_carry_evidence(
            config, [dict(ts=HOUR, rate="-.001"), dict(ts=2 * HOUR, rate="-.001")], 3 * HOUR, 10, 5
        )["reason"]
        == "below_rate_threshold"
    )
    assert (
        funding_carry_evidence({"carry_threshold": "0"}, history, 3 * HOUR, 10, 5)["reason"]
        == "legacy_rate_only"
    )
