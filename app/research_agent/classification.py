from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import EvidenceClassification, ResearchCategory


DATA_QUALITY_STATES = frozenset(
    {
        "INCOMPLETE",
        "CORPUS_FAIL",
        "CORPUS_QUALITY_FAILED_PRE_PERFORMANCE",
        "PARTIAL_BACKFILL",
        "STALE_INPUT",
        "UNRECONSTRUCTABLE",
    }
)
OPERATIONAL_STATES = frozenset(
    {
        "ERROR",
        "FAILED",
        "BLOCKED",
        "DEGRADED",
        "RECONCILIATION_FAILED",
        "RUNTIME_ERROR",
        "EXECUTION_ERROR",
    }
)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _integer(mapping: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(mapping.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None)
    return ()


def classify_structured_evidence(
    evidence: Mapping[str, Any] | None,
) -> EvidenceClassification:
    """Classify explicit machine-readable evidence only.

    Free-form prose is deliberately not interpreted. Unknown shapes and
    conflicting/ambiguous inputs fall back to NOISE_INSUFFICIENT and request
    semantic review instead of manufacturing a conclusion.
    """

    item = evidence or {}
    reason_codes: list[str] = []

    corpus_gate = _upper(item.get("corpus_gate") or item.get("corpus_gate_state"))
    survivor_state = _upper(item.get("survivor_state"))
    completeness = _upper(item.get("completeness_state"))
    data_status = _upper(item.get("data_status"))
    missing_sessions = _integer(item, "missing_session_count")
    incomplete_rows = _integer(item, "incomplete_rows")
    unreconstructable = _integer(item, "unreconstructable_count")
    pagination_complete = item.get("pagination_complete")
    performance_evaluated = item.get("performance_evaluated")

    if corpus_gate in {"FAIL", "FAILED", "CORPUS_FAIL"}:
        reason_codes.append("CORPUS_GATE_FAILED")
    if survivor_state == "CORPUS_QUALITY_FAILED_PRE_PERFORMANCE":
        reason_codes.append("CORPUS_QUALITY_FAILED_PRE_PERFORMANCE")
    if completeness in DATA_QUALITY_STATES or data_status in DATA_QUALITY_STATES:
        reason_codes.append("INCOMPLETE_CANONICAL_EVIDENCE")
    if missing_sessions:
        reason_codes.append("MISSING_SESSIONS")
    if incomplete_rows:
        reason_codes.append("INCOMPLETE_FORWARD_OUTCOMES")
    if unreconstructable:
        reason_codes.append("UNRECONSTRUCTABLE_EVIDENCE")
    if pagination_complete is False:
        reason_codes.append("PAGINATION_INCOMPLETE")

    if reason_codes:
        return EvidenceClassification(
            category=ResearchCategory.DATA_QUALITY,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            evidence_integrity_blocker=True,
            requires_semantic_review=False,
        )

    operational_statuses = {
        _upper(item.get("status")),
        _upper(item.get("operational_status")),
        _upper(item.get("runtime_status")),
        _upper(item.get("reconciliation_status")),
        _upper(item.get("execution_status")),
    }
    operational_counts = sum(
        _integer(item, key)
        for key in (
            "runtime_error_count",
            "reconciliation_failure_count",
            "execution_failure_count",
            "operational_incident_count",
        )
    )
    if operational_counts or operational_statuses.intersection(OPERATIONAL_STATES):
        return EvidenceClassification(
            category=ResearchCategory.OPERATIONAL_DEFECT,
            reason_codes=("EXPLICIT_OPERATIONAL_FAILURE",),
            evidence_integrity_blocker=True,
        )

    if bool(item.get("all_recorded_as_operational")):
        return EvidenceClassification(
            category=ResearchCategory.OPERATIONAL_DEFECT,
            reason_codes=("CANONICAL_OPERATIONAL_INCIDENTS",),
            evidence_integrity_blocker=True,
        )

    if bool(item.get("risk_sizing_observation")) or any(
        key in item
        for key in (
            "position_limit_utilization",
            "portfolio_stop_risk_utilization",
            "capital_constraint_count",
        )
    ):
        return EvidenceClassification(
            category=ResearchCategory.RISK_SIZING_OBSERVATION,
            reason_codes=("STRUCTURED_RISK_SIZING_OBSERVATION",),
            requires_semantic_review=True,
        )

    strategy_keys = {
        "expectancy",
        "profit_factor",
        "win_rate",
        "thesis_exit_count",
        "strategy_sample_count",
        "worst_period_expectancy",
    }
    if strategy_keys.intersection(item) and performance_evaluated is not False:
        return EvidenceClassification(
            category=ResearchCategory.STRATEGY_HYPOTHESIS,
            reason_codes=("STRUCTURED_STRATEGY_EVIDENCE",),
            requires_semantic_review=True,
        )

    explicit_codes = {_upper(code) for code in _strings(item.get("warning_codes"))}
    if explicit_codes.intersection(DATA_QUALITY_STATES):
        return EvidenceClassification(
            category=ResearchCategory.DATA_QUALITY,
            reason_codes=tuple(sorted(explicit_codes.intersection(DATA_QUALITY_STATES))),
            evidence_integrity_blocker=True,
        )
    if explicit_codes.intersection(OPERATIONAL_STATES):
        return EvidenceClassification(
            category=ResearchCategory.OPERATIONAL_DEFECT,
            reason_codes=tuple(sorted(explicit_codes.intersection(OPERATIONAL_STATES))),
            evidence_integrity_blocker=True,
        )

    return EvidenceClassification(
        category=ResearchCategory.NOISE_INSUFFICIENT,
        reason_codes=("AMBIGUOUS_OR_INSUFFICIENT_STRUCTURED_EVIDENCE",),
        requires_semantic_review=True,
        ambiguous=True,
    )


def report_evidence(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"completeness_state": "INCOMPLETE", "missing_session_count": 1}
    forward_status = (
        (report.get("candidate_forward_evidence") or {}).get("status") or {}
        if isinstance(report.get("candidate_forward_evidence"), Mapping)
        else {}
    )
    live_offline = report.get("live_vs_offline_consistency") or {}
    live_summary = (
        live_offline.get("summary") or []
        if isinstance(live_offline, Mapping)
        else []
    )
    unreconstructable = sum(
        _integer(row, "unreconstructable")
        for row in live_summary
        if isinstance(row, Mapping)
    )
    return {
        "completeness_state": report.get("completeness_state"),
        "missing_session_count": len(report.get("missing_trading_sessions") or []),
        "incomplete_rows": _integer(forward_status, "incomplete_rows")
        + _integer(forward_status, "error_rows"),
        "unreconstructable_count": unreconstructable,
        "warning_codes": report.get("warning_codes") or [],
        "performance_evaluated": report.get("performance_evaluated"),
    }

