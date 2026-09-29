from app.research_agent.control_state import (
    ADAPT,
    DEFENSIVE,
    NORMAL,
    RESEARCH,
    transition_control_state,
)


def health(state):
    return {
        "control_state": state,
        "control_reason_codes": [f"REQUEST_{state}"],
    }


def test_defensive_escalation_is_immediate():
    result = transition_control_state(
        previous_state=NORMAL,
        health_snapshot=health(DEFENSIVE),
    )
    assert result["state"] == DEFENSIVE
    assert result["changed"] is True


def test_defensive_cannot_jump_directly_to_normal():
    result = transition_control_state(
        previous_state=DEFENSIVE,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=2,
    )
    assert result["state"] == RESEARCH
    assert "DEFENSIVE_RECOVERY_TO_RESEARCH" in result["transition_reason_codes"]


def test_defensive_holds_without_repeated_clear_observations():
    result = transition_control_state(
        previous_state=DEFENSIVE,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=1,
    )
    assert result["state"] == DEFENSIVE


def test_normal_requires_two_adapt_observations():
    first = transition_control_state(
        previous_state=NORMAL,
        health_snapshot=health(ADAPT),
        consecutive_adapt_observations=1,
    )
    second = transition_control_state(
        previous_state=NORMAL,
        health_snapshot=health(ADAPT),
        consecutive_adapt_observations=2,
    )
    assert first["state"] == NORMAL
    assert second["state"] == ADAPT


def test_adapt_requires_two_clear_observations_to_return_normal():
    first = transition_control_state(
        previous_state=ADAPT,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=1,
    )
    second = transition_control_state(
        previous_state=ADAPT,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=2,
    )
    assert first["state"] == ADAPT
    assert second["state"] == NORMAL


def test_research_escalation_is_immediate_but_recovery_is_slow():
    escalated = transition_control_state(
        previous_state=NORMAL,
        health_snapshot=health(RESEARCH),
    )
    held = transition_control_state(
        previous_state=RESEARCH,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=2,
    )
    recovered = transition_control_state(
        previous_state=RESEARCH,
        health_snapshot=health(NORMAL),
        consecutive_clear_observations=3,
    )
    assert escalated["state"] == RESEARCH
    assert held["state"] == RESEARCH
    assert recovered["state"] == NORMAL


def test_control_state_has_no_production_authority():
    result = transition_control_state(
        previous_state=NORMAL,
        health_snapshot=health(ADAPT),
        consecutive_adapt_observations=2,
    )
    assert result["execution_authority"] is False
    assert result["risk_or_sizing_authority"] is False
    assert result["live_configuration_changed"] is False
    assert result["promotion_authorized"] is False
