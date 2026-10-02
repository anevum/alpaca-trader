"""Real PostgreSQL lifecycle tests; isolated CI database only."""
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from foundation.iren_handoff_gateway import prepare, associate, verify
from foundation.iren_work_gateway import job_update, objective_update, snapshot
from app.iren.work import choose_next_action

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
SHA = "a"*40


class RollbackTest(Exception):
    pass


# psycopg Rollback makes fixture cleanup non-error and rolls the entire test back.
@pytest.fixture(name="conn")
def isolated_conn():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as db:
        try:
            with db.transaction():
                yield db
                raise RollbackTest()
        except RollbackTest:
            pass


def setup(conn):
    key="test.codex."+str(uuid4())
    meta={"job_type":"SOFTWARE_BUILD","codex_scope":{"verification_checks":{
        "ready":{"source":"IREN","path":["ready"]}}}}
    state={"state":"HEALTHY","observed_at":datetime.now(timezone.utc).isoformat(),
        "incidents":{},"configuration_baseline":{"fingerprint":"fixed"},
        "topology":{"inventory_complete":True,"services":[
            {"service_id":"IREN","independent_runtime":True,"deployment":"iren","revision":SHA,"readiness":True},
            {"service_id":"RHEN","independent_runtime":True,"deployment":"rhen","revision":"b"*40,"readiness":True}]}}
    with conn.cursor() as cur:
        cur.execute("""insert into iren.objectives(objective_key,title,description,status,priority,metadata,success_criteria)
            values(%s,'Test handoff','Implement read-only evidence','READY',999,%s,'{"ready":true}')""",(key,Jsonb(meta)))
        cur.execute("""insert into iren.system_state(system_key,health,revision,state,observation_key,observed_at)
            values('IREN','HEALTHY',1,%s,'test',now()) on conflict(system_key) do update set state=excluded.state""",(Jsonb(state),))
    return key


def test_durable_prepare_idempotency_supersession_and_protected_callback(conn):
    key=setup(conn)
    body={"objective_key":key,"main_sha":SHA,"command_id":"command"}
    first=prepare(conn,body)["job"]
    assert prepare(conn,body)["job"]["job_id"]==first["job_id"]
    second=prepare(conn,{**body,"main_sha":"c"*40})["job"]
    assert second["job_id"]!=first["job_id"]
    with conn.cursor() as cur:
        cur.execute("select status,output->>'handoff_status' from iren.jobs where job_id=%s",(first["job_id"],))
        assert cur.fetchone()==("CANCELLED","SUPERSEDED")
    with pytest.raises(ValueError):
        job_update(conn,{"job_id":second["job_id"],"status":"SUCCEEDED","result":{"verified":True}})
    with pytest.raises(ValueError):
        objective_update(conn,{"objective_key":key,"status":"COMPLETE"})


def test_verification_completion_and_next_objective(conn):
    key=setup(conn)
    job=prepare(conn,{"objective_key":key,"main_sha":SHA})["job"]
    package=job["result"]["package"]
    missing=verify(conn,{"handoff_id":job["job_id"],"package_digest":package["package_digest"]})
    assert missing["objective_completed"] is False
    evidence={"handoff_id":job["job_id"],"package_digest":package["package_digest"],
        "github":{"observed_at":datetime.now(timezone.utc).isoformat(),"association_valid":True,"pr_number":10,"merged":True,"landed":True,
                  "ci_passed":True,"main_sha":SHA,"files":["app/iren/test.py"]},
        "observations":{"IREN":{"ready":True},"IREN_EXECUTOR":{"spending_authority":False,
                       "software_worker":{"daily_budget_usd":0,"job_budget_usd":0}}}}
    complete=verify(conn,evidence)
    assert complete["objective_completed"] is True
    assert complete["job"]["result"]["handoff_status"]=="VERIFIED"
    data=snapshot(conn)
    assert next(o for o in data["objectives"] if o["objective_key"]==key)["status"]=="COMPLETE"
    assert choose_next_action(data)["objective_key"]!=key


def test_association_is_not_completion_and_ambiguous_replacement_rejected(conn):
    key=setup(conn)
    job=prepare(conn,{"objective_key":key,"main_sha":SHA})["job"]
    assert associate(conn,{"handoff_id":job["job_id"],"pr_number":10})["job"]["status"]=="WAITING"
    with pytest.raises(ValueError,match="ambiguous"):
        associate(conn,{"handoff_id":job["job_id"],"pr_number":11})
    with pytest.raises(ValueError,match="supersession"):
        prepare(conn,{"objective_key":key,"main_sha":"c"*40})


def test_objective_change_invalidates_evidence(conn):
    key=setup(conn)
    job=prepare(conn,{"objective_key":key,"main_sha":SHA})["job"]
    with conn.cursor() as cur:
        cur.execute("update iren.objectives set protected_action=true where objective_key=%s",(key,))
    value=verify(conn,{"handoff_id":job["job_id"],"package_digest":job["result"]["package"]["package_digest"]})
    assert not value["objective_completed"]
    assert "objective_changed_requires_supersession" in value["job"]["result"]["verification"]["blockers"]


def test_rolling_deploy_old_worker_cannot_claim_handoff_commands(conn):
    from foundation.iren_work_gateway import command_create, commands_claim
    created=command_create(conn,{"command_text":"prepare for Codex","requested_by":"owner"})
    command_id=created["command"]["command_id"]
    old=commands_claim(conn,owner="iren-work-engine",limit=20)["commands"]
    assert not any(r["command_id"]==command_id for r in old)
    new=commands_claim(conn,owner="iren-work-engine-codex-v1",limit=20)["commands"]
    assert any(r["command_id"]==command_id for r in new)


def test_explicit_supersession_preserves_association_history(conn):
    from foundation.iren_handoff_gateway import supersede
    key=setup(conn)
    job=prepare(conn,{"objective_key":key,"main_sha":SHA})["job"]
    associate(conn,{"handoff_id":job["job_id"],"pr_number":99})
    old=supersede(conn,{"handoff_id":job["job_id"]})["job"]
    assert old["result"]["association"]["pr_number"]==99 and old["status"]=="CANCELLED"
    assert prepare(conn,{"objective_key":key,"main_sha":"d"*40})["job"]["job_id"]!=job["job_id"]
