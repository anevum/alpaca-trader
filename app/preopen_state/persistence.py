from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import PreOpenSettings


class PreOpenEventSink:
    def __init__(self, settings: PreOpenSettings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.persistence_enabled

    async def emit(
        self,
        *,
        event_type: str,
        event_key: str,
        payload: dict[str, Any],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "persistence_not_configured"}
        event = {
            "event_key": event_key,
            "run_id": None,
            "strategy_version_id": None,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now().astimezone().isoformat(),
            "symbol": None,
            "correlation_id": event_key,
            "source": "rhen-preopen-state",
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(
                self.settings.trading_ingest_url,
                headers={
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                    "content-type": "application/json",
                },
                json={"events": [event]},
            )
            response.raise_for_status()
            body = response.json()
            return {
                "ok": bool(body.get("ok")),
                "received": body.get("received"),
                "inserted": body.get("inserted"),
            }
