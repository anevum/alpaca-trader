from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


ACTIVE_JOB_STATES = {
    "QUEUED",
    "RUNNING",
    "WAITING",
    "BLOCKED",
    "NEEDS_APPROVAL",
}
TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
OBJECTIVE_STATES = {
    "LOCKED",
    "ACTIVE",
    "BLOCKED",
    "READY",
    "COMPLETE",
    "FUTURE",
    "OBSOLETE",
}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _rows(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    columns = [column.name for column in cur.description]
    return [_serialize(dict(zip(columns, row))) for row in cur.fetchall()]


def snapshot(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select objective_key,parent_key,title,description,status,owner_system,
                   priority,dependencies,success_criteria,protected_action,
                   metadata,created_at,updated_at,completed_at
            from iren.objectives
            order by priority desc, created_at asc
            """
        )
        objectives = _rows(cur)

        cur.execute(
            """
            select
                job_id,objective_key,title,instructions,owner_system,job_type,
                status,priority,protected_action,requires_human,
                requested_by,requested_via,
                lease_owner as claimed_by,lease_until,started_at,completed_at,
                output as result,error,metadata,created_at,updated_at
            from iren.jobs
            order by created_at desc
            limit 200
            """
        )
        jobs = _rows(cur)

        cur.execute(
            """
            select
                command_id,coalesce(command_text,command) as command_text,
                source,requested_by,status,result,linked_job_id,
                created_at,updated_at,completed_at
            from iren.commands
            order by created_at desc
            limit 100
            """
        )
        commands = _rows(cur)

        cur.execute(
            """
            select
                autopilot_enabled,autopilot_max_jobs_per_day,
                model_execution_authorized,updated_by,updated_at
            from iren.settings
            where singleton
            """
        )
        row = cur.fetchone()
        settings = {}
        if row:
            columns = [column.name for column in cur.description]
            settings = _serialize(dict(zip(columns, row)))

    return {
        "objectives": objectives,
        "jobs": jobs,
        "commands": commands,
        "settings": settings,
    }


def command_create(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("command_text") or "").strip()[:4000]
    source = str(body.get("source") or "iren").strip()[:40] or "iren"
    requested_by = str(body.get("requested_by") or "").strip()[:160] or None
    context = body.get("context")
    if not isinstance(context, dict):
        context = {}
    if not text:
        raise ValueError("invalid_iren_command")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into iren.commands (
                    requested_by,target_system,command,arguments,status,
                    command_text,source,context,created_at,updated_at
                )
                values (%s,'IREN',%s,%s,'QUEUED',%s,%s,%s,now(),now())
                returning
                    command_id,command_text,source,requested_by,status,created_at
                """,
                (
                    requested_by,
                    text,
                    Jsonb(context),
                    text,
                    source,
                    Jsonb(context),
                ),
            )
            row = cur.fetchone()
            columns = [column.name for column in cur.description]
    return {"command": _serialize(dict(zip(columns, row)))}


def commands_claim(
    conn: psycopg.Connection[Any],
    *,
    owner: str,
    limit: int,
) -> dict[str, Any]:
    owner = owner.strip()[:120]
    if not owner:
        raise ValueError("invalid_iren_command_owner")
    bounded = max(1, min(20, int(limit)))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                with candidates as (
                    select command_id
                    from iren.commands
                    where status='QUEUED'
                       or (status='PROCESSING' and lease_until < now())
                    order by created_at asc
                    limit %s
                    for update skip locked
                )
                update iren.commands c
                set status='PROCESSING',
                    owner=%s,
                    lease_until=now()+interval '120 seconds',
                    updated_at=now()
                from candidates x
                where c.command_id=x.command_id
                returning
                    c.command_id,c.command_text,c.source,c.requested_by,
                    c.status,c.context,c.created_at
                """,
                (bounded, owner),
            )
            rows = _rows(cur)
    return {"commands": rows}


def command_complete(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    command_id = str(body.get("command_id") or "").strip()
    status = str(body.get("status") or "").strip().upper()
    response = body.get("response")
    if not isinstance(response, dict):
        response = {}
    linked = str(body.get("linked_job_id") or "").strip() or None
    try:
        command_uuid = UUID(command_id)
        linked_uuid = UUID(linked) if linked else None
    except ValueError as exc:
        raise ValueError("invalid_iren_command_completion") from exc
    if status not in TERMINAL_JOB_STATES:
        raise ValueError("invalid_iren_command_completion")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.commands
                set status=%s,
                    result=%s,
                    linked_job_id=%s,
                    completed_at=now(),
                    updated_at=now(),
                    lease_until=null,
                    owner=null
                where command_id=%s
                returning command_id,status,result,linked_job_id,completed_at
                """,
                (
                    status,
                    Jsonb(response),
                    linked_uuid,
                    command_uuid,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("iren_command_not_found")
            columns = [column.name for column in cur.description]
    return {"command": _serialize(dict(zip(columns, row)))}


def job_create(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    job = body.get("job")
    if not isinstance(job, dict):
        raise ValueError("invalid_iren_job")
    title = str(job.get("title") or "").strip()[:240]
    instructions = str(job.get("instructions") or "").strip()[:12000]
    status = str(job.get("status") or "QUEUED").strip().upper()
    objective_key = str(job.get("objective_key") or "").strip() or None
    if not title or status not in {
        "QUEUED",
        "WAITING",
        "BLOCKED",
        "NEEDS_APPROVAL",
    }:
        raise ValueError("invalid_iren_job")
    metadata = job.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    job_id = uuid4()
    job_key = f"iren-work:{job_id}"
    job_type = str(job.get("job_type") or "AGENT_WORK").strip()[:80] or "AGENT_WORK"
    owner_system = str(job.get("owner_system") or "IREN").strip()[:80] or "IREN"

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into iren.jobs (
                    job_id,job_key,owner_system,workflow,status,
                    input,error,objective_key,title,instructions,job_type,
                    priority,protected_action,requires_human,
                    requested_by,requested_via,metadata
                )
                values (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s
                )
                returning
                    job_id,objective_key,title,instructions,owner_system,
                    job_type,status,priority,protected_action,requires_human,
                    requested_by,requested_via,metadata,created_at,updated_at
                """,
                (
                    job_id,
                    job_key,
                    owner_system,
                    job_type,
                    status,
                    Jsonb({"instructions": instructions}),
                    Jsonb({}),
                    objective_key,
                    title,
                    instructions,
                    job_type,
                    int(job.get("priority") or 0),
                    bool(job.get("protected_action")),
                    bool(job.get("requires_human")),
                    str(job.get("requested_by") or "").strip()[:160] or None,
                    str(job.get("requested_via") or "iren").strip()[:40] or "iren",
                    Jsonb(metadata),
                ),
            )
            row = cur.fetchone()
            columns = [column.name for column in cur.description]
            cur.execute(
                """
                insert into iren.job_events (job_id,event_type,event)
                values (%s,'CREATED',%s)
                """,
                (job_id, Jsonb({"source": "iren_work_engine"})),
            )
    return {"job": _serialize(dict(zip(columns, row)))}


def jobs_claim(
    conn: psycopg.Connection[Any],
    *,
    owner: str,
    limit: int,
) -> dict[str, Any]:
    owner = owner.strip()[:120]
    if not owner:
        raise ValueError("invalid_iren_job_owner")
    bounded = max(1, min(10, int(limit)))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                with candidates as (
                    select job_id
                    from iren.jobs
                    where status='QUEUED'
                       or (status='RUNNING' and lease_until < now())
                    order by priority desc, created_at asc
                    limit %s
                    for update skip locked
                )
                update iren.jobs j
                set status='RUNNING',
                    lease_owner=%s,
                    lease_until=now()+interval '300 seconds',
                    started_at=coalesce(started_at,now()),
                    updated_at=now()
                from candidates x
                where j.job_id=x.job_id
                returning
                    j.job_id,j.objective_key,j.title,j.instructions,
                    j.owner_system,j.job_type,j.status,j.priority,
                    j.protected_action,j.requires_human,j.requested_by,
                    j.requested_via,j.lease_owner as claimed_by,
                    j.lease_until,j.started_at,j.completed_at,
                    j.output as result,j.error,j.metadata,
                    j.created_at,j.updated_at
                """,
                (bounded, owner),
            )
            rows = _rows(cur)
    return {"jobs": rows}


def job_update(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    job_id = str(body.get("job_id") or "").strip()
    status = str(body.get("status") or "").strip().upper()
    allowed = ACTIVE_JOB_STATES | TERMINAL_JOB_STATES
    try:
        job_uuid = UUID(job_id)
    except ValueError as exc:
        raise ValueError("invalid_iren_job_update") from exc
    if status not in allowed:
        raise ValueError("invalid_iren_job_update")
    result = body.get("result")
    error = body.get("error")
    if not isinstance(result, dict):
        result = {}
    if not isinstance(error, dict):
        error = {}
    terminal = status in TERMINAL_JOB_STATES

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.jobs
                set status=%s,
                    output=%s,
                    error=%s,
                    completed_at=case when %s then now() else completed_at end,
                    lease_until=null,
                    lease_owner=null,
                    updated_at=now()
                where job_id=%s
                returning
                    job_id,objective_key,title,instructions,owner_system,
                    job_type,status,priority,protected_action,requires_human,
                    requested_by,requested_via,lease_owner as claimed_by,
                    lease_until,started_at,completed_at,
                    output as result,error,metadata,created_at,updated_at
                """,
                (
                    status,
                    Jsonb(result),
                    Jsonb(error),
                    terminal,
                    job_uuid,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("iren_job_not_found")
            columns = [column.name for column in cur.description]
            cur.execute(
                """
                insert into iren.job_events (job_id,event_type,event)
                values (%s,%s,%s)
                """,
                (
                    job_uuid,
                    status,
                    Jsonb({"result": result, "error": error}),
                ),
            )
    return {"job": _serialize(dict(zip(columns, row)))}


def settings_update(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    requested = body.get("settings")
    if not isinstance(requested, dict):
        requested = {}
    updated_by = str(body.get("updated_by") or "iren").strip()[:160] or "iren"
    autopilot = (
        requested.get("autopilot_enabled")
        if isinstance(requested.get("autopilot_enabled"), bool)
        else None
    )
    model_authorized = (
        requested.get("model_execution_authorized")
        if isinstance(requested.get("model_execution_authorized"), bool)
        else None
    )
    cap_raw = requested.get("autopilot_max_jobs_per_day")
    cap = None
    if cap_raw is not None:
        cap = max(1, min(12, int(cap_raw)))

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.settings
                set autopilot_enabled=coalesce(%s,autopilot_enabled),
                    autopilot_max_jobs_per_day=coalesce(%s,autopilot_max_jobs_per_day),
                    model_execution_authorized=coalesce(%s,model_execution_authorized),
                    updated_by=%s,
                    updated_at=now()
                where singleton
                returning
                    autopilot_enabled,autopilot_max_jobs_per_day,
                    model_execution_authorized,updated_by,updated_at
                """,
                (
                    autopilot,
                    cap,
                    model_authorized,
                    updated_by,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("iren_settings_not_found")
            columns = [column.name for column in cur.description]
    return {"settings": _serialize(dict(zip(columns, row)))}


def objective_update(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    key = str(body.get("objective_key") or "").strip()
    status = str(body.get("status") or "").strip().upper()
    if not key or status not in OBJECTIVE_STATES:
        raise ValueError("invalid_iren_objective_update")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.objectives
                set status=%s,
                    updated_at=now(),
                    completed_at=case when %s='COMPLETE' then now() else completed_at end
                where objective_key=%s
                returning *
                """,
                (status, status, key),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("iren_objective_not_found")
            columns = [column.name for column in cur.description]
    return {"objective": _serialize(dict(zip(columns, row)))}


def handle_work_action(
    database_url: str,
    action: str,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    actions = {
        "iren_work_snapshot",
        "iren_command_create",
        "iren_commands_claim",
        "iren_command_complete",
        "iren_job_create",
        "iren_jobs_claim",
        "iren_job_update",
        "iren_settings_update",
        "iren_objective_update",
    }
    if action not in actions:
        return None

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        if action == "iren_work_snapshot":
            return snapshot(conn)
        if action == "iren_command_create":
            return command_create(conn, body)
        if action == "iren_commands_claim":
            return commands_claim(
                conn,
                owner=str(body.get("owner") or ""),
                limit=int(body.get("limit") or 5),
            )
        if action == "iren_command_complete":
            return command_complete(conn, body)
        if action == "iren_job_create":
            return job_create(conn, body)
        if action == "iren_jobs_claim":
            return jobs_claim(
                conn,
                owner=str(body.get("owner") or ""),
                limit=int(body.get("limit") or 3),
            )
        if action == "iren_job_update":
            return job_update(conn, body)
        if action == "iren_settings_update":
            return settings_update(conn, body)
        if action == "iren_objective_update":
            return objective_update(conn, body)
    return None
