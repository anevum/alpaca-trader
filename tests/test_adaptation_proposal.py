import pytest

from app.research_agent.adaptation_proposal import (
    proposal_from_counterfactual,
    propose_parameter_change,
)


def test_daily_parameter_change_is_clipped_by_change_budget():
    result = propose_parameter_change(
        parameter="min_momentum_pct",
        current_value="0.0020",
        requested_value="0.0035",
        control_state="ADAPT",
        evidence={"reason": "validated counterfactual"},
        confidence="0.75",
        cadence="daily",
        source_strategy_version="LIVE-TEST",
    )
    assert result["proposed_value"] == "0.0022"
    assert result["clipped_by_change_budget"] is True
    assert result["authorization_required"] is True
    assert result["automatic_application_authorized"] is False
    assert result["execution_authority"] is False


def test_parameter_change_is_clipped_to_declared_range():
    result = propose_parameter_change(
        parameter="min_vwap_edge_pct",
        current_value="0.0002",
        requested_value="-0.005",
        control_state="ADAPT",
        evidence={},
        confidence="0.5",
    )
    assert result["requested_value"] == "-0.005"
    assert result["proposed_value"] == "0.0000"
    assert result["clipped_by_range"] is True


def test_normal_state_cannot_generate_adaptation_proposal():
    with pytest.raises(ValueError):
        propose_parameter_change(
            parameter="min_momentum_pct",
            current_value="0.0020",
            requested_value="0.0021",
            control_state="NORMAL",
            evidence={},
            confidence="0.5",
        )


def test_unknown_parameter_is_not_implicitly_made_adaptive():
    with pytest.raises(ValueError):
        propose_parameter_change(
            parameter="stop_pct",
            current_value="0.002",
            requested_value="0.003",
            control_state="ADAPT",
            evidence={},
            confidence="0.8",
        )


def test_counterfactual_selection_requires_validity_controls():
    result = proposal_from_counterfactual(
        parameter="min_momentum_pct",
        current_value="0.0020",
        control_state="ADAPT",
        alternatives=[
            {
                "counterfactual_id": "bad",
                "requested_value": "0.0010",
                "evidence_score": "0.99",
                "expected_improvement": "0.02",
                "confidence": "0.9",
                "selection_bias_status": "FAIL",
                "dependence_status": "PASS",
                "validity_passed": True,
            },
            {
                "counterfactual_id": "good",
                "requested_value": "0.0015",
                "evidence_score": "0.70",
                "expected_improvement": "0.01",
                "confidence": "0.7",
                "selection_bias_status": "CONTROLLED",
                "dependence_status": "CONTROLLED",
                "validity_passed": True,
            },
        ],
    )
    assert result is not None
    assert result["evidence"]["counterfactual_id"] == "good"


def test_no_valid_counterfactual_means_no_proposal():
    result = proposal_from_counterfactual(
        parameter="min_momentum_pct",
        current_value="0.0020",
        control_state="RESEARCH",
        alternatives=[
            {
                "counterfactual_id": "x",
                "requested_value": "0.0010",
                "selection_bias_status": "FAIL",
                "dependence_status": "PASS",
                "validity_passed": True,
            }
        ],
    )
    assert result is None


def test_proposal_id_is_deterministic_for_same_evidence():
    kwargs = dict(
        parameter="min_momentum_pct",
        current_value="0.0020",
        requested_value="0.0018",
        control_state="ADAPT",
        evidence={"id": "E-1"},
        confidence="0.8",
    )
    first = propose_parameter_change(**kwargs)
    second = propose_parameter_change(**kwargs)
    assert first["proposal_id"] == second["proposal_id"]
