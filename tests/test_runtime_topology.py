import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.contracts.service_health import ServiceObservation
from app.iren.core import reduce_state
from app.iren.topology import bounded_health, topology, project_status
from app.iren import service
from app import orchestration_scheduler as scheduler

STAMP = datetime(2020, 9, 30, 15, tzinfo=timezone.utc)

def observation(seconds=0):
    stamp = (STAMP + timedelta(seconds=seconds)).isoformat()
    return {"observed_at": stamp, "services": {
        "RHEN": {"ok": True, "startup_reconciled": True, "reconciliation_safe": True,
          "strategy_version_id": service.POLICY["expected_strategy"], "crypto_execution_enabled": False,
          "persistence": {"enabled": True, "last_sent_at": stamp, "dropped_count": 0}},
        "GRAEN": {"ok": True, "running": True}, "VELUM": {"ok": True, "running": False}, "PREOPEN": {"ok": True}},
        "configuration": {"fingerprint": "synthetic"}, "runs": [],
        "scheduler": {"configured": True, "last_error": False, "last_success_at": stamp}}

def state(seconds=0):
    obs = observation(seconds)
    result, _ = reduce_state({}, obs, service.POLICY)
    result["topology"] = topology(obs, result, service.IrenController().runtime_identity)
    return result

@pytest.mark.parametrize("seconds", [181, -1])
def test_stale_and_future_heartbeat(seconds):
    result = project_status({"state": state()}, STAMP + timedelta(seconds=seconds))
    assert result["stale"] and result["state"]["state"] == "STALE"
    assert all(x["status"] == "STALE" for x in result["state"]["topology"]["services"] if x["independent_runtime"])

def test_missing_heartbeat():
    assert project_status({"state": {}}, STAMP)["stale"]

def test_nostra_is_independent_runtime_and_workers_can_be_idle():
    rows = {r["service_id"]: r for r in state()["topology"]["services"]}
    assert rows["NOSTRA"]["runtime_kind"] == "SERVICE"
    assert rows["NOSTRA"]["independent_runtime"] is True
    assert rows["NOSTRA"]["service_name"] == "nostra"
    assert rows["VELUM"]["status"] == "IDLE"
    assert rows["GRAEN"]["status"] == "IDLE"
    assert rows["RHEN"]["status"] == "IDLE"
    assert rows["IREN"]["status"] == "IDLE"
    assert rows["IREN"]["schema_version"] == "service_heartbeat.v1"
    assert rows["RHEN"]["deployment"] is None  # Never invent provider identity.
    assert rows["RHEN"]["observation_source"] == "iren_http_probe"


def test_running_requires_explicit_activity_evidence():
    obs = observation()
    obs["services"]["GRAEN"]["activity_active"] = True
    obs["services"]["GRAEN"]["current_activity"] = "executing research problem GRAEN-42"
    result, _ = reduce_state({}, obs, service.POLICY)
    rows = {
        row["service_id"]: row
        for row in topology(
            obs,
            result,
            service.IrenController().runtime_identity,
        )["services"]
    }
    assert rows["GRAEN"]["status"] == "RUNNING"
    assert rows["GRAEN"]["current_activity"] == "executing research problem GRAEN-42"
    assert rows["VELUM"]["status"] == "IDLE"

@pytest.mark.parametrize("body", [None, [], {}, {"ok": "true"}, {"ok": True, "running": "false"},
    {"ok": True, "runtime_provenance": {"runtime_started_at": "bad"}}])
def test_malformed_service_observation(body):
    with pytest.raises(ValueError):
        bounded_health("VELUM", body)

def test_contract_rejects_naive_timestamp_and_boolean_coercion():
    row = state()["topology"]["services"][0]
    for patch in ({"observed_at": "2026-09-30T15:00:00"}, {"readiness": "true"}, {"unversioned": True}):
        with pytest.raises(ValidationError):
            ServiceObservation(**{**row, **patch})

def test_dependency_degradation_is_visible():
    obs = observation()
    obs["services"]["RHEN"]["persistence"]["last_sent_at"] = "2026-09-30T14:00:00Z"
    obs["scheduler"]["last_error"] = True
    result, _ = reduce_state({}, obs, service.POLICY)
    deps = topology(obs, result, service.IrenController().runtime_identity)["dependencies"]
    assert deps["telemetry"]["status"] == "STALE" and deps["scheduler"]["status"] == "DEGRADED"

def test_external_observation_survives_rhen_restart(monkeypatch):
    async def scenario():
        down = False
        def handler(request):
            if request.url.path.endswith("/configuration"):
                return httpx.Response(200, json={"fingerprint": "synthetic"})
            if request.url.host == "rhen":
                if down: raise httpx.ConnectError("simulated restart")
                return httpx.Response(200, json={"ok": True, "startup_reconciled": True,
                    "reconciliation_safe": True, "persistence": {"enabled": True}, "crypto": {"execution_enabled": False}})
            return httpx.Response(200, json={"ok": True})
        factory = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: factory(transport=httpx.MockTransport(handler), **kw))
        monkeypatch.setenv("IREN_RHEN_HEALTH_URL", "http://rhen/health")
        monkeypatch.setattr(scheduler.runtime.ledger, "recent", AsyncMock(return_value=[]))
        controller = service.IrenController()
        assert (await controller.observe())["services"]["RHEN"]["ok"]
        down = True
        assert (await controller.observe())["services"]["RHEN"]["ok"] is False
        down = False
        assert (await controller.observe())["services"]["RHEN"]["ok"]
    asyncio.run(scenario())

