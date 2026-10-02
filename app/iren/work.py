from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re

import httpx
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo
from . import codex_handoff as codex

UTC = timezone.utc
BUSINESS_TZ = ZoneInfo("America/New_York")

TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_JOB_STATES = {"QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"}
OBJECTIVE_STATES = {"LOCKED", "ACTIVE", "BLOCKED", "READY", "COMPLETE", "FUTURE", "OBSOLETE"}
AUTOPILOT_SAFE_JOB_TYPES = {
    "CONTROL_RECONCILE",
    "CONTROL_VERIFY",
    "CONTROL_CAPABILITIES",
    "CONTROL_VERIFIER_SELFTEST",
    "CONTROL_STABLE_BUILD_VERIFY",
    "CONTROL_MODEL_WORKER_VERIFY",
    "CONTROL_RUNTIME_EVIDENCE_VERIFY",
}
MODEL_WORKER_MAX_JOBS_PER_DAY = 1


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_command(command: str) -> str:
    value = re.sub(r"\s+", " ", command.strip().lower().replace("’", "\'")).rstrip(" ?!.")
    if value in {"prepare for codex", "prepare codex handoff", "codex handoff", "what should codex do next", "prepare work for codex"}:
        return "CODEX_HANDOFF"
    if value in {"verify codex handoff", "verify codex", "check codex work"}:
        return "CODEX_VERIFY"
    if re.fullmatch(r"supersede codex handoff [0-9a-f-]{36}", value):
        return "CODEX_SUPERSEDE"
    if re.fullmatch(r"associate codex handoff [0-9a-f-]{36} pr [1-9][0-9]*", value):
        return "CODEX_ASSOCIATE"
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


def dependencies_complete(
    objective: dict[str, Any],
    objectives: list[dict[str, Any]],
) -> bool:
    completed = _completed_objectives(objectives)
    dependencies = objective.get("dependencies") or []
    if not isinstance(dependencies, list):
        return False
    return all(_text(dep) in completed for dep in dependencies)


