from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


UTC = timezone.utc
IREN_STATE_KEY = "IREN"
IREN_STATE_VERSION = "iren-control-v1.0.0"
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "MISSED", "SKIPPED", "STALE"}
RETRYABLE_FAILURES = {
    "transient_infrastructure",
    "dependency_unavailable",
    "evidence_unavailable",
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


def _parse_datetime(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid_{field}")
    return parsed.astimezone(UTC)


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _validate_state_commit(body: dict[str, Any]) -> tuple[int, str, dict[str, Any], list[dict[str, Any]], datetime]:
    try:
        expected_revision = int(body.get("expected_revision"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_iren_observation") from exc
    observation_key = str(body.get("observation_key") or "")
    state = body.get("state")
    events = body.get("events") or []
    if expected_revision < 0:
        raise ValueError("invalid_iren_observation")
    if len(observation_key) != 64:
        raise ValueError("invalid_iren_observation")
    if not isinstance(state, dict) or state.get("version") != IREN_STATE_VERSION:
        raise ValueError("invalid_iren_observation")
    observed_at = _parse_datetime(state.get("observed_at"), "iren_observed_at")
    if observed_at > datetime.now(UTC) + timedelta(seconds=30):
        raise ValueError("invalid_iren_observation")
    if not isinstance(events, list):
        raise ValueError("invalid_iren_observation")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid_iren_event")
        if len(str(event.get("event_key") or "")) != 64:
            raise ValueError("invalid_iren_event")
        if str(event.get("transition") or "") not in {"OPEN", "ESCALATED", "RECOVERED"}:
            raise ValueError("invalid_iren_event")
        if str(event.get("route") or "") != "iren-control":
            raise ValueError("invalid_iren_event")
    return expected_revision, observation_key, state, events, observed_at


def iren_read(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select revision, state, updated_at, observation_key, observed_at
            from iren.system_state
            where system_key=%s
            """,
            (IREN_STATE_KEY,),
        )
        row = cur.fetchone()
        cur.execute(
            """
            select event, created_at, delivery_status, attempts
            from iren.control_events
            order by created_at desc
            limit 50
            """
        )
        events = [
            {
                "event": item[0],
                "created_at": _serialize(item[1]),
                "delivery_status": item[2],
                "attempts": item[3],
            }
            for item in cur.fetchall()
        ]
    if not row:
        return {
            "revision": 0,
            "state": {},
            "updated_at": None,
            "observation_key": None,
            "observed_at": None,
            "events": events,
        }
    return {
        "revision": int(row[0]),
        "state": row[1] or {},
        "updated_at": _serialize(row[2]),
        "observation_key": row[3],
        "observed_at": _serialize(row[4]),
        "events": events,
    }


def iren_commit(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    expected_revision, observation_key, state, events, observed_at = _validate_state_commit(body)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select revision, observation_key, state, observed_at
                from iren.system_state
                where system_key=%s
                for update
                """,
                (IREN_STATE_KEY,),
            )
            previous = cur.fetchone()
            if previous:
                revision = int(previous[0])
                if previous[1] == observation_key:
                    return {
                        "committed": True,
                        "idempotent": True,
                        "revision": revision,
                    }
                if revision != expected_revision:
                    return {
                        "committed": False,
                        "conflict": True,
                        "revision": revision,
                    }
                previous_observed_at = previous[3]
                if previous_observed_at and observed_at <= previous_observed_at:
                    raise ValueError("invalid_iren_observation")
                revision += 1
                cur.execute(
                    """
                    update iren.system_state
                    set health=%s,
                        state=%s,
                        revision=%s,
                        observed_at=%s,
                        observation_key=%s,
                        updated_at=now()
                    where system_key=%s
                    """,
                    (
                        str(state.get("state") or "STARTING"),
                        Jsonb(state),
                        revision,
                        observed_at,
                        observation_key,
                        IREN_STATE_KEY,
                    ),
                )
            else:
                if expected_revision != 0:
                    return {
                        "committed": False,
                        "conflict": True,
                        "revision": 0,
                    }
                revision = 1
                cur.execute(
                    """
                    insert into iren.system_state (
                        system_key, health, state, revision,
                        observed_at, observation_key
                    )
                    values (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        IREN_STATE_KEY,
                        str(state.get("state") or "STARTING"),
                        Jsonb(state),
                        revision,
                        observed_at,
                        observation_key,
                    ),
                )

            for event in events:
                cur.execute(
                    """
                    insert into iren.control_events (
                        event_key, revision, event
                    )
                    values (%s,%s,%s)
                    on conflict (event_key) do nothing
                    """,
                    (
                        str(event["event_key"]),
                        revision,
                        Jsonb(event),
                    ),
                )
    return {"committed": True, "revision": revision}


def notification_claim(
    conn: psycopg.Connection[Any],
    *,
    owner: str,
) -> dict[str, Any]:
    try:
        owner_uuid = UUID(owner)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_iren_owner") from exc
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select event_key
                from iren.control_events
                where delivery_status not like 'delivered:%'
                  and attempts < 3
                  and (lease_until is null or lease_until < now())
                order by created_at
                limit 10
                for update skip locked
                """
            )
            keys = [str(row[0]) for row in cur.fetchall()]
            if not keys:
                return {"events": []}
            cur.execute(
                """
                update iren.control_events
                set owner=%s,
                    lease_until=now()+interval '120 seconds',
                    attempts=attempts+1
                where event_key = any(%s)
                returning event
                """,
                (owner_uuid, keys),
            )
            events = [row[0] for row in cur.fetchall()]
    return {"events": events}


def notification_complete(
    conn: psycopg.Connection[Any],
    *,
    event_key: str,
    owner: str,
    delivery_status: str,
) -> dict[str, Any]:
    try:
        owner_uuid = UUID(owner)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_iren_owner") from exc
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.control_events
                set delivery_status=%s,
                    lease_until=null,
                    owner=null
                where event_key=%s
                  and owner=%s
                  and lease_until > now()
                returning event_key
                """,
                (
                    str(delivery_status)[:200],
                    str(event_key),
                    owner_uuid,
                ),
            )
            updated = cur.fetchone() is not None
    return {"updated": updated}


def scheduler_claim(conn: psycopg.Connection[Any], job: dict[str, Any]) -> dict[str, Any]:
    required = [
        "job_key",
        "workflow_id",
        "workflow_version",
        "scheduler_version",
        "scheduled_at",
        "trigger_type",
    ]
    missing = [key for key in required if not str(job.get(key) or "").strip()]
    if missing:
        raise ValueError("invalid_claim_shape:" + ",".join(missing))

    job_key = str(job["job_key"]).strip()
    scheduled_at = _parse_datetime(job["scheduled_at"], "scheduled_at")
    max_attempts = _positive_int(job.get("max_attempts"), 1, 10)
    lease_seconds = max(
        60,
        min(3600, _positive_int(job.get("lease_seconds"), 900, 3600)),
    )
    allow_retry = bool(job.get("allow_retry", False))
    details = job.get("details") if isinstance(job.get("details"), dict) else {}

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into iren.scheduler_runs (
                    job_key, workflow_id, workflow_version, scheduler_version,
                    scheduled_at, started_at, status, trigger_type,
                    attempt, max_attempts, worker_identity, source_commit,
                    input_identity, retry_state, catchup_state,
                    lease_until, details
                )
                values (
                    %s,%s,%s,%s,%s,now(),'RUNNING',%s,
                    1,%s,%s,%s,%s,'initial',%s,
                    now()+(%s * interval '1 second'),%s
                )
                on conflict (job_key) do nothing
                returning run_id, status, attempt, lease_until
                """,
                (
                    job_key,
                    str(job["workflow_id"]).strip(),
                    str(job["workflow_version"]).strip(),
                    str(job["scheduler_version"]).strip(),
                    scheduled_at,
                    str(job["trigger_type"]).strip(),
                    max_attempts,
                    str(job.get("worker_identity") or "") or None,
                    str(job.get("source_commit") or "") or None,
                    str(job.get("input_identity") or "") or None,
                    str(job.get("catchup_state") or "") or None,
                    lease_seconds,
                    Jsonb(details),
                ),
            )
            created = cur.fetchone()
            if created:
                return {
                    "claimed": True,
                    "duplicate": False,
                    "run_id": str(created[0]),
                    "status": created[1],
                    "attempt": created[2],
                    "lease_until": _serialize(created[3]),
                }

            cur.execute(
                """
                select
                    run_id, status, attempt, max_attempts,
                    lease_until, error_classification
                from iren.scheduler_runs
                where job_key=%s
                for update
                """,
                (job_key,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("scheduler_claim_missing_row")

            run_id, status, attempt, existing_max, lease_until, classification = row
            effective_max = max(int(existing_max), max_attempts)

            if (
                status == "RUNNING"
                and lease_until is not None
                and lease_until < datetime.now(UTC)
                and int(attempt) < effective_max
            ):
                cur.execute(
                    """
                    update iren.scheduler_runs
                    set attempt=attempt+1,
                        max_attempts=%s,
                        started_at=now(),
                        completed_at=null,
                        lease_until=now()+(%s * interval '1 second'),
                        worker_identity=%s,
                        source_commit=%s,
                        input_identity=%s,
                        retry_state='lease_recovery',
                        catchup_state=coalesce(%s, catchup_state),
                        updated_at=now()
                    where job_key=%s
                    returning run_id,status,attempt,lease_until
                    """,
                    (
                        effective_max,
                        lease_seconds,
                        str(job.get("worker_identity") or "") or None,
                        str(job.get("source_commit") or "") or None,
                        str(job.get("input_identity") or "") or None,
                        str(job.get("catchup_state") or "") or None,
                        job_key,
                    ),
                )
                recovered = cur.fetchone()
                return {
                    "claimed": True,
                    "duplicate": False,
                    "recovered": True,
                    "run_id": str(recovered[0]),
                    "status": recovered[1],
                    "attempt": recovered[2],
                    "lease_until": _serialize(recovered[3]),
                }

            if (
                allow_retry
                and status == "FAILED"
                and classification in RETRYABLE_FAILURES
                and int(attempt) < effective_max
            ):
                cur.execute(
                    """
                    update iren.scheduler_runs
                    set status='RUNNING',
                        attempt=attempt+1,
                        max_attempts=%s,
                        started_at=now(),
                        completed_at=null,
                        lease_until=now()+(%s * interval '1 second'),
                        worker_identity=%s,
                        source_commit=%s,
                        input_identity=%s,
                        retry_state='retry',
                        catchup_state=coalesce(%s, catchup_state),
                        updated_at=now()
                    where job_key=%s
                    returning run_id,status,attempt,lease_until
                    """,
                    (
                        effective_max,
                        lease_seconds,
                        str(job.get("worker_identity") or "") or None,
                        str(job.get("source_commit") or "") or None,
                        str(job.get("input_identity") or "") or None,
                        str(job.get("catchup_state") or "") or None,
                        job_key,
                    ),
                )
                retried = cur.fetchone()
                return {
                    "claimed": True,
                    "duplicate": False,
                    "retry": True,
                    "run_id": str(retried[0]),
                    "status": retried[1],
                    "attempt": retried[2],
                    "lease_until": _serialize(retried[3]),
                }

            return {
                "claimed": False,
                "duplicate": status in {"SUCCEEDED", "MISSED", "SKIPPED", "STALE"},
                "busy": status == "RUNNING",
                "exhausted": status == "FAILED" and int(attempt) >= int(existing_max),
                "run_id": str(run_id),
                "status": status,
                "attempt": int(attempt),
                "max_attempts": int(existing_max),
                "lease_until": _serialize(lease_until),
            }


def scheduler_complete(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    job_key = str(body.get("job_key") or "").strip()
    status = str(body.get("status") or "").strip().upper()
    if not job_key or status not in TERMINAL_STATUSES:
        raise ValueError("invalid_scheduler_completion")
    error_summary = body.get("error_summary")
    details = body.get("details")
    if not isinstance(error_summary, dict):
        error_summary = {}
    if not isinstance(details, dict):
        details = {}

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update iren.scheduler_runs
                set status=%s,
                    completed_at=now(),
                    lease_until=null,
                    output_identity=%s,
                    error_classification=%s,
                    error_summary=%s,
                    retry_state=%s,
                    catchup_state=coalesce(%s,catchup_state),
                    slack_notification_status=%s,
                    details=details || %s,
                    updated_at=now()
                where job_key=%s
                  and status in ('RUNNING','FAILED')
                returning run_id,status,attempt
                """,
                (
                    status,
                    str(body.get("output_identity") or "") or None,
                    str(body.get("error_classification") or "") or None,
                    Jsonb(error_summary),
                    str(body.get("retry_state") or "") or None,
                    str(body.get("catchup_state") or "") or None,
                    str(body.get("slack_notification_status") or "") or None,
                    Jsonb(details),
                    job_key,
                ),
            )
            row = cur.fetchone()
            if row:
                return {
                    "updated": True,
                    "idempotent": False,
                    "run_id": str(row[0]),
                    "status": row[1],
                    "attempt": int(row[2]),
                }

            cur.execute(
                "select status from iren.scheduler_runs where job_key=%s",
                (job_key,),
            )
            existing = cur.fetchone()
            if existing and existing[0] == status:
                return {
                    "updated": False,
                    "idempotent": True,
                    "status": existing[0],
                }
    raise ValueError("scheduler_job_not_completable")


def recent_runs(conn: psycopg.Connection[Any], *, limit: int) -> list[dict[str, Any]]:
    bounded = max(1, min(250, int(limit)))
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                run_id,job_key,workflow_id,workflow_version,scheduler_version,
                scheduled_at,started_at,completed_at,status,trigger_type,
                attempt,max_attempts,worker_identity,source_commit,input_identity,
                output_identity,error_classification,error_summary,retry_state,
                catchup_state,slack_notification_status,details,lease_until
            from iren.scheduler_runs
            order by scheduled_at desc
            limit %s
            """,
            (bounded,),
        )
        columns = [
            desc.name for desc in cur.description
        ]
        return [
            _serialize(dict(zip(columns, row)))
            for row in cur.fetchall()
        ]


def handle_action(database_url: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        if action == "iren_read":
            return iren_read(conn)
        if action == "iren_commit":
            return iren_commit(conn, body)
        if action == "iren_notifications_claim":
            return notification_claim(conn, owner=str(body.get("owner") or ""))
        if action == "iren_notification_complete":
            return notification_complete(
                conn,
                event_key=str(body.get("event_key") or ""),
                owner=str(body.get("owner") or ""),
                delivery_status=str(body.get("delivery_status") or ""),
            )
        if action == "claim":
            job = body.get("job")
            if not isinstance(job, dict):
                raise ValueError("invalid_claim_shape:job")
            return scheduler_claim(conn, job)
        if action == "complete":
            return scheduler_complete(conn, body)
    raise ValueError("invalid_action")
