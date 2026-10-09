"""Deterministic research agenda derived from canonical daily evidence.

This is an evidence triage contract, not an alpha discovery claim.
It cannot mutate strategies, place orders, access brokers, or promote results.
"""
from __future__ import annotations

from hashlib import sha256
from decimal import Decimal, InvalidOperation
from .candidate_cohort_audit import AUDIT_VERSION, MIN_SCREEN_CANDIDATES, MIN_OUTCOME_COVERAGE
import json
import re
from typing import Any, Mapping


AGENDA_VERSION = "rhen-rule-set-agenda-v1"
READY_STATES = frozenset({"READY", "COMPLETE"})
HARD_WARNINGS = (
    "truncat", "shedd", "unavailable", "unmeasurable", "degraded",
    "unreconstructable", "incomplete", "missing", "failed", "error",
    "ambiguous_attribution", "unlinked_executable_signal",
)
CHALLENGERS = (
    {
        "name": "relative-volume-150",
        "hypothesis": (
            "A point-in-time volume ratio of at least 1.50 reduces "
            "unprofitable momentum entries after all estimated costs."
        ),
        "entry_rules": [
            {"indicator": "relative_volume", "threshold": "1.5", "min_bars": 8}
        ],
    },
    {
        "name": "trend-persistence-067",
        "hypothesis": (
            "At least 67 percent upward completed-bar transitions improve "
            "entry selectivity after costs without losing too many winners."
        ),
        "entry_rules": [
            {"indicator": "trend_persistence", "threshold": "0.67", "min_bars": 8}
        ],
    },
    {
        "name": "volume-trend-confirmed",
        "hypothesis": (
            "Combining volume and trend confirmation beats the unchanged "
            "baseline in unseen sessions after friction and multiplicity."
        ),
        "entry_rules": [
            {"indicator": "relative_volume", "threshold": "1.5", "min_bars": 8},
            {"indicator": "trend_persistence", "threshold": "0.67", "min_bars": 8},
        ],
    },
)


def _dictionary(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _integer(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def build_rule_set_agenda(report: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a fixed, predeclared control/challenger slate.

    Research readiness does not equal live parity or promotion. All
    speculative studies are withheld when required source coverage is absent.
    """
    if not isinstance(report, Mapping):
        raise ValueError("daily research report must be a mapping")
    session = str(report.get("session") or "")
    fingerprint = str(report.get("source_fingerprint") or "")
    strategy = str(report.get("strategy_version_id") or "")
    warnings = report.get("data_quality_warnings")
    warnings = warnings if isinstance(warnings, list) else []
    broker = _dictionary(report.get("broker_history"))
    candidate = _dictionary(report.get("candidate_forward_evidence"))
    status = _dictionary(candidate.get("status"))
    readiness = _dictionary(candidate.get("readiness"))
    cohort = _dictionary(candidate.get("cohort_audit"))
    recon = _dictionary(report.get("reconstruction"))
    runtime = _dictionary(report.get("runtime"))
    persistence = _dictionary(runtime.get("persistence"))
    live_offline = _dictionary(report.get("live_vs_offline_consistency"))
    blockers: list[str] = []
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session):
        blockers.append("SESSION_ID_UNVERIFIED")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", fingerprint):
        blockers.append("SOURCE_FINGERPRINT_MISSING")
    if not strategy:
        blockers.append("STRATEGY_VERSION_MISSING")
    if broker.get("pagination") != "EXHAUSTED_WITHIN_BOUNDS":
        blockers.append("BROKER_FILL_ORDER_PAGINATION_UNVERIFIED")
    if recon.get("unmatched_sell_qty"):
        if any(str(value) not in {"0", "0.0", "0.00"} for value in recon["unmatched_sell_qty"].values()):
            blockers.append("UNMATCHED_BROKER_FILLS")
    if persistence.get("storage_analytics_shedding") or _integer(persistence.get("shed_count")):
        blockers.append("ANALYTICS_RETENTION_LOSS")
    if str(readiness.get("state") or "").upper() not in READY_STATES:
        blockers.append("CANDIDATE_FORWARD_READINESS_NOT_VERIFIED")
    if cohort.get("audit_version") != AUDIT_VERSION:
        blockers.append("CANONICAL_15M_COHORT_AUDIT_MISSING")
    else:
        try:
            n = int(cohort.get("candidate_count"))
            c = Decimal(str(cohort.get("coverage_15m")))
            if not c.is_finite():
                raise ValueError("nonfinite coverage")
        except (TypeError, ValueError, InvalidOperation):
            blockers.append("CANONICAL_15M_COHORT_AUDIT_INVALID")
        else:
            if n < MIN_SCREEN_CANDIDATES or c < MIN_OUTCOME_COVERAGE:
                blockers.append("CANONICAL_15M_COHORT_COVERAGE_INSUFFICIENT")
        if cohort.get("blocking_reasons") or cohort.get("state") != "READY":
            blockers.append("CANONICAL_15M_COHORT_AUDIT_BLOCKED")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", str(cohort.get("cohort_fingerprint") or "")):
            blockers.append("CANONICAL_15M_COHORT_FINGERPRINT_MISSING")
    if _integer(status.get("incomplete_rows")) or _integer(status.get("error_rows")):
        blockers.append("CANDIDATE_FORWARD_OUTCOMES_INCOMPLETE")
    summary = live_offline.get("summary")
    if isinstance(summary, list) and any(
        _integer(row.get("unreconstructable"))
        or (
            str(row.get("match_state") or "").upper() == "UNRECONSTRUCTABLE"
            and _integer(row.get("count"))
        )
        for row in summary if isinstance(row, Mapping)
    ):
        blockers.append("LIVE_VS_REPLAY_EVENTS_UNRECONSTRUCTABLE")
    if any(
        any(term in str(warning).lower() for term in HARD_WARNINGS)
        for warning in warnings
    ):
        blockers.append("CANONICAL_REPORT_HAS_HARD_DATA_WARNINGS")

    stage = (
        "READY_FOR_BOUNDED_OFFLINE_SCREEN"
        if not blockers else "AWAITING_COMPLETE_EVIDENCE"
    )
    manifest = {
        "version": AGENDA_VERSION,
        "session": session,
        "source_fingerprint": fingerprint,
        "control_strategy": strategy,
        "rule_sets": CHALLENGERS,
        "multiple_comparisons": len(CHALLENGERS),
    }
    return {
        "schema_version": AGENDA_VERSION,
        "session": session,
        "source_fingerprint": fingerprint,
        "strategy_version_id": strategy,
        "research_state": stage,
        "blocking_evidence": sorted(set(blockers)),
        "warning_count": len(warnings),
        "frozen_research_manifest": manifest,
        "manifest_sha256": sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "after_cost_comparison_required": True,
        "market_regime_and_session_holdout_required": True,
        "asof_universe_and_bars_required": True,
        "live_broker_fill_parity": "UNVERIFIED",
        "research_only": True,
        "execution_authority": False,
        "automatic_promotion_authorized": False,
        "new_live_entries_authorized": False,
    }
