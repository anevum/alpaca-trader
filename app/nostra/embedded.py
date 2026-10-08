from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .ledger import NostraLedger


UTC = timezone.utc


class CoreNostraGateway:
    """Direct, broker-isolated NOSTRA read adapter over RHEN Core."""

    configured = True

    def __init__(self, store: Any) -> None:
        self.store = store

    async def work(self) -> dict[str, Any]:
        body = await asyncio.to_thread(self.store.nostra_work)
        if (
            not isinstance(body, dict)
            or body.get("ok") is not True
            or body.get("research_only") is not True
            or body.get("execution_authority") is not False
        ):
            raise RuntimeError("embedded NOSTRA gateway violated authority boundary")
        return body


class CoreNostraLedger:
    """Append NOSTRA evidence directly to the owning RHEN Core store."""

    RECORDS = NostraLedger.RECORDS
    configured = True

    def __init__(self, store: Any) -> None:
        self.store = store

    async def _append(
        self,
        kind: str,
        record: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        if kind not in self.RECORDS:
            raise ValueError(f"unsupported NOSTRA ledger record: {kind}")

        event_type, id_field, time_field = self.RECORDS[kind]
        record_id = str(record.get(id_field) or "").strip()
        if not record_id:
            raise ValueError(f"{id_field} is required")
        if record.get("execution_authority") is not False:
            raise ValueError(
                "NOSTRA evidence must explicitly have no execution authority"
            )
        if record.get("research_only") is not True:
            raise ValueError("NOSTRA evidence must be research-only")

        occurred_at = (
            str(record.get(time_field))
            if time_field and record.get(time_field)
            else datetime.now(UTC).isoformat()
        )
        event = {
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
        result = await asyncio.to_thread(self.store.ingest_events, [event])
        return bool(isinstance(result, dict) and result.get("ok") is True)

    async def append_snapshot(
        self, record: dict[str, Any], *, correlation_id: str | None = None
    ) -> bool:
        return await self._append(
            "snapshot", record, correlation_id=correlation_id
        )

    async def append_forecast(
        self, record: dict[str, Any], *, correlation_id: str | None = None
    ) -> bool:
        return await self._append(
            "forecast", record, correlation_id=correlation_id
        )

    async def append_outcome(
        self, record: dict[str, Any], *, correlation_id: str | None = None
    ) -> bool:
        return await self._append(
            "outcome", record, correlation_id=correlation_id
        )

    async def append_score(
        self, record: dict[str, Any], *, correlation_id: str | None = None
    ) -> bool:
        return await self._append(
            "score", record, correlation_id=correlation_id
        )

    async def append_evaluation(
        self, record: dict[str, Any], *, correlation_id: str | None = None
    ) -> bool:
        return await self._append(
            "evaluation", record, correlation_id=correlation_id
        )
