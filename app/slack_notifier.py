from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx


class SlackNotifier:
    """Best-effort, non-blocking RHEN -> Slack incoming-webhook delivery."""

    def __init__(self, settings: Any):
        self.webhook_url = str(getattr(settings, "slack_webhook_url", "") or "").strip()
        self.timeout_seconds = float(
            getattr(settings, "slack_webhook_timeout_seconds", 5.0)
        )
        self.enabled = bool(self.webhook_url)
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._last_market_open: bool | None = None
        self._last_reconciliation_action: str | None = None
        self._dedupe: dict[str, float] = {}
        self.last_error: str | None = None
        self.dropped_messages = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "queue_depth": self._queue.qsize(),
            "dropped_messages": self.dropped_messages,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task is None:
            return
        async def drain():
            await self._queue.put(None)
            await self._task
        try:
            await asyncio.wait_for(drain(), timeout=15)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def notify_runtime_start(
        self,
        *,
        trading_mode: str,
        strategy_name: str,
        strategy_version_id: str,
        execution_authorized: bool,
    ) -> None:
        self._enqueue(
            (
                "*RHEN // ONLINE*\n"
                f"mode: `{trading_mode}` | strategy: `{strategy_name}` | "
                f"version: `{strategy_version_id or 'unversioned'}` | "
                f"execution: `{'authorized' if execution_authorized else 'not-authorized'}`"
            ),
            key=f"runtime:{trading_mode}:{strategy_name}:{strategy_version_id}:{execution_authorized}",
        )

    def observe_market_state(
        self,
        is_open: bool,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        if not self.enabled:
            return
        stamp = (observed_at or datetime.now(timezone.utc)).isoformat()
        previous = self._last_market_open
        self._last_market_open = is_open

        if previous is None:
            if is_open:
                self._enqueue(
                    f"*RHEN // MARKET OPEN*\n{stamp} | live session detected; scanner/execution loop active"
                )
            return
        if previous == is_open:
            return
        if is_open:
            self._enqueue(
                f"*RHEN // MARKET OPEN*\n{stamp} | regular session transition detected"
            )
        else:
            self._enqueue(
                f"*RHEN // MARKET CLOSED*\n{stamp} | regular session transition detected"
            )

    def record_event(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return

        kind = str(event.get("kind") or "")
        action = str(event.get("action") or "")
        if kind == "reconciliation":
            if action == self._last_reconciliation_action:
                return
            self._last_reconciliation_action = action

        important = (
            (kind == "execution" and action in {"buy", "sell", "recovered", "blocked", "warning", "error"})
            or (kind == "protection" and action in {"activated", "ratchet", "stop", "recovered", "warning", "error"})
            or (kind == "reconciliation" and action in {"safe", "blocked", "error"})
            or (kind == "persistence" and action in {"blocked", "warning", "error"})
            or (kind == "control" and action in {"entries_disabled", "entries_enabled", "cancel_orders"})
            or (kind == "risk" and action in {"blocked", "warning"})
            or (kind == "runtime" and action in {"warning", "error"})
            or (
                kind in {
                    "crypto_runtime",
                    "crypto_runtime_error",
                    "crypto_execution",
                    "crypto_protection",
                    "crypto_evidence",
                    "crypto_promotion",
                    "crypto_breaker",
                }
                and action in {
                    "startup",
                    "buy",
                    "sell",
                    "submitted",
                    "activated",
                    "promotion_ready",
                    "blocked",
                    "warning",
                    "error",
                    "recovered",
                }
            )
            or (kind in {"research_agent", "research_reporting", "research_scheduler", "preopen_state"}
                and action in {"completed", "recovered", "warning", "error"})
            or (
                kind == "asc"
                and action in {
                    "normal",
                    "adapt",
                    "research",
                    "defensive",
                    "promotion_ready",
                }
            )
        )
        if not important:
            return

        symbol = str(event.get("symbol") or "").upper()
        message = str(event.get("message") or event.get("reason") or "").strip()
        at = str(event.get("at") or "")
        label = f"{kind.upper()} {action.upper()}".strip()
        symbol_text = f" | `{symbol}`" if symbol else ""
        key = None if kind == "reconciliation" else f"event:{kind}:{action}:{symbol}:{message}"
        self._enqueue(
            f"*RHEN // {label}*\n{at}{symbol_text} | {message}",
            key=key,
        )

    def _enqueue(self, text: str, *, key: str | None = None) -> None:
        if not self.enabled:
            return
        if key is not None:
            now = time.monotonic()
            if key in self._dedupe and now - self._dedupe[key] < 600:
                return
            self._dedupe.pop(key, None)
            self._dedupe[key] = now
            if len(self._dedupe) > 1000:
                self._dedupe.pop(next(iter(self._dedupe)))
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            self.dropped_messages += 1
            print(
                "SLACK_WEBHOOK_DROP",
                {"dropped_messages": self.dropped_messages},
                flush=True,
            )

    async def _worker(self) -> None:
        while True:
            text = await self._queue.get()
            if text is None:
                self._queue.task_done()
                break
            try:
                if self._client is None:
                    raise RuntimeError("Slack webhook client is not started")
                for attempt in range(3):
                    try:
                        response = await self._client.post(self.webhook_url, json={"text": text})
                        response.raise_for_status()
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                        transient = not isinstance(exc, httpx.HTTPStatusError) or (
                            exc.response.status_code == 429 or exc.response.status_code >= 500
                        )
                        if not transient or attempt == 2:
                            raise
                        await asyncio.sleep(2 ** attempt)
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
                print(
                    "SLACK_WEBHOOK_ERROR",
                    {"error": self.last_error},
                    flush=True,
                )
            finally:
                self._queue.task_done()
