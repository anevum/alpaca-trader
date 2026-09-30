from __future__ import annotations

from typing import Any


class NostraLedger:
    """Durable append-only transport through RHEN's existing event ingest.

    Forecasts and their point-in-time snapshots use the critical persistence path.
    If durable ingest is unavailable, this class returns False rather than
    pretending the evidence was recorded.
    """

    RECORDS = {
        "snapshot": ("nostra_snapshot", "snapshot_id", "as_of_timestamp"),
        "forecast": ("nostra_forecast", "forecast_id", "generated_at"),
        "outcome": ("nostra_outcome", "outcome_id", "observed_at"),
        "score": ("nostra_score", "score_id", None),
    }

    def __init__(self, event_sink: Any):
        self.event_sink = event_sink

    async def _append(
        self,
        kind: str,
        record: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        if kind not in self.RECORDS:
            raise ValueError(f"unsupported NOSTRA ledger record: {kind}")
        if not bool(getattr(self.event_sink, "enabled", False)):
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
            else None
        )
        return bool(
            await self.event_sink.emit_critical(
                event_type=event_type,
                payload=record,
                symbol=str(record.get("symbol") or ""),
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                event_key=f"nostra:{event_type}:{record_id}",
            )
        )

    async def append_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        return await self._append("snapshot", snapshot, correlation_id=correlation_id)

    async def append_forecast(
        self,
        forecast: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        return await self._append("forecast", forecast, correlation_id=correlation_id)

    async def append_outcome(
        self,
        outcome: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        return await self._append("outcome", outcome, correlation_id=correlation_id)

    async def append_score(
        self,
        score: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> bool:
        return await self._append("score", score, correlation_id=correlation_id)
