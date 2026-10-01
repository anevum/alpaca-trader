from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import httpx

from app.iren.core import fresh, reduce_state
from app.iren.service import IrenController, app, controller, POLICY
from app import orchestration_scheduler as scheduler


def observation(seconds=0):
    stamp = (datetime(2026, 9, 30, 11, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return {
        "observed_at": stamp,
        "source_commit": "synthetic-commit",
        "services": {
            "RHEN": {"ok": True, "startup_reconciled": True, "reconciliation_safe": True,
                "strategy_version_id": POLICY["expected_strategy"], "crypto_execution_enabled": False,
                "persistence": {"enabled": True, "last_sent_at": stamp, "dropped_count": 0}},
            "VELUM": {"ok": True, "broker_orders_possible": False},
            "PREOPEN": {"ok": True},
            "GRAEN": {"ok": True, "broker_orders_possible": False, "execution_authority": False},
        },
        "configuration": {"fingerprint": "synthetic-baseline"},
        "scheduler": {"configured": True, "last_error": False, "last_success_at": stamp},
        "runs": [],
    }


def test_determinism_and_no_input_mutation():
    obs = observation()
    original = deepcopy(obs)
    assert reduce_state({}, obs, POLICY) == reduce_state({}, obs, POLICY)
    assert obs == original
    state, events = reduce_state({}, obs, POLICY)
    assert state["state"] == "HEALTHY" and events == []
    assert state["authority"]["model_invoked"] is False
    assert state["authority"]["trading_mutations"] is False


@pytest.mark.parametrize("field,value,key", [
    ("crypto_execution_enabled", True, "safety.crypto_execution"),
    ("strategy_version_id", "unapproved", "safety.strategy_identity"),
    ("reconciliation_safe", False, "safety.reconciliation_safe"),
    ("startup_reconciled", None, "safety.startup_reconciled"),
])
def test_protected_failures_open_immediately(field, value, key):
    obs = observation()
    obs["services"]["RHEN"][field] = value
    state, events = reduce_state({}, obs, POLICY)
    assert state["state"] == "ATTENTION_REQUIRED"
    assert any(e["key"] == key and e["transition"] == "OPEN" for e in events)


def test_warning_hysteresis_recovery_restart_and_recurrence():
    obs = observation()
    obs["services"]["VELUM"] = {"ok": False}
    state, events = reduce_state({}, obs, POLICY)
    assert state["state"] == "DEGRADED" and not events
    obs["observed_at"] = observation(60)["observed_at"]
    state, events = reduce_state(json.loads(json.dumps(state)), obs, POLICY)
    assert len(events) == 1 and events[0]["transition"] == "OPEN"
    opened_key = events[0]["event_key"]
    for seconds in (120, 180):
        state, events = reduce_state(state, observation(seconds), POLICY)
        assert not events
    state, events = reduce_state(state, observation(240), POLICY)
    assert events[0]["transition"] == "RECOVERED"
    for seconds in (300, 360):
        obs = observation(seconds)
        obs["services"]["VELUM"] = {"ok": False}
        state, events = reduce_state(state, obs, POLICY)
    assert events[0]["event_key"] != opened_key


def test_duplicate_and_out_of_order_observations_rejected():
    state, _ = reduce_state({}, observation(60), POLICY)
    for obs in (observation(60), observation()):
        with pytest.raises(ValueError, match="not_newer"):
            reduce_state(state, obs, POLICY)


def test_baseline_drift_never_auto_accepted():
    state, _ = reduce_state({}, observation(), POLICY)
    obs = observation(60)
    obs["configuration"]["fingerprint"] = "changed-risk"
    state, events = reduce_state(state, obs, POLICY)
    assert state["configuration_baseline"]["fingerprint"] == "synthetic-baseline"
    assert events[0]["key"] == "configuration.drift"


def test_incremental_evidence_loss_and_missing_data():
    state, _ = reduce_state({}, observation(), POLICY)
    for seconds in (60, 120):
        obs = observation(seconds)
        obs["services"]["RHEN"]["persistence"]["dropped_count"] = seconds
        state, events = reduce_state(state, obs, POLICY)
    assert any(e["key"] == "evidence.loss" for e in events)
    obs = observation(180)
    obs["services"]["RHEN"]["persistence"] = {}
    state, events = reduce_state(state, obs, POLICY)
    assert any(e["key"] == "evidence.disabled" for e in events)


def test_old_misses_do_not_become_current_incidents():
    obs = observation()
    obs["runs"] = [{"workflow_id": "rhen.preflight", "scheduled_at": "2026-09-29T12:45:00+00:00", "status": "MISSED"},
        {"workflow_id": "rhen.preflight", "scheduled_at": "2026-09-30T10:45:00+00:00", "status": "SUCCEEDED"}]
    state, _ = reduce_state({}, obs, POLICY)
    assert state["state"] == "HEALTHY"


def test_yesterday_preflight_before_todays_due_time_is_historical():
    obs = observation()
    obs["runs"] = [{"workflow_id": "rhen.preflight", "scheduled_at": "2026-09-29T12:45:00+00:00", "status": "MISSED"}]
    state, _ = reduce_state({}, obs, POLICY)
    assert state["state"] == "HEALTHY"


def test_scheduler_startup_grace_does_not_fabricate_success():
    obs = observation()
    obs["scheduler"]["started_at"] = obs["observed_at"]
    obs["scheduler"]["last_success_at"] = None
    state, events = reduce_state({}, obs, POLICY)
    assert not any(e["key"] == "scheduler.stale" for e in events)
    assert state["scheduler"]["last_success_at"] is None


def test_rolling_restart_does_not_count_an_extra_observation():
    async def scenario():
        c = IrenController()
        stamp = datetime.now(timezone.utc).isoformat()
        c.gateway = AsyncMock(return_value={"state": {"observed_at": stamp, "state": "DEGRADED"}, "revision": "9"})
        c.observe = AsyncMock()
        await c.tick()
        c.observe.assert_not_awaited()
        assert c.revision == 9 and c.state["state"] == "DEGRADED"
    asyncio.run(scenario())


def test_scheduler_staleness_and_expired_lease():
    obs = observation(1000)
    obs["scheduler"]["last_success_at"] = observation()["observed_at"]
    obs["runs"] = [{"workflow_id": "rhen.research.daily", "scheduled_at": observation()["observed_at"],
        "status": "RUNNING", "lease_until": observation()["observed_at"]}]
    state, events = reduce_state({}, obs, POLICY)
    assert any(e["key"] == "scheduler.stale" for e in events)
    assert state["incidents"]["workflow.rhen.research.daily"]["failure_count"] == 1


def test_future_or_naive_timestamps_fail_freshness():
    now = datetime(2026, 9, 30, 11, tzinfo=timezone.utc)
    assert not fresh("2026-09-30T12:00:00+00:00", now, 180)
    assert not fresh("2026-09-30T11:00:00", now, 180)
    assert not fresh(None, now, 180)


def test_durable_failure_does_not_publish_memory_state():
    async def scenario():
        c = IrenController()
        c.gateway = AsyncMock(side_effect=[{"state": {}, "revision": 0}, {"ok": True, "committed": False, "revision": 1}])
        c.observe = AsyncMock(return_value=observation())
        c.dispatch = AsyncMock()
        with pytest.raises(RuntimeError, match="revision_conflict"):
            await c.tick()
        assert c.state == {} and c.last_persisted_at is None
        c.dispatch.assert_not_awaited()
    asyncio.run(scenario())


def test_durable_revision_precedes_notification_dispatch():
    async def scenario():
        c = IrenController()
        c.gateway = AsyncMock(side_effect=[{"state": {}, "revision": 4}, {"ok": True, "committed": True, "revision": 5}])
        c.observe = AsyncMock(return_value=observation())
        c.dispatch = AsyncMock()
        c.verify_api = AsyncMock()
        await c.tick()
        assert c.revision == 5 and c.state["state"] == "HEALTHY"
        assert c.gateway.call_args_list[1].kwargs["expected_revision"] == 4
        c.dispatch.assert_awaited_once()
    asyncio.run(scenario())


def test_production_api_check_requires_auth_and_fresh_state(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/iren/status":
            if request.headers.get("x-anevum-scheduler-token") != "synthetic-token":
                return httpx.Response(401)
            return httpx.Response(200, json={"stale": False, "revision": "7"})
        return httpx.Response(200, json={"scheduler_version": scheduler.runtime.scheduler_version})
    factory = httpx.AsyncClient
    monkeypatch.setattr(scheduler.runtime, "token", "synthetic-token")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs))
    async def scenario():
        c = IrenController()
        c.revision = 7
        await c.verify_api()
        assert c.api_verified
    asyncio.run(scenario())


def test_status_is_authenticated_and_legacy_scheduler_routes_preserved(monkeypatch):
    monkeypatch.setattr(scheduler.runtime, "token", "a" * 40)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/v1/iren/status")).status_code == 401
            assert (await client.get("/v1/iren/policy", headers={"x-anevum-scheduler-token": "a" * 40})).status_code == 200
    asyncio.run(scenario())
    paths = {r.path for r in app.routes}
    assert {"/v1/status", "/v1/registry", "/v1/diagnostics"} <= paths
    assert not any("promote" in p or "restart" in p or "order" in p for p in paths)


def test_private_sql_revision_fence_and_bounded_notification_retries():
    sql = Path("database/iren_control_plane.sql").read_text()
    gateway = Path("foundation/iren_gateway.py").read_text()
    assert "for update" in sql and "expected_revision" in sql
    assert "security invoker" in sql
    assert sql.count("enable row level security") == 2
    assert "attempts < 3" in gateway and "skip locked" in gateway
    assert "owner_uuid" in gateway and "owner=%s" in gateway
