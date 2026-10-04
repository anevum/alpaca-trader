from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import psycopg

from foundation.iren_gateway import iren_read
from foundation.iren_work_gateway import command_create, snapshot


UTC = timezone.utc


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _fresh(value: Any, *, now: datetime) -> bool:
    stamp = _parse_stamp(value)
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return 0 <= age <= 180


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value] if isinstance(value, list) else []


def _summary(work: dict[str, Any]) -> dict[str, int]:
    objectives = _rows(work.get("objectives"))
    jobs = _rows(work.get("jobs"))
    active = [
        row
        for row in jobs
        if str(row.get("status") or "") in {
            "QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"
        }
    ]
    blocked = [
        row for row in objectives if str(row.get("status") or "") == "BLOCKED"
    ]
    decisions = [
        row
        for row in jobs
        if str(row.get("status") or "") == "NEEDS_APPROVAL"
        or row.get("requires_human") is True
    ]
    complete = sum(
        1 for row in objectives if str(row.get("status") or "") == "COMPLETE"
    )
    return {
        "objective_count": len(objectives),
        "objectives_complete": complete,
        "active_jobs": len(active),
        "blocked_objectives": len(blocked),
        "requires_human": len(decisions),
    }



def _operator_guidance(
    *,
    stale: bool,
    current_state: str,
    incidents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if stale:
        return [{
            "severity": "critical",
            "target": "IREN / Foundation",
            "title": "Canonical operations state is stale",
            "action": (
                "Restore fresh IREN observations before trusting downstream status. "
                "Check the canonical scheduler, Foundation availability, and durable state first."
            ),
        }]

    rows: list[dict[str, str]] = []
    for incident in incidents:
        key = str(incident.get("key") or "")
        severity = (
            "critical"
            if str(incident.get("severity") or "").lower() == "critical"
            else "warning"
        )
        if key.startswith("service."):
            rows.append({
                "severity": severity,
                "target": key.removeprefix("service."),
                "title": "Runtime health is unavailable or unhealthy",
                "action": (
                    "Inspect the service deployment and logs, verify its health endpoint "
                    "and dependencies, then let IREN confirm recovery."
                ),
            })
        elif key.startswith("evidence."):
            rows.append({
                "severity": severity,
                "target": "Foundation evidence",
                "title": "Durable evidence delivery is degraded",
                "action": (
                    "Check Foundation ingest and the RHEN evidence spool. Confirm new events "
                    "are durably landing before evaluating research or retrying downstream work."
                ),
            })
        elif key.startswith("scheduler.") or key.startswith("workflow."):
            rows.append({
                "severity": severity,
                "target": "IREN scheduler",
                "title": "Scheduled work is degraded",
                "action": (
                    "Inspect the latest scheduler run and failure classification. Retry only "
                    "idempotent work after the underlying dependency is healthy."
                ),
            })
        elif key.startswith("configuration."):
            rows.append({
                "severity": severity,
                "target": "Protected configuration",
                "title": "Configuration identity changed",
                "action": (
                    "Compare the current protected configuration with the recorded baseline "
                    "and explain the drift before accepting a new baseline."
                ),
            })
        elif key.startswith("safety."):
            rows.append({
                "severity": "critical",
                "target": "Safety boundary",
                "title": "Protected runtime invariant failed",
                "action": (
                    "Keep execution authority unchanged. Restore the expected safe state "
                    "before promotion, execution, or configuration changes."
                ),
            })
        else:
            rows.append({
                "severity": severity,
                "target": key or "ANEVUM",
                "title": str(incident.get("reason") or "Operational incident").replace("_", " "),
                "action": (
                    "Inspect the affected subsystem and its latest deployment evidence, "
                    "repair the root cause, then wait for IREN to verify recovery."
                ),
            })

    if not rows and current_state != "HEALTHY":
        rows.append({
            "severity": "warning",
            "target": "ANEVUM",
            "title": "Control state is not healthy",
            "action": (
                "Review runtime readiness and dependency freshness, then allow the next "
                "IREN observation cycle to confirm the state."
            ),
        })
    return rows


def _operator_projection(
    *,
    state: dict[str, Any],
    control: dict[str, Any],
    work: dict[str, Any],
    stale: bool,
    incidents: list[dict[str, Any]],
    current_state: str,
) -> dict[str, Any]:
    topology = state.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    services = _rows(topology.get("services"))
    independent = [
        row for row in services if row.get("independent_runtime") is True
    ]
    ready = [
        row
        for row in independent
        if row.get("readiness") is True
        and str(row.get("status") or "") in {"RUNNING", "IDLE"}
    ]
    problems = [
        row
        for row in independent
        if str(row.get("status") or "") not in {"RUNNING", "IDLE"}
        or row.get("readiness") is not True
    ]

    summary = _summary(work)
    transitions = []
    for row in _rows(control.get("events"))[:20]:
        event = row.get("event")
        if not isinstance(event, dict):
            continue
        transitions.append({
            "key": event.get("key"),
            "transition": event.get("transition"),
            "severity": event.get("severity"),
            "reason": event.get("reason"),
            "created_at": row.get("created_at"),
            "delivery_status": row.get("delivery_status"),
        })

    guidance = _operator_guidance(
        stale=stale,
        current_state=current_state,
        incidents=incidents,
    )
    if stale:
        message = (
            "Canonical ANEVUM operations state is stale. Restore IREN/Foundation "
            "before trusting downstream health."
        )
    elif current_state == "HEALTHY" and not guidance:
        message = (
            f"ANEVUM healthy. {len(ready)}/{len(independent)} independent runtimes "
            "ready. No operator action required."
        )
    else:
        message = (
            f"{len(guidance)} operator item"
            + ("" if len(guidance) == 1 else "s")
            + " require review."
        )

    return {
        "version": "anevum_operator.v1",
        "state": "STALE" if stale else current_state,
        "message": message,
        "inventory": {
            "independent_runtimes": len(independent),
            "ready": len(ready),
            "problems": len(problems),
            "complete": topology.get("inventory_complete") is True,
            "gaps": topology.get("inventory_gaps") or {},
            "verified_at": topology.get("inventory_verified_at"),
        },
        "work": summary,
        "guidance": guidance,
        "recent_transitions": transitions,
        "authority": {
            "read_only_projection": True,
            "trading_mutations": False,
            "protected_actions_bypassed": False,
        },
    }

def read_command_snapshot(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        return {
            "control": iren_read(conn),
            "work": snapshot(conn),
        }


def enqueue_command(
    database_url: str,
    *,
    command: str,
    requested_by: str,
) -> dict[str, Any]:
    text = str(command or "").strip()[:4000]
    if not text:
        raise ValueError("command_required")
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        return command_create(
            conn,
            {
                "command_text": text,
                "source": "command",
                "requested_by": str(requested_by or "")[:160],
                "context": {"surface": "ANEVUM Command"},
            },
        )


def project_command(
    snapshot_value: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    control = (
        dict(snapshot_value.get("control"))
        if isinstance(snapshot_value.get("control"), dict)
        else {}
    )
    work = (
        dict(snapshot_value.get("work"))
        if isinstance(snapshot_value.get("work"), dict)
        else {}
    )
    raw_state = (
        dict(control.get("state"))
        if isinstance(control.get("state"), dict)
        else {}
    )
    state = deepcopy(raw_state)
    stale = not _fresh(state.get("observed_at"), now=current)
    topology = (
        state.get("topology")
        if isinstance(state.get("topology"), dict)
        else None
    )

    if stale:
        state["state"] = "STALE"
        if topology:
            services = topology.get("services")
            if isinstance(services, list):
                for item in services:
                    if isinstance(item, dict) and item.get("independent_runtime"):
                        item.update({
                            "status": "STALE",
                            "readiness": False,
                            "liveness": None,
                        })
            dependencies = topology.get("dependencies")
            if isinstance(dependencies, dict):
                for dependency in dependencies.values():
                    if isinstance(dependency, dict):
                        dependency["status"] = "STALE"

    incident_map = (
        state.get("incidents")
        if isinstance(state.get("incidents"), dict)
        else {}
    )
    incidents = []
    for key, value in incident_map.items():
        if not isinstance(value, dict) or value.get("status") != "OPEN":
            continue
        incidents.append({
            "key": key,
            "severity": value.get("severity"),
            "reason": value.get("reason"),
            "opened_at": value.get("opened_at"),
        })

    objectives = _rows(work.get("objectives"))
    jobs = _rows(work.get("jobs"))
    job_events = _rows(work.get("job_events"))
    commands = _rows(work.get("commands"))
    current_state = str(state.get("state") or "UNKNOWN")

    from app.iren.work import status_summary
    from app.iren.codex_handoff import active_handoffs, mode
    summary = status_summary(work, state)
    handoffs = active_handoffs(work)

    baseline = state.get("configuration_baseline")
    baseline = baseline if isinstance(baseline, dict) else {}

    operator = _operator_projection(
        state=state,
        control=control,
        work={"objectives": objectives, "jobs": jobs},
        stale=stale,
        incidents=incidents,
        current_state=current_state,
    )

    return {
        "schema_version": "iren_command.v2",
        "work_schema_version": "iren_work.v1",
        "revision": control.get("revision"),
        "observed_at": state.get("observed_at"),
        "stale": stale,
        "state": current_state,
        "topology": topology,
        "incidents": incidents,
        "scheduler": state.get("scheduler"),
        "action_required": stale or current_state != "HEALTHY" or bool(incidents),
        "configuration_identity": baseline.get("fingerprint"),
        "operator": operator,
        "work": {
            **_summary({"objectives": objectives, "jobs": jobs}),
            "next_action": summary.get("next_action"),
            "execution_mode": "codex/manual software" if handoffs else mode(summary.get("next_action")),
            "handoffs": [r.get("result") | {"handoff_id": r.get("job_id"), "objective_key": r.get("objective_key")} for r in jobs if r.get("job_type") == "CODEX_HANDOFF" and isinstance(r.get("result"), dict)],
            "objectives": objectives,
            "jobs": jobs,
            "job_events": job_events,
            "commands": commands,
        },
    }
