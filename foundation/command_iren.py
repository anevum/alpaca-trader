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
    commands = _rows(work.get("commands"))
    current_state = str(state.get("state") or "UNKNOWN")

    baseline = state.get("configuration_baseline")
    baseline = baseline if isinstance(baseline, dict) else {}

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
        "work": {
            **_summary({"objectives": objectives, "jobs": jobs}),
            "objectives": objectives,
            "jobs": jobs,
            "commands": commands,
        },
    }
