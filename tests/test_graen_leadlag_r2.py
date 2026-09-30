from datetime import datetime, timezone

from graen.crypto.leadlag_r2 import (
    CANDIDATE_ID,
    METHODOLOGY_VERSION,
    development_gate,
    research_specification,
)


UTC = timezone.utc


def test_leadlag_r2_prespec_is_long_only_and_frozen_before_data():
    spec = research_specification()
    assert METHODOLOGY_VERSION == "graen-crypto-leadlag-r2"
    assert CANDIDATE_ID == "CRYPTO-LEADLAG-001-R2"
    assert spec["revision"] == 2
    assert spec["side_contract"] == "LONG_ONLY_POSITIVE_LEADER_IMPULSES"
    assert spec["leader_impulse_z_threshold"] == 2.0
    assert spec["follower_underreaction_z_threshold"] == 1.0
    assert spec["rolling_beta_lookback_hours"] == 168
    assert spec["leader_volatility_lookback_hours"] == 24
    assert spec["hold_minutes"] == 30
    assert spec["cooldown_minutes_per_follower"] == 60
    assert spec["holdout_opened_only_after_validation_pass"] is True
    assert spec["authority"]["execution_authority"] is False
    assert spec["authority"]["broker_orders_possible"] is False


def test_development_gate_fails_closed_on_insufficient_or_negative_sample():
    result = {
        "primary": {
            "trade_count": 19,
            "expectancy_per_trade": -0.0001,
        }
    }
    passed, reasons = development_gate(result)
    assert passed is False
    assert "development_trade_count_below_20" in reasons
    assert "development_high_cost_expectancy_nonpositive" in reasons
