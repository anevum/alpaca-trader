from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .config import Settings


class TradingEventSink:
    """Bounded, non-blocking delivery of trading telemetry to ANEVUM storage."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_sent_at: datetime | None = None
        self.last_error: str | None = None
        self.sent_count = 0
        self.dropped_count = 0

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.trading_ingest_url
            and self.settings.trading_ingest_token
            and self.settings.trading_run_id
            and self.settings.strategy_version_id
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "queued": self.queue.qsize(),
            "sent_count": self.sent_count,
            "dropped_count": self.dropped_count,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "last_error": self.last_error,
            "run_id": self.settings.trading_run_id or None,
            "strategy_version_id": self.settings.strategy_version_id or None,
        }

    async def start(self) -> None:
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            self.task = None

    def emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        event = {
            "event_key": f"{self.settings.trading_run_id}:{event_type}:{uuid4().hex}",
            "run_id": self.settings.trading_run_id,
            "strategy_version_id": self.settings.strategy_version_id,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "symbol": symbol or None,
            "correlation_id": correlation_id,
            "source": "alpaca-trader",
            "payload": payload or {},
        }
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_count += 1
            self.last_error = "event queue full; telemetry event dropped"

    async def _send_batch(
        self,
        http: httpx.AsyncClient,
        events: list[dict[str, Any]],
    ) -> bool:
        try:
            response = await http.post(
                self.settings.trading_ingest_url,
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={"events": events},
            )
            response.raise_for_status()
            self.sent_count += len(events)
            self.last_sent_at = datetime.now(timezone.utc)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    async def _run(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as http:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    first = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                batch = [first]
                while len(batch) < 50:
                    try:
                        batch.append(self.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                delivered = False
                for attempt in range(3):
                    if await self._send_batch(http, batch):
                        delivered = True
                        break
                    await asyncio.sleep(0.25 * (2 ** attempt))

                for _ in batch:
                    self.queue.task_done()

                if not delivered:
                    self.dropped_count += len(batch)
