from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


UTC = timezone.utc
STATUSES = {
    "QUEUED","RUNNING","WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"
}
TERMINAL = {"SUCCEEDED","FAILED","CANCELLED"}
RESEARCH_STAGES = {
    "CRYPTO_LEADLAG_R2_READY",
    "CRYPTO_V7_BATCH_READY",
    "CRYPTO_AUTONOMOUS_DEVELOPMENT",
    "CRYPTO_AUTONOMOUS_VALIDATION",
    "CRYPTO_AUTONOMOUS_HOLDOUT",
    "CRYPTO_AUTONOMOUS_VELUM_REPLAY",
    "CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT",
    "CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION",
    "CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT",
    "CRYPTO_ACTIVITY_SHOCK_V9_VELUM_REPLAY",
    "CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT",
    "CRYPTO_TREND_PULLBACK_V10_VALIDATION",
    "CRYPTO_TREND_PULLBACK_V10_HOLDOUT",
    "CRYPTO_TREND_PULLBACK_V10_VELUM_REPLAY",
    "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT",
    "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_REPLAY",
    "CRYPTO_BTC_MECHANISMS_V12_DEVELOPMENT",
    "CRYPTO_BTC_MECHANISMS_V12_VELUM_REPLAY",
    "CRYPTO_BTC_HYPOTHESES_V13_DEVELOPMENT",
    "CRYPTO_BTC_HYPOTHESES_V13_VELUM_REPLAY",
    "CRYPTO_BTC_HYPOTHESES_V13_VALIDATION",
    "CRYPTO_BTC_HYPOTHESES_V13_HOLDOUT",
    "CRYPTO_BTC_XGB_V14_R1_BROKER_PREFLIGHT",
    "CRYPTO_BTC_QUEUE_IMBALANCE_V14_R2A_PREFLIGHT",
    "CRYPTO_BTC_PASSIVE_SCALPING_V14_R2B_PREFLIGHT",
    "CRYPTO_CROSS_SECTIONAL_MOMENTUM_V14_R2C_PREFLIGHT",
    "CRYPTO_TRIANGULAR_ARBITRAGE_V14_R2D_PREFLIGHT",
    "CRYPTO_BTC_4H_TREND_V14_R2E_PREFLIGHT",
    "CRYPTO_BTC_DAILY_MOMENTUM_V14_R2F_DISCOVERY",
    "CRYPTO_BTC_DAILY_CONSENSUS_V14_R2G_DISCOVERY",
    "CRYPTO_BTC_4H_CONSENSUS_V14_R2H_TRANSFER",
    "CRYPTO_BTC_4H_CONSENSUS_V14_R2H_VELUM_REPLAY",
    "CRYPTO_RESEARCH_DIRECTOR_V1",
    "CRYPTO_COMPILED_DEVELOPMENT",
    "CRYPTO_COMPILED_VALIDATION",
    "CRYPTO_COMPILED_HOLDOUT",
}


