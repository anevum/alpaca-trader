from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Awaitable, Callable

UTC = timezone.utc

TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_JOB_STATES = {"QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"}
OBJECTIVE_STATES = {"LOCKED", "ACTIVE", "BLOCKED", "READY", "COMPLETE", "FUTURE", "OBSOLETE"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_command(command: str) -> str:
    value = re.sub(r"\s+", " ", command.strip().lower())
    if not value:
        return "STATUS"
    if value in {"status", "status?", "where are we", "where are we at", "update", "progress"}:
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
    return {_text(row.get("objective_id")) for row in objectives if row.get("status") == "COMPLETE"}


def _has_active_job(objective_id: str, jobs: list[dict[str, Any]]) -> bool:
    return any(_text(row.get("objective_id")) == objective_id and row.get("status") in ACTIVE_JOB_STATES for row in jobs)


def objective_ready(objective: dict[str, Any], objectives: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> bool:
    status = _text(objective.get("status")).upper()
    if status not in {"READY", "ACTIVE"}:
        return False
    objective_id = _text(objective.get("objective_id"))
    if not objective_id or _has_active_job(objective_id, jobs):
        return False
    completed = _completed_objectives(objectives)
    dependencies = objective.get("dependencies") or []
    if not isinstance(dependencies, list):
        dependencies = []
    return all(_text(dep) in completed for dep in dependencies)


def choose_next_action(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    objectives = list(snapshot.get("objectives") or [])
    jobs = list(snapshot.get("jobs") or [])
    candidates = [row for row in objectives if objective_ready(row, objectives, jobs)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            -int(row.get("priority") or 0),
            _text(row.get("created_at")),
            _text(row.get("objective_id")),
        )
    )
    row = candidates[0]
    return {
        "objective_id": row.get("objective_id"),
        "title": row.get("title"),
        "owner_system": row.get("owner_system") or "IREN",
        "job_type": (row.get("metadata") or {}).get("job_type") or "AGENT_WORK",
        "protected_action": bool(row.get("protected_action")),
        "reason": "highest_priority_ready_objective",
        "success_criteria": row.get("success_criteria") or {},
        "description": row.get("description") or "",
    }


def status_summary(snapshot: dict[str, Any], control_state: dict[str, Any]) -> dict[str, Any]:
    objectives = list(snapshot.get("objectives") or [])
    jobs = list(snapshot.get("jobs") or [])
    active_jobs = [row for row in jobs if row.get("status") in ACTIVE_JOB_STATES]
    blocked = [row for row in objectives if row.get("status") == "BLOCKED"]
    decisions = [row for row in jobs if row.get("status") == "NEEDS_APPROVAL" or row.get("requires_human") is True]
    complete = sum(1 for row in objectives if row.get("status") == "COMPLETE")
    current = choose_next_action(snapshot)
    return {
        "control_state": control_state.get("state") or "UNKNOWN",
        "objective_count": len(objectives),
        "objectives_complete": complete,
        "active_jobs": len(active_jobs),
        "blocked_objectives": len(blocked),
        "requires_human": len(decisions),
        "next_action": current,
    }


def build_job(next_action: dict[str, Any], requested_by: str, source: str) -> dict[str, Any]:
    protected = bool(next_action.get("protected_action"))
    return {
        "objective_id": next_action.get("objective_id"),
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
        return CommandResult(intent, {"message": "Current IREN status.", **summary})
    if intent == "NEXT":
        action = summary.get("next_action")
        return CommandResult(intent, {
            "message": "Next objective identified." if action else "No executable objective is currently ready.",
            **summary,
        })
    if intent == "DECISIONS":
        decisions = [row for row in snapshot.get("jobs") or [] if row.get("status") == "NEEDS_APPROVAL" or row.get("requires_human") is True]
        return CommandResult(intent, {"message": "Items requiring human authority.", "items": decisions, **summary})
    if intent in {"EXECUTE_NEXT", "FIX"}:
        action = summary.get("next_action")
        if not action:
            return CommandResult(intent, {"message": "No executable objective is currently ready.", **summary})
        job = build_job(action, requested_by, source)
        return CommandResult(intent, {"message": "IREN created the next work item.", "next_action": action}, job)
    return CommandResult(intent, {
        "message": "Directive captured as durable work for IREN triage.",
        **summary,
    }, {
        "objective_id": None,
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
        notify: Callable[[str], Awaitable[None]] | None = None,
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

    async def snapshot(self) -> dict[str, Any]:
        return await self.gateway("iren_work_snapshot")

    async def enqueue_command(self, command: str, *, source: str, requested_by: str) -> dict[str, Any]:
        return await self.gateway(
            "iren_command_create",
            command_text=command[:4000],
            source=source[:40],
            requested_by=requested_by[:160],
        )

    async def _notify(self, text: str) -> None:
        if self.notify is not None:
            await self.notify(text)

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
                    await self._notify(line)
            except Exception as exc:
                self.last_error = type(exc).__name__
                await self.gateway("iren_command_complete", command_id=command_id, status="FAILED", response={"error": type(exc).__name__})

    async def _execute_jobs(self) -> None:
        claimed = await self.gateway("iren_jobs_claim", owner="iren-work-engine", limit=3)
        for job in claimed.get("jobs") or []:
            job_id = _text(job.get("job_id"))
            job_type = _text(job.get("job_type")) or "AGENT_WORK"
            try:
                if job_type in {"AGENT_WORK", "GRAEN_RESEARCH_PROBLEM", "SOFTWARE_BUILD"}:
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="WAITING",
                        result={
                            "reason": "external_or_subsystem_agent_required",
                            "handoff_ready": True,
                            "owner_system": job.get("owner_system"),
                        },
                    )
                else:
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="BLOCKED",
                        error={"reason": "unsupported_job_type", "job_type": job_type},
                    )
                self.last_job_at = datetime.now(UTC).isoformat()
            except Exception as exc:
                self.last_error = type(exc).__name__
                await self.gateway("iren_job_update", job_id=job_id, status="FAILED", error={"type": type(exc).__name__, "message": str(exc)[:500]})

    async def tick(self) -> None:
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