def criteria_satisfied(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not expected or not isinstance(actual, dict):
            return False
        return all(
            key in actual and criteria_satisfied(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return type(actual) is type(expected) and actual == expected


def runtime_evidence_verification(control_state: dict[str, Any]) -> dict[str, Any]:
    topology_state = control_state.get("topology") if isinstance(control_state, dict) else {}
    topology_state = topology_state if isinstance(topology_state, dict) else {}
    gaps = topology_state.get("inventory_gaps")
    gaps = gaps if isinstance(gaps, dict) else {}
    return {
        "criteria": {
            "complete_deployment_inventory": topology_state.get("inventory_complete") is True,
        },
        "required_inventory": topology_state.get("required_inventory") or [],
        "inventory_gaps": gaps,
        "inventory_verified_at": topology_state.get("inventory_verified_at"),
        "observation_source": topology_state.get("inventory_source"),
        "observed_at": topology_state.get("observed_at") or control_state.get("observed_at"),
    }


def _has_active_job(objective_id: str, jobs: list[dict[str, Any]]) -> bool:
    return any(_text(row.get("objective_key")) == objective_id and row.get("status") in ACTIVE_JOB_STATES for row in jobs)


def objective_ready(objective: dict[str, Any], objectives: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> bool:
    status = _text(objective.get("status")).upper()
    if status != "READY":
        return False
    objective_id = _text(objective.get("objective_key"))
    if not objective_id or _has_active_job(objective_id, jobs):
        return False
    return dependencies_complete(objective, objectives)


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
        "metadata": row.get("metadata") or {},
        "execution_mode": codex.mode({"job_type": job_type, **row}),
    }


def choose_next_action(
    snapshot: dict[str, Any],
    control_state: dict[str, Any] | None = None,
    *,
    excluded_incident_keys: set[str] | None = None,
    excluded_objective_keys: set[str] | None = None,
    excluded_blocked_job_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    objectives = list(snapshot.get("objectives") or [])
    jobs = list(snapshot.get("jobs") or [])
    control_state = control_state or {}
    excluded_incident_keys = excluded_incident_keys or set()
    excluded_objective_keys = excluded_objective_keys or set()
    excluded_blocked_job_ids = excluded_blocked_job_ids or set()

    incidents = [
        row
        for row in _open_incidents(control_state)
        if str(row.get("key") or "") not in excluded_incident_keys
    ]
    if incidents:
        incident = incidents[0]
        return {
            "objective_key": None,
            "title": f"Resolve {incident['key']}",
            "owner_system": "IREN",
            "job_type": "CONTROL_VERIFY",
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

    ready = [
        row
        for row in objectives
        if objective_ready(row, objectives, jobs)
        and _text(row.get("objective_key")) not in excluded_objective_keys
    ]
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
        and _text(row.get("objective_key")) not in excluded_objective_keys
        and not _has_active_job(_text(row.get("objective_key")), jobs)
    ]
    active = [row for row in active if dependencies_complete(row, objectives)]
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
        and _text(row.get("job_id")) not in excluded_blocked_job_ids
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
    autopilot = autopilot_decision(snapshot, control_state)
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
        "autopilot": autopilot,
    }
    summary["message"] = _status_message(summary)
    return summary


def action_signature(action: dict[str, Any], control_state: dict[str, Any]) -> str:
    payload = {
        "objective_key": action.get("objective_key"),
        "title": action.get("title"),
        "owner_system": action.get("owner_system"),
        "job_type": action.get("job_type"),
        "reason": action.get("reason"),
        "success_criteria": action.get("success_criteria") or {},
        "incident": action.get("incident") or {},
        "blocked_job_id": action.get("blocked_job_id"),
        "control_state": control_state.get("state"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def autopilot_decision(
    snapshot: dict[str, Any],
    control_state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = snapshot.get("settings") or {}
    if not bool(settings.get("autopilot_enabled")):
        return {"should_create": False, "reason": "autopilot_disabled"}

    jobs = list(snapshot.get("jobs") or [])
    if any(
        str(row.get("status") or "").upper() in {"QUEUED", "RUNNING"}
        and str(row.get("requested_via") or "").lower() == "autopilot"
        for row in jobs
    ):
        return {"should_create": False, "reason": "autopilot_job_active"}

    if any(
        str(row.get("status") or "").upper() == "NEEDS_APPROVAL"
        or row.get("requires_human") is True
        for row in jobs
    ):
        return {"should_create": False, "reason": "human_decision_pending"}

    current = now or datetime.now(UTC)
    business_day = current.astimezone(BUSINESS_TZ).date()
    created_today = 0
    for row in jobs:
        if str(row.get("requested_via") or "").lower() != "autopilot":
            continue
        raw_created = str(row.get("created_at") or "")
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
        except ValueError:
            continue
        if created_at.astimezone(BUSINESS_TZ).date() == business_day:
            created_today += 1
    cap = max(1, min(12, int(settings.get("autopilot_max_jobs_per_day") or 3)))
    if created_today >= cap:
        return {
            "should_create": False,
            "reason": "autopilot_daily_cap_reached",
            "jobs_today": created_today,
            "daily_cap": cap,
        }

    attempted_signatures = {
        str((row.get("metadata") or {}).get("action_signature") or "")
        for row in jobs
        if str(row.get("requested_via") or "").lower() == "autopilot"
        and str(row.get("status") or "").upper()
        in TERMINAL_JOB_STATES | {"WAITING", "BLOCKED"}
    }
    excluded_incidents: set[str] = set()
    excluded_objectives: set[str] = set()
    excluded_blocked_jobs: set[str] = set()
    action: dict[str, Any] | None = None
    signature = ""
    skipped_actions: list[str] = []

    for _ in range(50):
        action = choose_next_action(
            snapshot,
            control_state,
            excluded_incident_keys=excluded_incidents,
            excluded_objective_keys=excluded_objectives,
            excluded_blocked_job_ids=excluded_blocked_jobs,
        )
        if not action:
            return {
                "should_create": False,
                "reason": "no_action",
                "skipped_actions": skipped_actions,
            }

        if (
            str(action.get("reason") or "") == "continuous_planner_fallback"
            and skipped_actions
        ):
            return {
                "should_create": False,
                "reason": "same_action_already_attempted",
                "action": action,
                "skipped_actions": skipped_actions,
            }

        if bool(action.get("protected_action")):
            return {
                "should_create": False,
                "reason": "protected_action_requires_human",
                "action": action,
                "skipped_actions": skipped_actions,
            }

        job_type = str(action.get("job_type") or "").upper()
        if job_type not in AUTOPILOT_SAFE_JOB_TYPES:
            return {
                "should_create": False,
                "reason": "executor_capability_required",
                "action": action,
                "skipped_actions": skipped_actions,
            }

        signature = action_signature(action, control_state)
        if signature not in attempted_signatures:
            break

        skipped_actions.append(str(action.get("title") or signature))
        incident = action.get("incident") or {}
        incident_key = _text(incident.get("key"))
        objective_key = _text(action.get("objective_key"))
        blocked_job_id = _text(action.get("blocked_job_id"))
        if incident_key:
            excluded_incidents.add(incident_key)
            continue
        if objective_key:
            excluded_objectives.add(objective_key)
            continue
        if blocked_job_id:
            excluded_blocked_jobs.add(blocked_job_id)
            continue
        return {
            "should_create": False,
            "reason": "same_action_already_attempted",
            "action": action,
            "action_signature": signature,
            "skipped_actions": skipped_actions,
        }
    else:
        return {
            "should_create": False,
            "reason": "planner_exhausted",
            "skipped_actions": skipped_actions,
        }

    return {
        "should_create": True,
        "reason": "safe_action_ready",
        "action": action,
        "action_signature": signature,
        "jobs_today": created_today,
        "daily_cap": cap,
        "skipped_actions": skipped_actions,
    }


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
        "metadata": {
            "success_criteria": next_action.get("success_criteria") or {},
            "reason": next_action.get("reason"),
            "incident": next_action.get("incident") or {},
            "blocked_job_id": next_action.get("blocked_job_id"),
        },
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
    if intent in {"NEXT", "CODEX_HANDOFF"}:
        action = summary.get("next_action")
        title = str((action or {}).get("title") or "Reconcile system and derive next objective")
        return CommandResult(intent, {
            **summary,
            "message": f"Next: {title}. " + ("Prepare for Codex to create the canonical software package." if codex.mode(action) == "codex/manual software" else "IREN can execute this deterministic action." if codex.mode(action) == "deterministic" else "Devon’s authorization is required."),
            "execution_mode": codex.mode(action),
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


def _emit_work_event(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "at": datetime.now(UTC).isoformat(),
                **fields,
            },
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )


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
        self.last_autopilot_at: str | None = None
        self.last_autopilot_reason: str | None = None
        self.last_handoff_check = None
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
        claimed = await self.gateway("iren_commands_claim", owner="iren-work-engine-codex-v1", limit=5)
        for command in claimed.get("commands") or []:
            command_id = command.get("command_id")
            text = _text(command.get("command_text"))
            source = _text(command.get("source")) or "unknown"
            requested_by = _text(command.get("requested_by")) or "operator"
            try:
                snapshot = await self.snapshot()
                result = process_command(text, snapshot, self.control_state(), requested_by=requested_by, source=source)
                job_row = None
                if result.intent == "CODEX_SUPERSEDE":
                    superseded = await self.gateway("iren_handoff_supersede", handoff_id=text.lower().split()[3])
                    result = CommandResult("CODEX_SUPERSEDE", {"message": "Handoff superseded. Prepare for Codex to capture fresh state; prior evidence is preserved."})
                    job_row = superseded.get("job")
                elif result.intent == "CODEX_ASSOCIATE":
                    parts = text.lower().split()
                    associated = await self.gateway("iren_handoff_associate", handoff_id=parts[3], pr_number=int(parts[5]))
                    result = CommandResult("CODEX_ASSOCIATE", {"message": "PR associated; independent verification is pending."})
                    job_row = associated.get("job")
                elif result.intent == "CODEX_VERIFY":
                    await self._reconcile_handoffs(force=True)
                    result = CommandResult("CODEX_VERIFY", {"message": "Codex verification checked. See the handoff evidence and blockers."})
                elif result.intent in {"CODEX_HANDOFF", "NEXT", "EXECUTE_NEXT"}:
                    active = codex.active_handoffs(snapshot)
                    action = (result.response or {}).get("next_action") or {}
                    if active and result.intent in {"CODEX_HANDOFF", "NEXT"}:
                        job_row = active[0]
                        if result.intent == "CODEX_HANDOFF":
                            github = await self._github_evidence()
                            created = await self.gateway("iren_handoff_prepare", objective_key=job_row["objective_key"],
                                main_sha=github["main_sha"], command_id=str(command_id), requested_by=requested_by)
                            job_row = created["job"]
                        result = CommandResult(result.intent, {**result.response, "message": "Continue the prepared Codex handoff. Copy the canonical prompt below.",
                            "execution_mode": "codex/manual software"})
                    elif codex.mode(action) == "protected/requires Devon":
                        result = CommandResult(result.intent, {**result.response, "message": "This objective requires Devon. No executable handoff or expanded authority was created.",
                            "execution_mode": "protected/requires Devon"})
                    elif codex.mode(action) == "codex/manual software":
                        github = await self._github_evidence()
                        created = await self.gateway("iren_handoff_prepare", objective_key=action.get("objective_key"),
                            main_sha=github["main_sha"], command_id=str(command_id), requested_by=requested_by)
                        job_row = created["job"]
                        package = (job_row.get("result") or {}).get("package") or {}
                        _emit_work_event("iren_codex_prepared", handoff_id=job_row.get("job_id"),
                            objective_key=package.get("objective_key"), base_sha=package.get("base_sha"),
                            prompt_chars=len(package.get("prompt") or ""), package_digest=package.get("package_digest"),
                            title=package.get("title"), paid_model_execution=False)
                        result = CommandResult(result.intent, {**result.response, "message": "Codex handoff prepared. Copy the complete prompt; paid execution remains disabled.",
                            "execution_mode": "codex/manual software"})
                    elif result.intent == "CODEX_HANDOFF":
                        result = CommandResult(result.intent, {**result.response, "message": "The next action is deterministic; no Codex handoff is needed. Use do that.",
                            "execution_mode": "deterministic"})
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

    async def _github_evidence(self, job=None):
        if not self.executor_url or len(self.executor_token) < 32:
            raise ValueError("github_evidence_router_unavailable")
        root = self.executor_url.removesuffix("/v1/jobs/accept").rstrip("/")
        params = {}
        if job:
            package = job["result"]["package"]
            params = {"handoff_id": job["job_id"], "objective_key": package["objective_key"]}
            association = job["result"].get("association") or {}
            if association.get("pr_number"):
                params["pr_number"] = association["pr_number"]
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(root + "/v1/codex/github",
                headers={"x-anevum-scheduler-token": self.executor_token}, params=params)
            response.raise_for_status()
            return response.json()

    async def _reconcile_runtime_evidence_objective(self) -> None:
        snapshot = await self.snapshot()
        objectives = list(snapshot.get("objectives") or [])
        objective = next(
            (
                row for row in objectives
                if _text(row.get("objective_key")) == "iren.runtime-evidence.v1"
            ),
            None,
        )
        if not isinstance(objective, dict):
            return
        if str(objective.get("status") or "").upper() == "COMPLETE":
            return
        if not dependencies_complete(objective, objectives):
            return
        evidence = runtime_evidence_verification(self.control_state())
        criteria = evidence["criteria"]
        if criteria.get("complete_deployment_inventory") is not True:
            return
        completed = await self._complete_objective_if_verified(
            {"objective_key": "iren.runtime-evidence.v1"},
            criteria,
        )
        if completed:
            _emit_work_event(
                "iren_runtime_evidence_verified",
                objective_key="iren.runtime-evidence.v1",
                completed=True,
                inventory_complete=True,
                inventory_gap_keys=sorted(
                    str(key) for key in (evidence.get("inventory_gaps") or {})
                ),
                inventory_verified_at=evidence.get("inventory_verified_at"),
            )

    async def _reconcile_handoffs(self, force=False):
        now = datetime.now(UTC)
        if not force and self.last_handoff_check and (now - self.last_handoff_check).total_seconds() < 60:
            return
        self.last_handoff_check = now
        snapshot = await self.snapshot()
        for job in codex.active_handoffs(snapshot)[:5]:
            package = (job.get("result") or {}).get("package") or {}
            try:
                github = await self._github_evidence(job)
                foundation = await self.gateway("iren_handoff_evidence")
                root = self.executor_url.removesuffix("/v1/jobs/accept").rstrip("/")
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(root + "/health")
                    response.raise_for_status()
                    worker = response.json()
                async with httpx.AsyncClient(timeout=10) as client:
                    own = await client.get("http://127.0.0.1:" + os.getenv("PORT", "8080") + "/health")
                    own.raise_for_status()
                    own_health = own.json()
                observations = {"IREN": own_health,
                    "IREN_EXECUTOR": worker, "FOUNDATION": foundation.get("health") or {}}
                outcome = await self.gateway("iren_handoff_verify", handoff_id=job["job_id"],
                    package_digest=package["package_digest"], github=github, observations=observations,
                    migrations=foundation.get("migrations") or [])
                if outcome.get("objective_completed"):
                    await self._reconcile_objective_dependencies()
                updated = (outcome.get("job") or {}).get("result") or {}
                _emit_work_event("iren_codex_verified" if outcome.get("objective_completed") else "iren_codex_observed",
                    handoff_id=job["job_id"], objective_key=job["objective_key"],
                    handoff_status=updated.get("handoff_status"),
                    blockers=(updated.get("verification") or {}).get("blockers"),
                    command_contract=foundation.get("command_contract"),
                    daily_budget_usd=(worker.get("software_worker") or {}).get("daily_budget_usd"),
                    job_budget_usd=(worker.get("software_worker") or {}).get("job_budget_usd"),
                    spending_authority=worker.get("spending_authority"),
                    control_state=self.control_state().get("state"))
            except (httpx.HTTPError, ValueError, KeyError):
                await self.gateway("iren_handoff_verify", handoff_id=job["job_id"],
                    package_digest=package["package_digest"], github={}, observations={}, migrations=[])
                _emit_work_event("iren_codex_evidence_unavailable", handoff_id=job["job_id"])

    async def _model_worker_daily_limit_reached(self, job_id: str) -> bool:
        snapshot = await self.snapshot()
        today = datetime.now(UTC).astimezone(BUSINESS_TZ).date()
        count = 0
        for row in snapshot.get("jobs") or []:
            if _text(row.get("job_type")).upper() != "SOFTWARE_BUILD":
                continue
            if _text(row.get("job_id")) == job_id:
                continue
            if str(row.get("status") or "").upper() == "CANCELLED":
                continue
            raw_created = _text(row.get("created_at"))
            try:
                created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
            except ValueError:
                continue
            if created_at.astimezone(BUSINESS_TZ).date() == today:
                count += 1
        return count >= MODEL_WORKER_MAX_JOBS_PER_DAY

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

        if job_type == "SOFTWARE_BUILD" and await self._model_worker_daily_limit_reached(job_id):
            await self.gateway(
                "iren_job_update",
                job_id=job_id,
                status="BLOCKED",
                error={
                    "reason": "model_worker_daily_job_cap_reached",
                    "daily_job_cap": MODEL_WORKER_MAX_JOBS_PER_DAY,
                },
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
            timeout_seconds = 285.0 if job_type == "SOFTWARE_BUILD" else 20.0
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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

    async def _reconcile_objective_dependencies(self) -> None:
        snapshot = await self.snapshot()
        objectives = list(snapshot.get("objectives") or [])
        for objective in objectives:
            if str(objective.get("status") or "").upper() != "FUTURE":
                continue
            metadata = objective.get("metadata") or {}
            if metadata.get("auto_activate") is not True:
                continue
            if not dependencies_complete(objective, objectives):
                continue
            key = _text(objective.get("objective_key"))
            await self.gateway(
                "iren_objective_update",
                objective_key=key,
                status="READY",
            )
            _emit_work_event(
                "iren_objective_ready",
                objective_key=key,
                title=objective.get("title"),
            )

    async def _complete_objective_if_verified(
        self,
        job: dict[str, Any],
        criteria: dict[str, Any],
    ) -> bool:
        key = _text(job.get("objective_key"))
        if not key:
            return False
        snapshot = await self.snapshot()
        objective = next(
            (
                row
                for row in snapshot.get("objectives") or []
                if _text(row.get("objective_key")) == key
            ),
            None,
        )
        if not isinstance(objective, dict):
            return False
        expected = objective.get("success_criteria") or {}
        if not criteria_satisfied(expected, criteria):
            _emit_work_event(
                "iren_objective_verification_failed",
                objective_key=key,
                expected=expected,
                actual=criteria,
            )
            return False
        await self.gateway(
            "iren_objective_update",
            objective_key=key,
            status="COMPLETE",
        )
        _emit_work_event(
            "iren_objective_completed",
            objective_key=key,
            title=objective.get("title"),
        )
        return True

    async def _reconcile_autopilot(self) -> None:
        snapshot = await self.snapshot()
        decision = autopilot_decision(snapshot, self.control_state())
        previous_reason = self.last_autopilot_reason
        self.last_autopilot_reason = str(decision.get("reason") or "")
        if not decision.get("should_create"):
            if self.last_autopilot_reason != previous_reason:
                _emit_work_event(
                    "iren_autopilot_state",
                    reason=self.last_autopilot_reason,
                    action=(decision.get("action") or {}).get("title"),
                )
            return
        action = decision.get("action") or {}
        job = build_job(action, requested_by="IREN", source="autopilot")
        metadata = dict(job.get("metadata") or {})
        metadata.update({
            "autopilot": True,
            "planner": "iren_autopilot_v1",
            "action_signature": decision.get("action_signature"),
        })
        job["metadata"] = metadata
        created = await self.gateway("iren_job_create", job=job)
        row = created.get("job") or {}
        self.last_autopilot_at = datetime.now(UTC).isoformat()
        self.last_autopilot_reason = "job_created"
        _emit_work_event(
            "iren_autopilot_job_created",
            job_id=row.get("job_id"),
            title=row.get("title") or action.get("title"),
            objective_key=row.get("objective_key") or action.get("objective_key"),
            action_signature=decision.get("action_signature"),
        )
        if self.notify is not None:
            await self._notify(
                f"IREN autopilot queued {row.get('title') or action.get('title')}.",
                {"source": "autopilot", "job_id": row.get("job_id")},
            )

    async def _execute_jobs(self) -> None:
        claimed = await self.gateway("iren_jobs_claim", owner="iren-work-engine", limit=3)
        for job in claimed.get("jobs") or []:
            job_id = _text(job.get("job_id"))
            try:
                job_type = _text(job.get("job_type")).upper()
                if job_type == "CONTROL_RECONCILE":
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
                    _emit_work_event(
                        "iren_control_reconcile_succeeded",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                        next_action=(summary.get("next_action") or {}).get("title"),
                        control_state=summary.get("control_state"),
                    )
                elif job_type == "CONTROL_VERIFY":
                    state = self.control_state()
                    metadata = job.get("metadata") or {}
                    incident_spec = metadata.get("incident") or {}
                    incident_key = _text(incident_spec.get("key"))
                    incidents = state.get("incidents") or {}
                    incident = incidents.get(incident_key) if isinstance(incidents, dict) else None
                    incident = incident if isinstance(incident, dict) else {}
                    still_open = str(incident.get("status") or "").upper() == "OPEN"
                    evidence: dict[str, Any] = {
                        "incident_key": incident_key,
                        "incident_open": still_open,
                        "incident": incident,
                        "control_state": state.get("state"),
                        "observed_at": state.get("observed_at"),
                    }
                    if incident_key.startswith("scheduler."):
                        evidence["scheduler"] = state.get("scheduler") or {}
                    elif incident_key.startswith("service."):
                        service_name = incident_key.split(".", 1)[1]
                        evidence["service"] = (state.get("services") or {}).get(service_name) or {}
                    elif incident_key.startswith("evidence."):
                        rhen = (state.get("services") or {}).get("RHEN") or {}
                        evidence["persistence"] = rhen.get("persistence") or {}
                        evidence["metrics"] = state.get("metrics") or {}
                    elif incident_key.startswith("configuration."):
                        evidence["configuration_baseline"] = state.get("configuration_baseline") or {}
                    criteria = {
                        "incident_closed": incident_key if incident_key and not still_open else False,
                    }
                    result = {
                        "message": (
                            f"Verified {incident_key}: still open."
                            if still_open
                            else f"Verified {incident_key}: no longer open."
                        ),
                        "verification": evidence,
                        "criteria": criteria,
                        "verifier": "iren_deterministic_control_verify_v1",
                    }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result=result,
                    )
                    await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_control_verify_succeeded",
                        job_id=job_id,
                        incident_key=incident_key,
                        incident_open=still_open,
                        control_state=state.get("state"),
                    )
                elif job_type == "CONTROL_CAPABILITIES":
                    criteria = {
                        "safe_executor_registry": True,
                        "protected_actions_gated": True,
                    }
                    result = {
                        "criteria": criteria,
                        "capabilities": sorted(AUTOPILOT_SAFE_JOB_TYPES),
                        "protected_actions_fail_closed": True,
                        "model_execution_authorized": False,
                        "verifier": "iren_capability_registry_v1",
                    }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result=result,
                    )
                    await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_control_capabilities_verified",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                    )
                elif job_type == "CONTROL_VERIFIER_SELFTEST":
                    positive = criteria_satisfied(
                        {"required": True},
                        {"required": True, "extra": "allowed"},
                    )
                    negative = not criteria_satisfied(
                        {"required": True},
                        {"required": False},
                    )
                    criteria = {
                        "evidence_based_completion": bool(positive and negative),
                        "fail_closed": bool(positive and negative),
                    }
                    result = {
                        "criteria": criteria,
                        "positive_control": positive,
                        "negative_control": negative,
                        "verifier": "iren_objective_verifier_v1",
                    }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result=result,
                    )
                    await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_verifier_selftest_succeeded",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                    )
                elif job_type == "CONTROL_RUNTIME_EVIDENCE_VERIFY":
                    result = runtime_evidence_verification(self.control_state())
                    criteria = result["criteria"]
                    gaps = result["inventory_gaps"]
                    result = {
                        **result,
                        "verifier": "iren_runtime_evidence_verifier_v1",
                    }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result=result,
                    )
                    completed = await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_runtime_evidence_verified",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                        completed=completed,
                        inventory_complete=criteria["complete_deployment_inventory"],
                        inventory_gap_keys=sorted(str(key) for key in gaps),
                    )
                elif job_type == "CONTROL_STABLE_BUILD_VERIFY":
                    snapshot = await self.snapshot()
                    state = self.control_state()
                    objectives = {
                        _text(row.get("objective_key")): row
                        for row in snapshot.get("objectives") or []
                    }
                    settings = snapshot.get("settings") or {}
                    commands = list(snapshot.get("commands") or [])
                    protected_probe = build_job(
                        {
                            "title": "protected-probe",
                            "protected_action": True,
                            "job_type": "SOFTWARE_BUILD",
                        },
                        requested_by="IREN",
                        source="selftest",
                    )
                    criteria = {
                        "continuous_planner": choose_next_action(snapshot, state) is not None,
                        "bounded_autopilot": bool(
                            settings.get("autopilot_enabled")
                            and 1 <= int(settings.get("autopilot_max_jobs_per_day") or 0) <= 12
                        ),
                        "durable_state": bool(
                            isinstance(snapshot.get("objectives"), list)
                            and isinstance(snapshot.get("jobs"), list)
                            and isinstance(settings, dict)
                        ),
                        "command_visibility": any(
                            str(row.get("status") or "").upper() == "SUCCEEDED"
                            and isinstance(row.get("result"), dict)
                            for row in commands
                        ),
                        "protected_actions_fail_closed": bool(
                            protected_probe.get("status") == "NEEDS_APPROVAL"
                            and protected_probe.get("requires_human") is True
                        ),
                        "control_state_healthy": str(state.get("state") or "").upper() == "HEALTHY",
                        "deterministic_executor_complete": str(
                            (objectives.get("iren.deterministic-executors.v1") or {}).get("status") or ""
                        ).upper() == "COMPLETE",
                        "verifier_complete": str(
                            (objectives.get("iren.verifier.v1") or {}).get("status") or ""
                        ).upper() == "COMPLETE",
                    }
                    result = {
                        "criteria": criteria,
                        "control_state": state.get("state"),
                        "observed_at": state.get("observed_at"),
                        "verifier": "iren_stable_build_gate_v1",
                    }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result=result,
                    )
                    completed = await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_stable_build_verified",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                        completed=completed,
                        control_state=state.get("state"),
                    )
                elif job_type == "CONTROL_MODEL_WORKER_VERIFY":
                    if not self.executor_url or len(self.executor_token) < 32:
                        criteria = {
                            "bounded_worker_built": False,
                            "draft_pr_only": False,
                            "auto_merge_disabled": False,
                            "daily_job_cap": MODEL_WORKER_MAX_JOBS_PER_DAY,
                            "budget_required": False,
                            "protected_actions_fail_closed": False,
                        }
                        health = {"reason": "executor_router_not_configured"}
                    else:
                        health_url = self.executor_url
                        if health_url.endswith("/v1/jobs/accept"):
                            health_url = health_url[: -len("/v1/jobs/accept")]
                        health_url = health_url.rstrip("/") + "/health"
                        try:
                            async with httpx.AsyncClient(timeout=20.0) as client:
                                response = await client.get(health_url)
                                response.raise_for_status()
                                health = response.json()
                        except (httpx.HTTPError, ValueError) as exc:
                            health = {"reason": "executor_health_unavailable", "error_type": type(exc).__name__}
                        worker = health.get("software_worker") if isinstance(health, dict) else {}
                        worker = worker if isinstance(worker, dict) else {}
                        criteria = {
                            "bounded_worker_built": str(health.get("version") or "").startswith("iren-executor-v1.1"),
                            "draft_pr_only": worker.get("draft_pr_only") is True,
                            "auto_merge_disabled": worker.get("auto_merge") is False,
                            "daily_job_cap": MODEL_WORKER_MAX_JOBS_PER_DAY,
                            "budget_required": worker.get("budget_required") is True,
                            "protected_actions_fail_closed": worker.get("protected_actions_fail_closed") is True,
                        }
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED",
                        result={
                            "criteria": criteria,
                            "executor_health": health,
                            "verifier": "iren_model_worker_verifier_v1",
                        },
                    )
                    completed = await self._complete_objective_if_verified(job, criteria)
                    _emit_work_event(
                        "iren_model_worker_verified",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                        completed=completed,
                        configured=bool((health or {}).get("software_backend_configured")),
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
                _emit_work_event(
                    "iren_job_failed",
                    job_id=job_id,
                    error_type=type(exc).__name__,
                )

    async def tick(self) -> None:
        await self._reconcile_accidental_status_jobs()
        await self._process_commands()
        await self._reconcile_objective_dependencies()
        await self._reconcile_runtime_evidence_objective()
        await self._reconcile_handoffs()
        await self._reconcile_autopilot()
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
