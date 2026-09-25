from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
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
        self.quarantined_count = 0
        self.last_quarantined_event: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.trading_ingest_url
            and self.settings.trading_ingest_token
            and self.settings.trading_run_id
            and self.settings.strategy_version_id
        )

    def _owns_broker_order(self, order: dict[str, Any]) -> bool:
        client_order_id = str(order.get("client_order_id") or "")
        if not client_order_id.startswith("anevum-"):
            return False
        owner_tag = str(getattr(self.settings, "order_owner_tag", "") or "")
        if not owner_tag:
            return True
        return f"-{owner_tag}-" in client_order_id

    def _owned_order_ids_from_snapshot(
        self,
        orders: list[dict[str, Any]],
    ) -> set[str]:
        owned_ids = {
            str(order.get("id") or "")
            for order in orders
            if order.get("id") and self._owns_broker_order(order)
        }

        changed = True
        while changed:
            changed = False
            for order in orders:
                order_id = str(order.get("id") or "")
                if not order_id or order_id in owned_ids:
                    continue
                replaces = str(order.get("replaces") or "")
                replaced_by = str(order.get("replaced_by") or "")
                if (
                    (replaces and replaces in owned_ids)
                    or (replaced_by and replaced_by in owned_ids)
                ):
                    owned_ids.add(order_id)
                    changed = True

        return owned_ids

    def _protective_stop_lineage_ids(
        self,
        orders: list[dict[str, Any]],
    ) -> set[str]:
        protective_ids = {
            str(order.get("id") or "")
            for order in orders
            if order.get("id")
            and "-hardstop-" in str(order.get("client_order_id") or "")
        }

        changed = True
        while changed:
            changed = False
            for order in orders:
                order_id = str(order.get("id") or "")
                if not order_id or order_id in protective_ids:
                    continue
                replaces = str(order.get("replaces") or "")
                replaced_by = str(order.get("replaced_by") or "")
                if (
                    (replaces and replaces in protective_ids)
                    or (replaced_by and replaced_by in protective_ids)
                ):
                    protective_ids.add(order_id)
                    changed = True

        return protective_ids

    def managed_symbols_from_snapshot(
        self,
        *,
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[str]:
        managed = {
            str(symbol).upper()
            for symbol in (getattr(self.settings, "allowed_symbols", set()) or set())
            if str(symbol).strip()
        }
        all_orders = [*orders, *open_orders]
        owned_order_ids = self._owned_order_ids_from_snapshot(all_orders)
        owned_orders = [
            order
            for order in all_orders
            if str(order.get("id") or "") in owned_order_ids
        ]
        owned_order_ids = {
            str(order.get("id") or "")
            for order in owned_orders
            if order.get("id")
        }
        for order in owned_orders:
            symbol = str(order.get("symbol") or "").upper()
            if symbol:
                managed.add(symbol)
        for activity in fills:
            order_id = str(activity.get("order_id") or "")
            if order_id not in owned_order_ids:
                continue
            symbol = str(activity.get("symbol") or "").upper()
            if symbol:
                managed.add(symbol)
        return sorted(managed)

    @staticmethod
    def _is_standing_protective_stop(
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> bool:
        client_order_id = str(order.get("client_order_id") or "")
        order_id = str(order.get("id") or "")
        return (
            "-hardstop-" in client_order_id
            or bool(protective_order_ids and order_id in protective_order_ids)
        )

    @classmethod
    def _projectable_order(
        cls,
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> bool:
        if not cls._is_standing_protective_stop(order, protective_order_ids):
            return True
        filled_qty = Decimal(str(order.get("filled_qty") or "0"))
        status = str(order.get("status") or "").lower()
        return filled_qty > 0 or status == "filled"

    @classmethod
    def _inferred_exit_reason(
        cls,
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> str | None:
        if cls._is_standing_protective_stop(order, protective_order_ids):
            return "broker protective stop filled"
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "queued": self.queue.qsize(),
            "sent_count": self.sent_count,
            "dropped_count": self.dropped_count,
            "quarantined_count": self.quarantined_count,
            "last_quarantined_event": self.last_quarantined_event,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "last_reconcile_at": (
                self.last_reconcile_at.isoformat() if self.last_reconcile_at else None
            ),
            "last_error": self.last_error,
            "run_id": self.settings.trading_run_id or None,
            "strategy_version_id": self.settings.strategy_version_id or None,
            "run_started_at": (
                self.settings.trading_run_started_at.isoformat()
                if self.settings.trading_run_started_at
                else None
            ),
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
        exit_metadata: dict[str, Any] | None = None,
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
                    "exit_metadata": exit_metadata or {},
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

        run_started_at = self.settings.trading_run_started_at

        def at_or_after_run_start(raw: Any) -> bool:
            if run_started_at is None:
                return True
            if not raw:
                return False
            try:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return False
            if stamp.tzinfo is None:
                return False
            return stamp.astimezone(timezone.utc) >= run_started_at

        bot_orders = [
            order
            for order in orders
            if self._owns_broker_order(order)
            and self._projectable_order(order)
            and at_or_after_run_start(order.get("submitted_at"))
        ]
        bot_orders.sort(key=lambda order: str(order.get("submitted_at") or ""))
        for order in bot_orders:
            self.record_broker_order(
                order,
                exit_reason=self._inferred_exit_reason(order),
                correlation_id=correlation_id,
            )

        bot_order_ids = {
            str(order.get("id") or "")
            for order in bot_orders
            if order.get("id")
        }
        for activity in fills:
            activity_id = str(activity.get("id") or "")
            order_id = str(activity.get("order_id") or "")
            if not activity_id or not order_id or order_id not in bot_order_ids:
                continue
            if not at_or_after_run_start(
                activity.get("transaction_time") or activity.get("date")
            ):
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

    def _at_or_after_run_start(self, raw: Any) -> bool:
        run_started_at = self.settings.trading_run_started_at
        if run_started_at is None:
            return True
        if not raw:
            return False
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            return False
        return stamp.astimezone(timezone.utc) >= run_started_at

    def _build_reconciliation_events(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        correlation_id: str | None,
        observed_at: datetime,
        position_metrics: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        observed_utc = observed_at.astimezone(timezone.utc)
        owned_order_ids = self._owned_order_ids_from_snapshot(orders)
        protective_order_ids = self._protective_stop_lineage_ids(orders)
        bot_orders = [
            order
            for order in orders
            if str(order.get("id") or "") in owned_order_ids
            and self._projectable_order(order, protective_order_ids)
            and self._at_or_after_run_start(order.get("submitted_at"))
        ]
        bot_orders.sort(key=lambda order: str(order.get("submitted_at") or ""))

        events: list[dict[str, Any]] = []
        for order in bot_orders:
            broker_order_id = str(order.get("id") or "")
            if not broker_order_id:
                continue
            status = str(order.get("status") or "unknown")
            filled_qty = str(order.get("filled_qty") or "0")
            updated = str(
                order.get("updated_at")
                or order.get("filled_at")
                or order.get("submitted_at")
                or ""
            )
            events.append(
                self._event(
                    event_type="broker_order",
                    event_key=(
                        f"{self.settings.trading_run_id}:order:{broker_order_id}:"
                        f"{status}:{filled_qty}:{updated}"
                    ),
                    symbol=str(order.get("symbol") or "").upper(),
                    correlation_id=correlation_id,
                    occurred_at=(
                        str(order.get("submitted_at"))
                        if order.get("submitted_at")
                        else None
                    ),
                    payload={
                        "order": order,
                        "exit_reason": self._inferred_exit_reason(
                            order,
                            protective_order_ids,
                        ),
                    },
                )
            )

        bot_order_ids = {
            str(order.get("id") or "")
            for order in bot_orders
            if order.get("id")
        }
        for activity in fills:
            activity_id = str(activity.get("id") or "")
            order_id = str(activity.get("order_id") or "")
            if not activity_id or not order_id or order_id not in bot_order_ids:
                continue
            if not self._at_or_after_run_start(
                activity.get("transaction_time") or activity.get("date")
            ):
                continue
            events.append(
                self._event(
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
        events.append(
            self._event(
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
                    "position_metrics": position_metrics or {},
                },
            )
        )
        return events

    async def sync_reconciliation(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        managed_symbols: list[str],
        correlation_id: str | None,
        observed_at: datetime,
        position_metrics: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("canonical trading persistence is not configured")

        events = self._build_reconciliation_events(
            account=account,
            positions=positions,
            orders=orders,
            fills=fills,
            correlation_id=correlation_id,
            observed_at=observed_at,
            position_metrics=position_metrics,
        )
        async with httpx.AsyncClient(timeout=8.0) as http:
            if not await self._send_batch(http, events):
                raise RuntimeError(self.last_error or "broker snapshot persistence failed")

            base = self.settings.trading_ingest_url.rsplit("/", 1)[0]
            response = await http.post(
                f"{base}/trading-reconcile",
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={
                    "action": "reconcile",
                    "reconcile": {
                        "run_id": self.settings.trading_run_id,
                        "strategy_version_id": self.settings.strategy_version_id,
                        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                        "managed_symbols": managed_symbols,
                        "broker_positions": positions,
                        "open_orders": open_orders,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("reconciliation endpoint returned no result")
            self.last_reconcile_at = observed_at.astimezone(timezone.utc)
            self.last_error = None
            return result

    async def resolve_intent_not_found(
        self,
        *,
        client_order_id: str,
        correlation_id: str | None,
        checked_at: datetime,
    ) -> bool:
        if not self.enabled:
            return False
        base = self.settings.trading_ingest_url.rsplit("/", 1)[0]
        async with httpx.AsyncClient(timeout=8.0) as http:
            response = await http.post(
                f"{base}/trading-reconcile",
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={
                    "action": "resolve_intent",
                    "intent": {
                        "run_id": self.settings.trading_run_id,
                        "client_order_id": client_order_id,
                        "state": "broker_not_found",
                        "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
                        "correlation_id": correlation_id,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            return bool(payload.get("ok"))

    async def persist_recovered_order(
        self,
        order: dict[str, Any],
        *,
        correlation_id: str | None,
        intent_id: str | None = None,
        position_id: str | None = None,
        exit_id: str | None = None,
        exit_reason: str | None = None,
    ) -> bool:
        broker_order_id = str(order.get("id") or "")
        if not broker_order_id:
            return False
        status = str(order.get("status") or "unknown")
        filled_qty = str(order.get("filled_qty") or "0")
        updated = str(
            order.get("updated_at")
            or order.get("filled_at")
            or order.get("submitted_at")
            or ""
        )
        event = self._event(
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
                "recovered_by_client_order_id": True,
            },
        )
        async with httpx.AsyncClient(timeout=8.0) as http:
            return await self._send_batch(http, [event])

    @staticmethod
    def _critical_ingest_event(event: dict[str, Any]) -> bool:
        return str(event.get("event_type") or "") in {
            "order_intent",
            "broker_order",
            "broker_fill",
        }

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
            return str(payload)
        except Exception:
            return response.text[:500]

    async def _send_chunk(
        self,
        http: httpx.AsyncClient,
        chunk: list[dict[str, Any]],
    ) -> bool:
        if not chunk:
            return True

        try:
            response = await http.post(
                self.settings.trading_ingest_url,
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={"events": chunk},
            )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

        if 200 <= response.status_code < 300:
            self.sent_count += len(chunk)
            return True

        detail = self._response_detail(response)
        if response.status_code != 400:
            self.last_error = (
                f"ingest HTTP {response.status_code}: {detail}"
            )
            return False

        # A 400 normally means one event in an otherwise valid reconciliation
        # snapshot was rejected by schema validation. Bisect the batch so valid
        # records are not repeatedly poisoned by the same malformed event.
        if len(chunk) > 1:
            midpoint = len(chunk) // 2
            left_ok = await self._send_chunk(http, chunk[:midpoint])
            if not left_ok:
                return False
            return await self._send_chunk(http, chunk[midpoint:])

        event = chunk[0]
        event_type = str(event.get("event_type") or "unknown")
        event_key = str(event.get("event_key") or "unknown")
        rejected = {
            "event_type": event_type,
            "event_key": event_key,
            "status_code": response.status_code,
            "response": detail,
        }
        if self._critical_ingest_event(event):
            self.last_error = (
                "critical ingest event rejected: "
                f"{event_type} {event_key}: {detail}"
            )
            return False

        # Noncritical telemetry is quarantined so accounting/order integrity can
        # keep reconciling. The exact rejected event remains visible in status.
        self.quarantined_count += 1
        self.last_quarantined_event = rejected
        return True

    async def _send_batch(
        self,
        http: httpx.AsyncClient,
        events: list[dict[str, Any]],
    ) -> bool:
        if not events:
            return True

        # The Edge Function accepts at most 100 events per request.
        for start in range(0, len(events), 100):
            if not await self._send_chunk(http, events[start : start + 100]):
                return False

        self.last_sent_at = datetime.now(timezone.utc)
        self.last_error = None
        return True

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
