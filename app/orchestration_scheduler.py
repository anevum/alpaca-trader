from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException

from .slack_brand import decorate_slack_message

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
REGISTRY_PATH = Path(os.getenv("SCHEDULER_REGISTRY_PATH", "app/schedule_registry.json"))
TERMINAL_OK = {"COMPLETED", "NOOP", "BLOCKED"}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _clock(day: date, value: str, tz: ZoneInfo = NY) -> datetime:
    parsed = time.fromisoformat(str(value))
    return datetime.combine(day, parsed, tzinfo=tz)


def _same_iso_week(left: date, right: date) -> bool:
    a = left.isocalendar()
    b = right.isocalendar()
    return (a.year, a.week) == (b.year, b.week)


@dataclass(frozen=True)
class ScheduledItem:
    workflow: dict[str, Any]
    scheduled_at: datetime
    trigger_reference: str
    details: dict[str, Any]

    @property
    def job_key(self) -> str:
        return (
            f"{self.workflow['workflow_id']}:{self.workflow['version']}:"
            f"{_iso(self.scheduled_at)}"
        )

    @property
    def input_identity(self) -> str:
        return _fingerprint(
            {
                "workflow_id": self.workflow["workflow_id"],
                "workflow_version": self.workflow["version"],
                "scheduled_at": _iso(self.scheduled_at),
                "trigger_reference": self.trigger_reference,
                "details": self.details,
            }
        )