def test_iren_restart_recovers_durable_incidents_without_rhen_lifecycle():
    async def scenario():
        saved = {"revision": 0, "state": {}}
        delivered = set()
        events = []
        async def gateway(action, **payload):
            if action == "iren_read": return deepcopy(saved)
            assert action == "iren_commit"
            assert payload["expected_revision"] == saved["revision"]
            saved.update(revision=saved["revision"] + 1, state=deepcopy(payload["state"]))
            for event in payload["events"]:
                if event["event_key"] not in delivered:
                    events.append(event)
                    delivered.add(event["event_key"])
            return {"committed": True, "revision": saved["revision"]}
        obs = observation()
        obs["services"]["RHEN"]["ok"] = False
        for seconds in (0, 60, 120):
            controller = service.IrenController()  # New process-local state each time.
            controller.gateway = gateway
            obs["observed_at"] = (STAMP + timedelta(seconds=seconds)).isoformat()
            controller.observe = AsyncMock(return_value=deepcopy(obs))
            controller.dispatch = AsyncMock()
            controller.verify_api = AsyncMock()
            await controller.tick()
        assert len([e for e in events if e["key"] == "service.RHEN" and e["transition"] == "OPEN"]) == 1
        for seconds in (180, 240, 300):
            controller.observe = AsyncMock(return_value=observation(seconds))
            await controller.tick()
        assert saved["state"]["incidents"]["service.RHEN"]["status"] == "CLOSED"
        assert len([e for e in events if e["key"] == "service.RHEN" and e["transition"] == "RECOVERED"]) == 1
    asyncio.run(scenario())

def test_database_outage_does_not_publish_new_state():
    async def scenario():
        controller = service.IrenController()
        controller.gateway = AsyncMock(side_effect=RuntimeError("database unavailable"))
        controller.observe = AsyncMock()
        with pytest.raises(RuntimeError):
            await controller.tick()
        controller.observe.assert_not_awaited()
        assert controller.last_persisted_at is None and controller.state == {}
    asyncio.run(scenario())

def test_independent_startup_and_shutdown(monkeypatch):
    async def scenario():
        controller = service.IrenController()
        controller.tick = AsyncMock()
        monkeypatch.setattr(service, "controller", controller)
        work_engine = service.IrenWorkEngine(AsyncMock(), lambda: {})
        work_engine.tick = AsyncMock()
        monkeypatch.setattr(service, "work_engine", work_engine)
        monkeypatch.setattr(scheduler.runtime, "start", AsyncMock())
        monkeypatch.setattr(scheduler.runtime, "stop", AsyncMock())
        async with service.lifespan(service.app):
            await asyncio.sleep(0)
            assert controller.task and not controller.task.done()
        assert controller.task is None
        scheduler.runtime.stop.assert_awaited_once()
    asyncio.run(scenario())

def test_readiness_requires_fresh_durable_state(monkeypatch):
    async def scenario():
        controller = service.IrenController()
        controller.task = asyncio.create_task(asyncio.Event().wait())
        work_engine = service.IrenWorkEngine(AsyncMock(), lambda: {})
        work_engine.task = asyncio.create_task(asyncio.Event().wait())
        monkeypatch.setattr(service, "controller", controller)
        monkeypatch.setattr(service, "work_engine", work_engine)
        monkeypatch.setattr(scheduler.SchedulerRuntime, "configured", property(lambda self: True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 503
            controller.last_persisted_at = datetime.now(timezone.utc).isoformat()
            assert (await client.get("/ready")).status_code == 200
        controller.task.cancel()
        work_engine.task.cancel()
        await asyncio.gather(controller.task, work_engine.task, return_exceptions=True)
    asyncio.run(scenario())

@pytest.mark.parametrize("module,forbidden", [
    ("app.iren.service", ["app.main", "app.alpaca_client", "app.execution", "app.config"]),
    ("app.main", ["app.iren.service", "app.orchestration_scheduler"]),
])
def test_separate_process_import_boundaries(module, forbidden):
    code = "import importlib,sys; importlib.import_module(" + repr(module) + "); assert not set(" + repr(forbidden) + ") & set(sys.modules)"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr


def test_worker_program_is_observed_not_hardcoded():
    health = bounded_health("GRAEN", {"ok": True, "program": "graen-crypto-native-v6"})
    assert health["runtime_identity"]["system_version"] == "graen-crypto-native-v6"

def test_rhen_lifespan_starts_and_stops_without_iren_in_isolated_process():
    code = '''
import asyncio, sys
from unittest.mock import AsyncMock, Mock
import httpx
from app import main

async def denied(*args, **kwargs):
    raise AssertionError("network forbidden in lifecycle simulation")
httpx.AsyncClient.request = denied

async def scenario():
    main._stop = asyncio.Event()
    started = []
    async def loop():
        started.append(True)
        await main._stop.wait()
    main.monitor_loop = loop
    main.crypto_monitor_loop = loop
    main.slack_market_observer_loop = loop
    for target in (main.event_sink, main.slack_notifier, main.research_reports):
        target.start = AsyncMock()
        target.stop = AsyncMock()
    main.event_sink.emit_critical = AsyncMock(return_value=True)
    main.event_sink.emit = Mock()
    main.slack_notifier.notify_runtime_start = Mock()
    assert not main.settings.credentials_configured
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert len(started) == 3
        assert main.runtime_state.startup_reconciled
        assert "app.iren.service" not in sys.modules
        assert "app.orchestration_scheduler" not in sys.modules
    assert main._stop.is_set()
    main.event_sink.stop.assert_awaited_once()
asyncio.run(scenario())
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
