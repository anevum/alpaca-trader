from __future__ import annotations

from dataclasses import dataclass

from .models import ExperimentWorkflowState, StageOutcome
from .policy import SafetyPolicy


class InvalidWorkflowTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransitionAuthorization:
    reference: str
    experiment_id: str
    experiment_key: str
    stage: str
    manifest_hash: str
    source_commit: str
    authorized_by: str
    authorized_at: str


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
    experiment_id: str | None = None
    experiment_key: str | None = None
    manifest_hash: str | None = None
    source_commit: str | None = None


COMPLETION_TARGETS = frozenset(
    {
        ExperimentWorkflowState.DEVELOPMENT_COMPLETE,
        ExperimentWorkflowState.VALIDATION_COMPLETE,
        ExperimentWorkflowState.HISTORICAL_COMPLETE,
    }
)

PROTECTED_STAGE_NAMES = {
    ExperimentWorkflowState.DEVELOPMENT_RUNNING: "development",
    ExperimentWorkflowState.VALIDATION_RUNNING: "validation",
    ExperimentWorkflowState.HOLDOUT_RUNNING: "holdout",
}


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
        authorization = request.authorization
        required = (
            request.experiment_id,
            request.experiment_key,
            request.manifest_hash,
            request.source_commit,
        )
        valid = (
            authorization is not None
            and all(value is not None and value.strip() for value in required)
            and bool(authorization.reference.strip())
            and authorization.experiment_id == request.experiment_id
            and authorization.experiment_key == request.experiment_key
            and authorization.stage == PROTECTED_STAGE_NAMES[target]
            and authorization.manifest_hash == request.manifest_hash
            and authorization.source_commit == request.source_commit
            and bool(authorization.authorized_by.strip())
            and bool(authorization.authorized_at.strip())
        )
        if not valid:
            raise InvalidWorkflowTransition(
                f"exact authorization is required for {exact}"
            )


def transition(request: TransitionRequest) -> ExperimentWorkflowState:
    validate_transition(request)
    return request.target
