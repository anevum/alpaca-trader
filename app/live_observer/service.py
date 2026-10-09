"""Bounded owner-only market and broker observation. No order or risk path.

This is a separate child of the existing Railway RHEN service. It can GET
Alpaca account state and subscribe to market/trade-update WebSockets, but has
no order submission, cancel, promotion, or strategy execution entrypoints.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.command_access import CommandAuthError, authenticate_command_admin
from app.command_visuals.visual_projector import VisualProjector
from app.market_fabric.broker_updates import BrokerInbox, BrokerUpdateStream, execution_marker
from app.market_fabric.stream_manager import MarketStreamManager
from app.market_fabric.stream_state import MarketStateStore

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
MAX_SUBSCRIBERS = 8
OBSERVATION_SYMBOL_CAP = 8


def utc_now() -> datetime:
    return datetime.now(UTC)


def symbol_scope() -> tuple[str, ...]:
    configured = (
        os.getenv("RHEN_OBSERVER_SYMBOLS", "").strip()
        or os.getenv("SCAN_SYMBOLS", "").strip()
        or os.getenv("EXTENDED_EQUITY_SYMBOLS", "").strip()
        or "SPY,QQQ,IWM"
    )
    unique: list[str] = []
    for raw in configured.split(","):
        symbol = raw.strip().upper()
        if not symbol or symbol in unique:
            continue
        if not all(letter.isascii() and (letter.isalnum() or letter in ".-") for letter in symbol):
            continue
        if len(symbol) > 12:
            continue
        unique.append(symbol)
        if len(unique) >= OBSERVATION_SYMBOL_CAP:
            break
    if not unique:
        raise ValueError("no valid market observation symbols")
    return tuple(unique)


def feed_window(now: datetime) -> bool:
    """Basic IEX stock subscription window, NOT proof an exchange is open."""
    local = now.astimezone(NY)
    return local.weekday() < 5 and time(8) <= local.time().replace(tzinfo=None) < time(17)


def clean_number(raw: object) -> float | None:
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


class ReadOnlyObserver:
    def __init__(self) -> None:
        self.symbols = symbol_scope()
        self.api_key = os.getenv("ALPACA_API_KEY", "").strip()
        self.api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        self.paper = os.getenv("TRADING_MODE", "paper").strip().lower() != "live"
        self.market_feed = os.getenv("RHEN_OBSERVER_MARKET_FEED", "iex").strip().lower()
        if self.market_feed not in {"iex", "sip"}:
            raise ValueError("observer feed must be iex or sip")
        if self.market_feed == "sip" and os.getenv("RHEN_OBSERVER_SIP_ENTITLED", "").lower() != "true":
            raise ValueError("SIP observation requires explicit entitlement verification")
        self.account_interval = max(5, min(60, int(os.getenv("RHEN_OBSERVER_ACCOUNT_SECONDS", "10"))))
        self.store = MarketStateStore(self.symbols, warm_bars=1, max_bars=120)
        self.visual = VisualProjector(self.store, flush_ms=200)
        self.publisher = self.visual.publisher
        self.publisher.snapshot = self.snapshot
        self.manager = MarketStreamManager(
            self.store,
            api_key=self.api_key,
            api_secret=self.api_secret,
            on_event=self.on_market_event,
            on_status=self.on_market_status,
            queue_max=512,
        )
        self.broker_stream_enabled = os.getenv(
            "RHEN_OBSERVER_BROKER_UPDATES_ENABLED", "true"
        ).strip().lower() == "true"
        self.broker: BrokerUpdateStream | None = None
        self.inbox: BrokerInbox | None = None
        self.account_wakeup = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.latest_account_value: float | None = None
        self.latest_account_at: str | None = None
        self.market_state = "STARTING"
        self.broker_state = "STARTING"
        self.visual.system_patch({
            "transport": "ALPACA_READ_ONLY",
            "connection_state": "STARTING",
            "broker_stream_state": "STARTING",
            "market_feed": self.market_feed,
            "market_scope": "BOUNDED_OWNER_WATCHLIST",
            "intended_symbols": len(self.symbols),
            "session": "UNKNOWN",
            "entry_authority": False,
            "broker_orders_possible": False,
            "account_sample_interval_seconds": self.account_interval,
            "stream_schema": "command-live.v1",
        })

    def snapshot(self) -> dict:
        # Only authenticated owner sockets receive these private observations.
        return self.visual.snapshot()

    async def on_market_event(self, event) -> None:
        self.visual.market(event, event.received_at)

    async def on_market_status(self, status: dict) -> None:
        self.market_state = str(status["connection_state"])
        self.visual.system_patch({
            **status,
            "market_feed": self.market_feed,
            "session": "IEX_WINDOW" if feed_window(utc_now()) else "OUTSIDE_FEED_WINDOW",
            "broker_orders_possible": False,
        })

    async def on_broker_event(self, event_id: str, data: dict) -> bool:
        marker = execution_marker(event_id, data)
        if marker:
            self.visual.critical(marker)
        # Observed broker messages are not canonical fills until reconciled.
        self.visual.system_patch({
            "latest_broker_update_at": utc_now().isoformat(),
            "broker_updates_are_canonical": False,
        })
        self.account_wakeup.set()
        return True

    async def market_loop(self) -> None:
        while True:
            if not feed_window(utc_now()):
                if self.market_state != "OUTSIDE_FEED_WINDOW":
                    self.market_state = "OUTSIDE_FEED_WINDOW"
                    self.visual.system_patch({
                        "connection_state": self.market_state,
                        "session": self.market_state,
                        "capability": "IEX_SINGLE_VENUE_WINDOW_ONLY" if self.market_feed == "iex" else "SIP_ENTITLED_WINDOW_ONLY",
                        "subscribed_symbols": 0,
                    })
                await asyncio.sleep(15)
                continue
            # Market streaming is independent of trading strategy/order loops.
            task = asyncio.create_task(self.manager.run(self.market_feed, "owner/IEX_WINDOW"))
            try:
                while feed_window(utc_now()):
                    await asyncio.sleep(5)
            finally:
                self.manager.running = False
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                self.store.subscribed.clear()
                self.market_state = "OUTSIDE_FEED_WINDOW"
                self.visual.system_patch({
                    "connection_state": self.market_state,
                    "session": self.market_state,
                    "subscribed_symbols": 0,
                })

    async def broker_loop(self) -> None:
        if not self.broker_stream_enabled:
            self.broker_state = "DISABLED"
            self.visual.system_patch({"broker_stream_state": self.broker_state})
            return
        # Durable, bounded at-most-once observations; never sent as orders.
        inbox_path = os.getenv("RHEN_OBSERVER_INBOX_PATH", "/data/rhen-observer-inbox.sqlite3")
        Path(inbox_path).parent.mkdir(parents=True, exist_ok=True)
        self.inbox = BrokerInbox(inbox_path, max_pending=1000, max_delivered=200)
        self.broker = BrokerUpdateStream(
            api_key=self.api_key, api_secret=self.api_secret,
            paper=self.paper, inbox=self.inbox, callback=self.on_broker_event,
        )
        task = asyncio.create_task(self.broker.run())
        previous = None
        try:
            while True:
                state = self.broker.state
                if state != previous:
                    self.broker_state = state
                    self.visual.system_patch({
                        "broker_stream_state": state,
                        "broker_stream_error": self.broker.last_error,
                        "broker_updates_are_canonical": False,
                    })
                    previous = state
                await asyncio.sleep(1)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self.inbox.close()
            self.inbox = None

    async def account_loop(self) -> None:
        if not self.api_key or not self.api_secret:
            self.visual.system_patch({"account_state": "CREDENTIALS_MISSING"})
            return
        base = (
            "https://paper-api.alpaca.markets"
            if self.paper else "https://api.alpaca.markets"
        )
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        last_request = 0.0
        async with httpx.AsyncClient(
            base_url=base, timeout=6.0,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        ) as client:
            while True:
                now = utc_now()
                try:
                    response = await client.get("/v2/account", headers=headers)
                    response.raise_for_status()
                    body = response.json()
                    equity = clean_number(body.get("equity"))
                    if equity is None or equity < 0:
                        raise ValueError("invalid broker account equity")
                    observed = utc_now().isoformat()
                    account = {
                        "equity": equity,
                        "cash": clean_number(body.get("cash")),
                        "buying_power": clean_number(body.get("buying_power")),
                        "observed_at": observed,
                        "provenance": "OBSERVED",
                        "source": "ALPACA/ACCOUNT_REST",
                        "quality_state": "LIVE",
                        "sampling_seconds": self.account_interval,
                    }
                    self.visual.system_patch({"account_state": "HEALTHY", "account_observation": account})
                    # Do not draw meaningless flatlines after exchange hours.
                    if feed_window(now) or self.latest_account_value != equity:
                        self.visual.point("account:equity", {
                            "timestamp": observed,
                            "value": equity,
                            "source": "ALPACA/ACCOUNT_REST",
                            "provenance": "OBSERVED",
                            "quality_state": "LIVE",
                            "data_character": "BROKER_REPORTED_EQUITY",
                        })
                    self.latest_account_value = equity
                    self.latest_account_at = observed
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.visual.system_patch({
                        "account_state": "STALE",
                        "account_error": type(exc).__name__,
                    })
                self.account_wakeup.clear()
                try:
                    await asyncio.wait_for(
                        self.account_wakeup.wait(), timeout=self.account_interval
                    )
                except asyncio.TimeoutError:
                    pass
                # Throttle update-triggered refetches, including fill bursts.
                await asyncio.sleep(1)

    async def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.publisher.run(), name="observer-publisher"),
            asyncio.create_task(self.market_loop(), name="observer-market"),
            asyncio.create_task(self.broker_loop(), name="observer-broker"),
            asyncio.create_task(self.account_loop(), name="observer-account"),
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()


observer: ReadOnlyObserver | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global observer
    observer = ReadOnlyObserver()
    await observer.start()
    try:
        yield
    finally:
        await observer.stop()
        observer = None


app = FastAPI(title="RHEN Read-Only Observer", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    if observer is None:
        return JSONResponse(status_code=503, content={"ok": False, "mode": "UNAVAILABLE"})
    return {
        "ok": True,
        "mode": "READ_ONLY",
        "market_state": observer.market_state,
        "broker_stream_state": observer.broker_state,
        "market_feed": observer.market_feed,
        "watched_symbols": len(observer.symbols),
        "active_clients": len(observer.publisher.clients),
        "entry_authority": False,
        "broker_orders_possible": False,
    }


@app.websocket("/v1/command/live")
async def private_live(websocket: WebSocket) -> None:
    if observer is None or not os.getenv("COMMAND_ACCESS_EMAILS", "").strip():
        await websocket.close(code=1013)
        return
    authorization = websocket.headers.get("authorization")
    try:
        await authenticate_command_admin(
            authorization,
            team_domain=os.getenv("CF_ACCESS_TEAM_DOMAIN", ""),
            audience=os.getenv("CF_ACCESS_AUD", ""),
            allowed_emails=os.getenv("COMMAND_ACCESS_EMAILS", ""),
        )
    except CommandAuthError:
        await websocket.close(code=1008)
        return
    if len(observer.publisher.clients) >= MAX_SUBSCRIBERS:
        await websocket.close(code=1013)
        return
    token = authorization[7:].strip()
    # Authenticated signature was verified; enforce expiry during the socket.
    expiry = float(jwt.decode(token, options={"verify_signature": False})["exp"])
    queue = observer.publisher.subscribe()
    try:
        await websocket.accept()
        while True:
            remaining = expiry - utc_now().timestamp()
            if remaining <= 0:
                await websocket.close(code=1008)
                break
            try:
                envelope = await asyncio.wait_for(queue.get(), timeout=min(remaining, 5))
            except asyncio.TimeoutError:
                continue
            if envelope is None:
                await websocket.close(code=1013)
                break
            await websocket.send_json(envelope)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        observer.publisher.unsubscribe(queue)
