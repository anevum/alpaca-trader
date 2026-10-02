"""Canonical handoffs live in iren.jobs; transactions fence preparation and completion."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.iren.codex_handoff import (package_for, digest, protected, verification, TERMINAL, fresh)
from app.iren.work import dependencies_complete, criteria_satisfied


def _job(cur, job_id):
    from foundation.iren_work_gateway import _rows
    cur.execute("select *, output as result from iren.jobs where job_id=%s and job_type='CODEX_HANDOFF' for update", (UUID(job_id),))
    rows = _rows(cur)
    if not rows:
        raise ValueError("handoff_not_found")
    return rows[0]


def _state(cur):
    cur.execute("select state from iren.system_state where system_key='IREN'")
    row = cur.fetchone()
    return row[0] if row else {}


def _event(cur, job_id, kind, event):
    cur.execute("insert into iren.job_events(job_id,event_type,event) values(%s,%s,%s)",
                (UUID(str(job_id)), kind, Jsonb(event)))


def prepare(conn, body):
    from foundation.iren_work_gateway import snapshot, _rows
    with conn.transaction():
        with conn.cursor() as cur:
            # One preparation/verification authority per objective across rolling replicas.
            cur.execute("select pg_advisory_xact_lock(hashtext(%s))", ("iren-codex-prepare",))
            data = snapshot(conn)
            key = body.get("objective_key")
            objective = next((o for o in data["objectives"] if o["objective_key"] == key), None)
            if not objective or objective["status"] not in {"READY", "ACTIVE"}:
                raise ValueError("objective_not_executable")
            if not dependencies_complete(objective, data["objectives"]):
                raise ValueError("objective_dependencies_incomplete")
            state = _state(cur)
            if not fresh(state.get("observed_at"), datetime.now(timezone.utc)):
                raise ValueError("control_observation_stale")
            job_id = str(uuid4())
            package = package_for(objective, handoff_id=job_id, command_id=body.get("command_id"),
                                  main_sha=str(body.get("main_sha") or ""), control=state)
            cur.execute("select *,output as result from iren.jobs where objective_key=%s and job_type='CODEX_HANDOFF' and status='WAITING' for update", (key,))
            for previous in _rows(cur):
                old = previous["result"]
                original = old.get("package") or {}
                if original.get("base_sha") == package["base_sha"] and original.get("objective_identity") == package["objective_identity"]:
                    return {"job": previous, "reused": True}
                if old.get("association") or old.get("handoff_status") not in {"PREPARED"}:
                    raise ValueError("in_progress_handoff_requires_explicit_supersession")
                old.update(handoff_status="SUPERSEDED", superseded_by=job_id)
                cur.execute("update iren.jobs set status='CANCELLED',output=%s,updated_at=now(),completed_at=now() where job_id=%s",
                            (Jsonb(old), UUID(previous["job_id"])))
                _event(cur, previous["job_id"], "SUPERSEDED", {"superseded_by": job_id})
            result = {"handoff_status": "PREPARED", "package": package, "association": None,
                      "verification": {"verified": False, "blockers": ["implementation_not_submitted"]}}
            cur.execute("""insert into iren.jobs(job_id,job_key,owner_system,workflow,status,objective_key,title,
                instructions,job_type,priority,protected_action,requires_human,requested_by,requested_via,metadata,output)
                values(%s,%s,'IREN','CODEX_HANDOFF','WAITING',%s,%s,%s,'CODEX_HANDOFF',100,false,false,%s,'codex',%s,%s)""",
                (UUID(job_id), "codex-handoff:"+job_id, key, objective["title"], objective["description"],
                 body.get("requested_by") or "IREN", Jsonb({"success_criteria": objective["success_criteria"],
                 "source_command_id": body.get("command_id")}), Jsonb(result)))
            _event(cur, job_id, "PREPARED", {"package_digest": package["package_digest"], "base_sha": package["base_sha"]})
            return {"job": _job(cur, job_id), "reused": False}


def associate(conn, body):
    with conn.transaction():
        with conn.cursor() as cur:
            row = _job(cur, str(body.get("handoff_id") or ""))
            result = row["result"]
            if result.get("handoff_status") in TERMINAL:
                raise ValueError("handoff_terminal")
            number = body.get("pr_number")
            if type(number) is not int or number < 1:
                raise ValueError("invalid_pull_request")
            candidate = {"repository": "anevum/alpaca-trader", "pr_number": number}
            if result.get("association") and result["association"] != candidate:
                raise ValueError("github_association_ambiguous")
            result.update(association=candidate, handoff_status="IN_PROGRESS",
                          verification={"verified": False, "blockers": ["github_association_pending_verification"]})
            cur.execute("update iren.jobs set output=%s,updated_at=now() where job_id=%s",
                        (Jsonb(result), UUID(row["job_id"])))
            _event(cur, row["job_id"], "ASSOCIATED", candidate)
            return {"job": _job(cur, row["job_id"])}


def verify(conn, body):
    from foundation.iren_work_gateway import _rows
    with conn.transaction():
        with conn.cursor() as cur:
            row = _job(cur, str(body.get("handoff_id") or ""))
            result = row["result"]
            if result.get("handoff_status") in TERMINAL:
                return {"job": row}
            old_verification = dict(result.get("verification") or {})
            old_status = result.get("handoff_status")
            package = result["package"]
            if body.get("package_digest") != package["package_digest"]:
                raise ValueError("handoff_revision_conflict")
            cur.execute("select * from iren.objectives where objective_key=%s for update", (row["objective_key"],))
            objective = _rows(cur)[0]
            current_identity = digest({k: objective.get(k) for k in
                ("objective_key", "description", "success_criteria", "dependencies", "protected_action", "metadata")})
            evidence = verification(package, body.get("github") or {}, _state(cur),
                                    body.get("observations") or {}, body.get("migrations") or [])
            if current_identity != package["objective_identity"] or protected(objective):
                evidence.update(verified=False, handoff_status="VERIFYING")
                evidence["blockers"].append("objective_changed_requires_supersession")
            if objective["status"] not in {"READY", "ACTIVE"}:
                evidence.update(verified=False, handoff_status="VERIFYING")
                evidence["blockers"].append("objective_not_executable")
            from foundation.iren_work_gateway import snapshot
            if not dependencies_complete(objective, snapshot(conn)["objectives"]):
                evidence.update(verified=False, handoff_status="VERIFYING")
                evidence["blockers"].append("objective_dependencies_incomplete")
            # Same predicate as IrenWorkEngine._complete_objective_if_verified, atomically
            # with the handoff transition so a crash cannot expose a false COMPLETE.
            complete = evidence["verified"] and criteria_satisfied(objective["success_criteria"], evidence["criteria"])
            result.update(handoff_status=evidence["handoff_status"], verification=evidence)
            if (body.get("github") or {}).get("association_valid"):
                result["association"] = {"repository": package["repository"], "pr_number": body["github"]["pr_number"]}
            status = "SUCCEEDED" if complete else "WAITING"
            cur.execute("""update iren.jobs set status=%s,output=%s,updated_at=now(),
                completed_at=case when %s then now() else completed_at end where job_id=%s""",
                (status, Jsonb(result), complete, UUID(row["job_id"])))
            # Log transitions/blocker changes, not a new event on every polling heartbeat.
            if complete or old_status != result["handoff_status"] or old_verification.get("blockers") != evidence["blockers"]:
                _event(cur, row["job_id"], "VERIFIED" if complete else "VERIFICATION_OBSERVED",
                       {"status": result["handoff_status"], "blockers": evidence["blockers"], "criteria": evidence["criteria"]})
            if complete:
                cur.execute("update iren.objectives set status='COMPLETE',completed_at=now(),updated_at=now() where objective_key=%s",
                            (row["objective_key"],))
            return {"job": _job(cur, row["job_id"]), "objective_completed": complete}


def evidence_snapshot(conn):
    import os
    with conn.cursor() as cur:
        cur.execute("select migration_name from anevum.schema_migrations order by migration_name")
        migrations = [row[0] for row in cur.fetchall()]
    from foundation.command_iren import project_command, read_command_snapshot
    projected = project_command(read_command_snapshot(str(conn.info.dsn)))
    handoffs = projected["work"].get("handoffs") or []
    return {"command_contract": {"schema_version": projected["schema_version"], "state": projected["state"],
                "handoff_count": len(handoffs), "copyable_prompts": all(bool((r.get("package") or {}).get("prompt")) for r in handoffs)},
            "migrations": migrations, "health": {"ok": True, "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"), "codex_handoff": "v1"}}
