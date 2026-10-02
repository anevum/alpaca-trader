from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.orchestration_scheduler import (
    NY,
    ScheduledItem,
    SchedulerRuntime,
    market_items,
    rolling_item,
)

UTC = timezone.utc


def workflow(**changes):
    value = {
        "workflow_id": "test.workflow",
        "version": "1.0.0",
        "schedule_type": "market_session_offset",
        "anchor": "open",
        "offset_minutes": 0,
        "catchup_policy": "catch_up",
        "stale_after_minutes": 120,
        "recovery_after_minutes": 5,
        "retry_policy": {"max_attempts": 3, "transient_only": True},
        "notification_policy": {
            "success": False,
            "success_route": "rhen-research",
            "failure_route": "rhen-alerts",
        },
        "implementation_target": "scheduler_self_health",
    }
    value.update(changes)
    return value


def test_market_schedule_respects_dst():
    item_summer = market_items(
        workflow(),
        [{"date": "2026-09-29", "open": "09:30", "close": "16:00"}],
    )[0]
    item_winter = market_items(
        workflow(),
        [{"date": "2026-11-02", "open": "09:30", "close": "16:00"}],
    )[0]

    assert item_summer.scheduled_at == datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
    assert item_winter.scheduled_at == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_early_close_uses_exchange_close_not_hardcoded_time():
    item = market_items(
        workflow(anchor="close", offset_minutes=15),
        [{"date": "2026-11-27", "open": "09:30", "close": "13:00"}],
    )[0]
    assert item.scheduled_at == datetime(2026, 11, 27, 18, 15, tzinfo=UTC)
    assert item.details["early_close"] is True


def test_holiday_absence_produces_no_synthetic_session():
    sessions = [
        {"date": "2026-12-24", "open": "09:30", "close": "13:00"},
        {"date": "2026-12-28", "open": "09:30", "close": "16:00"},
    ]
    items = market_items(workflow(), sessions)
    assert [item.trigger_reference for item in items] == ["2026-12-24", "2026-12-28"]


def test_last_session_of_week_handles_holiday_shortened_week():
    sessions = [
        {"date": "2026-12-21", "open": "09:30", "close": "16:00"},
        {"date": "2026-12-22", "open": "09:30", "close": "16:00"},
        {"date": "2026-12-23", "open": "09:30", "close": "16:00"},
        {"date": "2026-12-24", "open": "09:30", "close": "13:00"},
        {"date": "2026-12-28", "open": "09:30", "close": "16:00"},
    ]
    items = market_items(workflow(last_session_of_week=True), sessions)
    assert items[0].trigger_reference == "2026-12-24"


def test_job_key_is_deterministic_for_duplicate_prevention():
    w = workflow()
    scheduled = datetime(2026, 9, 29, 20, 15, tzinfo=UTC)
    a = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    b = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    assert a.job_key == b.job_key
    assert a.input_identity == b.input_identity


def test_cross_midnight_utc_schedule_is_timezone_safe():
    item = market_items(
        workflow(anchor="close", offset_minutes=600),
        [{"date": "2026-09-29", "open": "09:30", "close": "16:00"}],
    )[0]
    assert item.scheduled_at == datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def test_hourly_crypto_bucket_uses_completed_hour():
    w = workflow(
        schedule_type="hourly",
        minute=5,
        workflow_id="velum.crypto.replay",
    )
    now = datetime(2026, 9, 30, 3, 14, tzinfo=UTC)
    item = rolling_item(w, now)
    assert item.scheduled_at == datetime(2026, 9, 30, 3, 5, tzinfo=UTC)
    assert item.details["window_end"] == "2026-09-30T03:00:00+00:00"


def test_preflight_allows_sleeping_optional_research_agent():
    runtime = object.__new__(SchedulerRuntime)
    runtime.trader_url = "http://rhen/v1/scheduler"
    runtime.token = "test"
    runtime._post = AsyncMock(return_value={"ok": True})
    runtime._dependency_health = AsyncMock(
        return_value={
            "rhen": {"ok": True, "required_for_preflight": True},
            "velum": {"ok": True, "required_for_preflight": True},
            "research_agent": {"ok": False, "required_for_preflight": False},
        }
    )
    item = ScheduledItem(
        workflow(implementation_target="trader_preflight"),
        datetime(2026, 9, 30, 12, 45, tzinfo=UTC),
        "2026-09-30",
        {"session": "2026-09-30"},
    )

    result = asyncio.run(runtime._execute(item))

    assert result["local"]["ok"] is True
    assert result["dependencies"]["research_agent"]["ok"] is False


def test_preflight_still_blocks_unhealthy_required_dependency():
    runtime = object.__new__(SchedulerRuntime)
    runtime.trader_url = "http://rhen/v1/scheduler"
    runtime.token = "test"
    runtime._post = AsyncMock(return_value={"ok": True})
    runtime._dependency_health = AsyncMock(
        return_value={
            "rhen": {"ok": False, "required_for_preflight": True},
            "velum": {"ok": True, "required_for_preflight": True},
            "research_agent": {"ok": False, "required_for_preflight": False},
        }
    )
    item = ScheduledItem(
        workflow(implementation_target="trader_preflight"),
        datetime(2026, 9, 30, 12, 45, tzinfo=UTC),
        "2026-09-30",
        {"session": "2026-09-30"},
    )

    with pytest.raises(RuntimeError, match="preflight_dependency_unhealthy:rhen"):
        asyncio.run(runtime._execute(item))


class FakeLedger:
    def __init__(self):
        self.claims = []
        self.completions = []

    async def claim(self, item, **kwargs):
        self.claims.append((item, kwargs))
        return {"claimed": True}

    async def complete(self, item, **kwargs):
        self.completions.append((item, kwargs))
        return {"ok": True}


