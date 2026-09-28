from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .models import CanonicalEvidence, ResearchCategory, ResearchQuestionSnapshot


class EvidenceReadError(ValueError):
    pass


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _question(row: Mapping[str, Any]) -> ResearchQuestionSnapshot:
    category_value = row.get("category")
    category = ResearchCategory(category_value) if category_value else None
    required_data = tuple(_sequence(row.get("required_data")))
    return ResearchQuestionSnapshot(
        question_record_id=str(row.get("question_record_id") or ""),
        research_question_id=str(row.get("research_question_id") or ""),
        created_on=str(row.get("created_on") or ""),
        source_weekly_report_id=row.get("source_weekly_report_id"),
        source_agent_run_id=row.get("source_agent_run_id"),
        evidence_summary=(
            row.get("evidence_summary")
            if isinstance(row.get("evidence_summary"), Mapping)
            else {}
        ),
        sample_size=int(row.get("sample_size") or 0),
        question=str(row.get("question") or ""),
        why_it_matters=str(row.get("why_it_matters") or ""),
        required_data=required_data,
        status=str(row.get("status") or ""),
        linked_experiment_id=row.get("linked_experiment_id"),
        category=category,
        priority_score=(
            int(row["priority_score"])
            if row.get("priority_score") is not None
            else None
        ),
        evidence_cutoff=row.get("evidence_cutoff"),
        next_action=row.get("next_action"),
        last_reviewed_at=row.get("last_reviewed_at"),
        created_at=row.get("created_at"),
    )


def _latest_by_key(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
    stamp: str,
) -> tuple[Mapping[str, Any], ...]:
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        stable = str(row.get(key) or "")
        if not stable:
            continue
        existing = latest.get(stable)
        row_stamp = parse_datetime(row.get(stamp)) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        existing_stamp = (
            parse_datetime(existing.get(stamp))
            if existing is not None
            else None
        ) or datetime.min.replace(tzinfo=timezone.utc)
        if existing is None or (row_stamp, str(row)) >= (existing_stamp, str(existing)):
            latest[stable] = row
    return tuple(latest[key] for key in sorted(latest))


class CanonicalEvidenceReader:
    """Normalizes a canonical database export without reading market bars."""

    def __init__(self, document: Mapping[str, Any]):
        self.document = document

    @classmethod
    def from_file(cls, path: str | Path) -> "CanonicalEvidenceReader":
        payload = json.loads(Path(path).read_text())
        if not isinstance(payload, Mapping):
            raise EvidenceReadError("evidence file must contain a JSON object")
        return cls(payload)

    def read(self) -> CanonicalEvidence:
        strategy = self.document.get("current_strategy")
        if not isinstance(strategy, Mapping):
            raise EvidenceReadError("current_strategy is missing from canonical evidence")

        daily = self.document.get("latest_daily_report")
        weekly = self.document.get("latest_weekly_report")
        daily = daily if isinstance(daily, Mapping) else None
        weekly = weekly if isinstance(weekly, Mapping) else None

        question_rows = [
            row
            for row in _sequence(self.document.get("research_questions"))
            if isinstance(row, Mapping)
        ]
        questions = tuple(_question(row) for row in question_rows)

        experiment_rows = [
            row
            for row in _sequence(self.document.get("experiments"))
            if isinstance(row, Mapping)
        ]
        experiments = _latest_by_key(
            experiment_rows,
            key="experiment_key",
            stamp="created_at",
        )

        decision_rows = [
            row
            for row in _sequence(self.document.get("research_decisions"))
            if isinstance(row, Mapping)
            and str(row.get("status") or "").casefold() in {"final", "superseded"}
        ]
        decisions = _latest_by_key(
            decision_rows,
            key="decision_key",
            stamp="decided_at",
        )

        agent_runs = tuple(
            row
            for row in _sequence(self.document.get("agent_runs"))
            if isinstance(row, Mapping)
        )
        cutoff = parse_datetime(self.document.get("evidence_cutoff"))
        if cutoff is None:
            candidates = [
                parse_datetime((daily or {}).get("generated_at")),
                parse_datetime((weekly or {}).get("generated_at")),
            ]
            cutoff = max((item for item in candidates if item), default=None)

        search_ledger = self.document.get("search_ledger")
        search_ledger = (
            dict(search_ledger)
            if isinstance(search_ledger, Mapping)
            else {}
        )

        return CanonicalEvidence(
            current_strategy=dict(strategy),
            latest_daily_report=dict(daily) if daily else None,
            latest_weekly_report=dict(weekly) if weekly else None,
            research_questions=questions,
            experiments=experiments,
            research_decisions=decisions,
            agent_runs=agent_runs,
            search_ledger=search_ledger,
            evidence_cutoff=cutoff,
        )

def current_strategy_identity(evidence: CanonicalEvidence) -> tuple[str, str]:
    version = str(
        evidence.current_strategy.get("version_id")
        or evidence.current_strategy.get("strategy_version_id")
        or ""
    )
    name = str(evidence.current_strategy.get("strategy_name") or "")
    if not version or not name:
        raise EvidenceReadError("current strategy identity is incomplete")
    return version, name


def experiment_by_key(
    evidence: CanonicalEvidence,
    experiment_key: str,
) -> Mapping[str, Any] | None:
    return next(
        (
            row
            for row in evidence.experiments
            if row.get("experiment_key") == experiment_key
        ),
        None,
    )


def experiment_protected_state(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "experiment_key": row.get("experiment_key"),
        "status": row.get("status"),
        "workflow_state": row.get("workflow_state"),
        "stage_reached": row.get("stage_reached"),
        "survivor_state": row.get("survivor_state"),
        "terminal_decision": row.get("terminal_decision"),
        "terminal_reason": row.get("terminal_reason"),
        "validation_eligible": bool(row.get("validation_eligible", False)),
        "validation_opened": bool(row.get("validation_opened", False)),
        "holdout_opened": bool(row.get("holdout_opened", False)),
        "quarantine_accessed": bool(row.get("quarantine_accessed", False)),
    }
