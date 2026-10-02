from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import json

import httpx
import pytest

from app.iren.codex_handoff import package_for, verification, protected, render_prompt
from app.iren.codex_github import inspect_github
from app.iren.work import normalize_command, process_command, IrenWorkEngine, criteria_satisfied
from app.iren.executor_service import runtime, JobEnvelope, app

NOW = datetime.now(timezone.utc)
SHA = "a" * 40
ID = "c0de0001-2026-4002-8000-000000000001"


def objective():
    return {"objective_key": "iren.test", "title": "Implement a read-only IREN capability",
            "description": "Add deterministic evidence.", "status": "READY", "dependencies": [],
            "protected_action": False, "owner_system": "IREN", "priority": 100,
            "success_criteria": {"capability": True},
            "metadata": {"job_type": "SOFTWARE_BUILD", "codex_scope": {
                "verification_checks": {"capability": {"source": "IREN", "path": ["capability"]}}}}}


def control():
    return {"state": "HEALTHY", "observed_at": NOW.isoformat(), "incidents": {},
            "configuration_baseline": {"fingerprint": "fixed"},
            "topology": {"services": [
                {"service_id": "RHEN", "independent_runtime": True, "deployment": "protected", "revision": "b"*40, "readiness": True},
                {"service_id": "IREN", "independent_runtime": True, "deployment": "iren-before", "revision": SHA, "readiness": True},
            ]}}


def package(o=None, **kw):
    return package_for(o or objective(), handoff_id=ID, command_id="command-1", main_sha=SHA, control=control(), now=NOW, **kw)


def github():
    return {"observed_at": NOW.isoformat(), "association_valid": True, "merged": True, "landed": True, "ci_passed": True,
            "main_sha": SHA, "pr_number": 222, "files": ["app/iren/example.py"]}


def observations():
    return {"IREN": {"capability": True}, "IREN_EXECUTOR": {"spending_authority": False,
            "software_worker": {"daily_budget_usd": 0, "job_budget_usd": 0}}}


def test_package_is_reproducible_self_contained_and_preserves_criteria():
    p = package()
    assert package() == p
    assert p["success_criteria"] == objective()["success_criteria"]
    assert p["base_sha"] == SHA
    for key in ("handoff_id", "objective_key", "source_job_id", "source_command_id", "base_sha", "created_at",
                "dependencies", "active_incidents", "runtime_baseline", "protected_boundaries", "verification_checks"):
        assert key in p
        assert key in p["prompt"]
    for phrase in ("Inspect CURRENT main", "Foundation v2", "Rollback", "IREN-Handoff:", "zero", "No trader restart"):
        assert phrase in p["prompt"]
    assert p["prompt"] == render_prompt({k:v for k,v in p.items() if k not in {"prompt", "package_digest"}})


@pytest.mark.parametrize("field", ["protected_action", "requires_human"])
def test_protected_objectives_fail_closed(field):
    o = objective(); o[field] = True
    with pytest.raises(ValueError, match="protected"):
        package(o)


def test_no_handoff_expands_scope_or_authority():
    p = package()
    assert p["protected_authority"] is False and p["paid_model_execution"] is False and p["auto_merge"] is False
    o = objective(); o["metadata"]["codex_scope"]["allowed_paths"] = ["app/execution.py"]
    with pytest.raises(ValueError, match="scope"):
        package(o)


def test_sha_required_and_deterministic_objective_rejected():
    with pytest.raises(ValueError, match="sha"):
        package_for(objective(), handoff_id=ID, command_id=None, main_sha="", control=control())
    o = objective(); o["metadata"]["job_type"] = "CONTROL_RECONCILE"
    with pytest.raises(ValueError, match="not_software"):
        package(o)


@pytest.mark.parametrize("alias", ["prepare for Codex", "prepare Codex handoff", "Codex handoff", "what should Codex do next?"])
def test_aliases(alias):
    assert normalize_command(alias) == "CODEX_HANDOFF"


def test_next_is_actionable():
    r = process_command("what’s next?", {"objectives": [objective()], "jobs": []}, control(), requested_by="owner", source="command")
    assert r.intent == "NEXT" and r.response["execution_mode"] == "codex/manual software"
    assert r.response["next_action"]["objective_key"] == "iren.test"


