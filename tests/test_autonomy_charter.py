from copy import deepcopy

import pytest

from app.research_agent.autonomy import (
    AutonomyCondition,
    DEFAULT_CHARTER,
    build_engineering_requirement,
    classify_condition,
    standing_freeze_authorization,
    standing_stage_authorization,
)
from app.research_agent.authorization import (
    AuthorizationError,
    authorize_freeze,
    authorize_stage,
)


def test_charter_allows_research_but_never_runtime_source_mutation():
    assert DEFAULT_CHARTER.allows_action("GENERATE_HYPOTHESIS")
    assert DEFAULT_CHARTER.allows_action("OPEN_VALIDATION")
    assert not DEFAULT_CHARTER.allows_action("MODIFY_SOURCE_CODE")
    assert not DEFAULT_CHARTER.code_mutation_authority
    assert not DEFAULT_CHARTER.production_risk_increase_authority
    assert not DEFAULT_CHARTER.spending_authority


def test_standing_authorizations_are_exact_and_research_only():
    freeze = standing_freeze_authorization(
        proposal_id="P-1",
        proposal_revision=1,
        proposal_hash="a" * 64,
    )
    assert freeze["authorization_basis"] == "STANDING_RESEARCH_CHARTER"
    assert freeze["human_approval_required"] is False
    stage = standing_stage_authorization(
        experiment_id="E-1",
        experiment_key="K-1",
        stage="validation",
        manifest_hash="b" * 64,
        source_commit="c" * 40,
    )
    assert stage["stage"] == "validation"
    assert stage["production_authority"] is False


def test_authorization_api_is_strict_by_default_and_opt_in_for_standing_charter():
    kwargs = {
        "experiment_id": "E-1",
        "experiment_key": "K-1",
        "stage": "development",
        "manifest_hash": "m",
        "source_commit": "s",
    }
    with pytest.raises(AuthorizationError):
        authorize_stage([], **kwargs)
    result = authorize_stage([], allow_standing_charter=True, **kwargs)
    assert result.authorized_by == DEFAULT_CHARTER.charter_id

    with pytest.raises(AuthorizationError):
        authorize_freeze(
            [],
            proposal_id="P-1",
            proposal_revision=1,
            proposal_hash="h",
        )
    result = authorize_freeze(
        [],
        proposal_id="P-1",
        proposal_revision=1,
        proposal_hash="h",
        allow_standing_charter=True,
    )
    assert result.authorized_by == DEFAULT_CHARTER.charter_id


def test_explicit_final_revocation_outranks_standing_charter():
    revoked = {
        "decision_key": "R-1",
        "status": "final",
        "decision_type": "research_authorization",
        "evidence": {
            "authorized_action": "open_stage",
            "revoked": True,
        },
    }
    with pytest.raises(AuthorizationError):
        authorize_stage(
            [revoked],
            experiment_id="E-1",
            experiment_key="K-1",
            stage="development",
            manifest_hash="m",
            source_commit="s",
            allow_standing_charter=True,
        )



def test_unrelated_explicit_authorization_does_not_disable_charter():
    unrelated = {
        "decision_key": "OTHER",
        "status": "final",
        "decision_type": "research_authorization",
        "evidence": {
            "authorized_action": "open_stage",
            "experiment_id": "OTHER",
            "experiment_key": "OTHER",
            "stage": "development",
            "manifest_hash": "other",
            "source_commit": "other",
            "authorized_by": "operator",
            "authorized_at": "2026-10-04T20:00:00+00:00",
        },
    }
    result = authorize_stage(
        [unrelated],
        experiment_id="E-2",
        experiment_key="K-2",
        stage="development",
        manifest_hash="m2",
        source_commit="s2",
        allow_standing_charter=True,
    )
    assert result.authorized_by == DEFAULT_CHARTER.charter_id

def test_engineering_requirement_is_a_manual_handoff_not_runtime_permission():
    req = build_engineering_requirement(
        requirement_id="ENG-1",
        requested_by="GRAEN",
        title="Add a research primitive",
        reason="Hypothesis requires an unavailable causal feature.",
        capability_required="causal feature primitive",
        affected_components=["graen"],
        blocked_research=["H-1"],
        acceptance_tests=["feature is causal", "research resumes"],
    )
    assert req["condition"] == "ENGINEERING_REQUIRED"
    assert req["manual_chatgpt_workspace_required"] is True
    assert req["runtime_code_mutation_authorized"] is False
    assert req["runtime_git_write_authorized"] is False
    assert req["runtime_merge_authorized"] is False
    assert req["runtime_deploy_authorized"] is False
    assert "do not enable live trading" in req["handoff_prompt"].lower()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"active_operations": True}, AutonomyCondition.OPERATING),
        ({"active_research": True}, AutonomyCondition.RESEARCHING),
        ({"code_change_required": True}, AutonomyCondition.ENGINEERING_REQUIRED),
        (
            {"human_authority_required": True},
            AutonomyCondition.HUMAN_DECISION_REQUIRED,
        ),
        ({"evidence_blocked": True}, AutonomyCondition.BLOCKED),
        ({}, AutonomyCondition.IDLE),
    ],
)
def test_condition_classification(kwargs, expected):
    assert classify_condition(**kwargs) is expected
