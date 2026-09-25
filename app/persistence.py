from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .config import Settings


class TradingEventSink:
    """Durable, idempotent trading telemetry and canonical-ledger transport."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_sent_at: datetime | None = None
        self.last_error: str | None = None
        self.last_reconcile_at: datetime | None = None
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
            "last_reconcile_at": (
                self.last_reconcile_at.isoformat() if self.last_reconcile_at else None
            ),
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

    def _event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event_key": event_key
            or f"{self.settings.trading_run_id}:{event_type}:{uuid4().hex}",
            "run_id": self.settings.trading_run_id,
            "strategy_version_id": self.settings.strategy_version_id,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "symbol": symbol or None,
            "correlation_id": correlation_id,
            "source": "alpaca-trader",
            "payload": payload or {},
        }

    def emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        event = self._event(
            event_type=event_type,
            payload=payload,
            symbol=symbol,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            event_key=event_key,
        )
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_count += 1
            self.last_error = "event queue full; telemetry event dropped"

    async def emit_critical(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
    ) -> bool:
        """Persist before a new entry. Protective exits never depend on this path."""
        if not self.enabled:
            return True
        event = self._event(
            event_type=event_type,
            payload=payload,
            symbol=symbol,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            event_key=event_key,
        )
        async with httpx.AsyncClient(timeout=5.0) as http:
            for attempt in range(3):
                if await self._send_batch(http, [event]):
                    return True
                await asyncio.sleep(0.25 * (2 ** attempt))
        return False

    async def persist_entry_intent(
        self,
        *,
        signal: Any,
        qty: str,
        client_order_id: str,
        correlation_id: str | None,
        intended_at: datetime,
    ) -> dict[str, str] | None:
        signal_id = str(uuid4())
        intent_id = str(uuid4())
        position_id = str(uuid4())
        payload = {
            "signal": {
                "signal_id": signal_id,
                "symbol": signal.symbol.upper(),
                "side": "buy",
                "signal_at": intended_at.isoformat(),
                "reference_price": str(signal.reference_price),
                "stop_price": str(signal.stop_price),
                "target_price": str(signal.take_profit_price),
                "payload": {
                    "reason": signal.reason,
                    "metadata": signal.metadata or {},
                },
            },
            "intent": {
                "intent_id": intent_id,
                "idempotency_key": client_order_id,
                "signal_id": signal_id,
                "symbol": signal.symbol.upper(),
                "side": "buy",
                "order_type": "market",
                "time_in_force": "day",
                "requested_qty": qty,
                "requested_notional": str(signal.notional),
                "risk_decision": "approved",
                "risk_reason": "execution risk checks passed",
                "intended_at": intended_at.isoformat(),
                "payload": {
                    "client_order_id": client_order_id,
                    "position_id": position_id,
                },
            },
        }
        ok = await self.emit_critical(
            event_type="order_intent",
            event_key=f"{self.settings.trading_run_id}:intent:{client_order_id}",
            symbol=signal.symbol.upper(),
            correlation_id=correlation_id,
            occurred_at=intended_at.isoformat(),
            payload=payload,
        )
        if not ok:
            return None
        return {
            "signal_id": signal_id,
            "intent_id": intent_id,
            "position_id": position_id,
            "client_order_id": client_order_id,
        }

    def persist_exit_intent(
        self,
        *,
        symbol: str,
        qty: str,
        client_order_id: str,
        exit_reason: str,
        correlation_id: str | None,
        intended_at: datetime,
    ) -> dict[str, str]:
        intent_id = str(uuid4())
        exit_id = str(uuid4())
        payload = {
            "intent": {
                "intent_id": intent_id,
                "idempotency_key": client_order_id,
                "signal_id": None,
                "symbol": symbol.upper(),
                "side": "sell",
                "order_type": "market",
                "time_in_force": "day",
                "requested_qty": qty,
                "requested_notional": None,
                "risk_decision": "approved",
                "risk_reason": exit_reason,
                "intended_at": intended_at.isoformat(),
                "payload": {
                    "client_order_id": client_order_id,
                    "exit_id": exit_id,
                    "exit_reason": exit_reason,
                },
            }
        }
        self.emit(
            event_type="order_intent",
            event_key=f"{self.settings.trading_run_id}:intent:{client_order_id}",
            symbol=symbol.upper(),
            correlation_id=correlation_id,
            occurred_at=intended_at.isoformat(),
            payload=payload,
        )
        return {
            "intent_id": intent_id,
            "exit_id": exit_id,
            "client_order_id": client_order_id,
        }

    def record_broker_order(
        self,
        order: dict[str, Any],
        *,
        intent_id: str | None = None,
        position_id: str | None = None,
        exit_id: str | None = None,
        exit_reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        broker_order_id = str(order.get("id") or "")
        if not broker_order_id:
            return
        status = str(order.get("status") or "unknown")
        filled_qty = str(order.get("filled_qty") or "0")
        updated = str(
            order.get("updated_at")
            or order.get("filled_at")
            or order.get("submitted_at")
            or ""
        )
        self.emit(
            event_type="broker_order",
            event_key=(
                f"{self.settings.trading_run_id}:order:{broker_order_id}:"
                f"{status}:{filled_qty}:{updated}"
            ),
            symbol=str(order.get("symbol") or "").upper(),
            correlation_id=correlation_id,
            occurred_at=(
                str(order.get("submitted_at")) if order.get("submitted_at") else None
            ),
            payload={
                "order": order,
                "intent_id": intent_id,
                "position_id": position_id,
                "exit_id": exit_id,
                "exit_reason": exit_reason,
            },
        )

    def should_reconcile(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.last_reconcile_at is None:
            return True
        return (
            now.astimezone(timezone.utc) - self.last_reconcile_at
        ).total_seconds() >= self.settings.ledger_reconcile_seconds

    def record_reconciliation(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        correlation_id: str | None,
        observed_at: datetime,
    ) -> None:
        if not self.enabled:
            return
        observed_utc = observed_at.astimezone(timezone.utc)
        self.last_reconcile_at = observed_utc

        for order in orders:
            client_order_id = str(order.get("client_order_id") or "")
            if not client_order_id.startswith("anevum-"):
                continue
            self.record_broker_order(order, correlation_id=correlation_id)

        bot_order_ids = {
            str(order.get("id") or "")
            for order in orders
            if str(order.get("client_order_id") or "").startswith("anevum-")
            and order.get("id")
        }
        for activity in fills:
            activity_id = str(activity.get("id") or "")
            order_id = str(activity.get("order_id") or "")
            if not activity_id or not order_id or order_id not in bot_order_ids:
                continue
            self.emit(
                event_type="broker_fill",
                event_key=f"{self.settings.trading_run_id}:fill:{activity_id}",
                symbol=str(activity.get("symbol") or "").upper(),
                correlation_id=correlation_id,
                occurred_at=str(
                    activity.get("transaction_time")
                    or activity.get("date")
                    or observed_utc.isoformat()
                ),
                payload={"activity": activity},
            )

        gross_exposure = sum(
            abs(float(position.get("market_value") or 0))
            for position in positions
        )
        unrealized_pnl = sum(
            float(position.get("unrealized_pl") or 0)
            for position in positions
        )
        last_equity = float(account.get("last_equity") or 0)
        equity = float(account.get("equity") or 0)
        drawdown_pct = (
            max((last_equity - equity) / last_equity, 0.0)
            if last_equity > 0
            else 0.0
        )
        bucket = observed_utc.replace(second=0, microsecond=0).isoformat()
        self.emit(
            event_type="account_snapshot",
            event_key=f"{self.settings.trading_run_id}:account:{bucket}",
            correlation_id=correlation_id,
            occurred_at=observed_utc.isoformat(),
            payload={
                "equity": account.get("equity"),
                "last_equity": account.get("last_equity"),
                "cash": account.get("cash"),
                "buying_power": account.get("buying_power"),
                "realized_pnl": None,
                "unrealized_pnl": str(unrealized_pnl),
                "gross_exposure": str(gross_exposure),
                "net_exposure": str(gross_exposure),
                "drawdown_pct": str(drawdown_pct),
                "open_positions": len(positions),
                "positions": positions,
            },
        )

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
