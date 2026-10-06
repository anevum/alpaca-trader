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
from .codex_github import inspect_github

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
    if (
        value in {
            "maintenance prompt",
            "codex maintenance prompt",
            "prepare maintenance prompt",
            "generate maintenance prompt",
            "generate codex maintenance prompt",
            "new codex maintenance prompt",
        }
        or value.startswith("maintenance prompt:")
        or value.startswith("codex maintenance prompt:")
        or value.startswith("prepare maintenance prompt:")
        or value.startswith("generate maintenance prompt:")
        or value.startswith("generate codex maintenance prompt:")
    ):
        return "MAINTENANCE_PROMPT"
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


def runtime_evidence_criteria(control_state: dict[str, Any]) -> dict[str, Any]:
    topology_state = control_state.get("topology") if isinstance(control_state, dict) else {}
    topology_state = topology_state if isinstance(topology_state, dict) else {}
    return {
        "complete_deployment_inventory": topology_state.get("inventory_complete") is True,
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
            "title": f"Verify {incident['key']}",
            "owner_system": "IREN",
            "job_type": "CONTROL_VERIFY",
            "protected_action": False,
            "reason": f"open_{incident['severity']}_incident",
            "success_criteria": {"incident_closed": incident["key"]},
            "description": (
                f"Re-read the live evidence for {incident['key']} "
                f"({incident['reason']}) and record whether the incident is still open. "
                "This control verifies state only; it does not claim to repair an "
                "arbitrary subsystem fault."
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
            "title": f"Recheck {row.get('title') or row.get('job_type') or 'IREN work'}",
            "owner_system": row.get("owner_system") or "IREN",
            "job_type": "CONTROL_RECONCILE",
            "protected_action": False,
            "reason": "blocked_or_waiting_work",
            "success_criteria": {"job_unblocked": row.get("job_id")},
            "description": (
                "Re-read the durable job and dependency state and record whether the "
                "blocker has cleared. This control does not claim to repair a dependency "
                "that has no registered deterministic actuator."
            ),
            "blocked_job_id": row.get("job_id"),
        }

    return None


def _status_message(summary: dict[str, Any]) -> str:
    state = str(summary.get("control_state") or "UNKNOWN")
    incidents = list(summary.get("open_incidents") or [])
    active_jobs = int(summary.get("active_jobs") or 0)
    waiting_jobs = int(summary.get("waiting_jobs") or 0)
    next_action = summary.get("next_action")
    parts = [f"IREN is {state}."]
    if incidents:
        parts.append(f"{len(incidents)} incident{'s are' if len(incidents) != 1 else ' is'} open.")
    else:
        parts.append("No control-plane incidents are open.")
    if active_jobs:
        parts.append(f"{active_jobs} active job{'s' if active_jobs != 1 else ''}; {waiting_jobs} waiting or blocked.")
    if isinstance(next_action, dict) and next_action.get("title"):
        parts.append(f"Next: {next_action['title']}.")
    else:
        parts.append("No pending control action.")
    return " ".join(parts)


def _maintenance_focus(command: str) -> str:
    match = re.match(
        r"^\s*(?:maintenance prompt|codex maintenance prompt|prepare maintenance prompt|"
        r"generate maintenance prompt|generate codex maintenance prompt|new codex maintenance prompt)"
        r"(?:\s*[:\-]\s*|\s+for\s+)?(.*)$",
        command,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).strip())[:1000]


