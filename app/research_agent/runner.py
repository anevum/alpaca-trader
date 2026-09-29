from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .adaptation_proposal import proposal_from_counterfactual
from .audit import deterministic_run_key, input_fingerprint
from .classification import classify_structured_evidence, report_evidence
from .evidence import (
    current_strategy_identity,
    experiment_by_key,
    experiment_protected_state,
)
from .models import CanonicalEvidence, QueueItem, ResearchCategory, deterministic_dict
from .policy import EDGE_DISCOVERY_V1_EXPERIMENT_KEY, RDR_V21_EXPERIMENT_KEY
from .queue import build_queue
from .strategy_health import compute_strategy_health


def _normalized_requirement(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _question_readiness(item: QueueItem) -> dict[str, Any]:
    if item.classification.evidence_integrity_blocker:
        return {
            "state": "BLOCKED",
            "reason_codes": ["ACTIVE_EVIDENCE_INTEGRITY_BLOCKER"],
            "missing_requirements": [],
        }

    if item.classification.category is not ResearchCategory.STRATEGY_HYPOTHESIS:
        return {
            "state": "MONITOR",
            "reason_codes": list(item.classification.reason_codes),
            "missing_requirements": [],
        }

    evidence = item.snapshot.evidence_summary
    required = [_normalized_requirement(value) for value in item.snapshot.required_data]
    missing: list[str] = []

    if any("multiple independent sessions" in value for value in required):
        try:
            sessions_observed = int(evidence.get("sessions_observed") or 0)
        except (TypeError, ValueError):
            sessions_observed = 0
        if sessions_observed < 2:
            missing.append("MULTIPLE_INDEPENDENT_SESSIONS")

    if evidence.get("ready_for_semantic_review") is False:
        missing.append("EXPLICIT_SEMANTIC_READINESS_FALSE")

    if missing:
        return {
            "state": "WAITING",
            "reason_codes": ["REQUIRED_EVIDENCE_NOT_READY"],
            "missing_requirements": missing,
        }

    if item.snapshot.status == "READY_FOR_RESEARCH":
        return {
            "state": "READY",
            "reason_codes": ["QUESTION_STATUS_READY_FOR_RESEARCH"],
            "missing_requirements": [],
        }

    if item.classification.requires_semantic_review and item.snapshot.status in {
        "OPEN",
        "MONITOR",
    }:
        return {
            "state": "READY",
            "reason_codes": ["DETERMINISTIC_REQUIREMENTS_SATISFIED"],
            "missing_requirements": [],
        }

    return {
        "state": "WAITING",
        "reason_codes": ["SEMANTIC_REVIEW_NOT_YET_WARRANTED"],
        "missing_requirements": [],
    }


def _queue_record(item: QueueItem) -> dict[str, Any]:
    readiness = _question_readiness(item)
    return {
        "research_question_id": item.research_question_id,
        "question_record_id": str(item.snapshot.question_record_id),
        "status": item.snapshot.status,
        "category": item.classification.category.value,
        "priority_score": item.priority_score,
        "evidence_integrity_blocker": (
            item.classification.evidence_integrity_blocker
        ),
        "requires_semantic_review": item.classification.requires_semantic_review,
        "semantic_readiness": readiness["state"],
        "semantic_readiness_reason_codes": readiness["reason_codes"],
        "missing_requirements": readiness["missing_requirements"],
        "reason_codes": list(item.classification.reason_codes),
        "priority_inputs": {
            "severity": item.severity,
            "recurrence": item.recurrence,
            "research_value": item.research_value,
            "readiness": item.readiness,
            "estimated_compute_cost": item.estimated_compute_cost,
            "estimated_llm_cost": item.estimated_llm_cost,
        },
    }


class ResearchAgentRunner:
    """Deterministic review coordinator with no execution or model dependencies."""

    def __init__(self, evidence: CanonicalEvidence):
        self.evidence = evidence
        self._seen_run_keys = {
            str(row.get("run_key"))
            for row in evidence.agent_runs
            if row.get("run_key")
        }

    def status(self) -> dict[str, Any]:
        version, name = current_strategy_identity(self.evidence)
        queue = build_queue(self.evidence.research_questions)
        rdr = experiment_by_key(self.evidence, RDR_V21_EXPERIMENT_KEY)
        edge = experiment_by_key(
            self.evidence,
            EDGE_DISCOVERY_V1_EXPERIMENT_KEY,
        )
        rdr_state = experiment_protected_state(rdr) if rdr else None
        if rdr_state is not None:
            rdr_state["interpretation"] = (
                "CORPUS_QUALITY_FAILURE_NOT_STRATEGY_REJECTION"
                if rdr_state.get("survivor_state")
                == "corpus_quality_failed_pre_performance"
                else "UNCLASSIFIED"
            )
        edge_closed = bool(
            edge
            and edge.get("status") == "rejected"
            and edge.get("survivor_state") == "all_rejected"
        )
        daily_report = self.evidence.latest_daily_report or {}
        persisted_asc = daily_report.get("adaptive_strategy_control")
        persisted_asc = (
            persisted_asc if isinstance(persisted_asc, Mapping) else {}
        )
        persisted_health = persisted_asc.get("strategy_health")
        persisted_health = (
            persisted_health if isinstance(persisted_health, Mapping) else None
        )

        nostra = daily_report.get("nostra")
        nostra = nostra if isinstance(nostra, Mapping) else {}
        session_state = nostra.get("session_state")
        session_state = (
            session_state if isinstance(session_state, Mapping) else {}
        )
        latest_nostra = session_state.get("latest")
        latest_nostra = (
            latest_nostra if isinstance(latest_nostra, Mapping) else None
        )

        strategy_health = (
            dict(persisted_health)
            if persisted_health is not None
            else compute_strategy_health(
                daily_report=self.evidence.latest_daily_report,
                weekly_report=self.evidence.latest_weekly_report,
                nostra_state=latest_nostra,
            )
        )

        persisted_proposals = persisted_asc.get("parameter_proposals")
        proposals: dict[str, Any] = (
            dict(persisted_proposals)
            if isinstance(persisted_proposals, Mapping)
            else {}
        )
        lab = daily_report.get("counterfactual_lab")
        if (
            not proposals
            and isinstance(lab, Mapping)
            and strategy_health.get("control_state") in {"ADAPT", "RESEARCH"}
        ):
            baseline = lab.get("baseline_parameters")
            rolling = lab.get("rolling_searches")
            baseline = baseline if isinstance(baseline, Mapping) else {}
            rolling = rolling if isinstance(rolling, Mapping) else {}
            for parameter in lab.get("proposal_ready_parameters") or []:
                result = rolling.get(parameter)
                current_value = baseline.get(parameter)
                if not isinstance(result, Mapping) or current_value in (None, ""):
                    continue
                proposal = proposal_from_counterfactual(
                    parameter=str(parameter),
                    current_value=current_value,
                    alternatives=result.get("results") or [],
                    control_state=str(strategy_health["control_state"]),
                    source_strategy_version=version,
                )
                if proposal is not None:
                    proposals[str(parameter)] = proposal

        strategy_health = {
            **strategy_health,
            "parameter_proposals": proposals,
            "proposal_count": len(proposals),
            "automatic_application_authorized": False,
            "nostra_state": latest_nostra,
            "shadow_validation": persisted_asc.get("shadow_validation"),
            "graen_validation": persisted_asc.get("graen_validation"),
            "promotion_previews": persisted_asc.get("promotion_previews") or {},
            "next_session_shadow_plan": persisted_asc.get(
                "next_session_shadow_plan"
            ),
            "canonical_asc_artifact_present": bool(persisted_asc),
        }
        return {
            "agent_version": "rhen-research-agent-v1-foundation",
            "mode": "DETERMINISTIC_ONLY",
            "live_execution_connected": False,
            "scheduler_configured": False,
            "llm_usage": {"invoked": False},
            "current_strategy": {"version_id": version, "strategy_name": name},
            "question_history_count": len(self.evidence.research_questions),
            "open_queue_count": len(queue),
            "experiment_count": len(self.evidence.experiments),
            "decision_count": len(self.evidence.research_decisions),
            "adaptive_strategy_control": strategy_health,
            "rdr_v2_1": rdr_state,
            "edge_discovery_v1": {
                "closed": edge_closed,
                "status": edge.get("status") if edge else None,
                "survivor_state": edge.get("survivor_state") if edge else None,
                "automatic_revival_allowed": False,
            },
        }

    def readiness(self, *, cadence: str = "daily") -> dict[str, Any]:
        if cadence not in {"daily", "weekly"}:
            raise ValueError("readiness cadence must be daily or weekly")
        review = (
            self.daily_review(dry_run=True)
            if cadence == "daily"
            else self.weekly_review(dry_run=True)
        )

        blockers: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        monitors: list[dict[str, Any]] = []
        classification = review.get("classification") or {}
        report_reason_codes = list(classification.get("reason_codes") or [])

        if review.get("report_integrity_blocker"):
            blockers.append(
                {
                    "scope": "report",
                    "code": "REPORT_INTEGRITY",
                    "trigger_reference": review.get("trigger_reference"),
                    "reason_codes": report_reason_codes,
                }
            )
        elif report_reason_codes:
            limitations.append(
                {
                    "scope": "report",
                    "code": "KNOWN_EVIDENCE_LIMITATION",
                    "trigger_reference": review.get("trigger_reference"),
                    "reason_codes": report_reason_codes,
                }
            )

        strategy_questions: list[dict[str, Any]] = []
        for row in review.get("queue") or []:
            if row.get("evidence_integrity_blocker"):
                blockers.append(
                    {
                        "scope": "research_question",
                        "code": "QUEUE_EVIDENCE_INTEGRITY",
                        "research_question_id": row.get("research_question_id"),
                        "status": row.get("status"),
                        "category": row.get("category"),
                        "priority_score": row.get("priority_score"),
                        "reason_codes": list(row.get("reason_codes") or []),
                    }
                )
            elif row.get("category") in {
                ResearchCategory.OPERATIONAL_DEFECT.value,
                ResearchCategory.DATA_QUALITY.value,
            }:
                monitors.append(
                    {
                        "scope": "research_question",
                        "code": "NONBLOCKING_MONITOR",
                        "research_question_id": row.get("research_question_id"),
                        "status": row.get("status"),
                        "category": row.get("category"),
                        "priority_score": row.get("priority_score"),
                        "reason_codes": list(row.get("reason_codes") or []),
                    }
                )

            if row.get("category") == ResearchCategory.STRATEGY_HYPOTHESIS.value:
                strategy_questions.append(
                    {
                        "research_question_id": row.get("research_question_id"),
                        "status": row.get("status"),
                        "priority_score": row.get("priority_score"),
                        "requires_semantic_review": bool(
                            row.get("requires_semantic_review")
                        ),
                        "semantic_readiness": row.get("semantic_readiness"),
                        "semantic_readiness_reason_codes": list(
                            row.get("semantic_readiness_reason_codes") or []
                        ),
                        "missing_requirements": list(
                            row.get("missing_requirements") or []
                        ),
                    }
                )

        ready_questions = [
            row for row in strategy_questions
            if row.get("semantic_readiness") == "READY"
        ]
        waiting_questions = [
            row for row in strategy_questions
            if row.get("semantic_readiness") == "WAITING"
        ]

        if blockers:
            state = "BLOCKED"
        elif ready_questions and review.get("semantic_review_warranted"):
            state = "READY"
        elif waiting_questions or monitors or limitations:
            state = "WAITING"
        else:
            state = "IDLE"

        return {
            "state": state,
            "cadence": cadence,
            "gpt_would_run_now": bool(review.get("semantic_review_warranted")),
            "blocker_count": len(blockers),
            "blockers": blockers,
            "limitation_count": len(limitations),
            "limitations": limitations,
            "monitor_count": len(monitors),
            "monitors": monitors,
            "strategy_question_count": len(strategy_questions),
            "ready_strategy_question_count": len(ready_questions),
            "waiting_strategy_question_count": len(waiting_questions),
            "strategy_questions": strategy_questions,
            "trigger_reference": review.get("trigger_reference"),
            "evidence_cutoff": review.get("evidence_cutoff"),
            "input_fingerprint": review.get("input_fingerprint"),
            "duplicate_run_key": bool(review.get("duplicate")),
            "read_only": True,
            "model_invoked": False,
            "persisted": False,
        }

    def daily_review(self, *, dry_run: bool) -> dict[str, Any]:
        if not dry_run:
            raise ValueError("foundation daily review is dry-run only")
        report = self.evidence.latest_daily_report
        if not report:
            raise ValueError("no canonical daily report is available")
        reference = str(report.get("session") or report.get("report_key") or "unknown")
        return self._review("daily", reference, report)

    def weekly_review(self, *, dry_run: bool) -> dict[str, Any]:
        if not dry_run:
            raise ValueError("foundation weekly review is dry-run only")
        report = self.evidence.latest_weekly_report
        if not report:
            raise ValueError("no canonical weekly report is available")
        reference = str(
            report.get("period_end")
            or report.get("week_end")
            or report.get("report_key")
            or "unknown"
        )
        return self._review("weekly", reference, report)

    def _review(
        self,
        cadence: str,
        reference: str,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        queue = build_queue(self.evidence.research_questions)
        evidence_material = {
            "cadence": cadence,
            "report": report,
            "current_strategy": self.evidence.current_strategy,
            "question_snapshots": [deterministic_dict(row) for row in self.evidence.research_questions],
            "experiments": list(self.evidence.experiments),
            "research_decisions": list(self.evidence.research_decisions),
            "evidence_cutoff": self.evidence.evidence_cutoff,
        }
        fingerprint = input_fingerprint(evidence_material)
        run_key = deterministic_run_key(
            cadence=cadence,
            trigger_reference=reference,
            fingerprint=fingerprint,
        )
        duplicate = run_key in self._seen_run_keys
        self._seen_run_keys.add(run_key)

        classification = classify_structured_evidence(report_evidence(report))
        queue_records = [_queue_record(item) for item in queue]
        blockers = [
            row for row in queue_records if row["evidence_integrity_blocker"]
        ]
        strategy_questions = [
            row
            for row in queue_records
            if row["category"] == ResearchCategory.STRATEGY_HYPOTHESIS.value
        ]
        ready_strategy_questions = [
            row for row in strategy_questions if row["semantic_readiness"] == "READY"
        ]
        waiting_strategy_questions = [
            row for row in strategy_questions if row["semantic_readiness"] == "WAITING"
        ]
        proposed_updates = [
            {
                "research_question_id": row["research_question_id"],
                "category": row["category"],
                "priority_score": row["priority_score"],
                "evidence_cutoff": (
                    self.evidence.evidence_cutoff.isoformat()
                    if self.evidence.evidence_cutoff
                    else None
                ),
                "write_performed": False,
            }
            for row in queue_records
        ]
        report_integrity_blocker = bool(classification.evidence_integrity_blocker)
        semantic_review_warranted = (
            bool(ready_strategy_questions)
            and not bool(blockers)
            and not report_integrity_blocker
        )

        return {
            "mode": "DRY_RUN",
            "cadence": cadence,
            "run_key": run_key,
            "input_fingerprint": fingerprint,
            "duplicate": duplicate,
            "trigger_reference": reference,
            "evidence_cutoff": (
                self.evidence.evidence_cutoff.isoformat()
                if self.evidence.evidence_cutoff
                else None
            ),
            "classification": deterministic_dict(classification),
            "queue": queue_records,
            "blocker_count": len(blockers) + int(report_integrity_blocker),
            "queue_blocker_count": len(blockers),
            "report_integrity_blocker": report_integrity_blocker,
            "strategy_question_count": len(strategy_questions),
            "ready_strategy_question_count": len(ready_strategy_questions),
            "waiting_strategy_question_count": len(waiting_strategy_questions),
            "semantic_review_warranted": semantic_review_warranted,
            "proposed_state_updates": proposed_updates,
            "experiment_recommendation": None,
            "llm_usage": {"invoked": False, "provider": None, "model": None},
            "mutations": {
                "audit_record_persisted": False,
                "research_questions_written": 0,
                "experiments_written": 0,
                "research_stages_opened": 0,
                "strategy_changes": 0,
                "broker_calls": 0,
                "market_bar_reads": 0,
            },
        }

