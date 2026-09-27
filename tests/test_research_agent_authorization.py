from copy import deepcopy

import pytest

from app.research_agent.authorization import (
    AuthorizationError,
    authorize_freeze,
    authorize_stage,
)
from app.research_agent.proposal import proposal_hash


def stage_decision(**changes):
    evidence = {
        "authorized_action": "open_stage",
        "experiment_id": "experiment-id",
        "experiment_key": "experiment-key",
        "stage": "development",
        "manifest_hash": "manifest-hash",
        "source_commit": "source-commit",
        "authorized_by": "synthetic-operator",
        "authorized_at": "2026-09-27T12:00:00Z",
    }
    evidence.update(changes.pop("evidence", {}))
    return {
        "decision_key": "AUTH-STAGE-1",
        "status": "final",
        "decision_type": "research_authorization",
        "superseded_by_decision_id": None,
        "evidence": evidence,
        **changes,
    }


def test_exact_freeze_authorization_passes(proposal, freeze_decision):
    result = authorize_freeze(
        [freeze_decision],
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.revision,
        proposal_hash=proposal_hash(proposal),
    )
    assert result.decision_reference == "AUTH-FREEZE-SYNTHETIC-001"


def test_missing_wrong_hash_and_superseded_freeze_authorization_fail(
    proposal, freeze_decision
):
    with pytest.raises(AuthorizationError):
        authorize_freeze([], proposal_id=proposal.proposal_id, proposal_revision=1, proposal_hash="x")
    with pytest.raises(AuthorizationError):
        authorize_freeze([freeze_decision], proposal_id=proposal.proposal_id, proposal_revision=1, proposal_hash="wrong")
    superseded = deepcopy(freeze_decision)
    superseded["superseded_by_decision_id"] = "replacement"
    with pytest.raises(AuthorizationError):
        authorize_freeze(
            [superseded],
            proposal_id=proposal.proposal_id,
            proposal_revision=1,
            proposal_hash=proposal_hash(proposal),
        )


def test_exact_development_authorization_and_fail_closed_mismatches():
    kwargs = {
        "experiment_id": "experiment-id",
        "experiment_key": "experiment-key",
        "stage": "development",
        "manifest_hash": "manifest-hash",
        "source_commit": "source-commit",
    }
    assert authorize_stage([stage_decision()], **kwargs).stage == "development"
    for field, wrong in (
        ("experiment_id", "wrong"),
        ("experiment_key", "wrong"),
        ("stage", "validation"),
        ("manifest_hash", "wrong"),
        ("source_commit", "wrong"),
    ):
        invalid = dict(kwargs)
        invalid[field] = wrong
        with pytest.raises(AuthorizationError):
            authorize_stage([stage_decision()], **invalid)


@pytest.mark.parametrize("stage", ["validation", "holdout"])
def test_validation_and_holdout_remain_exactly_protected(stage):
    decision = stage_decision(evidence={"stage": stage})
    assert authorize_stage(
        [decision],
        experiment_id="experiment-id",
        experiment_key="experiment-key",
        stage=stage,
        manifest_hash="manifest-hash",
        source_commit="source-commit",
    ).stage == stage


def test_nonfinal_revoked_and_generic_approval_text_are_unauthorized():
    for decision in (
        stage_decision(status="draft"),
        stage_decision(evidence={"revoked": True}),
        {
            "decision_key": "generic",
            "status": "final",
            "decision_type": "research_authorization",
            "evidence": {"approved": True},
        },
    ):
        with pytest.raises(AuthorizationError):
            authorize_stage(
                [decision],
                experiment_id="experiment-id",
                experiment_key="experiment-key",
                stage="development",
                manifest_hash="manifest-hash",
                source_commit="source-commit",
            )