def _prompt_value(value: Any, fallback: str = "—", limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", _text(value))
    return (text or fallback)[:limit]


def _maintenance_rows(snapshot: dict[str, Any], key: str) -> list[dict[str, Any]]:
    evidence = snapshot.get("maintenance_evidence")
    if not isinstance(evidence, dict):
        return []
    rows = evidence.get(key)
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _maintenance_index(
    rows: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    limit: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows[:limit]:
        key = next((_text(row.get(field)) for field in key_fields if _text(row.get(field))), "")
        if not key:
            continue
        result[key] = {
            field: row.get(field)
            for field in value_fields
            if row.get(field) is not None
        }
    return result


def _maintenance_state(
    snapshot: dict[str, Any],
    control_state: dict[str, Any],
) -> dict[str, Any]:
    topology = control_state.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    services = topology.get("services")
    services = services if isinstance(services, list) else []
    objectives = [
        dict(row)
        for row in list(snapshot.get("objectives") or [])
        if isinstance(row, dict)
    ]
    jobs = [
        dict(row)
        for row in list(snapshot.get("jobs") or [])
        if isinstance(row, dict)
    ]
    incidents = _open_incidents(control_state)

    handoff_rows = [
        row for row in jobs
        if str(row.get("job_type") or "").upper() == "CODEX_HANDOFF"
    ]

    return {
        "control": {
            "state": control_state.get("state") or "UNKNOWN",
            "observed_at": control_state.get("observed_at"),
            "inventory_complete": topology.get("inventory_complete") is True,
        },
        "services": _maintenance_index(
            [dict(row) for row in services if isinstance(row, dict)],
            key_fields=("service_name", "service_id"),
            value_fields=("status", "readiness", "revision", "deployment"),
            limit=16,
        ),
        "incidents": {
            str(row.get("key")): {
                "severity": row.get("severity"),
                "reason": row.get("reason"),
                "opened_at": row.get("opened_at"),
            }
            for row in incidents
            if row.get("key")
        },
        "objectives": _maintenance_index(
            objectives,
            key_fields=("objective_key",),
            value_fields=("status", "owner_system", "priority", "updated_at", "completed_at"),
            limit=60,
        ),
        "jobs": _maintenance_index(
            jobs,
            key_fields=("job_id",),
            value_fields=("status", "job_type", "objective_key", "owner_system", "updated_at", "completed_at"),
            limit=80,
        ),
        "graen_problems": _maintenance_index(
            _maintenance_rows(snapshot, "graen_problems"),
            key_fields=("problem_id",),
            value_fields=("status", "research_stage", "candidate_id", "family", "updated_at"),
            limit=20,
        ),
        "graen_runs": _maintenance_index(
            _maintenance_rows(snapshot, "graen_runs"),
            key_fields=("run_id",),
            value_fields=("status", "methodology_version", "decision", "next_action", "strategy_version_id", "completed_at"),
            limit=20,
        ),
        "velum_replays": _maintenance_index(
            _maintenance_rows(snapshot, "velum_replays"),
            key_fields=("replay_id",),
            value_fields=("status", "asset_class", "strategy_version_id", "methodology_version", "result_type", "completed_at"),
            limit=20,
        ),
        "nostra_calibrations": _maintenance_index(
            _maintenance_rows(snapshot, "nostra_calibrations"),
            key_fields=("calibration_id",),
            value_fields=("model_version", "methodology_version", "sample_count", "created_at"),
            limit=8,
        ),
        "nostra_forecasts": _maintenance_index(
            _maintenance_rows(snapshot, "nostra_forecasts"),
            key_fields=("forecast_id",),
            value_fields=("subject", "model_version", "methodology_version", "issued_at", "observed_at"),
            limit=12,
        ),
        "strategy_activity": _maintenance_index(
            _maintenance_rows(snapshot, "strategy_activity_24h"),
            key_fields=("strategy_version_id",),
            value_fields=("last_event_at", "decision_cycles", "fills", "runtime_errors", "run_count"),
            limit=12,
        ),
        "handoffs": _maintenance_index(
            handoff_rows,
            key_fields=("job_id",),
            value_fields=("status", "objective_key", "updated_at", "completed_at"),
            limit=16,
        ),
    }


def _previous_maintenance_manifest(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for row in list(snapshot.get("commands") or []):
        if not isinstance(row, dict):
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        manifest = result.get("maintenance_manifest")
        if (
            result.get("prompt_version") == "iren-maintenance-v2"
            and isinstance(manifest, dict)
        ):
            return dict(manifest)
    return None


def _maintenance_changes(
    previous: dict[str, Any] | None,
    current_state: dict[str, Any],
) -> tuple[int, list[str]]:
    if not previous:
        return 0, ["First adaptive maintenance baseline; no previous v2 prompt exists."]

    previous_state = previous.get("state")
    if not isinstance(previous_state, dict):
        return 0, ["Previous maintenance prompt has no comparable state manifest."]

    changes: list[str] = []
    total = 0

    def add(message: str) -> None:
        nonlocal total
        total += 1
        if len(changes) < 16:
            changes.append(message)

    previous_control = previous_state.get("control")
    current_control = current_state.get("control")
    if previous_control != current_control:
        add(
            "Control state changed: "
            + _prompt_value((previous_control or {}).get("state"), "UNKNOWN")
            + " -> "
            + _prompt_value((current_control or {}).get("state"), "UNKNOWN")
        )

    labels = {
        "services": "service",
        "incidents": "incident",
        "objectives": "objective",
        "jobs": "job",
        "graen_problems": "GRAEN problem",
        "graen_runs": "GRAEN run",
        "velum_replays": "VELUM replay",
        "nostra_calibrations": "NOSTRA calibration",
        "nostra_forecasts": "NOSTRA forecast",
        "strategy_activity": "strategy",
        "handoffs": "Codex handoff",
    }
    for group, label in labels.items():
        before = previous_state.get(group)
        after = current_state.get(group)
        before = before if isinstance(before, dict) else {}
        after = after if isinstance(after, dict) else {}
        for key in sorted(set(before) | set(after)):
            if key not in before:
                add(f"New {label}: {key}")
            elif key not in after:
                add(f"{label.capitalize()} left current window: {key}")
            elif before.get(key) != after.get(key):
                before_status = (before.get(key) or {}).get("status")
                after_status = (after.get(key) or {}).get("status")
                if before_status != after_status and (before_status or after_status):
                    add(
                        f"{label.capitalize()} {key}: "
                        f"{_prompt_value(before_status, '—')} -> {_prompt_value(after_status, '—')}"
                    )
                else:
                    add(f"{label.capitalize()} changed: {key}")

    if total == 0:
        changes.append("No material state delta since the previous adaptive maintenance prompt.")
    elif total > len(changes):
        changes.append(f"{total - len(changes)} additional changes omitted from this compact delta.")
    return total, changes


def _maintenance_mode(
    snapshot: dict[str, Any],
    control_state: dict[str, Any],
    *,
    focus: str,
    change_count: int,
    changes: list[str],
) -> tuple[str, str]:
    incidents = _open_incidents(control_state)
    state = str(control_state.get("state") or "UNKNOWN").upper()
    topology = control_state.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    jobs = [row for row in list(snapshot.get("jobs") or []) if isinstance(row, dict)]
    blocked = [
        row for row in jobs
        if str(row.get("status") or "").upper() in {"WAITING", "BLOCKED", "NEEDS_APPROVAL"}
    ]
    active_research = [
        row for row in _maintenance_rows(snapshot, "graen_problems")
        if str(row.get("status") or "").upper() in {"RUNNING", "QUEUED", "WAITING", "BLOCKED"}
    ]
    research_delta = any(
        item.startswith(("GRAEN", "VELUM", "NOSTRA", "Strategy"))
        or "strategy" in item.lower()
        for item in changes
    )

    if any(row.get("severity") == "critical" for row in incidents):
        return "RECOVERY", "critical control-plane incident"
    if state == "STALE" or topology.get("inventory_complete") is not True:
        return "EVIDENCE_REPAIR", "canonical state or deployment inventory is incomplete"
    if incidents:
        return "STABILIZE", "open operational incident"
    if blocked:
        return "RECONCILE", "blocked/waiting work requires dependency reconciliation"
    if active_research or research_delta:
        return "RESEARCH", "research evidence is active or changed"
    if focus:
        return "FOCUSED", "operator supplied a specific focus"
    if change_count == 0:
        return "VERIFY_ONLY", "no material delta since the previous maintenance pass"
    return "TARGETED", "material state changed without an active incident"


def _maintenance_budget(mode: str) -> dict[str, Any]:
    if mode in {"RECOVERY", "EVIDENCE_REPAIR", "STABILIZE"}:
        return {
            "primary_objectives": 1,
            "supporting_changes": 2,
            "parallel_research_threads": 0,
            "scope": "repair one root cause and only the dependencies required to verify recovery",
        }
    if mode == "RESEARCH":
        return {
            "primary_objectives": 1,
            "supporting_changes": 2,
            "parallel_research_threads": 1,
            "scope": "advance one evidence chain from hypothesis through the next required gate",
        }
    if mode == "VERIFY_ONLY":
        return {
            "primary_objectives": 0,
            "supporting_changes": 0,
            "parallel_research_threads": 0,
            "scope": "read-only verification; make no change unless new evidence reveals a real defect",
        }
    return {
        "primary_objectives": 1,
        "supporting_changes": 2,
        "parallel_research_threads": 1,
        "scope": "one coherent change set; avoid unrelated cleanup or redesign",
    }


def build_maintenance_manifest(
    snapshot: dict[str, Any],
    control_state: dict[str, Any],
    *,
    focus: str = "",
) -> dict[str, Any]:
    state = _maintenance_state(snapshot, control_state)
    previous = _previous_maintenance_manifest(snapshot)
    change_count, changes = _maintenance_changes(previous, state)
    mode, driver = _maintenance_mode(
        snapshot,
        control_state,
        focus=focus,
        change_count=change_count,
        changes=changes,
    )
    budget = _maintenance_budget(mode)
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    previous_digest = (
        _text(previous.get("state_digest"))
        if isinstance(previous, dict)
        else ""
    )
    return {
        "version": "iren-maintenance-manifest.v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "driver": driver,
        "focus": focus,
        "state_digest": digest,
        "previous_state_digest": previous_digest or None,
        "changed_since_previous": bool(previous and previous_digest != digest),
        "change_count": change_count,
        "changes": changes,
        "budget": budget,
        "state": state,
    }


def _maintenance_research_lines(snapshot: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    problems = _maintenance_rows(snapshot, "graen_problems")[:6]
    runs = _maintenance_rows(snapshot, "graen_runs")[:6]
    replays = _maintenance_rows(snapshot, "velum_replays")[:5]
    calibrations = _maintenance_rows(snapshot, "nostra_calibrations")[:3]
    forecasts = _maintenance_rows(snapshot, "nostra_forecasts")[:4]
    strategies = _maintenance_rows(snapshot, "strategy_activity_24h")[:6]

    lines.append("GRAEN problems:")
    if problems:
        for row in problems:
            lines.append(
                "- "
                + _prompt_value(row.get("problem_id"), "problem")
                + " | " + _prompt_value(row.get("status"), "UNKNOWN")
                + " | stage=" + _prompt_value(row.get("research_stage"))
                + " | candidate=" + _prompt_value(row.get("candidate_id"))
                + " | " + _prompt_value(row.get("title"), "Untitled")
            )
    else:
        lines.append("- No current GRAEN problem evidence supplied.")

    lines.append("Recent GRAEN runs:")
    if runs:
        for row in runs:
            lines.append(
                "- "
                + _prompt_value(row.get("run_id"), "run")
                + " | " + _prompt_value(row.get("status"), "UNKNOWN")
                + " | decision=" + _prompt_value(row.get("decision"))
                + " | next=" + _prompt_value(row.get("next_action"))
                + " | strategy=" + _prompt_value(row.get("strategy_version_id"))
            )
    else:
        lines.append("- None supplied.")

    lines.append("VELUM replay evidence:")
    if replays:
        for row in replays:
            lines.append(
                "- "
                + _prompt_value(row.get("replay_id"), "replay")
                + " | " + _prompt_value(row.get("status"), "UNKNOWN")
                + " | strategy=" + _prompt_value(row.get("strategy_version_id"))
                + " | result=" + _prompt_value(row.get("result_type"))
            )
    else:
        lines.append("- None supplied.")

    lines.append("NOSTRA evidence:")
    if calibrations:
        latest = calibrations[0]
        lines.append(
            "- latest calibration "
            + _prompt_value(latest.get("calibration_id"), "—")
            + " | model=" + _prompt_value(latest.get("model_version"))
            + " | n=" + _prompt_value(latest.get("sample_count"))
        )
    else:
        lines.append("- No recent calibration supplied.")
    if forecasts:
        open_count = sum(1 for row in forecasts if not row.get("observed_at"))
        lines.append(f"- {len(forecasts)} recent forecasts in snapshot; {open_count} awaiting outcomes.")

    lines.append("RHEN strategy activity / 24h:")
    if strategies:
        for row in strategies:
            lines.append(
                "- "
                + _prompt_value(row.get("strategy_version_id"), "strategy")
                + " | decisions=" + _prompt_value(row.get("decision_cycles"), "0")
                + " | fills=" + _prompt_value(row.get("fills"), "0")
                + " | errors=" + _prompt_value(row.get("runtime_errors"), "0")
                + " | runs=" + _prompt_value(row.get("run_count"), "0")
                + " | last=" + _prompt_value(row.get("last_event_at"))
            )
    else:
        lines.append("- No strategy activity supplied for the last 24h.")
    return lines


def render_maintenance_prompt(
    snapshot: dict[str, Any],
    control_state: dict[str, Any],
    *,
    focus: str = "",
    manifest: dict[str, Any] | None = None,
) -> str:
    manifest = manifest or build_maintenance_manifest(
        snapshot,
        control_state,
        focus=focus,
    )
    objectives = [
        row for row in list(snapshot.get("objectives") or [])
        if isinstance(row, dict)
        and str(row.get("status") or "").upper() in {"ACTIVE", "READY", "BLOCKED"}
    ][:10]
    jobs = [
        row for row in list(snapshot.get("jobs") or [])
        if isinstance(row, dict)
        and str(row.get("status") or "").upper() in ACTIVE_JOB_STATES
    ][:10]
    incidents = _open_incidents(control_state)[:12]
    topology = control_state.get("topology") or {}
    topology = topology if isinstance(topology, dict) else {}
    services = topology.get("services") or []
    services = services if isinstance(services, list) else []
    current = choose_next_action(snapshot, control_state)
    active_handoffs = codex.active_handoffs(snapshot)
    budget = manifest.get("budget") if isinstance(manifest.get("budget"), dict) else {}
    changes = list(manifest.get("changes") or [])

    lines = [
        "IREN ADAPTIVE MAINTENANCE PASS v2",
        "",
        "Mission:",
        "Continue ANEVUM/RHEN from CURRENT evidence. Maintain the whole system as one coherent operating stack, "
        "but change only the smallest evidence-backed scope required in this pass.",
        "",
        "Canonical repositories:",
        f"- Backend/runtime: {codex.configured_repository()}",
        "- Frontend/Command: anevum/anevum-web",
        "",
        "PASS SELECTION",
        f"- Mode: {_prompt_value(manifest.get('mode'), 'TARGETED')}",
        f"- Driver: {_prompt_value(manifest.get('driver'), 'current evidence')}",
        f"- Material changes since previous prompt: {int(manifest.get('change_count') or 0)}",
        f"- Work budget: {int(budget.get('primary_objectives') or 0)} primary objective; "
        f"up to {int(budget.get('supporting_changes') or 0)} tightly coupled supporting changes; "
        f"{int(budget.get('parallel_research_threads') or 0)} parallel research thread(s).",
        f"- Scope rule: {_prompt_value(budget.get('scope'), 'one coherent change set')}",
        "",
        "CHANGES SINCE PREVIOUS MAINTENANCE PROMPT",
    ]
    lines.extend(f"- {item}" for item in changes[:16])

    lines.extend([
        "",
        "CURRENT CONTROL STATE",
        f"- State: {_prompt_value(control_state.get('state'), 'UNKNOWN')}",
        f"- Observed at: {_prompt_value(control_state.get('observed_at'))}",
        f"- Inventory complete: {'YES' if topology.get('inventory_complete') is True else 'NO/UNKNOWN'}",
        "",
        "Runtime services:",
    ])
    if services:
        for row in services[:12]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- "
                + _prompt_value(row.get("service_name") or row.get("service_id"), "service")
                + " | status=" + _prompt_value(row.get("status"), "UNKNOWN")
                + " | ready=" + (
                    "YES" if row.get("readiness") is True
                    else "NO" if row.get("readiness") is False
                    else "UNKNOWN"
                )
                + " | revision=" + _prompt_value(row.get("revision"))
                + " | deployment=" + _prompt_value(row.get("deployment"))
            )
    else:
        lines.append("- No service inventory supplied. Treat this as an evidence gap, not as healthy.")

    lines.extend(["", "Open incidents:"])
    if incidents:
        for row in incidents:
            lines.append(
                "- " + _prompt_value(row.get("key"), "incident")
                + " | severity=" + _prompt_value(row.get("severity"), "warning")
                + " | reason=" + _prompt_value(row.get("reason"), "unspecified")
            )
    else:
        lines.append("- None reported.")

    lines.extend(["", "CURRENT OBJECTIVES + JOBS", "Active objectives:"])
    if objectives:
        for row in objectives:
            lines.append(
                "- " + _prompt_value(row.get("objective_key"), "objective")
                + " | " + _prompt_value(row.get("status"), "UNKNOWN")
                + " | " + _prompt_value(row.get("title"), "Untitled")
                + " | owner=" + _prompt_value(row.get("owner_system"), "IREN")
            )
    else:
        lines.append("- None.")
    lines.append("Active/waiting jobs:")
    if jobs:
        for row in jobs:
            lines.append(
                "- " + _prompt_value(row.get("job_id"), "job")
                + " | " + _prompt_value(row.get("status"), "UNKNOWN")
                + " | " + _prompt_value(row.get("job_type"), "WORK")
                + " | " + _prompt_value(row.get("title"), "Untitled")
                + " | objective=" + _prompt_value(row.get("objective_key"))
            )
    else:
        lines.append("- None.")

    lines.extend(["", "CURRENT RESEARCH + STRATEGY EVIDENCE"])
    lines.extend(_maintenance_research_lines(snapshot))

    lines.extend(["", "IREN NEXT ACTION"])
    if current:
        lines.append(
            "- " + _prompt_value(current.get("title"), "Untitled")
            + " | mode=" + codex.mode(current)
            + " | reason=" + _prompt_value(current.get("reason"), "unspecified")
        )
    else:
        lines.append("- No pending canonical action.")

    lines.extend(["", "TRACKED CODEX HANDOFFS"])
    if active_handoffs:
        for row in active_handoffs[:4]:
            result = row.get("result") or {}
            package = result.get("package") or {}
            lines.append(
                "- " + _prompt_value(package.get("title") or row.get("title"), "handoff")
                + " | status=" + _prompt_value(result.get("handoff_status"), row.get("status") or "UNKNOWN")
                + " | objective=" + _prompt_value(row.get("objective_key"))
                + " | base=" + _prompt_value(package.get("base_sha"), "—", 12)
            )
        lines.append("- Continue or verify existing handoffs before creating overlapping software work.")
    else:
        lines.append("- None active.")

    lines.extend([
        "",
        "OPERATOR FOCUS",
        "- " + (
            _prompt_value(focus, "", 1000)
            if focus
            else "No additional focus supplied. Let the evidence and priority rules choose the pass."
        ),
        "",
        "SYSTEM RESPONSIBILITY MAP — ALWAYS INSPECT, SELECTIVELY CHANGE",
        "- Integrity: runtime health, deployment state, CI, schedulers, dependencies, configuration drift, credentials boundaries, cost/resource pressure.",
        "- Evidence: telemetry, provenance, event durability, reconciliation, data completeness, stale/blank Command fields, research observability.",
        "- Work control: IREN objectives, dependencies, priorities, duplicate/stale jobs, WAITING/BLOCKED work, completion evidence, Codex handoffs.",
        "- Research: GRAEN hypotheses/problems/runs/artifacts; VELUM replay/simulation evidence; NOSTRA calibration/forecast/regime evidence.",
        "- Strategy lifecycle: candidate strategy versions, replay validation, paper/forward evidence, promotion/rollback state, RHEN strategy registry/runtime wiring.",
        "- Execution boundary: broker integration, position/risk protection, order reconciliation, market-session behavior, live-vs-paper authority.",
        "- Operator surface: Command data accuracy and controls. Change UI only when it improves truth, observability, or required operation.",
        "- Cleanup: remove proven legacy/duplicate code only when the active path is verified and history/evidence is preserved.",
        "",
        "PRIORITY + SCOPE ALGORITHM",
        "1. Safety/integrity/evidence failures outrank research, strategy optimization, UX, cleanup, and new features.",
        "2. If canonical state is stale or incomplete, restore trustworthy observation before using downstream results to make decisions.",
        "3. Reconcile blocked/waiting/duplicate work before spawning new work for the same objective.",
        "4. Prefer finishing or invalidating the current research/strategy chain over opening a new parallel chain.",
        "5. Select ONE primary objective for this pass. Add supporting changes only when they are necessary to make that objective correct, testable, or deployable.",
        "6. Do not perform a broad refactor, strategy redesign, research expansion, and Command redesign in one pass. Split unrelated work into future objectives.",
        "7. If there is no material delta and no explicit operator focus, verify current state and stop. Do not manufacture churn.",
        "",
        "RESEARCH -> STRATEGY CONTROL LOOP",
        "1. Start from the current measurable problem or hypothesis. Reuse existing GRAEN work when it addresses the same question; do not duplicate experiments.",
        "2. GRAEN owns hypothesis/candidate development. Update the research question, features, methodology, or candidate only when evidence shows why.",
        "3. VELUM owns replay/simulation validation. A candidate that lacks replay evidence does not jump to RHEN merely because it looks promising.",
        "4. Use NOSTRA when regime/forecast/calibration evidence is relevant to the hypothesis; do not force NOSTRA into unrelated changes.",
        "5. When evidence rejects a candidate, record the rejection, retire/supersede the corresponding work, and move to the next justified hypothesis instead of tuning indefinitely.",
        "6. When evidence supports a candidate, create/version the smallest strategy change needed and advance it through the EXISTING canonical validation gates.",
        "7. Strategy changes must be versioned, attributable to the research evidence that motivated them, observable in Command, and reversible.",
        "8. Never overwrite a working strategy in place. Preserve the prior version and rollback path.",
        "9. Do not invent promotion thresholds. Use the repository's current methodology/gates. If a required gate is absent or ambiguous, surface that as the next maintenance problem.",
        "10. Paper/forward/canary evidence must remain distinct from live performance. Do not represent simulated evidence as live.",
        "11. Promotion to live execution may occur only through the current authorized promotion path and existing safety/risk policy. Do not silently expand broker-write authority, capital allocation, or risk.",
        "",
        "JOB + OBJECTIVE ORCHESTRATION",
        "1. Treat IREN objective/job state as durable coordination, not a cosmetic task list.",
        "2. Do not create a new job if an equivalent ACTIVE/QUEUED/RUNNING/WAITING job already owns the same work.",
        "3. When dependencies clear, advance the existing WAITING/BLOCKED work instead of duplicating it.",
        "4. Mark work COMPLETE only when success criteria have direct evidence. Keep unresolved work active/blocked with the real blocker.",
        "5. Supersede or obsolete stale paths when newer evidence invalidates them; preserve audit history rather than deleting evidence.",
        "6. Keep FUTURE ideas out of the current pass unless they become the highest-priority evidence-backed need.",
        "7. If Codex discovers a new concrete blocker or required follow-up, update/create the smallest canonical objective/job needed so the next generated prompt can continue from it.",
        "",
        "EXECUTION INSTRUCTIONS",
        "1. Inspect CURRENT main in both repositories, applicable AGENTS.md files, current GitHub CI, current Railway deployments/logs, and current Command/IREN/Research state before editing. This prompt is context, not authority.",
        "2. Reconcile the snapshot and delta above against live evidence. Ignore stale PRs, obsolete branches, retired services, old architecture, and already-completed work.",
        "3. State the selected primary objective and why it outranks alternatives before making changes.",
        "4. Work inside the pass budget. If the real fix exceeds the budget, complete the safest coherent slice and create/leave explicit follow-up state instead of sprawling.",
        "5. For research-driven changes, show the evidence chain: observation -> hypothesis -> GRAEN result -> VELUM/NOSTRA evidence as applicable -> strategy decision.",
        "6. Implement clear safe fixes instead of stopping at analysis. Update code, tests, configuration, research definitions, job/objective state, or strategy version only when that is the selected objective's required change.",
        "7. Run focused tests first, then the relevant full CI. Deploy only through existing canonical deployment paths. Verify runtime state and evidence after deployment.",
        "8. Keep paid model/API worker spending disabled unless the operator explicitly changes that policy.",
        "9. Do not make destructive infrastructure/credential changes, silently alter live risk/capital, or bypass protected owner approvals.",
        "10. Do not fabricate system health, research progress, performance, trades, or completion. Missing evidence is itself a blocker.",
        "",
        "CLOSEOUT CONTRACT",
        "Return a compact completion record containing:",
        "- primary objective selected;",
        "- evidence/research that justified it;",
        "- exact changes made;",
        "- objectives/jobs advanced, blocked, completed, superseded, or created;",
        "- research/strategy lifecycle movement (if any);",
        "- tests/CI/deploy verification;",
        "- remaining blockers and next canonical action;",
        "- explicit statement when no further change is justified.",
        "",
        "This prompt was assembled deterministically from current IREN state, bounded research evidence, and the previous maintenance manifest. No model/API worker was invoked to generate it.",
    ])
    return "\n".join(lines)


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
    action = summary.get("next_action")

    if intent == "STATUS":
        return CommandResult(intent, summary)

    if intent == "MAINTENANCE_PROMPT":
        focus = _maintenance_focus(command)
        manifest = build_maintenance_manifest(
            snapshot,
            control_state,
            focus=focus,
        )
        return CommandResult(intent, {
            **summary,
            "message": (
                "Adaptive maintenance prompt prepared: "
                + str(manifest.get("mode") or "TARGETED")
                + "; "
                + str(manifest.get("change_count") or 0)
                + " material change(s) since the previous v2 prompt."
            ),
            "maintenance_prompt": render_maintenance_prompt(
                snapshot,
                control_state,
                focus=focus,
                manifest=manifest,
            ),
            "maintenance_focus": focus,
            "maintenance_manifest": manifest,
            "prompt_version": "iren-maintenance-v2",
            "action_taken": False,
        })

    if intent == "NEXT":
        if not action:
            return CommandResult(intent, {
                **summary,
                "message": "No pending safe action.",
                "execution_mode": "idle",
                "action_taken": False,
            })
        execution_mode = codex.mode(action)
        return CommandResult(intent, {
            **summary,
            "message": f"Next: {action.get('title')}.",
            "execution_mode": execution_mode,
            "action_taken": False,
        })

    if intent == "CODEX_HANDOFF":
        if not action:
            return CommandResult(intent, {
                **summary,
                "message": "No pending software objective. No Codex handoff was created.",
                "execution_mode": "idle",
                "action_taken": False,
            })
        execution_mode = codex.mode(action)
        return CommandResult(intent, {
            **summary,
            "message": (
                f"Prepare Codex handoff: {action.get('title')}."
                if execution_mode == "codex/manual software"
                else "The pending action is not software work; no Codex handoff is needed."
                if execution_mode == "deterministic"
                else "This action requires owner authority; no Codex handoff was created."
            ),
            "execution_mode": execution_mode,
            "action_taken": False,
        })

    if intent == "DECISIONS":
        decisions = [
            row
            for row in snapshot.get("jobs") or []
            if row.get("status") == "NEEDS_APPROVAL" or row.get("requires_human") is True
        ]
        return CommandResult(intent, {
            **summary,
            "message": (
                f"{len(decisions)} owner decision{'s' if len(decisions) != 1 else ''} pending."
                if decisions
                else "No owner decisions are pending."
            ),
            "items": decisions,
            "action_taken": False,
        })

    if intent in {"EXECUTE_NEXT", "FIX"}:
        if not action:
            return CommandResult(intent, {
                **summary,
                "message": "No pending safe action. Nothing executed.",
                "execution_mode": "idle",
                "action_taken": False,
            })
        execution_mode = codex.mode(action)
        if execution_mode == "codex/manual software":
            return CommandResult(intent, {
                **summary,
                "message": "Software work requires a manual Codex handoff. Nothing was queued.",
                "next_action": action,
                "execution_mode": execution_mode,
                "action_taken": False,
            })
        job = build_job(action, requested_by, source)
        return CommandResult(intent, {
            **summary,
            "message": (
                f"Queued {action.get('title')} for owner approval."
                if job.get("status") == "NEEDS_APPROVAL"
                else f"Queued {action.get('title')}."
            ),
            "next_action": action,
            "execution_mode": execution_mode,
            "action_taken": True,
        }, job)

    return CommandResult(intent, {
        **summary,
        "message": "Unsupported control.",
        "supported_actions": [
            "status",
            "maintenance prompt",
            "what's next?",
            "do that",
            "what needs me?",
            "prepare for Codex",
            "verify Codex handoff",
        ],
        "action_taken": False,
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
        self.github_token = os.getenv("IREN_GITHUB_TOKEN", "").strip()
        self.github_repository = codex.configured_repository()

    async def snapshot(self) -> dict[str, Any]:
        snapshot = await self.gateway("iren_work_snapshot")
        settings = dict(snapshot.get("settings") or {})

        # Native RHEN Core replaced the old SQL settings table during the
        # consolidation. Keep the durable state visible, but allow deployment
        # policy to explicitly restore/disable bounded maintenance autopilot.
        raw_enabled = os.getenv("IREN_AUTOPILOT_ENABLED")
        if raw_enabled is not None:
            settings["autopilot_enabled"] = raw_enabled.strip().lower() in {
                "1", "true", "yes", "on",
            }
            settings["autopilot_enabled_source"] = "environment"
        else:
            settings.setdefault("autopilot_enabled", True)
            settings["autopilot_enabled_source"] = "durable_state"

        raw_cap = os.getenv("IREN_AUTOPILOT_MAX_JOBS_PER_DAY")
        if raw_cap is not None:
            try:
                settings["autopilot_max_jobs_per_day"] = max(
                    1, min(12, int(raw_cap))
                )
            except ValueError:
                settings.setdefault("autopilot_max_jobs_per_day", 3)
        else:
            settings.setdefault("autopilot_max_jobs_per_day", 3)

        snapshot["settings"] = settings
        return snapshot

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
                if normalize_command(text) == "MAINTENANCE_PROMPT":
                    snapshot["maintenance_evidence"] = await self.gateway(
                        "iren_maintenance_evidence"
                    )
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
                    active = codex.active_handoffs(snapshot)
                    if not active:
                        result = CommandResult(
                            "CODEX_VERIFY",
                            {
                                **result.response,
                                "message": "No active Codex handoff to verify.",
                                "action_taken": False,
                            },
                        )
                    else:
                        await self._reconcile_handoffs(force=True)
                        result = CommandResult(
                            "CODEX_VERIFY",
                            {
                                **result.response,
                                "message": "Handoff verification refreshed from current GitHub evidence.",
                                "action_taken": True,
                            },
                        )
                elif result.intent == "CODEX_HANDOFF":
                    active = codex.active_handoffs(snapshot)
                    action = (result.response or {}).get("next_action")
                    if active:
                        job_row = active[0]
                        result = CommandResult(
                            result.intent,
                            {
                                **result.response,
                                "message": "An active Codex handoff already exists.",
                                "execution_mode": "codex/manual software",
                                "action_taken": False,
                            },
                        )
                    elif not action:
                        pass
                    elif codex.mode(action) == "protected/requires Devon":
                        result = CommandResult(
                            result.intent,
                            {
                                **result.response,
                                "message": "This objective requires owner authority. No Codex handoff was created.",
                                "execution_mode": "protected/requires Devon",
                                "action_taken": False,
                            },
                        )
                    elif codex.mode(action) == "codex/manual software":
                        github = await self._github_evidence()
                        created = await self.gateway(
                            "iren_handoff_prepare",
                            objective_key=action.get("objective_key"),
                            main_sha=github["main_sha"],
                            command_id=str(command_id),
                            requested_by=requested_by,
                        )
                        job_row = created["job"]
                        package = (job_row.get("result") or {}).get("package") or {}
                        _emit_work_event(
                            "iren_codex_prepared",
                            handoff_id=job_row.get("job_id"),
                            objective_key=package.get("objective_key"),
                            base_sha=package.get("base_sha"),
                            prompt_chars=len(package.get("prompt") or ""),
                            package_digest=package.get("package_digest"),
                            title=package.get("title"),
                            paid_model_execution=False,
                        )
                        result = CommandResult(
                            result.intent,
                            {
                                **result.response,
                                "message": "Codex handoff prepared.",
                                "execution_mode": "codex/manual software",
                                "action_taken": True,
                            },
                        )
                    else:
                        result = CommandResult(
                            result.intent,
                            {
                                **result.response,
                                "message": "The pending action is deterministic; no Codex handoff was created.",
                                "execution_mode": "deterministic",
                                "action_taken": False,
                            },
                        )
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

    async def _github_get(self, path: str) -> Any:
        """Read bounded GitHub evidence directly; this never invokes a paid model."""
        if not codex.repository_allowed(self.github_repository):
            raise ValueError("github_repository_mismatch")
        if len(self.github_token) < 20:
            raise ValueError("github_read_token_not_configured")
        if not path or path.startswith("/") or "://" in path:
            raise ValueError("invalid_github_evidence_path")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/{self.github_repository}/{path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()

    async def _github_evidence(self, job=None):
        params: dict[str, Any] = {}
        if job:
            package = job["result"]["package"]
            params = {
                "handoff_id": job["job_id"],
                "objective_key": package["objective_key"],
            }
            association = job["result"].get("association") or {}
            if association.get("pr_number"):
                params["pr_number"] = association["pr_number"]
        return await inspect_github(
            self._github_get,
            repository=self.github_repository,
            **params,
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
                # Manual Codex handoffs are a zero-spend path. Record that policy
                # locally instead of depending on an executor runtime merely to
                # prove that paid model execution is disabled.
                worker = {
                    "spending_authority": False,
                    "software_backend_configured": False,
                    "evidence_source": "iren_local_zero_spend_policy",
                    "software_worker": {
                        "daily_budget_usd": 0,
                        "job_budget_usd": 0,
                        "model_invoked": False,
                        "manual_handoff": True,
                    },
                }
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
                    state = self.control_state()
                    topology_state = state.get("topology") if isinstance(state, dict) else {}
                    topology_state = topology_state if isinstance(topology_state, dict) else {}
                    gaps = topology_state.get("inventory_gaps")
                    gaps = gaps if isinstance(gaps, dict) else {}
                    criteria = runtime_evidence_criteria(state)
                    result = {
                        "criteria": criteria,
                        "required_inventory": topology_state.get("required_inventory") or [],
                        "inventory_gaps": gaps,
                        "inventory_verified_at": topology_state.get("inventory_verified_at"),
                        "observation_source": topology_state.get("inventory_source"),
                        "verifier": "iren_runtime_evidence_verifier_v1",
                    }
                    inventory_complete = criteria["complete_deployment_inventory"]
                    await self.gateway(
                        "iren_job_update",
                        job_id=job_id,
                        status="SUCCEEDED" if inventory_complete else "QUEUED",
                        result=result,
                    )
                    completed = (
                        await self._complete_objective_if_verified(job, criteria)
                        if inventory_complete
                        else False
                    )
                    _emit_work_event(
                        "iren_runtime_evidence_verified",
                        job_id=job_id,
                        objective_key=job.get("objective_key"),
                        completed=completed,
                        inventory_complete=inventory_complete,
                        inventory_gap_count=len(gaps),
                        retrying=not inventory_complete,
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
