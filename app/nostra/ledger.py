from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import httpx


UTC = timezone.utc


class NostraLedger:
    """Append-only NOSTRA evidence transport through ANEVUM Foundation."""

    RECORDS = {
        "snapshot": ("nostra_snapshot", "snapshot_id", "as_of_timestamp"),
        "forecast": ("nostra_forecast", "forecast_id", "generated_at"),
        "outcome": ("nostra_outcome", "outcome_id", "observed_at"),
        "score": ("nostra_score", "score_id", None),
    }

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.url = (
            url
            if url is not None
            else os.getenv("FOUNDATION_EVENTS_URL", "")
        ).strip()
        self.token = (
            token
            if token is not None
            else os.getenv("FOUNDATION_INGEST_TOKEN", "")
        ).strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self.url.startswith("http") and len(self.token) >= 32

    async def _append(
        self,
        kind: str,
        record: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        if kind not in self.RECORDS:
            raise ValueError(f"unsupported NOSTRA ledger record: {kind}")
        if not self.configured:
            return False

        event_type, id_field, time_field = self.RECORDS[kind]
        record_id = str(record.get(id_field) or "").strip()
        if not record_id:
            raise ValueError(f"{id_field} is required")
        if record.get("execution_authority") is not False:
            raise ValueError("NOSTRA evidence must explicitly have no execution authority")
        if record.get("research_only") is not True:
            raise ValueError("NOSTRA evidence must be research-only")

        occurred_at = (
            str(record.get(time_field))
            if time_field and record.get(time_field)
            else datetime.now(UTC).isoformat()
        )
        payload = {
            "events": [
                {
                    "event_key": f"nostra:{event_type}:{record_id}",
                    "run_id": record.get("run_id"),
                    "strategy_version_id": record.get("strategy_version_id"),
                    "event_type": event_type,
                    "occurred_at": occurred_at,
                    "symbol": str(record.get("symbol") or "") or None,
                    "correlation_id": correlation_id,
                    "source": "NOSTRA",
                    "payload": record,
                }
            ]
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-anevum-foundation-token": self.token,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception:
            return False
        return bool(isinstance(body, dict) and body.get("ok") is True)

    async def append_snapshot(self, record: dict[str, Any], *, correlation_id: str | None = None) -> bool:
        return await self._append("snapshot", record, correlation_id=correlation_id)

    async def append_forecast(self, record: dict[str, Any], *, correlation_id: str | None = None) -> bool:
        return await self._append("forecast", record, correlation_id=correlation_id)

    async def append_outcome(self, record: dict[str, Any], *, correlation_id: str | None = None) -> bool:
        return await self._append("outcome", record, correlation_id=correlation_id)

    async def append_score(self, record: dict[str, Any], *, correlation_id: str | None = None) -> bool:
        return await self._append("score", record, correlation_id=correlation_id)
