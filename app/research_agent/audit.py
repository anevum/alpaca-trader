from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping
from uuid import UUID, uuid4

from .init import AGENT_VERSION
from .models import AgentRun, AgentRunStatus, canonical_json, deterministic_dict


RATIONALE_FIELDS = frozenset(
    {
        "conclusion",
        "supporting_evidence",
        "contradicting_evidence",
        "uncertainties",
    }
)


class DuplicateRunKey(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def input_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def deterministic_run_key(
    *,
    cadence: str,
    trigger_reference: str,
    fingerprint: str,
) -> str:
    cadence = cadence.strip().lower()
    if cadence not in {"daily", "weekly"}:
        raise ValueError("cadence must be daily or weekly")
    if not trigger_reference.strip():
        raise ValueError("trigger_reference is required")
    return (
        f"research-agent:v1:{cadence}:"
        f"{trigger_reference.strip()}:{fingerprint[:16]}"
    )


def _validated_rationale(
    rationale: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if rationale is None:
        return None
    unexpected = set(rationale) - RATIONALE_FIELDS
    if unexpected:
        raise ValueError(
            f"unsupported rationale fields: {', '.join(sorted(unexpected))}"
        )
    return dict(rationale)


def create_run(
    *,
    run_key: str,
    source_commit: str,
    trigger: str,
    input_fingerprint_value: str,
    trigger_reference: str | None = None,
    evidence_cutoff: datetime | None = None,
    input_artifacts: tuple[Mapping[str, Any], ...] = (),
    open_question_ids: tuple[str, ...] = (),
    operator_identity: str | None = None,
    started_at: datetime | None = None,
    run_id: UUID | None = None,
) -> AgentRun:
    if not run_key.strip():
        raise ValueError("run_key is required")
    if len(input_fingerprint_value) != 64:
        raise ValueError("input fingerprint must be a SHA-256 hex digest")
    return AgentRun(
        run_id=run_id or uuid4(),
        run_key=run_key,
        agent_version=AGENT_VERSION,
        source_commit=source_commit,
        trigger=trigger,
        trigger_reference=trigger_reference,
        started_at=started_at or utc_now(),
        evidence_cutoff=evidence_cutoff,
        input_artifacts=input_artifacts,
        input_fingerprint=input_fingerprint_value,
        open_question_ids=open_question_ids,
        llm_usage={"invoked": False, "provider": None, "model": None},
        operator_identity=operator_identity,
    )


def complete_run(
    run: AgentRun,
    *,
    status: AgentRunStatus,
    proposed_actions: tuple[Mapping[str, Any], ...] = (),
    actions_taken: tuple[Mapping[str, Any], ...] = (),
    tools_invoked: tuple[Mapping[str, Any], ...] = (),
    output_artifact: Mapping[str, Any] | tuple[Any, ...] | None = None,
    rationale_summary: Mapping[str, Any] | None = None,
    error_summary: Mapping[str, Any] | None = None,
    approval_required: bool = False,
    authorization_reference: str | None = None,
    completed_at: datetime | None = None,
) -> AgentRun:
    if run.status is not AgentRunStatus.RUNNING:
        raise ValueError("only a RUNNING audit record may be completed")
    if status is AgentRunStatus.RUNNING:
        raise ValueError("completion status cannot be RUNNING")
    if run.llm_usage.get("invoked") is not False:
        raise ValueError("foundation runs must remain deterministic-only")
    return replace(
        run,
        completed_at=completed_at or utc_now(),
        proposed_actions=proposed_actions,
        actions_taken=actions_taken,
        tools_invoked=tools_invoked,
        output_artifact=output_artifact,
        approval_required=approval_required,
        authorization_reference=authorization_reference,
        status=status,
        error_summary=dict(error_summary) if error_summary is not None else None,
        rationale_summary=_validated_rationale(rationale_summary),
    )


def fail_run(
    run: AgentRun,
    *,
    code: str,
    message: str,
    completed_at: datetime | None = None,
) -> AgentRun:
    return complete_run(
        run,
        status=AgentRunStatus.FAILED,
        error_summary={"code": code, "message": message},
        completed_at=completed_at,
    )


def run_record(run: AgentRun) -> dict[str, Any]:
    record = deterministic_dict(run)
    if "chain_of_thought" in record or "reasoning" in record:
        raise AssertionError("hidden reasoning must never be persisted")
    return record


class InMemoryAuditRepository:
    """Test/local idempotency store; not a substitute for canonical persistence."""

    def __init__(self) -> None:
        self._by_key: dict[str, AgentRun] = {}
        self._lock = RLock()

    def create(self, run: AgentRun) -> None:
        with self._lock:
            if run.run_key in self._by_key:
                raise DuplicateRunKey(run.run_key)
            self._by_key[run.run_key] = run

    def save(self, run: AgentRun) -> None:
        with self._lock:
            existing = self._by_key.get(run.run_key)
            if existing is None or existing.run_id != run.run_id:
                raise KeyError(run.run_key)
            self._by_key[run.run_key] = run

    def get(self, run_key: str) -> AgentRun | None:
        with self._lock:
            return self._by_key.get(run_key)

    def is_duplicate(self, run_key: str) -> bool:
        return self.get(run_key) is not None

