from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from .audit import complete_run, create_run, run_record
from .evidence import CanonicalEvidenceReader, EvidenceReadError
from .init import AGENT_VERSION
from .models import AgentRunStatus, CanonicalEvidence
from .runner import ResearchAgentRunner
from .theory import public_theory_projection, theory_status


UTC = timezone.utc


def _source_commit() -> str:
    return (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("RHEN_RESEARCH_SOURCE_COMMIT")
        or ""
    ).strip()


def _truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def sanitized_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    def rows(name: str) -> list[dict[str, Any]]:
        return [
            {
                "scope": row.get("scope"),
                "code": row.get("code"),
                "reason_codes": list(row.get("reason_codes") or []),
            }
            for row in readiness.get(name) or []
        ]

    waiting_requirements = sorted(
        {
            str(requirement)
            for question in readiness.get("strategy_questions") or []
            if question.get("semantic_readiness") == "WAITING"
            for requirement in question.get("missing_requirements") or []
            if requirement
        }
    )
    return {
        "state": readiness.get("state"),
        "cadence": readiness.get("cadence"),
        "gpt_would_run_now": bool(readiness.get("gpt_would_run_now")),
        "blocker_count": int(readiness.get("blocker_count") or 0),
        "blockers": rows("blockers"),
        "limitation_count": int(readiness.get("limitation_count") or 0),
        "limitations": rows("limitations"),
        "monitor_count": int(readiness.get("monitor_count") or 0),
        "monitors": rows("monitors"),
        "strategy_question_count": int(
            readiness.get("strategy_question_count") or 0
        ),
        "ready_strategy_question_count": int(
            readiness.get("ready_strategy_question_count") or 0
        ),
        "waiting_strategy_question_count": int(
            readiness.get("waiting_strategy_question_count") or 0
        ),
        "waiting_requirements": waiting_requirements,
        "trigger_reference": readiness.get("trigger_reference"),
        "evidence_cutoff": readiness.get("evidence_cutoff"),
        "read_only": True,
        "model_invoked": False,
        "persisted": False,
    }