def _is_native_research_stage(stage: str) -> bool:
    """Native research code supersedes any unfinished generic code-promotion handoff."""
    return stage in RESEARCH_STAGES and not stage.startswith("CRYPTO_COMPILED_")


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _row(cur: psycopg.Cursor[Any], row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return _serialize(dict(zip([c.name for c in cur.description], row)))


def _rows(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [_serialize(dict(zip(cols, row))) for row in cur.fetchall()]


def _obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status not in STATUSES:
        raise ValueError("invalid_status")
    return status


def _uuid(value: Any, error: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _snapshot(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select problem_id,problem_key,title,statement,domain,status,priority,source,
                   requested_by,linked_iren_job_id,constraints,success_criteria,metadata,
                   created_at,updated_at,started_at,completed_at
            from graen.problems
            order by
              case status when 'RUNNING' then 0 when 'QUEUED' then 1
                          when 'WAITING' then 2 else 3 end,
              priority desc, created_at asc
            limit 200
            """
        )
        problems = _rows(cur)
        cur.execute(
            """
            select run_id,problem_id,worker_id,runtime_version,source_commit,deployment_id,
                   methodology_version,status,input_snapshot,result_summary,model_usage,
                   started_at,completed_at,created_at
            from graen.runs
            order by started_at desc
            limit 100
            """
        )
        runs = _rows(cur)
        cur.execute(
            """
            select artifact_id,problem_id,run_id,artifact_key,artifact_type,
                   methodology_version,source_commit,content_hash,content,created_at
            from graen.artifacts
            order by created_at desc
            limit 100
            """
        )
        artifacts = _rows(cur)
        cur.execute(
            """
            select singleton,worker_id,runtime_version,source_commit,deployment_id,
                   started_at,heartbeat_at,last_claim_at,last_completion_at,
                   active_problem_id,queue_depth,last_error,metadata,updated_at
            from graen.runtime_state where singleton
            """
        )
        runtime_state = _row(cur, cur.fetchone())
    return {
        "problems": problems,
        "runs": runs,
        "artifacts": artifacts,
        "runtime_state": runtime_state,
    }


def _update_runtime(
    cur: psycopg.Cursor[Any],
    *,
    worker_id: str | None = None,
    runtime_version: str | None = None,
    source_commit: str | None = None,
    deployment_id: str | None = None,
    active_problem_id: UUID | None = None,
    set_active: bool = False,
    last_error: str | None = None,
    set_error: bool = False,
    claimed: bool = False,
    completed: bool = False,
) -> None:
    cur.execute("select metadata from graen.runtime_state where singleton for update")
    row = cur.fetchone()
    metadata = _obj(row[0] if row else {})
    cur.execute(
        """
        update graen.runtime_state
        set worker_id=coalesce(%s,worker_id),
            runtime_version=coalesce(%s,runtime_version),
            source_commit=coalesce(%s,source_commit),
            deployment_id=coalesce(%s,deployment_id),
            heartbeat_at=now(),
            last_claim_at=case when %s then now() else last_claim_at end,
            last_completion_at=case when %s then now() else last_completion_at end,
            active_problem_id=case when %s then %s else active_problem_id end,
            queue_depth=(select count(*) from graen.problems where status='QUEUED'),
            last_error=case when %s then %s else last_error end,
            metadata=%s,
            updated_at=now()
        where singleton
        """,
        (
            worker_id, runtime_version, source_commit, deployment_id,
            claimed, completed, set_active, active_problem_id,
            set_error, last_error, Jsonb(metadata),
        ),
    )


def _create_problem(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    title = str(body.get("title") or "").strip()[:240]
    statement = str(body.get("statement") or "").strip()[:12000]
    domain = str(body.get("domain") or "GENERAL_RESEARCH").strip()[:80]
    priority = max(0, min(100, int(body.get("priority") or 50)))
    source = str(body.get("source") or "IREN").strip()[:80]
    requested_by = str(body.get("requested_by") or "").strip()[:160] or None
    linked_raw = str(body.get("linked_iren_job_id") or "").strip()
    linked = _uuid(linked_raw, "invalid_problem") if linked_raw else None
    if not title or not statement:
        raise ValueError("invalid_problem")
    key = hashlib.sha256(
        "\n".join([title, statement, domain, linked_raw]).encode()
    ).hexdigest()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into graen.problems (
                    problem_key,title,statement,domain,status,priority,source,requested_by,
                    linked_iren_job_id,constraints,success_criteria,metadata
                )
                values (%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s)
                on conflict (problem_key) do update set updated_at=now()
                returning *
                """,
                (
                    key,title,statement,domain,priority,source,requested_by,linked,
                    Jsonb(_obj(body.get("constraints"))),
                    Jsonb(_obj(body.get("success_criteria"))),
                    Jsonb(_obj(body.get("metadata"))),
                ),
            )
            problem = _row(cur, cur.fetchone())
    return {"problem": problem}


def _claim_problem(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    worker = str(body.get("worker_id") or "").strip()[:160]
    runtime = str(body.get("runtime_version") or "").strip()[:160]
    if not worker or not runtime:
        raise ValueError("invalid_worker")
    source = str(body.get("source_commit") or "").strip()[:160] or None
    deployment = str(body.get("deployment_id") or "").strip()[:160] or None
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select problem_id from graen.problems
                where status='QUEUED'
                order by priority desc, created_at asc
                limit 1 for update skip locked
                """
            )
            candidate = cur.fetchone()
            problem = None
            if candidate:
                cur.execute(
                    """
                    update graen.problems
                    set status='RUNNING',started_at=coalesce(started_at,now()),updated_at=now()
                    where problem_id=%s returning *
                    """,
                    (candidate[0],),
                )
                problem = _row(cur, cur.fetchone())
            _update_runtime(
                cur, worker_id=worker, runtime_version=runtime,
                source_commit=source, deployment_id=deployment,
                active_problem_id=_uuid(problem["problem_id"], "invalid_problem") if problem else None,
                set_active=True, last_error=None, set_error=True,
                claimed=problem is not None,
            )
            if not problem:
                return {"problem": None}
            cur.execute(
                """
                insert into graen.runs (
                    problem_id,worker_id,runtime_version,source_commit,deployment_id,
                    status,input_snapshot
                ) values (%s,%s,%s,%s,%s,'RUNNING',%s)
                returning *
                """,
                (
                    problem["problem_id"],worker,runtime,source,deployment,
                    Jsonb({
                        "problem_key":problem["problem_key"],
                        "domain":problem["domain"],
                    }),
                ),
            )
            run = _row(cur, cur.fetchone())
    return {"problem": problem, "run": run}


def _heartbeat(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    worker = str(body.get("worker_id") or "").strip()[:160]
    runtime = str(body.get("runtime_version") or "").strip()[:160]
    if not worker or not runtime:
        raise ValueError("invalid_worker")
    active_raw = str(body.get("active_problem_id") or "").strip()
    active = _uuid(active_raw, "invalid_worker") if active_raw else None
    with conn.transaction():
        with conn.cursor() as cur:
            _update_runtime(
                cur,
                worker_id=worker,
                runtime_version=runtime,
                source_commit=str(body.get("source_commit") or "").strip()[:160] or None,
                deployment_id=str(body.get("deployment_id") or "").strip()[:160] or None,
                active_problem_id=active,
                set_active=True,
                last_error=str(body.get("last_error") or "").strip()[:1000] or None,
                set_error=True,
            )
    return {}


def _queue_stage(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id = _uuid(body.get("problem_id"), "invalid_research_stage")
    stage = str(body.get("stage") or "").strip()[:120]
    if not stage:
        raise ValueError("invalid_research_stage")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) from graen.runs where problem_id=%s and status='RUNNING'",
                (problem_id,),
            )
            if int(cur.fetchone()[0]) > 0:
                raise ValueError("research_problem_run_active")
            cur.execute(
                "select metadata from graen.problems where problem_id=%s for update",
                (problem_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("graen_problem_not_found")
            metadata = _obj(row[0])
            metadata["research_stage"] = stage
            metadata.update(_obj(body.get("metadata")))
            if _is_native_research_stage(stage):
                metadata.pop("code_promotion", None)
            cur.execute(
                """
                update graen.problems
                set status='WAITING',completed_at=null,metadata=%s,updated_at=now()
                where problem_id=%s returning *
                """,
                (Jsonb(metadata),problem_id),
            )
            problem = _row(cur, cur.fetchone())
    return {"problem": problem}


def _claim_research(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    worker = str(body.get("worker_id") or "").strip()[:160]
    runtime = str(body.get("runtime_version") or "").strip()[:160]
    methodology = str(body.get("methodology_version") or "").strip()[:160]
    domain = str(body.get("domain") or "").strip()[:80]
    if not worker or not runtime or not methodology or not domain:
        raise ValueError("invalid_research_worker")
    source = str(body.get("source_commit") or "").strip()[:160] or None
    deployment = str(body.get("deployment_id") or "").strip()[:160] or None
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select p.problem_id
                from graen.problems p
                where p.status='WAITING'
                  and p.domain=%s
                  and (
                    p.metadata->'code_promotion' is null
                    or p.metadata->'code_promotion'->>'phase'='COMPLETE'
                  )
                  and (
                    (
                      select r.result_summary->>'state'
                      from graen.runs r
                      where r.problem_id=p.problem_id
                      order by r.started_at desc limit 1
                    )='READY_FOR_RESEARCH_EXECUTOR'
                    or p.metadata->>'research_stage'=any(%s)
                  )
                order by p.priority desc,p.created_at asc
                limit 1 for update skip locked
                """,
                (domain,list(RESEARCH_STAGES)),
            )
            candidate = cur.fetchone()
            if not candidate:
                _executor_heartbeat_locked(
                    cur, worker, runtime, methodology, source, deployment, None, None
                )
                return {"problem": None}
            cur.execute(
                """
                update graen.problems set status='RUNNING',updated_at=now()
                where problem_id=%s returning *
                """,
                (candidate[0],),
            )
            problem = _row(cur, cur.fetchone())
            cur.execute(
                """
                insert into graen.runs (
                    problem_id,worker_id,runtime_version,source_commit,deployment_id,
                    methodology_version,status,input_snapshot,model_usage
                ) values (%s,%s,%s,%s,%s,%s,'RUNNING',%s,%s)
                returning *
                """,
                (
                    candidate[0],worker,runtime,source,deployment,methodology,
                    Jsonb({
                        "problem_key":problem["problem_key"],
                        "domain":problem["domain"],
                        "constraints":problem["constraints"],
                        "success_criteria":problem["success_criteria"],
                    }),
                    Jsonb({"invoked":False}),
                ),
            )
            run = _row(cur, cur.fetchone())
            _executor_heartbeat_locked(
                cur,worker,runtime,methodology,source,deployment,candidate[0],None
            )
    return {"problem":problem,"run":run}


def _executor_heartbeat_locked(
    cur: psycopg.Cursor[Any],
    worker: str,
    runtime: str | None,
    methodology: str | None,
    source: str | None,
    deployment: str | None,
    active: UUID | None,
    error: str | None,
) -> None:
    cur.execute("select metadata from graen.runtime_state where singleton for update")
    row=cur.fetchone()
    metadata=_obj(row[0] if row else {})
    metadata["research_executor"]={
        "worker_id":worker,
        "runtime_version":runtime,
        "methodology_version":methodology,
        "source_commit":source,
        "deployment_id":deployment,
        "heartbeat_at":datetime.now(UTC).isoformat(),
        "active_problem_id":str(active) if active else None,
        "last_error":error,
    }
    cur.execute(
        "update graen.runtime_state set metadata=%s,updated_at=now() where singleton",
        (Jsonb(metadata),),
    )


def _executor_heartbeat(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    worker=str(body.get("worker_id") or "").strip()[:160]
    runtime=str(body.get("runtime_version") or "").strip()[:160]
    methodology=str(body.get("methodology_version") or "").strip()[:160]
    if not worker or not runtime or not methodology:
        raise ValueError("invalid_executor_heartbeat")
    active_raw=str(body.get("active_problem_id") or "").strip()
    active=_uuid(active_raw,"invalid_executor_heartbeat") if active_raw else None
    with conn.transaction():
        with conn.cursor() as cur:
            _executor_heartbeat_locked(
                cur,worker,runtime,methodology,
                str(body.get("source_commit") or "").strip()[:160] or None,
                str(body.get("deployment_id") or "").strip()[:160] or None,
                active,
                str(body.get("last_error") or "").strip()[:1000] or None,
            )
    return {}


def _record_artifact(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_artifact")
    run_raw=str(body.get("run_id") or "").strip()
    run_id=_uuid(run_raw,"invalid_artifact") if run_raw else None
    artifact_type=str(body.get("artifact_type") or "").strip()[:100]
    if not artifact_type:
        raise ValueError("invalid_artifact")
    content=_obj(body.get("content"))
    content_hash=_hash(content)
    artifact_key=":".join([str(problem_id),str(run_id) if run_id else "none",artifact_type,content_hash])
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into graen.artifacts (
                    problem_id,run_id,artifact_key,artifact_type,methodology_version,
                    source_commit,content_hash,content
                ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (artifact_key) do nothing
                returning *
                """,
                (
                    problem_id,run_id,artifact_key,artifact_type,
                    str(body.get("methodology_version") or "").strip()[:160] or None,
                    str(body.get("source_commit") or "").strip()[:160] or None,
                    content_hash,Jsonb(content),
                ),
            )
            row=cur.fetchone()
            artifact=_row(cur,row) if row else None
    return {"inserted":artifact is not None,"artifact":artifact,"content_hash":content_hash}


def _complete_problem(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_completion")
    run_id=_uuid(body.get("run_id"),"invalid_completion")
    status=_status(body.get("status"))
    if status not in {"WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"}:
        raise ValueError("invalid_completion")
    summary=_obj(body.get("result_summary"))
    usage=_obj(body.get("model_usage"))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update graen.runs set status=%s,result_summary=%s,model_usage=%s,
                    completed_at=case when %s then now() else completed_at end
                where run_id=%s and problem_id=%s
                """,
                (status,Jsonb(summary),Jsonb(usage),status in TERMINAL,run_id,problem_id),
            )
            cur.execute(
                """
                update graen.problems set status=%s,updated_at=now(),
                    completed_at=case when %s then now() else completed_at end
                where problem_id=%s
                """,
                (status,status in TERMINAL,problem_id),
            )
            _update_runtime(
                cur,active_problem_id=None,set_active=True,
                last_error=(str(summary.get("error") or "research_failed")[:1000]
                            if status=="FAILED" else None),
                set_error=True,completed=True,
            )
    return {}


def _block_research(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_research_claim_block")
    run_id=_uuid(body.get("run_id"),"invalid_research_claim_block")
    worker=str(body.get("worker_id") or "").strip()[:160]
    if not worker:
        raise ValueError("invalid_research_claim_block")
    error=str(body.get("error") or "research_claim_failed").strip()[:1000]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select status from graen.runs
                where run_id=%s and problem_id=%s for update
                """,
                (run_id,problem_id),
            )
            row=cur.fetchone()
            if not row:
                raise ValueError("graen_run_not_found")
            if row[0]=="RUNNING":
                cur.execute(
                    """
                    update graen.runs
                    set status='BLOCKED',result_summary=%s,completed_at=now()
                    where run_id=%s and problem_id=%s
                    """,
                    (
                        Jsonb({
                            "state":"RESEARCH_EXECUTION_BLOCKED",
                            "decision":"REPAIR_REQUIRED",
                            "next_action":"RESUME_FROZEN_STAGE_AFTER_REPAIR",
                            "error":error,
                            "execution_authority":False,
                        }),run_id,problem_id,
                    ),
                )
            cur.execute(
                """
                update graen.problems set status='BLOCKED',updated_at=now()
                where problem_id=%s and status='RUNNING'
                """,
                (problem_id,),
            )
            _executor_heartbeat_locked(cur,worker,None,None,None,None,None,error)
    return {"status":"BLOCKED"}


def _complete_research(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_research_completion")
    run_id=_uuid(body.get("run_id"),"invalid_research_completion")
    worker=str(body.get("worker_id") or "").strip()[:160]
    status=_status(body.get("status"))
    if not worker or status not in {"WAITING","BLOCKED","SUCCEEDED","FAILED","CANCELLED"}:
        raise ValueError("invalid_research_completion")
    summary=_obj(body.get("result_summary"))
    usage=_obj(body.get("model_usage"))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update graen.runs
                set status=%s,result_summary=%s,model_usage=%s,completed_at=now()
                where run_id=%s and problem_id=%s
                """,
                (status,Jsonb(summary),Jsonb(usage),run_id,problem_id),
            )
            cur.execute(
                """
                select linked_iren_job_id,metadata from graen.problems
                where problem_id=%s for update
                """,
                (problem_id,),
            )
            row=cur.fetchone()
            if not row:
                raise ValueError("graen_problem_not_found")
            linked_job=row[0]
            metadata=_obj(row[1])
            metadata.pop("research_stage",None)
            cur.execute(
                """
                update graen.problems
                set status=%s,updated_at=now(),
                    completed_at=case when %s then now() else null end,
                    metadata=%s
                where problem_id=%s
                """,
                (status,status in TERMINAL,Jsonb(metadata),problem_id),
            )
            if linked_job:
                cur.execute(
                    "select protected_action,output from iren.jobs where job_id=%s for update",
                    (linked_job,),
                )
                job=cur.fetchone()
                if job:
                    protected=bool(job[0])
                    output=_obj(job[1])
                    output.update({
                        "graen_problem_id":str(problem_id),
                        "graen_run_id":str(run_id),
                        "graen_result":summary,
                        "graen_synced_at":datetime.now(UTC).isoformat(),
                        "graen_protected_completion_blocked":status=="SUCCEEDED" and protected,
                    })
                    job_status=("WAITING" if status=="SUCCEEDED" and protected else status)
                    failure=(status in {"FAILED","CANCELLED"})
                    error={
                        "source":"GRAEN",
                        "graen_problem_id":str(problem_id),
                        "graen_run_id":str(run_id),
                        "result":summary,
                    } if failure else {}
                    cur.execute(
                        """
                        update iren.jobs
                        set status=%s,output=%s,error=%s,
                            completed_at=case
                              when %s and not protected_action then now()
                              when %s then now()
                              else completed_at end,
                            updated_at=now()
                        where job_id=%s
                        """,
                        (
                            job_status,Jsonb(output),Jsonb(error),
                            status=="SUCCEEDED",failure,linked_job,
                        ),
                    )
            _executor_heartbeat_locked(
                cur,worker,None,None,None,None,None,
                (str(summary.get("error") or "research_failed")[:1000]
                 if status=="FAILED" else None),
            )
            cur.execute(
                """
                select metadata from graen.runtime_state where singleton for update
                """
            )
            r=cur.fetchone()
            rmeta=_obj(r[0] if r else {})
            ex=_obj(rmeta.get("research_executor"))
            ex["last_completion_at"]=datetime.now(UTC).isoformat()
            ex["last_status"]=status
            rmeta["research_executor"]=ex
            cur.execute(
                "update graen.runtime_state set metadata=%s,updated_at=now() where singleton",
                (Jsonb(rmeta),),
            )
    return {}


def _with_forward_shadow(
    value: dict[str, Any],
    shadow: dict[str, Any],
) -> dict[str, Any]:
    """Store parallel forward shadows without letting R2G replace canonical R2F."""
    out = dict(value)
    shadows = _obj(out.get("forward_shadows"))
    primary = _obj(out.get("forward_shadow"))
    primary_candidate = str(primary.get("candidate_id") or "")
    if primary_candidate:
        shadows.setdefault(primary_candidate, primary)

    candidate = str(shadow.get("candidate_id") or "")
    if candidate:
        shadows[candidate] = dict(shadow)
    out["forward_shadows"] = shadows

    if (
        str(shadow.get("candidate_methodology") or "")
        == "graen-btc-consensus-trend-v14-r2g"
    ):
        out["forward_shadow_comparison"] = dict(shadow)
    else:
        out["forward_shadow"] = dict(shadow)
    return out


def _shadow_checkpoint(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_shadow_checkpoint")
    activation=str(body.get("activation_id") or "").strip()[:160]
    candidate=str(body.get("candidate_id") or "").strip()[:160]
    status=str(body.get("status") or "").strip().upper()
    evidence=_obj(body.get("evidence"))
    methodology=str(evidence.get("candidate_methodology") or "").strip()
    evidence_phase=str(evidence.get("evidence_phase") or "FORWARD_SHADOW").strip().upper()
    if not activation or not candidate or status not in {
        "COLLECTING","READY_FOR_HUMAN_REVIEW","SHADOW_REJECTED"
    }:
        raise ValueError("invalid_shadow_checkpoint")
    if evidence_phase not in {"FORWARD_SHADOW","VALIDATION","HOLDOUT"}:
        raise ValueError("invalid_shadow_checkpoint_phase")
    shadow={
        "activation_id":activation,
        "candidate_id":candidate,
        "candidate_methodology":methodology or None,
        "evidence_phase":evidence_phase,
        "status":status,
        "evidence":evidence,
        "synced_at":datetime.now(UTC).isoformat(),
        "execution_authority":False,
        "broker_orders_possible":False,
        "promotion_authorized":False,
    }

    def record_v13_artifact(cur: psycopg.Cursor[Any], artifact_type: str, content: dict[str, Any]) -> str | None:
        content_hash=_hash(content)
        artifact_key=":".join([str(problem_id),"none",artifact_type,content_hash])
        cur.execute(
            """
            insert into graen.artifacts (
                problem_id,run_id,artifact_key,artifact_type,methodology_version,
                source_commit,content_hash,content
            ) values (%s,null,%s,%s,%s,null,%s,%s)
            on conflict (artifact_key) do nothing
            returning artifact_id
            """,
            (
                problem_id,artifact_key,artifact_type,
                "graen-btc-hypothesis-tournament-v13",
                content_hash,Jsonb(content),
            ),
        )
        inserted=cur.fetchone()
        if inserted:
            return str(inserted[0])
        cur.execute(
            "select artifact_id from graen.artifacts where artifact_key=%s",
            (artifact_key,),
        )
        row=cur.fetchone()
        return str(row[0]) if row else None

    protected_action_required=status=="READY_FOR_HUMAN_REVIEW"
    transition=None
    artifact_id=None
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select linked_iren_job_id,metadata from graen.problems where problem_id=%s for update",
                (problem_id,),
            )
            row=cur.fetchone()
            if not row:
                raise ValueError("graen_problem_not_found")
            linked=row[0]
            metadata=_obj(row[1])
            metadata.pop("research_stage",None)
            metadata = _with_forward_shadow(metadata, shadow)

            if methodology=="graen-btc-hypothesis-tournament-v13":
                frozen=_obj(metadata.get("v13_candidate_spec"))
                if frozen and str(frozen.get("candidate_id") or "")!=candidate:
                    raise ValueError("v13_shadow_candidate_does_not_match_frozen_spec")
                if evidence_phase=="VALIDATION" and status in {
                    "READY_FOR_HUMAN_REVIEW","SHADOW_REJECTED"
                }:
                    validation_passed=status=="READY_FOR_HUMAN_REVIEW"
                    if validation_passed and metadata.get("v13_velum_passed") is not True:
                        raise ValueError("v13_validation_without_velum_pass")
                    metadata["v13_validation_passed"]=validation_passed
                    metadata["v13_validation_activation_id"]=activation
                    content={
                        "campaign_id":"btc-hypothesis-tournament-v13",
                        "candidate_id":candidate,
                        "candidate_spec":frozen,
                        "stage":"VALIDATION",
                        "passed":validation_passed,
                        "checkpoint":evidence,
                        "execution_authority":False,
                        "broker_orders_possible":False,
                        "promotion_authorized":False,
                    }
                    artifact_id=record_v13_artifact(
                        cur,"CRYPTO_BTC_V13_VALIDATION_RESULT",content
                    )
                    metadata["v13_validation_artifact_id"]=artifact_id
                    if validation_passed:
                        metadata["research_stage"]="CRYPTO_BTC_HYPOTHESES_V13_HOLDOUT"
                        transition="QUEUE_HOLDOUT"
                    else:
                        transition="VALIDATION_REJECTED"
                elif evidence_phase=="HOLDOUT" and status in {
                    "READY_FOR_HUMAN_REVIEW","SHADOW_REJECTED"
                }:
                    if (
                        metadata.get("v13_velum_passed") is not True
                        or metadata.get("v13_validation_passed") is not True
                    ):
                        raise ValueError("v13_holdout_opened_before_predecessor_gates")
                    holdout_passed=status=="READY_FOR_HUMAN_REVIEW"
                    metadata["v13_holdout_passed"]=holdout_passed
                    metadata["v13_holdout_activation_id"]=activation
                    metadata["v13_promotion_ready"]=holdout_passed
                    content={
                        "campaign_id":"btc-hypothesis-tournament-v13",
                        "candidate_id":candidate,
                        "candidate_spec":frozen,
                        "stage":"HOLDOUT",
                        "passed":holdout_passed,
                        "checkpoint":evidence,
                        "statistical_promotion_ready":holdout_passed,
                        "live_execution_authorized":False,
                        "execution_authority":False,
                        "broker_orders_possible":False,
                        "promotion_authorized":False,
                    }
                    artifact_type=(
                        "CRYPTO_BTC_V13_PROMOTION_READY"
                        if holdout_passed
                        else "CRYPTO_BTC_V13_HOLDOUT_REJECTED"
                    )
                    artifact_id=record_v13_artifact(cur,artifact_type,content)
                    metadata["v13_holdout_artifact_id"]=artifact_id
                    transition=(
                        "PROMOTION_READY_RESEARCH_ONLY"
                        if holdout_passed
                        else "HOLDOUT_REJECTED"
                    )

            cur.execute(
                """
                update graen.problems
                set status='WAITING',completed_at=null,metadata=%s,updated_at=now()
                where problem_id=%s
                """,
                (Jsonb(metadata),problem_id),
            )
            if linked:
                cur.execute(
                    "select output from iren.jobs where job_id=%s for update",
                    (linked,),
                )
                j=cur.fetchone()
                if j:
                    output=_with_forward_shadow(_obj(j[0]), shadow)
                    output.update({
                        "current_stage":(
                            "V13_HOLDOUT_QUEUED"
                            if transition=="QUEUE_HOLDOUT"
                            else "V13_PROMOTION_READY_RESEARCH_ONLY"
                            if transition=="PROMOTION_READY_RESEARCH_ONLY"
                            else "FORWARD_SHADOW_READY_FOR_HUMAN_REVIEW"
                            if status=="READY_FOR_HUMAN_REVIEW"
                            else "FORWARD_SHADOW_REJECTED"
                            if status=="SHADOW_REJECTED"
                            else "FORWARD_SHADOW_RUNNING"
                        ),
                        "v13_transition":transition,
                        "v13_artifact_id":artifact_id,
                        "execution_authority":False,
                        "broker_orders_possible":False,
                    })
                    cur.execute(
                        """
                        update iren.jobs
                        set status='WAITING',
                            requires_human=%s,
                            completed_at=null,
                            output=%s,
                            updated_at=now()
                        where job_id=%s
                        """,
                        (
                            bool(
                                transition=="PROMOTION_READY_RESEARCH_ONLY"
                                or (
                                    status=="READY_FOR_HUMAN_REVIEW"
                                    and transition!="QUEUE_HOLDOUT"
                                )
                            ),
                            Jsonb(output),linked,
                        ),
                    )
    return {
        "status":status,
        "evidence_phase":evidence_phase,
        "transition":transition,
        "artifact_id":artifact_id,
        "protected_action_required":bool(
            transition=="PROMOTION_READY_RESEARCH_ONLY"
            or (protected_action_required and transition!="QUEUE_HOLDOUT")
        ),
        "execution_authority":False,
        "broker_orders_possible":False,
        "promotion_authorized":False,
    }


def _compiled_evidence(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_compiled_stage")
    spec_hash=str(body.get("spec_hash") or "")
    stage=str(body.get("stage") or "")
    epoch=str(body.get("epoch") or "")
    if len(spec_hash)!=64 or stage not in {"development","validation","holdout"}:
        raise ValueError("invalid_compiled_stage")
    predecessor={"validation":"development","holdout":"validation"}.get(stage)
    wanted=[stage]+([predecessor] if predecessor else [])
    with conn.cursor() as cur:
        cur.execute(
            """
            select artifact_id,content from graen.artifacts
            where problem_id=%s and artifact_type='COMPILED_STAGE_RESULT'
              and content->>'spec_hash'=%s
              and content->>'epoch'=%s
              and content->>'stage'=any(%s)
            order by created_at asc
            """,
            (problem_id,spec_hash,epoch,wanted),
        )
        rows=cur.fetchall()
    current=None
    prior=None
    for artifact_id,content in rows:
        value={**_obj(content),"artifact_id":str(artifact_id)}
        if value.get("stage")==stage:
            current=value
        elif value.get("stage")==predecessor:
            prior=value
    return {"current":current,"predecessor":prior}


def _promotion_claim(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_promotion_identity")
    owner=_uuid(body.get("owner"),"invalid_promotion_identity")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select status,metadata from graen.problems where problem_id=%s for update",
                (problem_id,),
            )
            row=cur.fetchone()
            if not row or row[0] not in {"WAITING","BLOCKED"}:
                return {"claimed":False}
            metadata=_obj(row[1])
            state=_obj(metadata.get("code_promotion"))
            lease=_obj(metadata.get("code_promotion_lease"))
            if state.get("phase")=="COMPLETE":
                return {"claimed":False}
            until=lease.get("until")
            if until:
                try:
                    if datetime.fromisoformat(str(until).replace("Z","+00:00"))>datetime.now(UTC):
                        return {"claimed":False}
                except ValueError:
                    pass
            revision=int(metadata.get("code_promotion_revision") or 0)
            prespec=_obj(state.get("prespec") or metadata.get("research_implementation_spec"))
            exposure={}
            exposure_id=str(prespec.get("exposure_artifact_id") or "")
            try:
                exposure_uuid=UUID(exposure_id)
            except ValueError:
                exposure_uuid=None
            if exposure_uuid:
                cur.execute(
                    """
                    select artifact_id,content from graen.artifacts
                    where artifact_id=%s and problem_id=%s
                      and artifact_type='RESEARCH_CORPUS_EXPOSURE_LEDGER'
                    """,
                    (exposure_uuid,problem_id),
                )
                a=cur.fetchone()
                if a:
                    exposure={**_obj(a[1]),"artifact_id":str(a[0])}
            cur.execute("select metadata from graen.runtime_state where singleton")
            r=cur.fetchone()
            executor=_obj(_obj(r[0] if r else {}).get("research_executor"))
            metadata["code_promotion_lease"]={
                "owner":str(owner),
                "until":(datetime.now(UTC)+timedelta(minutes=5)).isoformat(),
            }
            cur.execute(
                "update graen.problems set metadata=%s,updated_at=now() where problem_id=%s",
                (Jsonb(metadata),problem_id),
            )
    return {
        "claimed":True,
        "revision":revision,
        "state":state,
        "prespec":prespec,
        "exposure":exposure,
        "prespec_artifact_id":metadata.get("code_prespec_artifact_id"),
        "executor_heartbeat":executor,
    }


def _promotion_save(conn: psycopg.Connection[Any], body: dict[str, Any]) -> dict[str, Any]:
    problem_id=_uuid(body.get("problem_id"),"invalid_promotion_state")
    owner=_uuid(body.get("owner"),"invalid_promotion_state")
    try:
        expected=int(body.get("expected_revision"))
    except (TypeError,ValueError) as exc:
        raise ValueError("invalid_promotion_state") from exc
    state=_obj(body.get("state"))
    if len(json.dumps(state,default=str))>100000:
        raise ValueError("invalid_promotion_state")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select status,metadata from graen.problems where problem_id=%s for update",
                (problem_id,),
            )
            row=cur.fetchone()
            if not row or row[0] not in {"WAITING","BLOCKED"}:
                raise ValueError("promotion_problem_not_idle")
            metadata=_obj(row[1])
            previous=_obj(metadata.get("code_promotion"))
            lease=_obj(metadata.get("code_promotion_lease"))
            try:
                lease_until=datetime.fromisoformat(str(lease.get("until") or "").replace("Z","+00:00"))
            except ValueError as exc:
                raise ValueError("promotion_lease_or_revision_conflict") from exc
            if (
                str(lease.get("owner"))!=str(owner)
                or int(metadata.get("code_promotion_revision") or 0)!=expected
                or lease_until<=datetime.now(UTC)
            ):
                raise ValueError("promotion_lease_or_revision_conflict")
            if previous.get("spec_hash") and (
                previous.get("spec_hash")!=state.get("spec_hash")
                or previous.get("prespec")!=state.get("prespec")
            ):
                raise ValueError("immutable_prespec_changed")

            prespec_id=metadata.get("code_prespec_artifact_id")
            if state.get("prespec") and not prespec_id:
                spec_hash=str(state.get("spec_hash") or "")
                if state.get("phase")!="BRANCH" or len(spec_hash)!=64:
                    raise ValueError("prespec_must_be_frozen_before_branch")
                content={
                    "specification":state["prespec"],
                    "specification_hash":spec_hash,
                }
                content_hash=_hash(content)
                key=f"{problem_id}:research-code-prespec:{spec_hash}"
                cur.execute(
                    """
                    insert into graen.artifacts (
                        problem_id,artifact_key,artifact_type,methodology_version,
                        content_hash,content
                    ) values (%s,%s,'RESEARCH_CODE_FROZEN_PRESPEC',
                              'graen.research-code-promotion.v1',%s,%s)
                    on conflict (artifact_key) do update set artifact_key=excluded.artifact_key
                    returning artifact_id
                    """,
                    (problem_id,key,content_hash,Jsonb(content)),
                )
                prespec_id=str(cur.fetchone()[0])

            if state.get("phase")=="COMPLETE":
                if (
                    previous.get("phase")!="RESUME"
                    or state.get("resume_stage")!="CRYPTO_COMPILED_DEVELOPMENT"
                    or not state.get("merge_sha")
                    or not state.get("deployment_id")
                    or not state.get("executor_heartbeat_at")
                    or not isinstance(state.get("ci"),list)
                    or not state.get("ci")
                    or not prespec_id
                ):
                    raise ValueError("verified_deployment_required_before_resume")
                cur.execute(
                    "select 1 from graen.runs where problem_id=%s and status='RUNNING' limit 1",
                    (problem_id,),
                )
                if cur.fetchone():
                    raise ValueError("active_research_run_prevents_resume")

            if previous==state:
                metadata["code_promotion_lease"]={}
                cur.execute(
                    "update graen.problems set metadata=%s,updated_at=now() where problem_id=%s",
                    (Jsonb(metadata),problem_id),
                )
                return {"revision":expected,"prespec_artifact_id":prespec_id}

            revision=expected+1
            metadata.update({
                "code_promotion":state,
                "code_promotion_revision":revision,
                "code_prespec_artifact_id":prespec_id,
                "code_promotion_lease":{},
            })
            if state.get("phase")=="COMPLETE":
                metadata["research_stage"]="CRYPTO_COMPILED_DEVELOPMENT"
                metadata["compiled_specification_hash"]=state.get("spec_hash")
            event={"revision":revision,**state,"prespec_artifact_id":prespec_id}
            event_hash=_hash(event)
            cur.execute(
                """
                insert into graen.artifacts (
                    problem_id,artifact_key,artifact_type,methodology_version,
                    content_hash,content
                ) values (%s,%s,'RESEARCH_CODE_PROMOTION_EVENT',
                          'graen.research-code-promotion.v1',%s,%s)
                """,
                (
                    problem_id,f"{problem_id}:research-code-event:{revision}",
                    event_hash,Jsonb(event),
                ),
            )
            cur.execute(
                "update graen.problems set metadata=%s,updated_at=now() where problem_id=%s",
                (Jsonb(metadata),problem_id),
            )
    return {"revision":revision,"prespec_artifact_id":prespec_id}


CRYPTO_EXECUTION_CONTRACT_KEYS = (
    "strategy_family",
    "strategy_version_id",
    "model_version",
    "calibration_version",
    "regime_version",
    "execution_adapter_version",
)


def _crypto_execution_contract(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    return {
        key: str(source.get(key) or "").strip()
        for key in CRYPTO_EXECUTION_CONTRACT_KEYS
    }


def crypto_promotion_matches_contract(
    content: Mapping[str, Any],
    requested_contract: Mapping[str, Any],
) -> bool:
    requested = _crypto_execution_contract(requested_contract)
    if any(not requested[key] for key in CRYPTO_EXECUTION_CONTRACT_KEYS):
        return False
    artifact_contract = _crypto_execution_contract(
        content.get("execution_contract")
        if isinstance(content, Mapping)
        else None
    )
    if artifact_contract != requested:
        return False
    return bool(
        content.get("statistical_promotion_ready") is True
        or content.get("promotion_ready") is True
        or content.get("passed") is True
        and str(content.get("stage") or "").upper() == "HOLDOUT"
    )


def _crypto_promotion_status(
    conn: psycopg.Connection[Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    requested = _crypto_execution_contract(_obj(body.get("execution_contract")))
    if any(not requested[key] for key in CRYPTO_EXECUTION_CONTRACT_KEYS):
        return {
            "status": "GATED",
            "promotion_ready": False,
            "reason": "incomplete_execution_contract",
            "execution_contract": requested,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    with conn.cursor() as cur:
        cur.execute(
            """
            select artifact_id,problem_id,run_id,artifact_type,methodology_version,
                   content,created_at
            from graen.artifacts
            where artifact_type like 'CRYPTO_%PROMOTION_READY%'
            order by created_at desc
            limit 100
            """
        )
        rows = cur.fetchall()

    for (
        artifact_id,
        problem_id,
        run_id,
        artifact_type,
        methodology_version,
        content,
        created_at,
    ) in rows:
        payload = _obj(content)
        if not crypto_promotion_matches_contract(payload, requested):
            continue
        return {
            "status": "PROMOTION_READY",
            "promotion_ready": True,
            "reason": "matching_protected_research_artifact",
            "execution_contract": requested,
            "artifact": {
                "artifact_id": str(artifact_id),
                "problem_id": str(problem_id),
                "run_id": str(run_id) if run_id else None,
                "artifact_type": artifact_type,
                "methodology_version": methodology_version,
                "created_at": created_at.isoformat() if created_at else None,
            },
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    return {
        "status": "GATED",
        "promotion_ready": False,
        "reason": "no_matching_promotion_artifact",
        "execution_contract": requested,
        "execution_authority": False,
        "live_execution_authorized": False,
    }


def handle_graen_action(
    database_url: str,
    action: str | None,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
) -> dict[str, Any]:
    body=body or {}
    with psycopg.connect(database_url,connect_timeout=5) as conn:
        if method=="GET":
            return _snapshot(conn)
        handlers={
            "create_problem":_create_problem,
            "claim_problem":_claim_problem,
            "heartbeat":_heartbeat,
            "queue_research_stage":_queue_stage,
            "claim_research_problem":_claim_research,
            "executor_heartbeat":_executor_heartbeat,
            "record_artifact":_record_artifact,
            "complete_problem":_complete_problem,
            "block_research_claim":_block_research,
            "complete_research_problem":_complete_research,
            "shadow_checkpoint":_shadow_checkpoint,
            "compiled_stage_evidence":_compiled_evidence,
            "research_promotion_claim":_promotion_claim,
            "research_promotion_save":_promotion_save,
            "crypto_promotion_status":_crypto_promotion_status,
        }
        handler=handlers.get(str(action or ""))
        if not handler:
            raise ValueError("invalid_action")
        return handler(conn,body)
