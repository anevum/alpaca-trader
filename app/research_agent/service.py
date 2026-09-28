from __future__ import annotations

import asyncio
import hmac
import json
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.slack_notifier import SlackNotifier

from .audit import complete_run, create_run, run_record
from .gateway import ResearchGateway, ResearchGatewayError
from .models import AgentRunStatus
from .runner import ResearchAgentRunner
from .theory import public_theory_projection, theory_status
from .semantic import (
    OpenAISemanticReviewer,
    RUNTIME_VERSION,
    SemanticReviewError,
    apply_semantic_output,
    evidence_packet,
)


FORBIDDEN_RUNTIME_VARIABLES = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "EXECUTION_ENABLED",
    "BOT_ARMED",
    "LIVE_TRADING",
    "I_ACKNOWLEDGE_LIVE_TRADING",
)


class ReviewRequest(BaseModel):
    cadence: Literal["daily", "weekly"] = "daily"
    invoke_model: bool = False
    persist: bool = True
    expected_session: date | None = None


def _truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _source_commit() -> str:
    return (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("RHEN_RESEARCH_SOURCE_COMMIT")
        or ""
    ).strip()


def _isolation_violations() -> list[str]:
    violations: list[str] = []
    for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET"):
        value = os.environ.get(name, "").strip()
        if value and value != "DISABLED":
            violations.append(name)
    for name in (
        "EXECUTION_ENABLED",
        "BOT_ARMED",
        "LIVE_TRADING",
        "I_ACKNOWLEDGE_LIVE_TRADING",
    ):
        if _truthy(name):
            violations.append(name)
    return violations


def _gateway() -> ResearchGateway:
    return ResearchGateway(
        os.environ.get("RHEN_RESEARCH_GATEWAY_URL", ""),
        os.environ.get("RHEN_RESEARCH_GATEWAY_TOKEN", ""),
    )


