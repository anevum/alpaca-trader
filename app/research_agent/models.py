from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class ResearchCategory(str, Enum):
    OPERATIONAL_DEFECT = "OPERATIONAL_DEFECT"
    DATA_QUALITY = "DATA_QUALITY"
    STRATEGY_HYPOTHESIS = "STRATEGY_HYPOTHESIS"
    RISK_SIZING_OBSERVATION = "RISK_SIZING_OBSERVATION"
    NOISE_INSUFFICIENT = "NOISE_INSUFFICIENT"


class AgentRunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    NOOP = "NOOP"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ExperimentWorkflowState(str, Enum):
    FROZEN = "FROZEN"
    DEVELOPMENT_RUNNING = "DEVELOPMENT_RUNNING"
    DEVELOPMENT_COMPLETE = "DEVELOPMENT_COMPLETE"
    VALIDATION_RUNNING = "VALIDATION_RUNNING"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    HOLDOUT_RUNNING = "HOLDOUT_RUNNING"
    HISTORICAL_COMPLETE = "HISTORICAL_COMPLETE"
    CHALLENGER_CANDIDATE = "CHALLENGER_CANDIDATE"
    ARCHIVED = "ARCHIVED"


class StageOutcome(str, Enum):
    PASS = "PASS"
    CORPUS_FAIL = "CORPUS_FAIL"
    PERFORMANCE_FAIL = "PERFORMANCE_FAIL"
    INTEGRITY_ABORT = "INTEGRITY_ABORT"
    EXECUTION_FAIL = "EXECUTION_FAIL"


@dataclass(frozen=True, slots=True)
class EvidenceClassification:
    category: ResearchCategory
    reason_codes: tuple[str, ...] = ()
    evidence_integrity_blocker: bool = False
    requires_semantic_review: bool = False
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class ResearchQuestionSnapshot:
    question_record_id: UUID | str
    research_question_id: str
    created_on: date | str
    question: str
    why_it_matters: str
    status: str
    evidence_summary: Mapping[str, Any] = field(default_factory=dict)
    sample_size: int = 0
    required_data: tuple[Any, ...] = ()
    source_weekly_report_id: UUID | str | None = None
    source_agent_run_id: UUID | str | None = None
    linked_experiment_id: UUID | str | None = None
    category: ResearchCategory | None = None
    priority_score: int | None = None
    evidence_cutoff: datetime | str | None = None
    next_action: str | None = None
    last_reviewed_at: datetime | str | None = None
    created_at: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class QueueItem:
    research_question_id: str
    snapshot: ResearchQuestionSnapshot
    classification: EvidenceClassification
    priority_score: int
    severity: int
    recurrence: int
    research_value: int
    readiness: int
    estimated_compute_cost: int
    estimated_llm_cost: int


@dataclass(frozen=True, slots=True)
class ResearchLease:
    lease_key: str
    holder_run_id: UUID
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    scope: Mapping[str, Any] = field(default_factory=dict)
    operator_identity: str | None = None
    takeover_from_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: UUID
    run_key: str
    agent_version: str
    source_commit: str
    trigger: str
    started_at: datetime
    input_fingerprint: str
    trigger_reference: str | None = None
    completed_at: datetime | None = None
    evidence_cutoff: datetime | None = None
    input_artifacts: tuple[Mapping[str, Any], ...] = ()
    open_question_ids: tuple[str, ...] = ()
    proposed_actions: tuple[Mapping[str, Any], ...] = ()
    actions_taken: tuple[Mapping[str, Any], ...] = ()
    tools_invoked: tuple[Mapping[str, Any], ...] = ()
    experiment_ids: tuple[UUID, ...] = ()
    decision_ids: tuple[UUID, ...] = ()
    output_artifact: Mapping[str, Any] | tuple[Any, ...] | None = None
    approval_required: bool = False
    authorization_reference: str | None = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    error_summary: Mapping[str, Any] | None = None
    rationale_summary: Mapping[str, Any] | None = None
    llm_usage: Mapping[str, Any] = field(default_factory=lambda: {"invoked": False})
    operator_identity: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalEvidence:
    current_strategy: Mapping[str, Any]
    latest_daily_report: Mapping[str, Any] | None
    latest_weekly_report: Mapping[str, Any] | None
    research_questions: tuple[ResearchQuestionSnapshot, ...]
    experiments: tuple[Mapping[str, Any], ...]
    research_decisions: tuple[Mapping[str, Any], ...]
    agent_runs: tuple[Mapping[str, Any], ...] = ()
    evidence_cutoff: datetime | None = None


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        normalized = normalized.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=repr)
    return value


def deterministic_dict(value: Any) -> dict[str, Any]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("deterministic_dict requires a mapping or dataclass")
    return converted


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

