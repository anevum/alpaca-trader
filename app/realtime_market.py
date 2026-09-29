from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .config import Settings
from .state import RuntimeState


def _iso_utc(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).isoformat()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def select_hot_symbols(settings: Settings, state: RuntimeState) -> list[str]:
    """Return the priority-ordered real-time set without exceeding feed limits."""
    ordered = [
        *settings.universe_always_include,
        *settings.confirmation_symbols,
        *state.universe_active_symbols,
        *settings.scan_symbols,
    ]
    seen: set[str] = set()
    selected: list[str] = []
    for raw in ordered:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        selected.append(symbol)
        if len(selected) >= settings.realtime_symbol_limit:
            break
    return selected


class RealtimeMarketStream:
    """Alpaca market-data WebSocket ingestion.

    This service is intentionally read-only. It creates a fast quote/trade state
    surface that RHEN/NOSTRA can consume without changing the existing order
    path. REALTIME_EXECUTION_ENABLED remains a separate authorization gate.
    """

    def __init__(self, settings: Settings, state: RuntimeState):
        self.settings = settings
        self.state = state
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._subscribed: set[str] = set()
        self._reconnects = 0
        self._messages = 0

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.realtime_market_enabled
            and self.settings.credentials_configured
        )

    @property
    def url(self) -> str:
        return (
            "wss://stream.data.alpaca.markets/"
            f"v2/{self.settings.data_feed}"
        )

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="rhen-realtime-market")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self.state.realtime_connected = False

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "execution_enabled": self.settings.realtime_execution_enabled,
            "shadow_only": self.settings.realtime_shadow_only,
            "connected": self.state.realtime_connected,
            "feed": self.settings.data_feed,
            "symbol_limit": self.settings.realtime_symbol_limit,
            "symbols": list(self.state.realtime_symbols),
            "symbol_count": len(self.state.realtime_symbols),
            "last_message_at": self.state.realtime_last_message_at,
            "last_error": self.state.realtime_last_error,
            "messages": self._messages,
            "reconnects": self._reconnects,
        }

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._reconnects += 1
                self.state.realtime_connected = False
                self.state.realtime_last_error = f"{type(exc).__name__}: {exc}"
                self.state.record_event(
                    kind="realtime_market",
                    action="reconnect",
                    message="real-time market stream reconnecting",
                    reason=self.state.realtime_last_error,
                    emit=False,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2.0, 30.0)

    async def _session(self) -> None:
        async with connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_queue=4096,
        ) as websocket:
            await self._expect_success(websocket, "connected")
            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.settings.alpaca_api_key,
                        "secret": self.settings.alpaca_api_secret,
                    }
                )
            )
            await self._expect_success(websocket, "authenticated")

            self.state.realtime_connected = True
            self.state.realtime_last_error = None
            self._subscribed.clear()
            await self._sync_subscriptions(websocket)
            next_sync = monotonic() + self.settings.realtime_sync_seconds

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=max(1.0, self.settings.realtime_sync_seconds),
                    )
                except asyncio.TimeoutError:
                    raw = None
                except ConnectionClosed:
                    raise

                if raw is not None:
                    self._handle_payload(raw)

                if monotonic() >= next_sync:
                    await self._sync_subscriptions(websocket)
                    next_sync = monotonic() + self.settings.realtime_sync_seconds

    async def _expect_success(self, websocket, expected: str) -> None:
        raw = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        messages = json.loads(raw)
        if not isinstance(messages, list):
            messages = [messages]
        for message in messages:
            if message.get("T") == "error":
                raise RuntimeError(
                    f"Alpaca stream error {message.get('code')}: "
                    f"{message.get('msg')}"
                )
            if message.get("T") == "success" and message.get("msg") == expected:
                return
        raise RuntimeError(f"Alpaca stream did not confirm {expected}")

    async def _sync_subscriptions(self, websocket) -> None:
        desired = set(select_hot_symbols(self.settings, self.state))
        remove = sorted(self._subscribed - desired)
        add = sorted(desired - self._subscribed)

        if remove:
            await websocket.send(
                json.dumps(
                    {
                        "action": "unsubscribe",
                        "trades": remove,
                        "quotes": remove,
                    }
                )
            )
        if add:
            await websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "trades": add,
                        "quotes": add,
                    }
                )
            )

        self._subscribed = desired
        self.state.realtime_symbols = select_hot_symbols(self.settings, self.state)

    def _handle_payload(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        messages = json.loads(raw)
        if not isinstance(messages, list):
            messages = [messages]

        now = datetime.now(timezone.utc)
        for message in messages:
            message_type = str(message.get("T") or "")
            if message_type == "error":
                raise RuntimeError(
                    f"Alpaca stream error {message.get('code')}: "
                    f"{message.get('msg')}"
                )
            if message_type == "q":
                self.state.record_realtime_quote(
                    symbol=str(message.get("S") or ""),
                    bid=message.get("bp"),
                    ask=message.get("ap"),
                    bid_size=message.get("bs"),
                    ask_size=message.get("as"),
                    market_timestamp=_iso_utc(message.get("t")),
                    received_at=now,
                )
                self._messages += 1
            elif message_type == "t":
                self.state.record_realtime_trade(
                    symbol=str(message.get("S") or ""),
                    price=message.get("p"),
                    size=message.get("s"),
                    market_timestamp=_iso_utc(message.get("t")),
                    received_at=now,
                )
                self._messages += 1

    def snapshot(self, symbol: str) -> dict[str, Any]:
        return self.state.realtime_snapshot(
            symbol,
            max_age_seconds=self.settings.realtime_quote_max_age_seconds,
        )