def runtime_for_process_test():
    runtime = object.__new__(SchedulerRuntime)
    runtime.scheduler_version = "anevum-scheduler-v1.0.0"
    runtime.ledger = FakeLedger()
    runtime.running_job = None
    runtime._notify = AsyncMock(return_value="suppressed:test")
    runtime._execute = AsyncMock(return_value={"ok": True})
    return runtime


def test_missed_preflight_is_recorded_not_replayed():
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.preflight",
        catchup_policy="skip_after_window",
        stale_after_minutes=45,
    )
    scheduled = datetime(2026, 9, 29, 12, 45, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    asyncio.run(runtime._process(item, scheduled + timedelta(hours=2)))

    assert runtime._execute.await_count == 0
    assert runtime.ledger.completions[0][1]["status"] == "MISSED"
    assert runtime.ledger.completions[0][1]["error_classification"] == "stale_execution"


def test_catchup_runs_inside_allowed_window():
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.research.daily",
        catchup_policy="catch_up",
        stale_after_minutes=720,
        recovery_after_minutes=15,
    )
    scheduled = datetime(2026, 9, 29, 20, 25, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    asyncio.run(runtime._process(item, scheduled + timedelta(hours=2)))

    assert runtime._execute.await_count == 1
    assert runtime.ledger.completions[0][1]["status"] == "SUCCEEDED"
    assert runtime.ledger.completions[0][1]["catchup_state"] == "catchup"


def test_expired_catchup_is_stale_and_not_executed():
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.research.daily",
        catchup_policy="catch_up",
        stale_after_minutes=60,
    )
    scheduled = datetime(2026, 9, 29, 20, 25, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    asyncio.run(runtime._process(item, scheduled + timedelta(hours=3)))

    assert runtime._execute.await_count == 0
    assert runtime.ledger.completions[0][1]["status"] == "STALE"


def test_failure_classification_bounds_retry_eligibility():
    import httpx

    request = httpx.Request("GET", "https://example.invalid")
    auth = httpx.HTTPStatusError(
        "unauthorized",
        request=request,
        response=httpx.Response(401, request=request),
    )
    unavailable = httpx.HTTPStatusError(
        "unavailable",
        request=request,
        response=httpx.Response(503, request=request),
    )
    assert SchedulerRuntime._classify_failure(auth) == "authentication"
    assert SchedulerRuntime._classify_failure(unavailable) == "dependency_unavailable"


def test_historical_missed_job_is_durable_but_does_not_alert(monkeypatch):
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.preflight",
        catchup_policy="skip_after_window",
        stale_after_minutes=45,
    )
    scheduled = datetime(2026, 9, 29, 12, 45, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    monkeypatch.setenv("SCHEDULER_HISTORICAL_ALERT_MINUTES", "120")
    asyncio.run(runtime._process(item, scheduled + timedelta(hours=6)))

    assert runtime._notify.await_count == 0
    assert runtime.ledger.completions[0][1]["status"] == "MISSED"
    assert (
        runtime.ledger.completions[0][1]["slack_status"]
        == "suppressed:historical_reconciliation"
    )


def test_recent_missed_job_still_alerts(monkeypatch):
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.preflight",
        catchup_policy="skip_after_window",
        stale_after_minutes=45,
    )
    scheduled = datetime(2026, 9, 29, 12, 45, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-09-29", {"session": "2026-09-29"})
    monkeypatch.setenv("SCHEDULER_HISTORICAL_ALERT_MINUTES", "120")
    asyncio.run(runtime._process(item, scheduled + timedelta(minutes=60)))

    assert runtime._notify.await_count == 1
    assert runtime.ledger.completions[0][1]["status"] == "MISSED"


def test_scheduler_notification_uses_workflow_subsystem_identity():
    runtime = object.__new__(SchedulerRuntime)
    runtime.slack = type(
        "Slack",
        (),
        {"send": AsyncMock(return_value="delivered:rhen-research")},
    )()
    item = ScheduledItem(
        workflow(
            workflow_id="graen.research.checkpoint",
            subsystem="GRAEN",
            owner="GRAEN",
            notification_policy={
                "success": True,
                "success_route": "rhen-research",
                "failure_route": "rhen-alerts",
            },
        ),
        datetime(2026, 9, 30, 20, 50, tzinfo=UTC),
        "2026-09-30",
        {"session": "2026-09-30"},
    )
    asyncio.run(runtime._notify(item, status="SUCCEEDED", result={"ok": True}, error=None))
    args, _ = runtime.slack.send.call_args
    assert args[0] == "rhen-research"
    assert args[1].startswith("*GRAEN // graen.research.checkpoint // SUCCEEDED*")
    assert "scheduled by IREN:" in args[1]


def test_session_close_can_recover_next_morning():
    runtime = runtime_for_process_test()
    w = workflow(
        workflow_id="rhen.session_close",
        version="1.0.1",
        anchor="close",
        offset_minutes=15,
        catchup_policy="catch_up",
        stale_after_minutes=1440,
        recovery_after_minutes=15,
        retry_policy={"max_attempts": 4, "transient_only": True},
        implementation_target="trader_session_close",
    )
    scheduled = datetime(2026, 10, 1, 20, 15, tzinfo=UTC)
    item = ScheduledItem(w, scheduled, "2026-10-01", {"session": "2026-10-01"})

    asyncio.run(runtime._process(item, scheduled + timedelta(hours=15)))

    assert runtime._execute.await_count == 1
    assert runtime.ledger.completions[0][1]["status"] == "SUCCEEDED"
    assert runtime.ledger.completions[0][1]["catchup_state"] == "catchup"
