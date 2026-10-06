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
            order by (status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')) desc, created_at desc
            limit 200
            """
        )
        jobs = _rows(cur)

        cur.execute(
            """
            select
                e.event_id,e.job_id,e.event_type,e.event,e.created_at,
                j.owner_system,j.objective_key,j.title,j.job_type
            from iren.job_events e
            join iren.jobs j on j.job_id=e.job_id
            order by e.created_at desc,e.event_id desc
            limit 200
            """
        )
        job_events = _rows(cur)

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
        "job_events": job_events,
        "commands": commands,
        "settings": settings,
    }


def _maintenance_research_control(
    problems: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    active_states = {"RUNNING", "WAITING", "QUEUED", "BLOCKED"}
    problem = next(
        (
            row
            for row in problems
            if str(row.get("status") or "").upper() in active_states
        ),
        problems[0] if problems else None,
    )
    problem_id = str((problem or {}).get("problem_id") or "")
    stage = str((problem or {}).get("research_stage") or "").upper()
    latest = next(
        (
            row
            for row in runs
            if not problem_id or str(row.get("problem_id") or "") == problem_id
        ),
        runs[0] if runs else None,
    )
    next_action = str((latest or {}).get("next_action") or "").upper()
    decision = str((latest or {}).get("decision") or "").upper()
    rejected = sum(
        1
        for row in runs
        if str(row.get("problem_id") or "") == problem_id
        and "REJECTED" in str(row.get("result_state") or "").upper()
    )

    mode = "IDLE"
    reason = "No active frozen research chain is exposed."
    review_required = False
    review_kind = None

    if stage == "ADAPTIVE_PROGRAM_EXHAUSTED":
        mode = "RESEARCH_REVIEW_REQUIRED"
        reason = (
            "The bounded hypothesis family is exhausted. Do not restart it. "
            "A materially new hypothesis family requires a deliberate Work/Codex pass."
        )
        review_required = True
        review_kind = "NEW_HYPOTHESIS_FAMILY"
    elif (
        stage == "CANDIDATE_READY_FOR_STRATEGY_REVIEW"
        or next_action == "PROTECTED_STRATEGY_UPDATE_REVIEW"
    ):
        mode = "RELEASE_REVIEW_REQUIRED"
        reason = (
            "Evidence reached the protected strategy boundary. Strategy patching "
            "or production release requires explicit review."
        )
        review_required = True
        review_kind = "STRATEGY_PATCH_OR_RELEASE"
    elif any(str(row.get("status") or "").upper() == "RUNNING" for row in runs):
        mode = "AUTOMATED_TEST"
        reason = "A frozen experiment is running inside its existing methodology."
    elif stage.startswith("CRYPTO_COMPILED_") or stage == "RESEARCH_IMPLEMENTATION_REQUIRED":
        mode = "AUTOMATED_TEST"
        reason = "A frozen research chain may advance through its existing validation gates."
    elif problem is not None:
        mode = "OBSERVING"
        reason = "Research evidence is durable, but no bounded experiment is currently executing."

    return {
        "schema_version": "research_control.v1",
        "mode": mode,
        "reason": reason,
        "review_required": review_required,
        "review_kind": review_kind,
        "work_credit_recommended": review_required,
        "problem_id": problem_id or None,
        "stage": stage or None,
        "decision": decision or None,
        "next_action": next_action or None,
        "rejected_generations": rejected,
    }


def maintenance_evidence(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Bounded evidence used only to assemble the next maintenance handoff."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select problem_id,title,status,
                   metadata->>'research_stage' as research_stage,
                   metadata->>'candidate_id' as candidate_id,
                   metadata->>'family' as family,
                   updated_at,started_at,completed_at
            from graen.problems
            order by
              case status when 'RUNNING' then 0 when 'QUEUED' then 1
                          when 'WAITING' then 2 when 'BLOCKED' then 3 else 4 end,
              updated_at desc
            limit 20
            """
        )
        graen_problems = _rows(cur)

        cur.execute(
            """
            select run_id,problem_id,status,methodology_version,
                   result_summary->>'state' as result_state,
                   result_summary->>'decision' as decision,
                   result_summary->>'next_action' as next_action,
                   result_summary->>'strategy_version_id' as strategy_version_id,
                   started_at,completed_at,created_at
            from graen.runs
            order by coalesce(completed_at,started_at,created_at) desc
            limit 20
            """
        )
        graen_runs = _rows(cur)

        cur.execute(
            """
            select replay.replay_id,replay.asset_class,replay.methodology_version,
                   replay.strategy_version_id,replay.status,
                   result.result_type,
                   replay.started_at,replay.completed_at
            from velum.replays replay
            left join lateral (
                select result_type
                from velum.results
                where replay_id=replay.replay_id
                order by created_at desc
                limit 1
            ) result on true
            order by coalesce(replay.completed_at,replay.started_at) desc nulls last
            limit 20
            """
        )
        velum_replays = _rows(cur)

        cur.execute(
            """
            select calibration_id,model_version,methodology_version,
                   sample_count,created_at
            from nostra.calibration_runs
            order by created_at desc
            limit 8
            """
        )
        nostra_calibrations = _rows(cur)

        cur.execute(
            """
            select forecast_id,forecast_key,model_version,methodology_version,
                   subject,issued_at,observed_at
            from nostra.forecasts
            left join nostra.outcomes using (forecast_id)
            order by issued_at desc
            limit 12
            """
        )
        nostra_forecasts = _rows(cur)

        cur.execute(
            """
            select strategy_version_id,
                   max(occurred_at) as last_event_at,
                   count(*) filter (where event_type='decision_cycle') as decision_cycles,
                   count(*) filter (where event_type='broker_fill') as fills,
                   count(*) filter (where event_type='runtime_error') as runtime_errors,
                   count(distinct run_id) as run_count
            from rhen.events
            where occurred_at >= now() - interval '24 hours'
              and strategy_version_id is not null
            group by strategy_version_id
            order by max(occurred_at) desc
            limit 12
            """
        )
        strategy_activity = _rows(cur)

    return {
        "observed_at": datetime.now().astimezone().isoformat(),
        "research_control": _maintenance_research_control(
            graen_problems,
            graen_runs,
        ),
        "graen_problems": graen_problems,
        "graen_runs": graen_runs,
        "velum_replays": velum_replays,
        "nostra_calibrations": nostra_calibrations,
        "nostra_forecasts": nostra_forecasts,
        "strategy_activity_24h": strategy_activity,
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
    from app.iren.work import normalize_command
    if normalize_command(text).startswith("CODEX_"):
        context = {**context, "required_capability": "CODEX_HANDOFF"}
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
                    where (status='QUEUED'
                       or (status='PROCESSING' and lease_until < now()))
                      and (context->>'required_capability' is distinct from 'CODEX_HANDOFF'
                           or %s='iren-work-engine-codex-v1')
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
                (owner, bounded, owner),
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
    if job_type == "CODEX_HANDOFF":
        raise ValueError("use_canonical_handoff_prepare")
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
                where job_id=%s and job_type <> 'CODEX_HANDOFF'
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
                  and not (%s='COMPLETE' and exists(select 1 from iren.jobs j where j.objective_key=iren.objectives.objective_key
                      and j.job_type='CODEX_HANDOFF' and j.status='WAITING'))
                returning *
                """,
                (status, status, key, status),
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
        "iren_maintenance_evidence",
        "iren_command_create",
        "iren_commands_claim",
        "iren_command_complete",
        "iren_job_create",
        "iren_jobs_claim",
        "iren_job_update",
        "iren_settings_update",
        "iren_objective_update",
        "iren_handoff_prepare", "iren_handoff_associate", "iren_handoff_verify", "iren_handoff_evidence", "iren_handoff_supersede",
    }
    if action not in actions:
        return None

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        if action.startswith("iren_handoff_"):
            from foundation import iren_handoff_gateway as handoff
            if action == "iren_handoff_evidence":
                return handoff.evidence_snapshot(conn)
            handler = {"iren_handoff_prepare": handoff.prepare, "iren_handoff_associate": handoff.associate,
                       "iren_handoff_verify": handoff.verify, "iren_handoff_supersede": handoff.supersede}[action]
            return handler(conn, body)
        if action == "iren_work_snapshot":
            return snapshot(conn)
        if action == "iren_maintenance_evidence":
            return maintenance_evidence(conn)
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
