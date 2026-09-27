"""Network adapters and one deterministic Agent Support evaluation.

Only the dedicated gateway may access the database. Railway observations must
come from a separately authorized, live observer; manual runs supply a
connector-observed bundle. This module has no broker or model imports.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import httpx

from .adapter import from_canonical_sources
from .escalation import reconcile
from .railway import load_role_map
from .snapshot import build_snapshot

NY = ZoneInfo("America/New_York")


def isolation_violations(environment: dict[str, str] | None = None) -> list[str]:
    env = environment if environment is not None else os.environ
    return sorted(key for key, value in env.items() if value and (
        key.startswith(("ALPACA_", "OPENAI_"))
        or key in {"ADMIN_TOKEN", "TRADING_INGEST_TOKEN", "BOT_ARMED",
                   "EXECUTION_ENABLED", "LIVE_TRADING", "I_ACKNOWLEDGE_LIVE_TRADING",
                   "SUPABASE_DB_URL", "SUPABASE_SERVICE_ROLE_KEY"}
    ))


def sessions(now: datetime) -> list[str]:
    local = now.astimezone(NY).date()
    start = local - timedelta(days=10)
    return [item.date().isoformat() for item in
            xcals.get_calendar("XNYS").sessions_in_range(start.isoformat(), local.isoformat())]


def week_end(now: datetime) -> str:
    local = now.astimezone(NY).date()
    return (local - timedelta(days=(local.weekday() - 4) % 7)).isoformat()


class SupportRuntime:
    def __init__(self, client: httpx.AsyncClient, *,
                 gateway_url: str, read_token: str, write_token: str,
                 research_url: str, preopen_url: str):
        self.client = client
        self.gateway_url = gateway_url.rstrip("/")
        self.read_token = read_token
        self.write_token = write_token
        self.research_url = research_url.rstrip("/")
        self.preopen_url = preopen_url.rstrip("/")

    async def _gateway(self, view: str, *, actions: list[dict] | None = None) -> Any:
        headers = {"x-rhen-support-token": self.write_token if actions is not None else self.read_token}
        if actions is None:
            reply = await self.client.get(self.gateway_url, params={"view": view}, headers=headers)
        else:
            reply = await self.client.post(self.gateway_url, params={"view": view},
                                           headers=headers, json=actions)
        reply.raise_for_status()
        payload = reply.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("support gateway contract unavailable")
        return payload.get("applied") if actions is not None else payload.get("data")

    async def evaluate(self, *, railway_status: list[dict],
                       railway_configs: dict[str, dict], persist: bool,
                       now: datetime | None = None) -> dict[str, Any]:
        if isolation_violations():
            raise RuntimeError("support runtime isolation violation")
        if not all((self.gateway_url, self.read_token, self.write_token,
                    self.research_url, self.preopen_url)):
            raise RuntimeError("support runtime sources are not configured")
        now = now or datetime.now(timezone.utc)
        mapping = load_role_map()
        # Every source is read for each run. An unavailable critical source
        # prevents writes and therefore cannot falsely resolve an open alert.
        bounded = await self._gateway("evidence")
        alerts = await self._gateway("alerts")
        research = await self.client.get(f"{self.research_url}/v1/readiness/public")
        research.raise_for_status()
        preopen = await self.client.get(f"{self.preopen_url}/health")
        preopen.raise_for_status()
        status = await self.client.get(f"{self.preopen_url}/v1/status")
        status.raise_for_status()
        if not isinstance(bounded, dict) or bounded.get("evidence_version") != "rhen-support-evidence-v1":
            raise ValueError("bounded canonical evidence is malformed")
        if not isinstance(alerts, list) or not all(isinstance(row, dict) for row in alerts):
            raise ValueError("open support alert state is malformed")
        preopen_health = preopen.json()
        preopen_status = status.json()
        if not isinstance(preopen_health, dict) or not isinstance(preopen_status, dict):
            raise ValueError("preopen state is malformed")
        preopen_health["last_snapshot"] = (preopen_status.get("runtime") or {}).get("last_snapshot")
        local = now.astimezone(NY)
        due_end = week_end(now)
        period_inputs = bounded["period_inputs"]
        first_session = period_inputs.get("earliest_daily_session")
        if not isinstance(first_session, str) or len(first_session) != 10:
            raise ValueError("canonical daily coverage start malformed")
        expected_sessions = [day for day in sessions(now)
                             if day >= first_session]
        evidence = from_canonical_sources(
            command_evidence=bounded["command_evidence"],
            period_inputs=period_inputs, expected_sessions=expected_sessions,
            railway_status=railway_status, railway_configs=railway_configs,
            railway_project_id=mapping["project_id"],
            railway_environment_id=mapping["environment_id"],
            research_readiness=research.json(), preopen_health=preopen_health,
            preopen_expected_after=None,
            market_session_active=(local.weekday() < 5 and
                                   local.replace(hour=9, minute=30) <= local <
                                   local.replace(hour=16, minute=0)),
            weekly_report_due=(local.date().isoformat() >= due_end and
                               local.weekday() >= 4 and local.hour >= 18),
            expected_week_end=due_end,
        )
        snapshot = build_snapshot(evidence, now=now)
        open_alerts = {row["alert_key"]: row for row in alerts}
        reasons = snapshot["integrity"]["reasons"]
        unavailable = {row["source"] for row in reasons
                       if any(part in row["code"] for part in
                              ("UNAVAILABLE", "MALFORMED", "EVIDENCE_MISSING",
                               "SCHEMA_DRIFT", "TIME_INVALID"))}
        assessed = {"railway", "research_agent", "runtime", "telemetry",
                    "preopen", "reports", "research", "context"} - unavailable
        actions = reconcile(reasons, open_alerts, observed_at=now, assessed_sources=assessed)
        applied = None
        if persist and actions:
            applied = await self._gateway("alerts", actions=actions)
            if applied != len(actions):
                raise RuntimeError("support action count mismatch")
        return {"schema_version": "rhen-support-run-v1", "observed_at": now.isoformat(),
                "persisted": persist, "integrity": snapshot["integrity"],
                "context": snapshot, "proposed_actions": actions,
                "applied_actions": applied or 0,
                "open_alert_count_before": len(open_alerts)}
