import pytest

from app.research_agent.models import ExperimentWorkflowState as S
from app.research_agent.models import StageOutcome as O
from app.research_agent.state_machine import (
    InvalidWorkflowTransition,
    TransitionAuthorization,
    TransitionRequest,
    transition,
)


IDENTITY = {
    "experiment_id": "experiment-id",
    "experiment_key": "experiment-key",
    "manifest_hash": "manifest-hash",
    "source_commit": "source-commit",
}


def auth(stage_name: str, **changes) -> TransitionAuthorization:
    values = {
        "reference": "AUTH-TEST-1",
        "experiment_id": IDENTITY["experiment_id"],
        "experiment_key": IDENTITY["experiment_key"],
        "stage": stage_name,
        "manifest_hash": IDENTITY["manifest_hash"],
        "source_commit": IDENTITY["source_commit"],
        "authorized_by": "synthetic-operator",
        "authorized_at": "2026-09-27T12:00:00Z",
    }
    values.update(changes)
    return TransitionAuthorization(**values)


def protected_request(current, target, stage, **changes):
    values = {
        "current": current,
        "target": target,
        "authorization": auth(stage),
        **IDENTITY,
    }
    values.update(changes)
    return TransitionRequest(**values)


def test_all_legal_core_transitions():
    assert transition(
        protected_request(S.FROZEN, S.DEVELOPMENT_RUNNING, "development")
    ) is S.DEVELOPMENT_RUNNING
    assert transition(
        TransitionRequest(
            S.DEVELOPMENT_RUNNING,
            S.DEVELOPMENT_COMPLETE,
            completing_outcome=O.PASS,
        )
    ) is S.DEVELOPMENT_COMPLETE
    assert transition(
        protected_request(
            S.DEVELOPMENT_COMPLETE,
            S.VALIDATION_RUNNING,
            "validation",
            previous_outcome=O.PASS,
        )
    ) is S.VALIDATION_RUNNING
    assert transition(
        TransitionRequest(
            S.VALIDATION_RUNNING,
            S.VALIDATION_COMPLETE,
            completing_outcome=O.PASS,
        )
    ) is S.VALIDATION_COMPLETE
    assert transition(
        protected_request(
            S.VALIDATION_COMPLETE,
            S.HOLDOUT_RUNNING,
            "holdout",
            previous_outcome=O.PASS,
        )
    ) is S.HOLDOUT_RUNNING
    assert transition(
        TransitionRequest(
            S.HOLDOUT_RUNNING,
            S.HISTORICAL_COMPLETE,
            completing_outcome=O.PASS,
        )
    ) is S.HISTORICAL_COMPLETE
    assert transition(
        TransitionRequest(
            S.HISTORICAL_COMPLETE,
            S.CHALLENGER_CANDIDATE,
            previous_outcome=O.PASS,
        )
    ) is S.CHALLENGER_CANDIDATE


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.FROZEN, S.VALIDATION_RUNNING),
        (S.DEVELOPMENT_RUNNING, S.HOLDOUT_RUNNING),
        (S.HISTORICAL_COMPLETE, S.FROZEN),
        (S.ARCHIVED, S.VALIDATION_RUNNING),
    ],
)
def test_prohibited_skips_and_backward_reopening(current, target):
    with pytest.raises(InvalidWorkflowTransition):
        transition(TransitionRequest(current, target, previous_outcome=O.PASS))


def test_development_without_authorization_fails_regression():
    with pytest.raises(InvalidWorkflowTransition, match="exact authorization"):
        transition(TransitionRequest(S.FROZEN, S.DEVELOPMENT_RUNNING))


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("experiment_id", "wrong"),
        ("experiment_key", "wrong"),
        ("stage", "validation"),
        ("manifest_hash", "wrong"),
        ("source_commit", "wrong"),
    ],
)
def test_development_authorization_must_match_every_exact_field(field, wrong):
    request = protected_request(
        S.FROZEN,
        S.DEVELOPMENT_RUNNING,
        "development",
        authorization=auth("development", **{field: wrong}),
    )
    with pytest.raises(InvalidWorkflowTransition, match="exact authorization"):
        transition(request)


def test_failure_outcomes_checksum_and_semantic_only_block_progression():
    with pytest.raises(InvalidWorkflowTransition):
        transition(
            protected_request(
                S.DEVELOPMENT_COMPLETE,
                S.VALIDATION_RUNNING,
                "validation",
                previous_outcome=O.CORPUS_FAIL,
            )
        )
    with pytest.raises(InvalidWorkflowTransition, match="checksum mismatch"):
        transition(
            TransitionRequest(
                S.FROZEN,
                S.DEVELOPMENT_RUNNING,
                expected_checksum="a",
                actual_checksum="b",
            )
        )
    with pytest.raises(InvalidWorkflowTransition, match="semantic recommendation"):
        transition(
            protected_request(
                S.FROZEN,
                S.DEVELOPMENT_RUNNING,
                "development",
                semantic_recommendation_only=True,
            )
        )


@pytest.mark.parametrize(
    ("current", "target", "outcome"),
    [
        (S.FROZEN, S.DEVELOPMENT_RUNNING, None),
        (S.DEVELOPMENT_COMPLETE, S.VALIDATION_RUNNING, O.PASS),
        (S.VALIDATION_COMPLETE, S.HOLDOUT_RUNNING, O.PASS),
    ],
)
def test_every_protected_stage_rejects_missing_authorization(current, target, outcome):
    with pytest.raises(InvalidWorkflowTransition, match="exact authorization"):
        transition(
            TransitionRequest(
                current,
                target,
                previous_outcome=outcome,
                **IDENTITY,
            )
        )
