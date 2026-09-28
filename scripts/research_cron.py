"""One-shot scheduler; same research API, no broker or deployment authority."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app.slack_notifier import SlackNotifier

NY = ZoneInfo("America/New_York")


def due(now: datetime) -> bool:
    local = now.astimezone(NY)
    return local.weekday() < 5 and time(16, 10) <= local.time() < time(17)


def successful(result: dict) -> bool:
    return (result.get("status") in {"COMPLETED", "NOOP", "BLOCKED"}
            and (result.get("persisted") is True or result.get("reason") == "duplicate_run_key"))


def emit(event: str, **details) -> None:
    print(json.dumps({"event": event, "at": datetime.now(timezone.utc).isoformat(), **details}), flush=True)


async def run(*, verify_session: date | None = None) -> int:
    now = datetime.now(NY)
    if verify_session is None and not due(now):
        emit("rhen_research_scheduler_noop", reason="outside_1610_1700_et", local_time=now.isoformat())
        return 0
    for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "ADMIN_TOKEN"):
        if os.environ.get(name, "").strip():
            emit("rhen_research_scheduler_failed", reason="forbidden_execution_credential", variable=name)
            return 1
    url = os.environ.get("RHEN_RESEARCH_REVIEW_URL", "").strip()
    token = os.environ.get("RHEN_REVIEW_TOKEN", "").strip()
    notifier = SlackNotifier(SimpleNamespace(slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", "")))
    await notifier.start()
    try:
        if not url.startswith("https://") or not token:
            raise ValueError("scheduler_configuration_incomplete")
        session = verify_session or now.date()
        async with httpx.AsyncClient(timeout=httpx.Timeout(255, connect=10)) as client:
            calendar_url = os.environ.get("RHEN_TRADER_CALENDAR_URL", "").strip()
            last_session = now.weekday() == 4
            if verify_session is None:
                if not calendar_url.startswith("https://"):
                    raise ValueError("session_calendar_not_configured")
                calendar_response = await client.get(calendar_url, timeout=30)
                calendar_response.raise_for_status()
                calendar = calendar_response.json()
                if calendar.get("session_date") != session.isoformat():
                    raise ValueError("session_calendar_not_current")
                if calendar.get("is_trading_session") is False:
                    emit("rhen_research_scheduler_noop", reason="exchange_holiday", session=session.isoformat())
                    return 0
                if calendar.get("is_trading_session") is not True:
                    raise ValueError("session_calendar_invalid")
                last_session = calendar.get("is_last_session_of_week") is True
            cadences = ["daily", "weekly"] if last_session and verify_session is None else ["daily"]
            for cadence in cadences:
                # Readiness may retry safely before any model or audit invocation.
                ready_url = url.rsplit("/", 1)[0] + "/readiness"
                headers = {"x-rhen-agent-admin-token": token}
                for attempt in range(5):
                    try:
                        response = await client.get(ready_url, params={"cadence": cadence},
                                                    headers=headers, timeout=45)
                        response.raise_for_status()
                        ready = response.json()
                        if ready.get("trigger_reference") != session.isoformat():
                            raise ValueError("canonical_report_not_current")
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                        if attempt == 4:
                            raise
                        await asyncio.sleep(15)
                # Never blindly retry an ambiguous POST timeout; the API has a 240s bound.
                response = await client.post(url, headers=headers, json={
                    "cadence": cadence, "invoke_model": verify_session is None,
                    "persist": True, "expected_session": session.isoformat(),
                })
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or not successful(result):
                    raise ValueError("research_completion_unconfirmed")
                emit("rhen_research_scheduler_completed", cadence=cadence, session=session.isoformat(),
                     status=result.get("status"), persisted=result.get("persisted"),
                     duplicate=result.get("reason") == "duplicate_run_key", run_id=result.get("run_id"),
                     verification=verify_session is not None)
        return 0
    except Exception as exc:
        emit("rhen_research_scheduler_failed", error_type=type(exc).__name__)
        notifier.record_event({"kind": "research_scheduler", "action": "error",
                               "at": datetime.now(timezone.utc).isoformat(),
                               "message": "Scheduled research did not confirm completion. Inspect scheduler logs and canonical audit."})
        return 1
    finally:
        await notifier.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-session", type=date.fromisoformat,
                        help="Explicit audit verification; model invocation is always disabled.")
    args = parser.parse_args()
    return asyncio.run(run(verify_session=args.verify_session))


if __name__ == "__main__":
    raise SystemExit(main())
