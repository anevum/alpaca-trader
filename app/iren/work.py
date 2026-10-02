from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re

import httpx
from typing import Any, Awaitable, Callable

UTC = timezone.utc

TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_JOB_STATES = {"QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"}
OBJECTIVE_STATES = {"LOCKED", "ACTIVE", "BLOCKED", "READY", "COMPLETE", "FUTURE", "OBSOLETE"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_command(command: str) -> str:
    value = re.sub(r"\s+", " ", command.strip().lower()).rstrip(" ?!.")
    if not value:
        return "STATUS"
    if value in {
        "status",
        "status?",
        "current status",
        "iren status",
        "current iren status",
        "where are we",
        "where are we at",
        "update",
        "progress",
    }:
        return "STATUS"
    if value in {"what next", "what's next", "whats next", "next", "what do we do next", "what should we do next"}:
        return "NEXT"
    if value in {"do that", "do it", "continue", "proceed", "go", "run it", "start it"}:
        return "EXECUTE_NEXT"
    if value in {"fix it", "repair it", "resolve it"}:
        return "FIX"
    if value in {"what needs me", "needs me", "what requires me", "decisions"}:
        return "DECISIONS"
    if value.startswith("status "):
        return "STATUS"
    return "DIRECTIVE"


def _completed_objectives(objectives: list[dict[str, Any]]) -> set[str]:
    return {_text(row.get("objective_key")) for row in objectives if row.get("status") == "COMPLETE"}


def _has_active_job(objective_id: str, jobs: list[dict[str, Any]]) -> bool:
    return any(_text(row.get("objective_key")) == objective_id and row.get("status") in ACTIVE_JOB_STATES for row in jobs)


def objective_ready(objective: dict[str, Any], objectives: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> bool:
    status = _text(objective.get("status")).upper()
    if status != "READY":
        return False
    objective_id = _text(objective.get("objective_key"))
    if not objective_id or _has_active_job(objective_id, jobs):
        return False
    completed = _completed_objectives(objectives)
    dependencies = objective.get("dependencies") or []
    if not isinstance(dependencies, list):
        dependencies = []
    return all(_text(dep) in completed for dep in dependencies)


def _open_incidents(control_state: dict[str, Any]) -> list[dict[str, Any]]:
    incidents = control_state.get("incidents") or {}
    if not isinstance(incidents, dict):
        return []
    rows = []
    for key, raw in incidents.items():
        if not isinstance(raw, dict) or str(raw.get("status") or "").upper() != "OPEN":
            continue
        rows.append({
            "key": str(key),
            "severity": str(raw.get("severity") or "warning").lower(),
            "reason": str(raw.get("reason") or "open_incident"),
            "opened_at": raw.get("opened_at"),
        })
    rows.sort(key=lambda row: (0 if row["severity"] == "critical" else 1, row["key"]))
    return rows


def _objective_action(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    job_type = metadata.get("job_type")
    if not job_type:
        job_type = "CONTROL_RECONCILE" if str(row.get("owner_system") or "IREN").upper() == "IREN" else "AGENT_WORK"
    return {
        "objective_key": row.get("objective_key"),
        "title": row.get("title"),
        "owner_system": row.get("owner_system") or "IREN",
        "job_type": job_type,
        "protected_action": bool(row.get("protected_action")),
        "reason": reason,
        "success_criteria": row.get("success_criteria") or {},
        "description": row.get("description") or "",
    }


def choose_next_action(
    snapshot: dict[str, Any],
    control_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    objectives = list(snapshot.get("objectives") or [])
    jobs = list(snapshot.get("jobs") or [])
    control_state = control_state or {}

    incidents = _open_incidents(control_state)
    if incidents:
        incident = incidents[0]
        return {
            "objective_key": None,
            "title": f"Resolve {incident['key']}",
            "owner_system": "IREN",
            "job_type": "CONTROL_RECONCILE",
            "protected_action": False,
            "reason": f"open_{incident['severity']}_incident",
            "success_criteria": {"incident_closed": incident["key"]},
            "description": (
                f"Inspect the live evidence for {incident['key']} "
                f"({incident['reason']}), determine the safest concrete next action, "
                "execute only non-protected control-plane work, and escalate any "
                "protected mutation that requires Devon."
            ),
            "incident": incident,
        }

    ready = [row for row in objectives if objective_ready(row, objectives, jobs)]
    if ready:
        ready.sort(
            key=lambda row: (
                -int(row.get("priority") or 0),
                _text(row.get("created_at")),
                _text(row.get("objective_key")),
            )
        )
        return _objective_action(ready[0], reason="highest_priority_ready_objective")

    active = [
        row for row in objectives
        if str(row.get("status") or "").upper() == "ACTIVE"
        and not _has_active_job(_text(row.get("objective_key")), jobs)
    ]
    if active:
        active.sort(
            key=lambda row: (
                -int(row.get("priority") or 0),
                _text(row.get("created_at")),
                _text(row.get("objective_key")),
            )
        )
        return _objective_action(active[0], reason="highest_priority_active_objective")

    blocked_jobs = [
        row for row in jobs
        if str(row.get("status") or "").upper() in {"WAITING", "BLOCKED", "NEEDS_APPROVAL"}
    ]
    if blocked_jobs:
        blocked_jobs.sort(
            key=lambda row: (
                -int(row.get("priority") or 0),
                _text(row.get("created_at")),
            )
        )
        row = blocked_jobs[0]
        return {
            "objective_key": row.get("objective_key"),
            "title": f"Unblock {row.get('title') or row.get('job_type') or 'IREN work'}",
            "owner_system": row.get("owner_system") or "IREN",
            "job_type": "CONTROL_RECONCILE",
            "protected_action": False,
            "reason": "blocked_or_waiting_work",
            "success_criteria": {"job_unblocked": row.get("job_id")},
            "description": (
                "Inspect why this durable job is waiting or blocked, identify whether "
                "the missing dependency can be repaired automatically, and escalate "
                "only if human authority is actually required."
            ),
            "blocked_job_id": row.get("job_id"),
        }

    return {
        "objective_key": None,
        "title": "Reconcile system and derive next objective",
        "owner_system": "IREN",
        "job_type": "CONTROL_RECONCILE",
        "protected_action": False,
        "reason": "continuous_planner_fallback",
        "success_criteria": {"next_action_derived": True},
        "description": (
            "Re-read live control state, objectives, jobs, schedules, and incidents; "
            "identify the highest-value safe next action and surface it as the current "
            "IREN objective."
        ),
    }


def _status_message(summary: dict[str, Any]) -> str:
    state = str(summary.get("control_state") or "UNKNOWN")
    incidents = list(summary.get("open_incidents") or [])
    active_jobs = int(summary.get("active_jobs") or 0)
    waiting_jobs = int(summary.get("waiting_jobs") or 0)
    next_action = summary.get("next_action") or {}
    next_title = str(next_action.get("title") or "Reconcile system and derive next objective")
    parts = [f"IREN is {state}."]
    if incidents:
        parts.append(f"{len(incidents)} incident{'s are' if len(incidents) != 1 else ' is'} open.")
    else:
        parts.append("No control-plane incidents are open.")
    if active_jobs:
        parts.append(f"{active_jobs} active job{'s' if active_jobs != 1 else ''}; {waiting_jobs} waiting or blocked.")
    parts.append(f"Next: {next_title}.")
    return " ".join(parts)


def status_summary(snapshot: dict[str, Any], control_state: dict[str, Any]) -> dict[str, Any]:
    objectives = list(snapshot.get("objectives") or [])
    jobs = list(snapshot.get("jobs") or [])
    active_jobs = [row for row in jobs if row.get("status") in ACTIVE_JOB_STATES]
    waiting = [
        row for row in jobs
        if str(row.get("status") or "").upper() in {"WAITING", "BLOCKED", "NEEDS_APPROVAL"}
    ]
    blocked = [row for row in objectives if row.get("status") == "BLOCKED"]
    decisions = [row for row in jobs if row.get("status") == "NEEDS_APPROVAL" or row.get("requires_human") is True]
    complete = sum(1 for row in objectives if row.get("status") == "COMPLETE")
    incidents = _open_incidents(control_state)
    current = choose_next_action(snapshot, control_state)
    summary = {
        "control_state": control_state.get("state") or "UNKNOWN",
        "objective_count": len(objectives),
        "objectives_complete": complete,
        "active_jobs": len(active_jobs),
        "waiting_jobs": len(waiting),
        "blocked_objectives": len(blocked),
        "requires_human": len(decisions),
        "open_incidents": incidents,
        "next_action": current,
    }
    summary["message"] = _status_message(summary)
    return summary


def build_job(next_action: dict[str, Any], requested_by: str, source: str) -> dict[str, Any]:
    protected = bool(next_action.get("protected_action"))
    return {
        "objective_key": next_action.get("objective_key"),
        "title": next_action.get("title") or "IREN work item",
        "instructions": next_action.get("description") or next_action.get("title") or "Continue objective.",
        "owner_system": next_action.get("owner_system") or "IREN",
        "job_type": next_action.get("job_type") or "AGENT_WORK",
        "status": "NEEDS_APPROVAL" if protected else "QUEUED",
        "priority": 100,
        "protected_action": protected,
        "requires_human": protected,
        "requested_by": requested_by,
        "requested_via": source,
        "metadata": {"success_criteria": next_action.get("success_criteria") or {}},
    }


@dataclass
class CommandResult:
    intent: str
    response: dict[str, Any]
    job: dict[str, Any] | None = None


def process_command(command: str, snapshot: dict[str, Any], control_state: dict[str, Any], *, requested_by: str, source: str) -> CommandResult:
    intent = normalize_command(command)
    summary = status_summary(snapshot, control_state)
    if intent == "STATUS":
        return CommandResult(intent, summary)
    if intent == "NEXT":
        action = summary.get("next_action")
        title = str((action or {}).get("title") or "Reconcile system and derive next objective")
        return CommandResult(intent, {
            **summary,
            "message": f"Next: {title}.",
        })
    if intent == "DECISIONS":
        decisions = [row for row in snapshot.get("jobs") or [] if row.get("status") == "NEEDS_APPROVAL" or row.get("requires_human") is True]
        return CommandResult(intent, {"message": "Items requiring human authority.", "items": decisions, **summary})
    if intent in {"EXECUTE_NEXT", "FIX"}:
        action = summary.get("next_action") or choose_next_action(snapshot, control_state)
        job = build_job(action, requested_by, source)
        return CommandResult(intent, {
            **summary,
            "message": f"IREN queued: {action.get('title')}.",
            "next_action": action,
        }, job)
    return CommandResult(intent, {
        "message": "Directive captured as durable work for IREN triage.",
        **summary,
    }, {
        "objective_key": None,
        "title": command.strip()[:180] or "Operator directive",
        "instructions": command.strip(),
        "owner_system": "IREN",
        "job_type": "AGENT_WORK",
        "status": "QUEUED",
        "priority": 90,
        "protected_action": False,
        "requires_human": False,
        "requested_by": requested_by,
        "requested_via": source,
        "metadata": {"intent": "DIRECTIVE"},
    })


class IrenWorkEngine:
    def __init__(
        self,
        gateway: Callable[..., Awaitable[dict[str, Any]]],
        control_state: Callable[[], dict[str, Any]],
        notify: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        self.gateway = gateway
        self.control_state = control_state
        self.notify = notify
        self.interval_seconds = max(2.0, interval_seconds)
        self.stop_event = __import__("asyncio").Event()
        self.task = None
        self.last_error: str | None = None
        self.last_command_at: str | None = None
        self.last_job_at: str | None = None
        self.executor_url = os.getenv("IREN_EXECUTOR_URL", "").strip()
        self.executor_token = os.getenv("IREN_EXECUTOR_TOKEN", "").strip()

    async def snapshot(self) -> dict[str, Any]:
        return await self.gateway("iren_work_snapshot")

    async def enqueue_command(self, command: str, *, source: str, requested_by: str) -> dict[str, Any]:
        return await self.gateway(
            "iren_command_create",
            command_text=command[:4000],
            source=source[:40],
            requested_by=requested_by[:160],
        )

    async def _notify(self, text: str, context: dict[str, Any] | None = None) -> None:
        if self.notify is not None:
            await self.notify(text, context or {})

    async def _process_commands(self) -> None:
        claimed = await self.gateway("iren_commands_claim", owner="iren-work-engine", limit=5)
        for command in claimed.get("commands") or []:
            command_id = command.get("command_id")
            text = _text(command.get("command_text"))
            source = _text(command.get("source")) or "unknown"
            requested_by = _text(command.get("requested_by")) or "operator"
            try:
                snapshot = await self.snapshot()
                result = process_command(text, snapshot, self.control_state(), requested_by=requested_by, source=source)
                job_row = None
                if result.job:
                    created = await self.gateway("iren_job_create", job=result.job)
                    job_row = created.get("job")
                response = {**result.response, "intent": result.intent, "job": job_row}
                await self.gateway("iren_command_complete", command_id=command_id, status="SUCCEEDED", response=response, linked_job_id=(job_row or {}).get("job_id"))
                self.last_command_at = datetime.now(UTC).isoformat()
                if source == "slack":
                    line = response.get("message") or "IREN command completed."
                    if job_row:
                        line += f" Job {job_row.get('job_id')} is {job_row.get('status')}."
                    await self._notify(line, command.get("context") or {})
            except Exception as exc:
                self.last_error = type(exc).__name__
                await self.gateway("iren_command_complete", command_id=command_id, status="FAILED", response={"error": type(exc).__name__})

    async def _route_job(self, job: dict[str, Any]) -> None:
        job_id = _text(job.get("job_id"))
        job_type = _text(job.get("job_type")) or "AGENT_WORK"
        routable = {"AGENT_WORK", "GRAEN_RESEARCH_PROBLEM", "SOFTWARE_BUILD"}
        if job_type not in routable:
            await self.gateway(
                "iren_job_update",
                job_id=job_id,
                status="BLOCKED",
                error={"reason": "unsupported_job_type", "job_type": job_type},
            )
            return

        if not self.executor_url or len(self.executor_token) < 32:
            await self.gateway(
                "iren_job_update",
                job_id=job_id,
                status="WAITING",
                result={
                    "reason": "execution_router_not_configured",
                    "handoff_ready": True,
                    "owner_system": job.get("owner_system"),
                },
            )
            return

        payload = {
            "job_id": job_id,
            "objective_key": job.get("objective_key"),
            "title": job.get("title") or job_type,
            "instructions": job.get("instructions") or "",
            "owner_system": job.get("owner_system") or "IREN",
            "job_type": job_type,
            "protected_action": bool(job.get("protected_action")),
            "requires_human": bool(job.get("requires_human")),
            "metadata": job.get("metadata") or {},
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    self.executor_url,
                    headers={"x-anevum-scheduler-token": self.executor_token},
                    json=payload,
                )
                response.raise_for_status()
                routed = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            await self.gateway(
                "iren_job_update",
                job_id=job_id,
                status="WAITING",
                result={
                    "reason": "execution_router_unavailable",
                    "error_type": type(exc).__name__,
                    "handoff_ready": True,
                },
            )
            return

        status = str(routed.get("status") or "WAITING").upper()
        if status not in {"WAITING", "BLOCKED", "NEEDS_APPROVAL", "SUCCEEDED", "FAILED"}:
            status = "BLOCKED"
            routed = {**routed, "reason": "invalid_executor_status"}

        await self.gateway(
            "iren_job_update",
            job_id=job_id,
            status=status,
            result={
                "execution_router": routed,
                "routed_at": datetime.now(UTC).isoformat(),
            },
            error={} if status != "FAILED" else {"reason": routed.get("reason") or "executor_failed"},
        )

    async def _reconcile_accidental_status_jobs(self) -> None:
        snapshot = await self.snapshot()
        for job in snapshot.get("jobs") or []:
            if str(job.get("status") or "").upper() != "WAITING":
                continue
            if str((job.get("metadata") or {}).get("intent") or "").upper() != "DIRECTIVE":
                continue
            if normalize_command(str(job.get("title") or "")) != "STATUS":
                continue
            result = job.get("result") or {}
            router = result.get("execution_router") if isinstance(result, dict) else {}
            reason = str((router or {}).get("reason") or "")
            if reason != "external_execution_backend_not_configured":
                continue
            await self.gateway(
                "iren_job_update",
                job_id=_text(job.get("job_id")),
                status="CANCELLED",
                result={
                    "reason": "retired_parser_artifact",
                    "message": "Cancelled obsolete status directive created before status normalization fix.",
                },
            )

    async def _execute_jobs(self) -> None:
        claimed = await self.gateway("iren_jobs_claim", owner="iren-work-engine", limit=3)
        for job in claimed.get("jobs") or []:
            job_id = _text(job.get("job_id"))
            try:
                if _text(job.get("job_type")).upper() == "CONTROL_RECONCILE":
                    snapshot = await self.snapshot()
                    summary = status_summary(snapshot, self.control_state())
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result={
                            "message": summary.get("message"),
                            "next_action": summary.get("next_action"),
                            "control_state": summary.get("control_state"),
                            "open_incidents": summary.get("open_incidents"),
                            "planner": "iren_continuous_planner_v1",
                        },
                    )
                else:
                    await self._route_job(job)
                self.last_job_at = datetime.now(UTC).isoformat()
            except Exception as exc:
                self.last_error = type(exc).__name__
                await self.gateway(
                    "iren_job_update",
                    job_id=job_id,
                    status="FAILED",
                    error={"type": type(exc).__name__, "message": str(exc)[:500]},
                )

    async def tick(self) -> None:
        await self._reconcile_accidental_status_jobs()
        await self._process_commands()
        await self._execute_jobs()
        self.last_error = None

    async def run(self) -> None:
        import asyncio
        while not self.stop_event.is_set():
            try:
                await self.tick()
            except Exception as exc:
                self.last_error = type(exc).__name__
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        import asyncio
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="iren-work-engine")

    async def stop(self) -> None:
        import asyncio
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