def _require_operator(token: str | None) -> None:
    expected = os.environ.get("RHEN_RESEARCH_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="operator token is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _health() -> dict:
    violations = _isolation_violations()
    gateway = _gateway()
    model_enabled = _truthy("RHEN_RESEARCH_MODEL_ENABLED")
    key_present = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    commit_bound = bool(_source_commit())
    enabled = _truthy("RHEN_RESEARCH_AGENT_ENABLED")
    return {
        "ok": enabled and gateway.configured and commit_bound and not violations,
        "service": "rhen-research-agent",
        "agent_version": RUNTIME_VERSION,
        "theory_program": theory_status(),
        "enabled": enabled,
        "canonical_gateway_configured": gateway.configured,
        "source_commit_bound": commit_bound,
        "semantic_model": {
            "enabled": model_enabled,
            "provider": "openai" if model_enabled else None,
            "model": os.environ.get("RHEN_RESEARCH_MODEL", "gpt-5.6-terra"),
            "reasoning_effort": os.environ.get(
                "RHEN_RESEARCH_REASONING_EFFORT", "high"
            ),
            "credentials_configured": key_present,
        },
        "authority": {
            "live_strategy_mutation": False,
            "live_risk_or_sizing_mutation": False,
            "broker_calls": False,
            "stage_opening": False,
            "methodology_freeze": False,
            "quarantine_access": False,
            "production_promotion": False,
        },
        "scheduler_configured": _truthy("RHEN_RESEARCH_SCHEDULER_CONFIGURED"),
        "autorun": _truthy("RHEN_RESEARCH_AUTORUN"),
        "persistence_scope": "research_agent_runs_and_search_ledger",
        "broker_credentials_present": bool(violations),
        "isolation_violations": violations,
    }


app = FastAPI(title="RHEN Research Agent v1", version="1.0")
review_lock = asyncio.Lock()
notifications = SlackNotifier(SimpleNamespace(slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", "")))
operational_state = {"last_review_started_at": None, "last_review_completed_at": None,
                     "last_review_status": None, "last_error": None}


def _operation(action: str, message: str) -> None:
    event = {"at": datetime.now(timezone.utc).isoformat(), "kind": "research_agent",
             "action": action, "message": message}
    print(json.dumps({"event": "rhen_research_operation", **event}), flush=True)
    notifications.record_event(event)


@app.on_event("startup")
async def verify_canonical_readiness():
    await notifications.start()
    state = _health()
    if not state["ok"]:
        _operation("error", "Research agent startup failed its configuration/isolation check.")
        await notifications.stop()
        raise RuntimeError("research agent isolation/configuration check failed")
    try:
        evidence = await _gateway().fetch_evidence()
        review = ResearchAgentRunner(evidence).daily_review(dry_run=True)
    except ResearchGatewayError as exc:
        _operation("error", "Research startup could not read canonical evidence after bounded retries.")
        await notifications.stop()
        raise RuntimeError(f"canonical research gateway readiness failed: {exc}") from exc
    print(
        json.dumps(
            {
                "event": "rhen_research_agent_ready",
                "agent_version": RUNTIME_VERSION,
                "evidence_cutoff": (
                    evidence.evidence_cutoff.isoformat()
                    if evidence.evidence_cutoff
                    else None
                ),
                "blocker_count": int(review.get("blocker_count") or 0),
                "strategy_question_count": int(
                    review.get("strategy_question_count") or 0
                ),
                "semantic_review_warranted": bool(
                    review.get("semantic_review_warranted")
                ),
                "dry_run": True,
                "persisted": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )


@app.on_event("shutdown")
async def stop_notifications():
    await notifications.stop()


def _sanitized_readiness(readiness: dict) -> dict:
    def rows(name: str) -> list[dict]:
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


@app.get("/health")
async def health():
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    state["source_commit"] = _source_commit()
    state["operations"] = {**operational_state, "review_in_progress": review_lock.locked()}
    state["slack_notifications"] = notifications.status()
    return state


@app.get("/v1/readiness/public")
async def public_readiness():
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail={"state": "UNAVAILABLE"})
    try:
        evidence = await _gateway().fetch_evidence()
    except ResearchGatewayError as exc:
        raise HTTPException(status_code=502, detail="canonical readiness unavailable") from exc
    readiness = ResearchAgentRunner(evidence).readiness(cadence="daily")
    return _sanitized_readiness(readiness)


@app.get("/v1/theory/public")
async def public_theory():
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail={"state": "UNAVAILABLE"})
    return public_theory_projection()


@app.get("/v1/status")
async def status(x_rhen_agent_admin_token: str | None = Header(default=None)):
    _require_operator(x_rhen_agent_admin_token)
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    try:
        evidence = await _gateway().fetch_evidence()
    except ResearchGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    runner = ResearchAgentRunner(evidence)
    return {
        "service": state,
        "canonical_research_state": runner.status(),
        "research_readiness": runner.readiness(cadence="daily"),
        "mathematics_and_theory": theory_status(),
    }


@app.get("/v1/readiness")
async def readiness(
    cadence: Literal["daily", "weekly"] = "daily",
    x_rhen_agent_admin_token: str | None = Header(default=None),
):
    _require_operator(x_rhen_agent_admin_token)
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    try:
        evidence = await _gateway().fetch_evidence()
    except ResearchGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ResearchAgentRunner(evidence).readiness(cadence=cadence)


@app.post("/v1/review")
async def review(
    request: ReviewRequest,
    x_rhen_agent_admin_token: str | None = Header(default=None),
):
    _require_operator(x_rhen_agent_admin_token)
    if review_lock.locked():
        raise HTTPException(status_code=409, detail="review_in_progress")
    async with review_lock:
        operational_state["last_review_started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            result = await asyncio.wait_for(_review(request, x_rhen_agent_admin_token), timeout=240)
        except Exception as exc:
            operational_state["last_error"] = type(exc).__name__
            operational_state["last_review_status"] = "FAILED"
            _operation("error", f"Research request failed ({type(exc).__name__}); completion is unconfirmed.")
            if isinstance(exc, TimeoutError):
                raise HTTPException(status_code=504, detail="research_review_timeout") from exc
            raise
        previous_error = operational_state["last_error"]
        operational_state["last_error"] = "review_failed" if result["status"] == "FAILED" else None
        operational_state["last_review_completed_at"] = datetime.now(timezone.utc).isoformat()
        operational_state["last_review_status"] = result["status"]
        if result.get("persisted"):
            action = "error" if result["status"] == "FAILED" else "completed"
            _operation(action, f"{request.cadence} research {result['status']}; audit persisted; run {result['run_id']}.")
        if previous_error and result["status"] != "FAILED":
            _operation("recovered", "Research review path recovered and completed.")
        return result


async def _review(request: ReviewRequest, x_rhen_agent_admin_token: str | None):
    _require_operator(x_rhen_agent_admin_token)
    state = _health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)

    gateway = _gateway()
    try:
        evidence = await gateway.fetch_evidence()
    except ResearchGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    runner = ResearchAgentRunner(evidence)
    deterministic = (
        runner.daily_review(dry_run=True)
        if request.cadence == "daily"
        else runner.weekly_review(dry_run=True)
    )

    if request.expected_session and deterministic.get("trigger_reference") != request.expected_session.isoformat():
        raise HTTPException(status_code=409, detail="canonical_report_not_current")

    if deterministic.get("duplicate"):
        return {
            "status": "NOOP",
            "reason": "duplicate_run_key",
            "run_key": deterministic["run_key"],
            "model_invoked": False,
            "persisted": False,
        }

    run = create_run(
        run_key=str(deterministic["run_key"]),
        source_commit=_source_commit(),
        trigger=request.cadence,
        trigger_reference=str(deterministic["trigger_reference"]),
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
                "gateway": "research-agent-gateway",
                "evidence_cutoff": (
                    evidence.evidence_cutoff.isoformat()
                    if evidence.evidence_cutoff
                    else None
                ),
            },
        ),
        operator_identity="rhen-research-agent-service",
    )
    run = replace(run, agent_version=RUNTIME_VERSION)

    model_invoked = False
    semantic = None
    llm_usage = {"invoked": False, "provider": None, "model": None}
    status_value = AgentRunStatus.NOOP
    approval_required = False
    proposed_actions: tuple[dict, ...] = ()
    error_summary = None
    rationale = {
        "conclusion": "Deterministic review completed without semantic model invocation.",
        "supporting_evidence": [
            f"blocker_count={deterministic.get('blocker_count', 0)}",
            f"strategy_question_count={deterministic.get('strategy_question_count', 0)}",
        ],
        "contradicting_evidence": [],
        "uncertainties": [],
    }

    if int(deterministic.get("blocker_count") or 0) > 0:
        status_value = AgentRunStatus.BLOCKED
        rationale["conclusion"] = (
            "Canonical evidence contains an integrity blocker; semantic review was not invoked."
        )
    elif request.invoke_model and not deterministic.get("semantic_review_warranted"):
        status_value = AgentRunStatus.NOOP
        rationale["conclusion"] = (
            "Deterministic gates found no strategy question warranting semantic review."
        )
    elif request.invoke_model:
        if not _truthy("RHEN_RESEARCH_MODEL_ENABLED"):
            raise HTTPException(status_code=409, detail="semantic model is not enabled")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not configured")
        reviewer = OpenAISemanticReviewer(
            api_key,
            model=os.environ.get("RHEN_RESEARCH_MODEL", "gpt-5.6-terra"),
            reasoning_effort=os.environ.get(
                "RHEN_RESEARCH_REASONING_EFFORT", "high"
            ),
        )
        try:
            raw_semantic, llm_usage = await reviewer.review(
                evidence_packet(evidence, deterministic)
            )
            model_invoked = True
            semantic = apply_semantic_output(
                raw_semantic,
                run_id=run.run_id,
                source_commit=_source_commit(),
                evidence_cutoff=evidence.evidence_cutoff,
            )
        except (SemanticReviewError, ValueError) as exc:
            status_value = AgentRunStatus.BLOCKED
            error_summary = {
                "code": "SEMANTIC_REVIEW_REJECTED",
                "message": str(exc),
            }
            rationale = {
                "conclusion": "Semantic output was rejected by the bounded runtime.",
                "supporting_evidence": [],
                "contradicting_evidence": [str(exc)],
                "uncertainties": [],
            }
        else:
            rationale = semantic["rationale"]
            if semantic["action"] == "PROPOSE_EXPERIMENT":
                proposal = semantic.get("proposal") or {}
                accepted = bool(semantic.get("proposal_accepted"))
                status_value = (
                    AgentRunStatus.COMPLETED if accepted else AgentRunStatus.BLOCKED
                )
                approval_required = accepted
                proposed_actions = (
                    {
                        "action": "experiment_proposal",
                        "proposal_id": proposal.get("proposal_id"),
                        "revision": proposal.get("revision"),
                        "proposal_hash": proposal.get("proposal_hash"),
                        "deterministic_design_passed": accepted,
                        "approval_required": accepted,
                        "write_performed": False,
                        "stage_opened": False,
                    },
                )
            elif semantic["action"] == "BLOCKED":
                status_value = AgentRunStatus.BLOCKED
            else:
                status_value = AgentRunStatus.NOOP

    search_ledger = (
        semantic.get("search_ledger")
        if isinstance(semantic, dict)
        else None
    )

    output_artifact = {
        "deterministic_review": deterministic,
        "semantic_review": semantic,
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
        status=status_value,
        proposed_actions=proposed_actions,
        actions_taken=(
            (
                {
                    "action": "persist_agent_run",
                    "scope": "audit_and_search_ledger"
                    if search_ledger is not None
                    else "audit_only",
                },
            )
            if request.persist and _truthy("RHEN_RESEARCH_PERSIST_RUNS", True)
            else ()
        ),
        tools_invoked=(
            {"tool": "canonical_evidence_gateway", "mode": "read_only"},
            *(
                ({"tool": "openai_responses", "mode": "semantic_only"},)
                if model_invoked
                else ()
            ),
        ),
        output_artifact=output_artifact,
        rationale_summary=rationale,
        error_summary=error_summary,
        approval_required=approval_required,
    )
    completed = replace(completed, llm_usage=llm_usage)
    record = run_record(completed)

    persisted = False
    search_ledger_persisted = False
    if request.persist and _truthy("RHEN_RESEARCH_PERSIST_RUNS", True):
        try:
            if isinstance(search_ledger, dict):
                persistence = await gateway.persist_run_with_search_ledger(
                    record,
                    search_ledger,
                )
                search_ledger_persisted = bool(
                    persistence.get("search_ledger_recorded", False)
                )
            else:
                await gateway.persist_run(record)
            persisted = True
        except ResearchGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "status": completed.status.value,
        "run_key": completed.run_key,
        "run_id": str(completed.run_id),
        "model_invoked": model_invoked,
        "persisted": persisted,
        "search_ledger_persisted": search_ledger_persisted,
        "approval_required": completed.approval_required,
        "output": output_artifact,
        "llm_usage": llm_usage,
    }