class EmbeddedResearchReview:
    """Deterministic evidence review hosted by RHEN Core.

    This class intentionally imports no semantic model or external research
    client. It may classify canonical evidence and persist an audit record, but
    it cannot call a model, mutate live strategy state, or place broker orders.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self.started_at = datetime.now(UTC)
        self.lock = asyncio.Lock()
        self.last_review_started_at: str | None = None
        self.last_review_completed_at: str | None = None
        self.last_review_status: str | None = None
        self.last_error: str | None = None

    def health(self) -> dict[str, Any]:
        enabled = _truthy("RHEN_RESEARCH_EVIDENCE_ENABLED", True)
        return {
            "ok": enabled,
            "service": "rhen-research-evidence",
            "agent_version": AGENT_VERSION,
            "embedded": True,
            "independent_runtime": False,
            "deterministic_only": True,
            "enabled": enabled,
            "model_invoked": False,
            "semantic_model": {
                "enabled": False,
                "execution_surface": "operator_or_chatgpt_work",
            },
            "research_director": {
                "enabled": False,
                "execution_surface": "operator_or_chatgpt_work",
                "execution_authority": False,
                "broker_calls": False,
            },
            "authority": {
                "live_strategy_mutation": False,
                "live_risk_or_sizing_mutation": False,
                "broker_calls": False,
                "production_promotion": False,
            },
            "runtime_provenance": {
                "system_version": AGENT_VERSION,
                "git_commit": _source_commit() or None,
                "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID") or None,
                "runtime_started_at": self.started_at.isoformat(),
            },
            "operations": {
                "last_review_started_at": self.last_review_started_at,
                "last_review_completed_at": self.last_review_completed_at,
                "last_review_status": self.last_review_status,
                "last_error": self.last_error,
                "review_in_progress": self.lock.locked(),
            },
        }

    async def evidence(self) -> CanonicalEvidence:
        document = await asyncio.to_thread(self.store.canonical_evidence)
        try:
            return CanonicalEvidenceReader(document).read()
        except EvidenceReadError as exc:
            raise RuntimeError(
                f"canonical research evidence is invalid: {exc}"
            ) from exc

    async def readiness(self, cadence: str = "daily") -> dict[str, Any]:
        evidence = await self.evidence()
        return ResearchAgentRunner(evidence).readiness(cadence=cadence)

    async def status(self) -> dict[str, Any]:
        evidence = await self.evidence()
        runner = ResearchAgentRunner(evidence)
        return {
            "service": self.health(),
            "canonical_research_state": runner.status(),
            "research_readiness": runner.readiness(cadence="daily"),
            "mathematics_and_theory": theory_status(),
        }

    async def public_readiness(self) -> dict[str, Any]:
        return sanitized_readiness(await self.readiness("daily"))

    def public_theory(self) -> dict[str, Any]:
        return public_theory_projection()

    async def review(
        self,
        cadence: str,
        *,
        expected_session: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        if cadence not in {"daily", "weekly"}:
            raise ValueError("cadence must be daily or weekly")
        if self.lock.locked():
            raise RuntimeError("research_review_in_progress")

        async with self.lock:
            self.last_review_started_at = datetime.now(UTC).isoformat()
            try:
                result = await self._review(
                    cadence,
                    expected_session=expected_session,
                    persist=persist,
                )
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.last_review_status = "FAILED"
                raise
            else:
                self.last_error = None
                self.last_review_status = str(result.get("status") or "UNKNOWN")
                return result
            finally:
                self.last_review_completed_at = datetime.now(UTC).isoformat()

    async def _review(
        self,
        cadence: str,
        *,
        expected_session: str | None,
        persist: bool,
    ) -> dict[str, Any]:
        evidence = await self.evidence()
        runner = ResearchAgentRunner(evidence)
        deterministic = (
            runner.daily_review(dry_run=True)
            if cadence == "daily"
            else runner.weekly_review(dry_run=True)
        )

        trigger_reference = str(deterministic.get("trigger_reference") or "")
        if expected_session and trigger_reference != expected_session:
            raise RuntimeError("canonical_report_not_current")

        if deterministic.get("duplicate"):
            return {
                "status": "NOOP",
                "reason": "duplicate_run_key",
                "run_key": deterministic["run_key"],
                "model_invoked": False,
                "persisted": False,
                "approval_required": False,
            }

        run = create_run(
            run_key=str(deterministic["run_key"]),
            source_commit=_source_commit(),
            trigger=cadence,
            trigger_reference=trigger_reference,
            evidence_cutoff=evidence.evidence_cutoff,
            input_fingerprint_value=str(deterministic["input_fingerprint"]),
            open_question_ids=tuple(
                str(row.get("research_question_id"))
                for row in deterministic.get("queue") or []
                if row.get("research_question_id")
            ),
            input_artifacts=(
                {
                    "type": "canonical_evidence",
                    "source": "rhen-core",
                    "evidence_cutoff": (
                        evidence.evidence_cutoff.isoformat()
                        if evidence.evidence_cutoff
                        else None
                    ),
                },
            ),
            operator_identity="rhen-core-deterministic-research",
        )

        blocker_count = int(deterministic.get("blocker_count") or 0)
        status = (
            AgentRunStatus.BLOCKED
            if blocker_count > 0
            else AgentRunStatus.NOOP
        )
        rationale = {
            "conclusion": (
                "Canonical evidence contains an integrity blocker."
                if blocker_count > 0
                else "Deterministic evidence review completed; semantic review was not invoked."
            ),
            "supporting_evidence": [
                f"blocker_count={blocker_count}",
                "strategy_question_count="
                + str(deterministic.get("strategy_question_count") or 0),
            ],
            "contradicting_evidence": [],
            "uncertainties": [],
        }
        output_artifact = {
            "deterministic_review": deterministic,
            "semantic_review": None,
            "safety": {
                "live_strategy_changes": 0,
                "live_risk_or_sizing_changes": 0,
                "broker_calls": 0,
                "research_stages_opened": 0,
                "methodology_freezes": 0,
                "quarantine_reads": 0,
                "production_promotions": 0,
            },
        }
        completed = complete_run(
            run,
            status=status,
            actions_taken=(
                (
                    {
                        "action": "persist_agent_run",
                        "scope": "audit_only",
                    },
                )
                if persist
                else ()
            ),
            tools_invoked=(
                {
                    "tool": "canonical_evidence_store",
                    "mode": "read_only",
                },
            ),
            output_artifact=output_artifact,
            rationale_summary=rationale,
            approval_required=False,
        )
        record = run_record(completed)

        persisted = False
        if persist:
            response = await asyncio.to_thread(
                self.store.record_research_audit,
                record,
            )
            persisted = bool(response.get("ok"))

        return {
            "status": completed.status.value,
            "run_key": completed.run_key,
            "run_id": str(completed.run_id),
            "model_invoked": False,
            "persisted": persisted,
            "search_ledger_persisted": False,
            "approval_required": False,
            "semantic_review_required": bool(
                deterministic.get("semantic_review_warranted")
            ),
            "output": output_artifact,
            "llm_usage": {
                "invoked": False,
                "provider": None,
                "model": None,
            },
        }
