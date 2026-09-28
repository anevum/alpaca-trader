from __future__ import annotations

import asyncio
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
        self._known_bot_positions: set[str] | None = None
        self._seen_fill_ids: set[str] = set()
        self._dedupe: set[str] = set()
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
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            await self._queue.put(None)
        await self._task
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
        )
        if not important:
            return

        symbol = str(event.get("symbol") or "").upper()
        message = str(event.get("message") or event.get("reason") or "").strip()
        at = str(event.get("at") or "")
        label = f"{kind.upper()} {action.upper()}".strip()
        symbol_text = f" | `{symbol}`" if symbol else ""
        key = f"event:{kind}:{action}:{symbol}:{message}"
        self._enqueue(
            f"*RHEN // {label}*\n{at}{symbol_text} | {message}",
            key=key,
        )

    def observe_broker_snapshot(
        self,
        *,
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        observed_at: datetime | None = None,
    ) -> None:
        if not self.enabled:
            return

        bot_orders = [
            order
            for order in recent_orders
            if str(order.get("client_order_id") or "").startswith("anevum-")
        ]
        bot_order_ids = {
            str(order.get("id") or "")
            for order in bot_orders
            if order.get("id")
        }
        bot_symbols = {
            str(order.get("symbol") or "").upper()
            for order in bot_orders
            if order.get("symbol")
        }
        active_positions = {
            str(position.get("symbol") or "").upper()
            for position in positions
            if str(position.get("symbol") or "").upper() in bot_symbols
            and float(position.get("qty") or 0) != 0
        }

        fill_rows: list[tuple[str, dict[str, Any]]] = []
        for fill in fills:
            fill_id = str(fill.get("id") or fill.get("activity_id") or "")
            order_id = str(fill.get("order_id") or "")
            if not fill_id or order_id not in bot_order_ids:
                continue
            fill_rows.append((fill_id, fill))

        if self._known_bot_positions is None:
            self._known_bot_positions = set(active_positions)
            self._seen_fill_ids.update(fill_id for fill_id, _ in fill_rows)
            if active_positions:
                symbols = ", ".join(sorted(active_positions))
                self._enqueue(
                    f"*RHEN // POSITION STATE RESTORED*\nmanaged open positions: {symbols}",
                    key=f"restored:{symbols}",
                )
            return

        for fill_id, fill in fill_rows:
            if fill_id in self._seen_fill_ids:
                continue
            self._seen_fill_ids.add(fill_id)
            side = str(fill.get("side") or "").upper()
            symbol = str(fill.get("symbol") or "").upper()
            qty = str(fill.get("qty") or fill.get("quantity") or "")
            price = str(fill.get("price") or fill.get("filled_avg_price") or "")
            fill_time = str(
                fill.get("transaction_time")
                or fill.get("filled_at")
                or observed_at
                or datetime.now(timezone.utc).isoformat()
            )
            self._enqueue(
                f"*RHEN // FILL*\n{fill_time} | `{symbol}` | {side} | qty {qty or '?'} | price {price or '?'}",
                key=f"fill:{fill_id}",
            )

        opened = active_positions - self._known_bot_positions
        closed = self._known_bot_positions - active_positions
        stamp = (observed_at or datetime.now(timezone.utc)).isoformat()
        for symbol in sorted(opened):
            self._enqueue(
                f"*RHEN // POSITION OPEN*\n{stamp} | `{symbol}`",
                key=f"position-open:{symbol}:{stamp[:16]}",
            )
        for symbol in sorted(closed):
            self._enqueue(
                f"*RHEN // POSITION CLOSED*\n{stamp} | `{symbol}`",
                key=f"position-closed:{symbol}:{stamp[:16]}",
            )
        self._known_bot_positions = set(active_positions)

    def _enqueue(self, text: str, *, key: str | None = None) -> None:
        if not self.enabled:
            return
        if key is not None:
            if key in self._dedupe:
                return
            self._dedupe.add(key)
            if len(self._dedupe) > 1000:
                self._dedupe = set(list(self._dedupe)[-500:])
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
                response = await self._client.post(self.webhook_url, json={"text": text})
                response.raise_for_status()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(
                    "SLACK_WEBHOOK_ERROR",
                    {"error": self.last_error},
                    flush=True,
                )
            finally:
                self._queue.task_done()
