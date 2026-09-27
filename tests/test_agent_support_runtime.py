import asyncio
import json
import os
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent_support.escalation import reconcile
from app.agent_support.railway import load_role_map
from app.agent_support.runtime import SupportRuntime, isolation_violations, sessions
from app.agent_support.service import app

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_external_credentials(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("ALPACA_", "OPENAI_")) or key in {
            "ADMIN_TOKEN", "TRADING_INGEST_TOKEN", "BOT_ARMED",
            "EXECUTION_ENABLED", "LIVE_TRADING", "I_ACKNOWLEDGE_LIVE_TRADING",
            "SUPABASE_DB_URL", "SUPABASE_SERVICE_ROLE_KEY",
        }:
            monkeypatch.delenv(key)


def topology():
    mapping = load_role_map()
    status = [{"id": row["service_id"], "name": row["expected_name"],
               "latestDeployment": {"id": f"deployment-{row['role']}", "status": "SUCCESS"},
               "cronSchedule": "5 22 * * 1-5" if row["role"] == "research_scheduler" else None}
              for row in mapping["assignments"]]
    configs = {row["service_id"]: {
        "source": {"repo": row["source_repo"]} if row.get("source_repo")
                  else {"image": row["source_image"]},
        "deploy": {"startCommand": row["command_marker"],
                   "cronSchedule": "5 22 * * 1-5" if row["role"] == "research_scheduler" else None},
    } for row in mapping["assignments"]}
    return status, configs


def payload():
    mapping = load_role_map()
    dates = sessions(NOW)
    return {
        "evidence_version": "rhen-support-evidence-v1",
        "command_evidence": {
            "evidence_version": "rhen-command-evidence-v1",
            "provenance": {"runtime": {
                "service_id": mapping["assignments"][0]["service_id"],
                "service_name": mapping["assignments"][0]["expected_name"],
                "deployment_id": "deployment-production_trading",
                "git_commit": "a" * 40, "strategy_version_id": "LIVE-1"},
                "latest_scan_cycle": {"scan_cycle_id": 1}},
            "telemetry_health": {"latest_received_at": NOW.isoformat()},
            "latest_weekly": None,
        },
        "period_inputs": {
            "strategy_versions": [{"version_id": "LIVE-1",
                                   "strategy_name": "rolling_momentum_vwap"}],
            "daily_reports": [
                {"event_id": f"event-{day}", "occurred_at": f"{day}T21:00:00Z",
                 "payload": {"session": day, "strategy_version_id": "LIVE-1",
                             "generated_at": f"{day}T20:59:00Z"}}
                for day in dates if day < "2026-09-28"
            ],
            "duplicate_checks": {},
            "earliest_daily_session": min(dates),
        },
    }


def readiness(state="WAITING"):
    result = {"state": state, "cadence": "daily", "gpt_would_run_now": False,
              "blocker_count": 0, "blockers": [], "limitation_count": 1,
              "limitations": [{"scope": "report", "code": "KNOWN_EVIDENCE_LIMITATION",
                               "reason_codes": ["UNRECONSTRUCTABLE_EVIDENCE"]}],
              "monitor_count": 1,
              "monitors": [{"scope": "research_question", "code": "NONBLOCKING_MONITOR",
                            "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"]}],
              "strategy_question_count": 1, "ready_strategy_question_count": 0,
              "waiting_strategy_question_count": 1,
              "waiting_requirements": ["MULTIPLE_INDEPENDENT_SESSIONS"],
              "trigger_reference": "2026-09-25",
              "evidence_cutoff": "2026-09-28T19:59:00Z",
              "read_only": True, "model_invoked": False, "persisted": False}
    if state == "BLOCKED":
        result.update(blocker_count=1,
                      blockers=[{"scope": "report", "code": "REPORT_INTEGRITY",
                                 "reason_codes": ["EXPLICIT_OPERATIONAL_FAILURE"]}])
    return result


def run_once(*, research=None, persist=False, malformed=False):
    writes = []
    def handler(request):
        if request.url.host == "gateway":
            view = request.url.params["view"]
            if request.method == "POST":
                writes.append(request)
                return httpx.Response(200, json={"ok": True,
                                                  "applied": len(json.loads(request.content))})
            return httpx.Response(200, json={"ok": True, "data":
                payload() if view == "evidence" and not malformed else
                {"bad": True} if view == "evidence" else []})
        if request.url.path.endswith("readiness/public"):
            return httpx.Response(200, json=research or readiness())
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"runtime": {}})

    async def work():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            status, configs = topology()
            runtime = SupportRuntime(client, gateway_url="https://gateway",
                                     read_token="read", write_token="write",
                                     research_url="https://research", preopen_url="https://preopen")
            return await runtime.evaluate(railway_status=status, railway_configs=configs,
                                          persist=persist, now=NOW)
    return asyncio.run(work()), writes


def test_waiting_and_nonblocking_limitations_are_not_critical():
    result, writes = run_once()
    assert result["persisted"] is False and writes == []
    assert result["integrity"]["state"] == "DEGRADED"
    assert result["context"]["research_readiness"]["waiting_requirements"] == ["MULTIPLE_INDEPENDENT_SESSIONS"]
    assert not any(row["code"] == "RESEARCH_READINESS_BLOCKED" for row in result["integrity"]["reasons"])


def test_active_blocker_and_persisted_support_actions():
    result, writes = run_once(research=readiness("BLOCKED"), persist=True)
    assert result["integrity"]["state"] == "BLOCKED"
    assert result["applied_actions"] == len(result["proposed_actions"])
    assert len(writes) == 1
    assert all(action["source_component"] in {"research_agent", "railway", "reports"}
               for action in result["proposed_actions"])


def test_malformed_source_cannot_write():
    import pytest
    with pytest.raises(ValueError):
        run_once(malformed=True, persist=True)


def test_resolution_and_repeated_observation_keep_one_identity():
    finding = {"source": "research_agent", "code": "RESEARCH_READINESS_WAITING",
               "reference": "2026-09-25", "state": "DEGRADED"}
    first = reconcile([finding], {}, observed_at=NOW, assessed_sources={"research_agent"})
    key = first[0]["alert_key"]
    again = reconcile([finding], {key: first[0]}, observed_at=NOW,
                      assessed_sources={"research_agent"})
    assert len(again) == 1 and again[0]["alert_key"] == key
    cleared = reconcile([], {key: first[0]}, observed_at=NOW,
                        assessed_sources={"research_agent"})
    assert cleared[0]["state"] == "RESOLVED"
    assert reconcile([], {key: first[0]}, observed_at=NOW,
                     assessed_sources=set()) == []


def test_unauthorized_run_and_forbidden_runtime_variables():
    with TestClient(app) as client:
        assert client.post("/v1/run", json={"railway_status": [], "railway_configs": {}}).status_code == 401
        assert client.get("/health").json()["trading_execution_authority"] is False
    assert isolation_violations({"ALPACA_API_KEY": "key", "OPENAI_API_KEY": "model",
                                 "EXECUTION_ENABLED": "true"}) == [
        "ALPACA_API_KEY", "EXECUTION_ENABLED", "OPENAI_API_KEY"]
