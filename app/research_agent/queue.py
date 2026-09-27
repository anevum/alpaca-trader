from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .classification import classify_structured_evidence
from .models import (
    EvidenceClassification,
    QueueItem,
    ResearchCategory,
    ResearchQuestionSnapshot,
)
from .priority import calculate_priority, prioritize


OPEN_STATUSES = frozenset({"OPEN", "MONITOR", "READY_FOR_RESEARCH"})


def _stamp(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def latest_question_snapshots(
    snapshots: Iterable[ResearchQuestionSnapshot],
) -> tuple[ResearchQuestionSnapshot, ...]:
    latest: dict[str, ResearchQuestionSnapshot] = {}
    for snapshot in snapshots:
        stable = snapshot.research_question_id
        if not stable:
            continue
        existing = latest.get(stable)
        snapshot_key = (_stamp(snapshot.created_at), str(snapshot.question_record_id))
        existing_key = (
            (_stamp(existing.created_at), str(existing.question_record_id))
            if existing is not None
            else None
        )
        if existing is None or snapshot_key > existing_key:
            latest[stable] = snapshot
    return tuple(latest[key] for key in sorted(latest))


def _bounded(value: Any, maximum: int, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(maximum, parsed))


def _classification(snapshot: ResearchQuestionSnapshot) -> EvidenceClassification:
    inferred = classify_structured_evidence(snapshot.evidence_summary)
    if snapshot.category is None:
        return inferred
    return EvidenceClassification(
        category=snapshot.category,
        reason_codes=inferred.reason_codes,
        evidence_integrity_blocker=snapshot.category
        in {ResearchCategory.OPERATIONAL_DEFECT, ResearchCategory.DATA_QUALITY},
        requires_semantic_review=inferred.requires_semantic_review,
        ambiguous=inferred.ambiguous,
    )


def _priority_inputs(
    snapshot: ResearchQuestionSnapshot,
    classification: EvidenceClassification,
) -> dict[str, int]:
    evidence = snapshot.evidence_summary
    explicit = evidence.get("priority_inputs")
    values: Mapping[str, Any] = explicit if isinstance(explicit, Mapping) else evidence
    sessions = _bounded(evidence.get("sessions_observed"), 4)
    return {
        "severity": _bounded(values.get("severity"), 3),
        "recurrence": _bounded(values.get("recurrence"), 3, max(0, sessions - 1)),
        "research_value": _bounded(values.get("research_value"), 3),
        "readiness": _bounded(
            values.get("readiness"),
            2,
            2 if snapshot.status == "READY_FOR_RESEARCH" else 0,
        ),
        "estimated_compute_cost": _bounded(
            values.get("estimated_compute_cost"), 3
        ),
        "estimated_llm_cost": _bounded(values.get("estimated_llm_cost"), 3),
    }


def build_queue(
    snapshots: Iterable[ResearchQuestionSnapshot],
) -> list[QueueItem]:
    items: list[QueueItem] = []
    for snapshot in latest_question_snapshots(snapshots):
        if snapshot.status not in OPEN_STATUSES:
            continue
        classification = _classification(snapshot)
        inputs = _priority_inputs(snapshot, classification)
        score = calculate_priority(classification.category, **inputs)
        items.append(
            QueueItem(
                research_question_id=snapshot.research_question_id,
                snapshot=snapshot,
                classification=classification,
                priority_score=score,
                **inputs,
            )
        )
    return prioritize(items)