@pytest.mark.parametrize("key", ["association_valid", "merged", "landed", "ci_passed"])
def test_absent_github_evidence_cannot_complete(key):
    g = github(); g[key] = False
    assert not verification(package(), g, control(), observations(), [], now=NOW)["verified"]


def test_objective_evidence_not_inferred_from_merge():
    assert not verification(package(), github(), control(), {}, [], now=NOW)["verified"]


def test_positive_verification_uses_existing_criterion_predicate():
    result = verification(package(), github(), control(), observations(), [], now=NOW)
    assert result["verified"], result["blockers"]
    assert criteria_satisfied(objective()["success_criteria"], result["criteria"])


@pytest.mark.parametrize("mutation", ["stale", "incident", "rhen_restart", "drift", "scope", "budget", "missing_identity", "migration"])
def test_runtime_boundary_failures(mutation):
    p,g,s,o = package(),github(),control(),observations()
    if mutation == "stale": s["observed_at"] = "2000-01-01T00:00:00Z"
    if mutation == "incident": s["incidents"] = {"x": {"status":"OPEN"}}
    if mutation == "rhen_restart": s["topology"]["services"][0]["deployment"] = "unexpected"
    if mutation == "drift": s["configuration_baseline"]["fingerprint"] = "changed"
    if mutation == "scope": g["files"] = ["app/execution.py"]
    if mutation == "budget": o["IREN_EXECUTOR"]["software_worker"]["job_budget_usd"] = 1
    if mutation == "missing_identity": p["runtime_baseline"]["RHEN"]["deployment"] = None
    if mutation == "migration": p["required_migrations"] = ["not_applied.sql"]
    assert not verification(p,g,s,o,[],now=NOW)["verified"]


def test_zero_budget_cannot_call_paid_worker(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    monkeypatch.setenv("IREN_MODEL_DAILY_BUDGET_USD", "0")
    monkeypatch.setenv("IREN_MODEL_JOB_BUDGET_USD", "0")
    monkeypatch.setenv("IREN_GITHUB_TOKEN", "g"*40)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-"+"x"*40)
    job = JobEnvelope(job_id="x", title="Build", job_type="SOFTWARE_BUILD")
    result = runtime.accept(job)
    assert result["model_invoked"] is False
    assert not runtime.software_backend_configured
    assert not runtime.health()["spending_authority"]


def test_github_reader_requires_auth(monkeypatch):
    monkeypatch.setenv("IREN_EXECUTOR_TOKEN", "a"*40)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/v1/codex/github")).status_code == 401
    asyncio.run(run())


def test_github_discovery_ambiguous_fails_closed():
    async def get(path):
        if path == "branches/main": return {"commit": {"sha": SHA}}
        return [{"number":1},{"number":2}]
    r=asyncio.run(inspect_github(get,handoff_id=ID,objective_key="iren.test"))
    assert not r["association_valid"]


def test_engine_prepare_and_deterministic_path_without_paid_calls(monkeypatch):
    calls=[]
    async def run(software):
        o=objective()
        if not software: o["metadata"]["job_type"]="CONTROL_RECONCILE"
        async def gateway(action,**payload):
            calls.append(action)
            if action=="iren_commands_claim": return {"commands":[{"command_id":"cmd","command_text":"prepare for Codex","source":"command"}]}
            if action=="iren_work_snapshot": return {"objectives":[o],"jobs":[]}
            if action=="iren_handoff_prepare": return {"job":{"job_id":ID,"result":{"package":package()}}}
            return {}
        e=IrenWorkEngine(gateway,control)
        async def evidence(*args): return {"main_sha":SHA}
        monkeypatch.setattr(e,"_github_evidence",evidence)
        await e._process_commands()
    asyncio.run(run(True))
    assert "iren_handoff_prepare" in calls and "iren_job_create" not in calls
    calls.clear()
    asyncio.run(run(False))
    assert "iren_handoff_prepare" not in calls and "iren_job_create" not in calls
