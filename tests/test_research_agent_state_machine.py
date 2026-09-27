import pytest

from app.research_agent.models import ExperimentWorkflowState as S
from app.research_agent.models import StageOutcome as O
from app.research_agent.state_machine import (
    InvalidWorkflowTransition,
    TransitionAuthorization,
    TransitionRequest,
    transition,
)


def auth(current: S, target: S) -> TransitionAuthorization:
    return TransitionAuthorization(
        reference="AUTH-TEST-1",
        exact_transition=f"{current.value}->{target.value}",
    )


def test_all_legal_core_transitions():
    assert transition(TransitionRequest(S.FROZEN, S.DEVELOPMENT_RUNNING)) is S.DEVELOPMENT_RUNNING
    assert transition(
        TransitionRequest(
            S.DEVELOPMENT_RUNNING,
            S.DEVELOPMENT_COMPLETE,
            completing_outcome=O.PASS,
        )
    ) is S.DEVELOPMENT_COMPLETE
    assert transition(
        TransitionRequest(
            S.DEVELOPMENT_COMPLETE,
            S.VALIDATION_RUNNING,
            previous_outcome=O.PASS,
            authorization=auth(S.DEVELOPMENT_COMPLETE, S.VALIDATION_RUNNING),
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
        TransitionRequest(
            S.VALIDATION_COMPLETE,
            S.HOLDOUT_RUNNING,
            previous_outcome=O.PASS,
            authorization=auth(S.VALIDATION_COMPLETE, S.HOLDOUT_RUNNING),
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


def test_failure_outcomes_checksum_and_semantic_only_block_progression():
    with pytest.raises(InvalidWorkflowTransition):
        transition(
            TransitionRequest(
                S.DEVELOPMENT_COMPLETE,
                S.VALIDATION_RUNNING,
                previous_outcome=O.CORPUS_FAIL,
                authorization=auth(S.DEVELOPMENT_COMPLETE, S.VALIDATION_RUNNING),
            )
        )
    with pytest.raises(InvalidWorkflowTransition):
        transition(
            TransitionRequest(
                S.FROZEN,
                S.DEVELOPMENT_RUNNING,
                expected_checksum="a",
                actual_checksum="b",
            )
        )
    with pytest.raises(InvalidWorkflowTransition):
        transition(
            TransitionRequest(
                S.FROZEN,
                S.DEVELOPMENT_RUNNING,
                semantic_recommendation_only=True,
            )
        )


def test_protected_stage_requires_exact_authorization():
    with pytest.raises(InvalidWorkflowTransition):
        transition(
            TransitionRequest(
                S.DEVELOPMENT_COMPLETE,
                S.VALIDATION_RUNNING,
                previous_outcome=O.PASS,
                authorization=TransitionAuthorization("AUTH", "wrong"),
            )
        )