def market_items(
    workflow: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> list[ScheduledItem]:
    output: list[ScheduledItem] = []
    ordered = sorted(sessions, key=lambda row: row["date"])
    for index, session in enumerate(ordered):
        session_date = date.fromisoformat(str(session["date"]))
        if workflow.get("last_session_of_week"):
            next_date = (
                date.fromisoformat(str(ordered[index + 1]["date"]))
                if index + 1 < len(ordered)
                else None
            )
            if next_date is not None and _same_iso_week(session_date, next_date):
                continue
        anchor = str(workflow.get("anchor") or "open")
        raw_clock = session.get(anchor)
        if not raw_clock:
            continue
        scheduled = _clock(session_date, str(raw_clock)) + timedelta(
            minutes=int(workflow.get("offset_minutes") or 0)
        )
        output.append(
            ScheduledItem(
                workflow=workflow,
                scheduled_at=scheduled.astimezone(UTC),
                trigger_reference=session_date.isoformat(),
                details={
                    "session": session_date.isoformat(),
                    "market_open": session.get("open"),
                    "market_close": session.get("close"),
                    "early_close": str(session.get("close")) != "16:00",
                },
            )
        )
    return output


def rolling_item(workflow: dict[str, Any], now: datetime) -> ScheduledItem:
    current = now.astimezone(UTC)
    schedule_type = workflow["schedule_type"]
    if schedule_type == "hourly":
        minute = int(workflow.get("minute") or 0)
        candidate = current.replace(minute=minute, second=0, microsecond=0)
        if candidate > current:
            candidate -= timedelta(hours=1)
        reference = (candidate - timedelta(minutes=minute)).replace(
            minute=0, second=0, microsecond=0
        )
        return ScheduledItem(
            workflow=workflow,
            scheduled_at=candidate,
            trigger_reference=_iso(reference),
            details={"window_end": _iso(reference)},
        )
    interval = max(5, int(workflow.get("interval_minutes") or 15))
    floored = current.replace(
        minute=(current.minute // interval) * interval,
        second=0,
        microsecond=0,
    )
    return ScheduledItem(
        workflow=workflow,
        scheduled_at=floored,
        trigger_reference=_iso(floored),
        details={"interval_minutes": interval},
    )


def next_rolling_item(workflow: dict[str, Any], now: datetime) -> ScheduledItem:
    item = rolling_item(workflow, now)
    if item.scheduled_at <= now.astimezone(UTC):
        if workflow["schedule_type"] == "hourly":
            return ScheduledItem(
                workflow=workflow,
                scheduled_at=item.scheduled_at + timedelta(hours=1),
                trigger_reference=_iso(
                    datetime.fromisoformat(item.trigger_reference) + timedelta(hours=1)
                ),
                details={
                    "window_end": _iso(
                        datetime.fromisoformat(item.trigger_reference) + timedelta(hours=1)
                    )
                },
            )
        interval = int(workflow.get("interval_minutes") or 15)
        return ScheduledItem(
            workflow=workflow,
            scheduled_at=item.scheduled_at + timedelta(minutes=interval),
            trigger_reference=_iso(
                datetime.fromisoformat(item.trigger_reference)
                + timedelta(minutes=interval)
            ),
            details={"interval_minutes": interval},
        )
    return item


class LedgerClient:
    def __init__(self, gateway_url: str, token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token

    @property
    def configured(self) -> bool:
        secure_remote = self.gateway_url.startswith("https://")
        local_loopback = self.gateway_url.startswith(
            ("http://127.0.0.1:", "http://localhost:")
        )
        return (secure_remote or local_loopback) and len(self.token) >= 32

    @property
    def headers(self) -> dict[str, str]:
        return {"x-anevum-ingest-token": self.token}

    async def claim(
        self,
        item: ScheduledItem,
        *,
        scheduler_version: str,
        catchup_state: str | None,
    ) -> dict[str, Any]:
        retry = item.workflow.get("retry_policy") or {}
        payload = {
            "action": "claim",
            "job": {
                "job_key": item.job_key,
                "workflow_id": item.workflow["workflow_id"],
                "workflow_version": item.workflow["version"],
                "scheduler_version": scheduler_version,
                "scheduled_at": _iso(item.scheduled_at),
                "trigger_type": "recovery" if catchup_state else "schedule",
                "max_attempts": int(retry.get("max_attempts") or 1),
                "lease_seconds": 1800,
                "allow_retry": bool(retry.get("transient_only", True)),
                "retry_delay_seconds": max(
                    0,
                    min(3600, int(retry.get("delay_seconds") or 0)),
                ),
                "worker_identity": os.getenv("RAILWAY_SERVICE_ID") or "anevum-scheduler",
                "source_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
                "input_identity": item.input_identity,
                "catchup_state": catchup_state,
                "details": {
                    "trigger_reference": item.trigger_reference,
                    **item.details,
                },
            },
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.gateway_url,
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("scheduler_ledger_claim_unconfirmed")
        return result

    async def complete(
        self,
        item: ScheduledItem,
        *,
        status: str,
        output: Any = None,
        error_classification: str | None = None,
        error_summary: dict[str, Any] | None = None,
        catchup_state: str | None = None,
        slack_status: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "action": "complete",
            "job_key": item.job_key,
            "status": status,
            "output_identity": _fingerprint(output) if output is not None else None,
            "error_classification": error_classification,
            "error_summary": error_summary or {},
            "catchup_state": catchup_state,
            "slack_notification_status": slack_status,
            "details": {
                "trigger_reference": item.trigger_reference,
                "result": output if isinstance(output, dict) else None,
            },
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.gateway_url,
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("scheduler_ledger_completion_unconfirmed")
        return result

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                self.gateway_url,
                headers=self.headers,
                params={"limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
        return list(payload.get("runs") or [])


class RoutedSlack:
    ROUTES = {
        "rhen-live": "SLACK_WEBHOOK_RHEN_LIVE",
        "rhen-daily": "SLACK_WEBHOOK_RHEN_DAILY",
        "rhen-research": "SLACK_WEBHOOK_RHEN_RESEARCH",
        "rhen-alerts": "SLACK_WEBHOOK_RHEN_ALERTS",
        "iren-control": "SLACK_WEBHOOK_IREN_CONTROL",
    }

    async def send(self, route: str, text: str) -> str:
        specific = os.getenv(self.ROUTES.get(route, ""), "").strip()
        fallback = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        url = specific or fallback
        if not url:
            return f"not_configured:{route}"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(url, json={"text": decorate_slack_message(text, route=route)})
                response.raise_for_status()
            return (
                f"delivered:{route}"
                if specific
                else f"delivered:fallback:{route}"
            )
        except Exception as exc:
            return f"failed:{route}:{type(exc).__name__}"


class SchedulerRuntime:
    def __init__(self):
        raw = json.loads(REGISTRY_PATH.read_text())
        self.registry = raw
        self.scheduler_version = str(raw["scheduler_version"])
        self.workflows = list(raw["workflows"])
        self.token = os.getenv("TRADING_INGEST_TOKEN", "").strip()
        self.ledger = LedgerClient(
            os.getenv("SCHEDULER_GATEWAY_URL", "").strip(),
            self.token,
        )
        self.trader_url = os.getenv(
            "RHEN_TRADER_SCHEDULER_URL",
            "http://alpaca-trader.railway.internal:8080/v1/scheduler",
        ).rstrip("/")
        self.velum_url = os.getenv(
            "VELUM_SCHEDULER_URL",
            "http://rhen-velum.railway.internal:8080/v1/scheduler",
        ).rstrip("/")
        self.research_url = os.getenv("RHEN_RESEARCH_REVIEW_URL", "").strip()
        self.research_token = os.getenv("RHEN_REVIEW_TOKEN", "").strip()
        self.tick_seconds = max(15, _env_int("SCHEDULER_TICK_SECONDS", 30))
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_tick_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.running_job: str | None = None
        self.calendar_cache: list[dict[str, Any]] = []
        self.calendar_loaded_at: datetime | None = None
        self.next_runs: dict[str, str] = {}
        self.slack = RoutedSlack()
        self.forbidden_credentials = [
            name
            for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "ADMIN_TOKEN")
            if os.getenv(name, "").strip()
        ]

    @property
    def configured(self) -> bool:
        return (
            self.ledger.configured
            and self.trader_url.startswith("http")
            and self.velum_url.startswith("http")
            and self.research_url.startswith("http")
            and bool(self.research_token)
            and not self.forbidden_credentials
        )

    @property
    def scheduler_headers(self) -> dict[str, str]:
        return {"x-anevum-scheduler-token": self.token}

    @property
    def research_headers(self) -> dict[str, str]:
        return {"x-rhen-agent-admin-token": self.research_token}

    @property
    def enabled(self) -> bool:
        if os.getenv("RHEN_UNIFIED_ROLE") == "iren":
            # In the consolidated runtime IREN is the single scheduler owner.
            # Other child processes inherit the disabled flag so there is no
            # second scheduler competing for the same durable job keys.
            return True
        raw = os.getenv("RHEN_CANONICAL_SCHEDULER_ENABLED")
        if raw is None:
            return True
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def workflow_enabled(self, workflow: dict[str, Any]) -> bool:
        # The registry remains the authoritative workflow set. In unified mode
        # IREN owns every enabled workflow. Replay/research services may be
        # represented in the registry while disabled for explicit on-demand use.
        return bool(workflow.get("enabled"))

    async def start(self) -> None:
        if not self.enabled:
            print(
                json.dumps(
                    {
                        "event": "scheduler_disabled",
                        "reason": "RHEN_CANONICAL_SCHEDULER_ENABLED=false",
                    }
                ),
                flush=True,
            )
            return
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="anevum-canonical-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=15)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.tick()
                self.last_error = None
                self.last_success_at = datetime.now(UTC)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(
                    json.dumps(
                        {
                            "event": "scheduler_tick_failed",
                            "at": _iso(datetime.now(UTC)),
                            "error": self.last_error,
                        }
                    ),
                    flush=True,
                )
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.tick_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def calendar(self, now: datetime) -> list[dict[str, Any]]:
        if (
            self.calendar_cache
            and self.calendar_loaded_at is not None
            and now - self.calendar_loaded_at < timedelta(minutes=10)
        ):
            return self.calendar_cache
        start = (now.astimezone(NY).date() - timedelta(days=5)).isoformat()
        end = (now.astimezone(NY).date() + timedelta(days=14)).isoformat()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.trader_url + "/calendar",
                headers=self.scheduler_headers,
                params={"start": start, "end": end},
            )
            response.raise_for_status()
            payload = response.json()
        sessions = list(payload.get("sessions") or [])
        if not sessions:
            raise RuntimeError("exchange_calendar_empty")
        self.calendar_cache = sessions
        self.calendar_loaded_at = now
        return sessions

    def _items_for(
        self,
        workflow: dict[str, Any],
        sessions: list[dict[str, Any]],
        now: datetime,
    ) -> list[ScheduledItem]:
        schedule_type = workflow["schedule_type"]
        if schedule_type == "market_session_offset":
            return market_items(workflow, sessions)
        if schedule_type in {"hourly", "interval"}:
            return [rolling_item(workflow, now)]
        raise ValueError("unsupported_schedule_type")

    def _next_for(
        self,
        workflow: dict[str, Any],
        sessions: list[dict[str, Any]],
        now: datetime,
    ) -> ScheduledItem | None:
        if workflow["schedule_type"] in {"hourly", "interval"}:
            return next_rolling_item(workflow, now)
        future = [
            item
            for item in market_items(workflow, sessions)
            if item.scheduled_at > now
        ]
        return min(future, key=lambda item: item.scheduled_at) if future else None

    async def tick(self, now: datetime | None = None) -> None:
        if not self.configured:
            raise RuntimeError(
                "scheduler_not_configured"
                + (
                    ":forbidden_credentials=" + ",".join(self.forbidden_credentials)
                    if self.forbidden_credentials
                    else ""
                )
            )
        current = (now or datetime.now(UTC)).astimezone(UTC)
        self.last_tick_at = current
        active_workflows = [w for w in self.workflows if self.workflow_enabled(w)]
        sessions = await self.calendar(current) if any(w.get("market_calendar_dependency") for w in active_workflows) else []

        for workflow in self.workflows:
            if not self.workflow_enabled(workflow):
                continue
            next_item = self._next_for(workflow, sessions, current)
            if next_item is not None:
                self.next_runs[workflow["workflow_id"]] = _iso(next_item.scheduled_at)

            items = self._items_for(workflow, sessions, current)
            for item in items:
                if item.scheduled_at > current:
                    continue
                age_minutes = (current - item.scheduled_at).total_seconds() / 60
                stale_after = int(workflow.get("stale_after_minutes") or 60)
                reconciliation_horizon = max(stale_after, 24 * 60)
                if age_minutes > reconciliation_horizon:
                    continue
                catchup = workflow.get("catchup_policy")
                if catchup == "latest_only":
                    await self._process(item, current)
                    continue
                await self._process(item, current)

    async def _process(self, item: ScheduledItem, now: datetime) -> None:
        workflow = item.workflow
        age_minutes = max(0, (now - item.scheduled_at).total_seconds() / 60)
        stale_after = int(workflow.get("stale_after_minutes") or 60)
        catchup_policy = str(workflow.get("catchup_policy") or "skip_after_window")

        terminal_without_run: str | None = None
        catchup_state: str | None = None
        if age_minutes > stale_after:
            terminal_without_run = (
                "MISSED" if catchup_policy == "skip_after_window" else "STALE"
            )
            catchup_state = (
                "window_missed"
                if terminal_without_run == "MISSED"
                else "catchup_expired"
            )
        elif age_minutes > float(workflow.get("recovery_after_minutes") or 5):
            catchup_state = "catchup"

        claim = await self.ledger.claim(
            item,
            scheduler_version=self.scheduler_version,
            catchup_state=catchup_state,
        )
        if not claim.get("claimed"):
            return

        if terminal_without_run:
            # Historical startup reconciliation belongs in the durable ledger,
            # not in Slack. Only a recently missed operational window is still
            # actionable enough to alert an operator.
            historical_alert_minutes = max(
                30,
                int(os.getenv("SCHEDULER_HISTORICAL_ALERT_MINUTES", "120")),
            )
            if age_minutes > historical_alert_minutes:
                slack_status = "suppressed:historical_reconciliation"
            else:
                slack_status = await self._notify(
                    item,
                    status=terminal_without_run,
                    result=None,
                    error=catchup_state,
                )
            await self.ledger.complete(
                item,
                status=terminal_without_run,
                error_classification="stale_execution",
                error_summary={
                    "age_minutes": round(age_minutes, 2),
                    "policy": catchup_policy,
                },
                catchup_state=catchup_state,
                slack_status=slack_status,
            )
            return

        self.running_job = item.job_key
        try:
            result = await self._execute(item)
            slack_status = await self._notify(
                item,
                status="SUCCEEDED",
                result=result,
                error=None,
            )
            await self.ledger.complete(
                item,
                status="SUCCEEDED",
                output=result,
                catchup_state=catchup_state,
                slack_status=slack_status,
            )
            print(
                json.dumps(
                    {
                        "event": "scheduler_job_completed",
                        "workflow_id": workflow["workflow_id"],
                        "job_key": item.job_key,
                        "scheduled_at": _iso(item.scheduled_at),
                        "catchup_state": catchup_state,
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            classification = self._classify_failure(exc)
            slack_status = await self._notify(
                item,
                status="FAILED",
                result=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            await self.ledger.complete(
                item,
                status="FAILED",
                error_classification=classification,
                error_summary={
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                },
                catchup_state=catchup_state,
                slack_status=slack_status,
            )
            print(
                json.dumps(
                    {
                        "event": "scheduler_job_failed",
                        "workflow_id": workflow["workflow_id"],
                        "job_key": item.job_key,
                        "classification": classification,
                        "error_type": type(exc).__name__,
                    }
                ),
                flush=True,
            )
        finally:
            self.running_job = None

    async def _execute(self, item: ScheduledItem) -> dict[str, Any]:
        target = item.workflow["implementation_target"]
        session = item.details.get("session") or item.trigger_reference

        if target == "graen_btc_discovery":
            return await self._post(
                os.getenv("GRAEN_BTC_DISCOVERY_URL", "http://127.0.0.1:8111/v1/btc-discovery/tick"),
                self.scheduler_headers, {}, timeout=600)

        if target == "trader_preflight":
            local = await self._post_with_startup_retry(
                self.trader_url + "/preflight",
                self.scheduler_headers,
                {"session": session, "scheduled_at": _iso(item.scheduled_at)},
                attempts=4,
                delay_seconds=2.0,
            )
            dependencies = await self._dependency_health()
            blocking = sorted(
                name
                for name, row in dependencies.items()
                if row.get("required_for_preflight") and not row.get("ok")
            )
            if blocking:
                raise RuntimeError(
                    "preflight_dependency_unhealthy:" + ",".join(blocking)
                )
            return {"local": local, "dependencies": dependencies}

        if target == "trader_market_open":
            return await self._post(
                self.trader_url + "/market-open",
                self.scheduler_headers,
                {"session": session, "scheduled_at": _iso(item.scheduled_at)},
            )

        if target == "trader_session_close":
            return await self._post(
                self.trader_url + "/session-close",
                self.scheduler_headers,
                {"session": session, "scheduled_at": _iso(item.scheduled_at)},
                timeout=300,
            )

        if target == "research_agent_daily":
            # Scheduled research must remain functional without a paid model API.
            # Semantic/model review stays available for explicit operator-driven
            # work, but the daily operating workflow is deterministic by default.
            return await self._research_review("daily", str(session), invoke_model=False)

        if target == "weekly_operating_review":
            operating = await self._post(
                self.trader_url + "/weekly-review",
                self.scheduler_headers,
                {"session": session, "scheduled_at": _iso(item.scheduled_at)},
                timeout=300,
            )
            research = await self._research_review(
                "weekly",
                str(session),
                invoke_model=False,
            )
            return {
                "operating_review": operating,
                "research_review": research,
                "system_state": await self._weekly_system_state(),
            }

        if target == "velum_equity":
            return await self._post(
                self.velum_url + "/equity",
                self.scheduler_headers,
                {"session": session, "scheduled_at": _iso(item.scheduled_at)},
                timeout=1200,
            )

        if target == "velum_crypto":
            return await self._post(
                self.velum_url + "/crypto",
                self.scheduler_headers,
                {
                    "window_end": item.details["window_end"],
                    "scheduled_at": _iso(item.scheduled_at),
                },
                timeout=1200,
            )

        if target == "graen_checkpoint":
            base = self.research_url.split("/v1/", 1)[0]
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    base + "/v1/status",
                    headers=self.research_headers,
                )
                response.raise_for_status()
                payload = response.json()
            return {
                "research_only": True,
                "production_authority": False,
                "mathematics_and_theory": payload.get("mathematics_and_theory"),
                "research_readiness": payload.get("research_readiness"),
            }

        if target == "scheduler_self_health":
            return {
                "scheduler_version": self.scheduler_version,
                "configured": self.configured,
                "running_job": self.running_job,
                "next_runs": self.next_runs,
            }

        raise RuntimeError("unsupported_implementation_target")

    async def _weekly_system_state(self) -> dict[str, Any]:
        dependencies = await self._dependency_health()
        recent = await self.ledger.recent(limit=250)
        failures = [
            row for row in recent
            if row.get("status") in {"FAILED", "MISSED", "STALE"}
        ]
        successes = [row for row in recent if row.get("status") == "SUCCEEDED"]

        velum_status: dict[str, Any]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    self.velum_url.split("/v1/scheduler", 1)[0] + "/status"
                )
                response.raise_for_status()
                velum_status = response.json()
        except Exception as exc:
            velum_status = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        nostra = next(
            (
                dict(row)
                for row in self.registry.get("independent_runtimes", [])
                if row.get("subsystem") == "NOSTRA"
            ),
            {
                "subsystem": "NOSTRA",
                "mode": "embedded_autorun",
                "scheduler_owned": False,
                "independent_runtime": False,
                "health_owner": "IREN",
                "research_only": True,
                "execution_authority": False,
                "status": "UNKNOWN",
            },
        )
        return {
            "scheduler_version": self.scheduler_version,
            "dependency_health": dependencies,
            "scheduler_reliability": {
                "recent_run_count": len(recent),
                "successful_run_count": len(successes),
                "failed_missed_or_stale_count": len(failures),
                "recent_failures": failures[:20],
            },
            "velum": velum_status,
            "nostra": nostra,
            "disabled_workflows": [
                row["workflow_id"]
                for row in self.workflows
                if not row.get("enabled")
            ],
            "protected_actions": {
                "live_strategy_mutation": False,
                "risk_mutation": False,
                "capital_allocation_mutation": False,
                "crypto_live_activation": False,
            },
        }


    async def _research_review(
        self,
        cadence: str,
        session: str,
        *,
        invoke_model: bool,
    ) -> dict[str, Any]:
        ready_url = self.research_url.rsplit("/", 1)[0] + "/readiness"
        async with httpx.AsyncClient(timeout=httpx.Timeout(270, connect=10)) as client:
            ready = await client.get(
                ready_url,
                params={"cadence": cadence},
                headers=self.research_headers,
            )
            ready.raise_for_status()
            readiness = ready.json()
            if readiness.get("trigger_reference") != session:
                raise RuntimeError("canonical_report_not_current")
            response = await client.post(
                self.research_url,
                headers=self.research_headers,
                json={
                    "cadence": cadence,
                    "invoke_model": invoke_model,
                    "persist": True,
                    "expected_session": session,
                },
            )
            response.raise_for_status()
            result = response.json()
        if (
            result.get("status") not in TERMINAL_OK
            or not (
                result.get("persisted") is True
                or result.get("reason") == "duplicate_run_key"
            )
        ):
            raise RuntimeError("research_completion_unconfirmed")
        return result

    async def _dependency_health(self) -> dict[str, dict[str, Any]]:
        urls = {
            "rhen": self.trader_url.split("/v1/scheduler", 1)[0] + "/health",
            "velum": self.velum_url.split("/v1/scheduler", 1)[0] + "/health",
            "research_agent": os.getenv(
                "IREN_RESEARCH_AGENT_HEALTH_URL",
                self.research_url.rsplit("/v1/research/review", 1)[0]
                + "/v1/research/health",
            ),
        }
        required_for_preflight = {
            "rhen": True,
            "velum": False,
            # The research agent is intentionally sleep-capable/on-demand.
            # Its availability is reported, but it must not gate market preflight.
            "research_agent": False,
        }
        output: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for name, url in urls.items():
                try:
                    response = await client.get(url)
                    payload = response.json() if response.content else {}
                    output[name] = {
                        "ok": response.is_success and payload.get("ok", True) is not False,
                        "http_status": response.status_code,
                        "required_for_preflight": required_for_preflight[name],
                    }
                except Exception as exc:
                    output[name] = {
                        "ok": False,
                        "error": type(exc).__name__,
                        "required_for_preflight": required_for_preflight[name],
                    }
        return output

    async def _post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float = 90,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("invalid_dependency_response")
        return result

    async def _post_with_startup_retry(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        attempts: int = 4,
        delay_seconds: float = 2.0,
        timeout: float = 90,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                return await self._post(url, headers, payload, timeout=timeout)
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt >= attempts:
                    raise
                last_error = exc
                await asyncio.sleep(delay_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("startup_retry_exhausted")

    async def _notify(
        self,
        item: ScheduledItem,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> str:
        policy = item.workflow.get("notification_policy") or {}
        if status == "SUCCEEDED" and not policy.get("success"):
            return "suppressed:success"
        route = (
            str(policy.get("success_route"))
            if status == "SUCCEEDED"
            else str(policy.get("failure_route"))
        )
        if not route or route == "None":
            return "suppressed:no_route"
        suffix = ""
        if status in {"FAILED", "MISSED", "STALE"} and error:
            suffix = "\n" + error[:600]
        subsystem = str(item.workflow.get("subsystem") or "IREN").upper()
        text = (
            f"*{subsystem} // {item.workflow['workflow_id']} // {status}*\n"
            f"scheduled by IREN: {_iso(item.scheduled_at)} | "
            f"reference: {item.trigger_reference}{suffix}"
        )
        return await self.slack.send(route, text)

    @staticmethod
    def _classify_failure(exc: Exception) -> str:
        if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
            return "transient_infrastructure"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in {401, 403}:
                return "authentication"
            if code == 429 or code >= 500:
                return "dependency_unavailable"
            if code == 409:
                return "invalid_state"
            if code == 422:
                return "configuration"
        if isinstance(exc, ValueError):
            return "configuration"
        if "evidence" in str(exc).lower() or "report_not_current" in str(exc).lower():
            return "evidence_unavailable"
        return "code_defect"

    async def diagnostics(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        sessions = await self.calendar(now)
        next_runs: dict[str, str | None] = {}
        for workflow in self.workflows:
            if not workflow.get("enabled"):
                continue
            item = self._next_for(workflow, sessions, now)
            next_runs[workflow["workflow_id"]] = (
                _iso(item.scheduled_at) if item else None
            )
        return {
            "scheduler_version": self.scheduler_version,
            "registry_schema_version": self.registry.get("schema_version"),
            "timezone": self.registry.get("timezone"),
            "timestamp_storage": self.registry.get("timestamp_storage"),
            "configured": self.configured,
            "forbidden_credentials_present": self.forbidden_credentials,
            "next_expected_runs": next_runs,
            "active_workflows": [
                row["workflow_id"] for row in self.workflows if row.get("enabled")
            ],
            "disabled_workflows": [
                row["workflow_id"] for row in self.workflows if not row.get("enabled")
            ],
        }

    async def status(self) -> dict[str, Any]:
        runs = await self.ledger.recent(limit=100) if self.ledger.configured else []
        success = [row for row in runs if row.get("status") == "SUCCEEDED"]
        failed = [
            row
            for row in runs
            if row.get("status") in {"FAILED", "MISSED", "STALE"}
        ]
        return {
            **(await self.diagnostics()),
            "started_at": _iso(self.started_at),
            "last_tick_at": _iso(self.last_tick_at) if self.last_tick_at else None,
            "last_success_at": _iso(self.last_success_at) if self.last_success_at else None,
            "last_error": self.last_error,
            "running_job": self.running_job,
            "last_successful_runs": success[:20],
            "last_failed_runs": failed[:20],
            "recent_runs": runs,
        }


runtime = SchedulerRuntime()


def _require_scheduler_token(value: str | None) -> None:
    expected = runtime.token
    if not expected:
        raise HTTPException(status_code=503, detail="scheduler token not configured")
    if value is None or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM Canonical Scheduler", lifespan=lifespan)


@app.get("/health")
async def health():
    healthy = runtime.configured and runtime.last_error is None
    body = {
        "ok": healthy,
        "system": "IREN",
        "service": "anevum-canonical-scheduler",
        "scheduler_version": runtime.scheduler_version,
        "configured": runtime.configured,
        "last_tick_at": _iso(runtime.last_tick_at) if runtime.last_tick_at else None,
        "last_success_at": _iso(runtime.last_success_at) if runtime.last_success_at else None,
        "last_error": runtime.last_error,
        "running_job": runtime.running_job,
    }
    if not healthy:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/v1/diagnostics")
async def diagnostics():
    return await runtime.diagnostics()


@app.get("/v1/status")
async def status(x_anevum_scheduler_token: str | None = Header(default=None)):
    _require_scheduler_token(x_anevum_scheduler_token)
    return await runtime.status()


@app.get("/v1/registry")
async def registry():
    return runtime.registry
