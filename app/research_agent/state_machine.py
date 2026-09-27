from __future__ import annotations

from dataclasses import dataclass

from .models import ExperimentWorkflowState, StageOutcome
from .policy import SafetyPolicy


class InvalidWorkflowTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransitionAuthorization:
    reference: str
    exact_transition: str


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    current: ExperimentWorkflowState
    target: ExperimentWorkflowState
    previous_outcome: StageOutcome | None = None
    completing_outcome: StageOutcome | None = None
    authorization: TransitionAuthorization | None = None
    expected_checksum: str | None = None
    actual_checksum: str | None = None
    semantic_recommendation_only: bool = False


COMPLETION_TARGETS = frozenset(
    {
        ExperimentWorkflowState.DEVELOPMENT_COMPLETE,
        ExperimentWorkflowState.VALIDATION_COMPLETE,
        ExperimentWorkflowState.HISTORICAL_COMPLETE,
    }
)


def transition_name(
    current: ExperimentWorkflowState,
    target: ExperimentWorkflowState,
) -> str:
    return f"{current.value}->{target.value}"


def validate_transition(request: TransitionRequest) -> None:
    if request.semantic_recommendation_only:
        raise InvalidWorkflowTransition(
            "semantic recommendation alone cannot change experiment workflow state"
        )
    if (
        request.expected_checksum is not None
        or request.actual_checksum is not None
    ) and request.expected_checksum != request.actual_checksum:
        raise InvalidWorkflowTransition("checksum mismatch blocks stage transition")

    current = request.current
    target = request.target
    allowed = {
        ExperimentWorkflowState.FROZEN: {
            ExperimentWorkflowState.DEVELOPMENT_RUNNING,
            ExperimentWorkflowState.ARCHIVED,
        },
        ExperimentWorkflowState.DEVELOPMENT_RUNNING: {
            ExperimentWorkflowState.DEVELOPMENT_COMPLETE,
        },
        ExperimentWorkflowState.DEVELOPMENT_COMPLETE: {
            ExperimentWorkflowState.VALIDATION_RUNNING,
            ExperimentWorkflowState.ARCHIVED,
        },
        ExperimentWorkflowState.VALIDATION_RUNNING: {
            ExperimentWorkflowState.VALIDATION_COMPLETE,
        },
        ExperimentWorkflowState.VALIDATION_COMPLETE: {
            ExperimentWorkflowState.HOLDOUT_RUNNING,
            ExperimentWorkflowState.ARCHIVED,
        },
        ExperimentWorkflowState.HOLDOUT_RUNNING: {
            ExperimentWorkflowState.HISTORICAL_COMPLETE,
        },
        ExperimentWorkflowState.HISTORICAL_COMPLETE: {
            ExperimentWorkflowState.CHALLENGER_CANDIDATE,
            ExperimentWorkflowState.ARCHIVED,
        },
        ExperimentWorkflowState.CHALLENGER_CANDIDATE: {
            ExperimentWorkflowState.ARCHIVED,
        },
        ExperimentWorkflowState.ARCHIVED: set(),
    }
    if target not in allowed[current]:
        raise InvalidWorkflowTransition(
            f"forbidden workflow transition: {transition_name(current, target)}"
        )

    if target in COMPLETION_TARGETS and request.completing_outcome is None:
        raise InvalidWorkflowTransition("stage completion requires an explicit outcome")

    if target in {
        ExperimentWorkflowState.VALIDATION_RUNNING,
        ExperimentWorkflowState.HOLDOUT_RUNNING,
        ExperimentWorkflowState.CHALLENGER_CANDIDATE,
    } and request.previous_outcome is not StageOutcome.PASS:
        raise InvalidWorkflowTransition("the prior deterministic stage must PASS")

    if SafetyPolicy.requires_authorization(target):
        exact = transition_name(current, target)
        if (
            request.authorization is None
            or not request.authorization.reference.strip()
            or request.authorization.exact_transition != exact
        ):
            raise InvalidWorkflowTransition(
                f"exact authorization is required for {exact}"
            )


def transition(request: TransitionRequest) -> ExperimentWorkflowState:
    validate_transition(request)
    return request.target
