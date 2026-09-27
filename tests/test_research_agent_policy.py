import pytest

from app.research_agent.models import ExperimentWorkflowState
from app.research_agent.policy import SafetyPolicy, SafetyPolicyViolation


def test_live_mutations_and_automatic_promotion_are_structurally_forbidden():
    policy = SafetyPolicy()
    with pytest.raises(SafetyPolicyViolation):
        policy.require_no_live_strategy_mutation("change threshold")
    with pytest.raises(SafetyPolicyViolation):
        policy.require_no_live_risk_or_sizing_mutation("increase size")
    with pytest.raises(SafetyPolicyViolation):
        policy.assert_no_automatic_promotion()


def test_terminal_rdr_and_rejected_edge_family_are_protected():
    policy = SafetyPolicy()
    with pytest.raises(SafetyPolicyViolation):
        policy.assert_experiment_mutable(
            "edge-discovery-v2-residual-downshock-rebound-v2.1"
        )
    with pytest.raises(SafetyPolicyViolation):
        policy.assert_family_may_be_proposed("controlled continuation")


def test_development_validation_and_holdout_start_are_protected_targets():
    assert SafetyPolicy.requires_authorization(
        ExperimentWorkflowState.DEVELOPMENT_RUNNING
    )
    assert SafetyPolicy.requires_authorization(
        ExperimentWorkflowState.VALIDATION_RUNNING
    )
    assert SafetyPolicy.requires_authorization(ExperimentWorkflowState.HOLDOUT_RUNNING)
