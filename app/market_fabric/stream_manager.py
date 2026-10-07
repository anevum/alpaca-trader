"""Event-driven, bounded WebSocket observation. No polling prices, no orders."""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from uuid import uuid4

from websockets.asyncio.client import connect

from .contracts import normalize
from .feed_router import ENDPOINTS, validate_symbols


class ProviderStreamError(RuntimeError):
    def __init__(self, code):
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else None
        super().__init__("market provider rejected stream")


class MarketStreamManager:
    def __init__(self, store, *, api_key: str, api_secret: str, on_event,
                 bootstrap=None, connect_factory=connect, queue_max=10000, on_status=None):
        validate_symbols(store.symbols)
        self.store = store
        self.api_key, self.api_secret = api_key, api_secret
        self.on_event, self.bootstrap = on_event, bootstrap
        self.connect_factory = connect_factory
        self.queue_max = queue_max
        self.reconnects = 0
        self.errors = 0
        self.last_error = None
        self.last_error_code = None
        self.subscribed_channels = set()
        self.unavailable_channels = set()
        self.running = False
        self.sequence = 0
        self.on_status = on_status
        self.processed_events = 0
        self.cooperative_yields = 0
        self.max_callback_ms = 0.0

    async def status(self, state, *, force=False):
        changed = self.store.connection != state
        self.store.connection = state
        if self.on_status and (changed or force):
            await self.on_status({"connection_state":state,"stream_generation":self.store.generation,
                "subscribed_symbols":len(self.store.subscribed),"intended_symbols":len(self.store.symbols),
                "subscribed_channels":sorted(self.subscribed_channels),"unavailable_channels":sorted(self.unavailable_channels),
                "stream_errors":self.errors,"stream_error":self.last_error,"stream_error_code":self.last_error_code,
                "processed_market_events":self.processed_events,"cooperative_yields":self.cooperative_yields,
                "max_market_callback_ms":self.max_callback_ms,
                "source_at":datetime.now(timezone.utc).isoformat(),"provenance":"OPERATIONAL","entry_authority":False})

    async def consume(self, ws, feed, session_id):
        generation = uuid4().hex
        self.store.begin(generation, feed, session_id)
        self.sequence = 0
        self.subscribed_channels.clear()
        self.unavailable_channels.clear()
        await self.status("AUTHENTICATING",force=True)
        await ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
        authenticated = False
        subscribe_sent = False
        pending_channel = None
        bootstrapped = False
        loop = asyncio.get_running_loop()
        yielded_at = loop.time()
        # Sequential consumption applies transport backpressure; bounded websockets max_queue
        # preserves bars rather than losing them to a quote-only drop policy.
        async for frame in ws:
            messages = json.loads(frame)
            if not isinstance(messages, list):
                raise ValueError("invalid market frame")
            for raw in messages:
                if raw.get("T") == "error":
                    # Overnight feeds have different channel capabilities. A
                    # rejected bar channel must not tear down accepted quotes.
                    if feed == "overnight" and raw.get("code") == 410 and pending_channel in {"bars", "updatedBars"}:
                        self.unavailable_channels.add(pending_channel)
                        if pending_channel == "bars":
                            self.unavailable_channels.add("updatedBars")
                        pending_channel = None
                        await self.status(self.store.connection,force=True)
                        continue
                    raise ProviderStreamError(raw.get("code"))
                if raw.get("T") == "success" and raw.get("msg") == "authenticated":
                    authenticated = True
                    await self.status("SUBSCRIBING")
                    channels = ("quotes",) if feed == "overnight" else ("quotes", "bars", "updatedBars")
                    await ws.send(json.dumps({"action": "subscribe", **{k:list(self.store.symbols) for k in channels}}))
                    pending_channel = "quotes" if feed == "overnight" else None
                    subscribe_sent = True
                elif raw.get("T") == "subscription":
                    if not authenticated or not subscribe_sent:
                        raise ValueError("subscription before authentication")
                    expected = set(self.store.symbols)
                    required = ("quotes", pending_channel) if pending_channel else ("quotes",) if feed == "overnight" else ("quotes", "bars", "updatedBars")
                    if any(set(raw.get(k, [])) != expected for k in required):
                        raise ValueError("subscription coverage mismatch")
                    self.store.subscribed = expected
                    self.subscribed_channels = {k for k in ("quotes", "bars", "updatedBars") if set(raw.get(k, [])) == expected}
                    await self.status("WARMING",force=True)
                    if self.bootstrap and "bars" in self.subscribed_channels and not bootstrapped:
                        await self.bootstrap(feed, session_id)
                        bootstrapped = True
                    if feed == "overnight" and pending_channel in {"quotes", "bars"}:
                        pending_channel = "bars" if pending_channel == "quotes" else "updatedBars"
                        await ws.send(json.dumps({"action":"subscribe", pending_channel:list(self.store.symbols)}))
                    else:
                        pending_channel = None
                elif raw.get("T") in {"q", "b", "u", "s"}:
                    if not authenticated or self.store.subscribed != set(self.store.symbols):
                        raise ValueError("data before verified subscriptions")
                    if raw.get("T") in {"b", "u"} and ("bars" if raw["T"] == "b" else "updatedBars") not in self.subscribed_channels:
                        raise ValueError("data before verified channel")
                    self.sequence += 1
                    now = datetime.now(timezone.utc)
                    event = normalize(raw, generation=generation, sequence=self.sequence, feed=feed,
                                      session=session_id.split("/")[-1], session_id=session_id, received_at=now)
                    if self.store.apply(event):
                        callback_at = loop.time()
                        await self.on_event(event)
                        self.max_callback_ms = max(self.max_callback_ms, (loop.time()-callback_at)*1000)
                        self.processed_events += 1
                    ready = all(self.store.snapshot(s, now)["evaluable"] for s in self.store.symbols)
                    await self.status("HEALTHY" if ready else "WARMING")
                # Buffered frames can make recv and synchronous shadow callbacks
                # complete without yielding. Give broker, ping/reconnect, freshness
                # and Command publisher tasks a turn after a bounded work slice.
                # Every event remains ordered; no quote/bar or broker event is dropped.
                if loop.time()-yielded_at >= .01:
                    await asyncio.sleep(0)
                    self.cooperative_yields += 1
                    yielded_at = loop.time()

    async def run(self, feed, session_id):
        if feed not in ENDPOINTS or not self.api_key or not self.api_secret:
            raise ValueError("valid feed and observation credentials required")
        self.running = True
        failures = 0
        try:
            while self.running:
                await self.status("CONNECTING")
                try:
                    async with self.connect_factory("wss://stream.data.alpaca.markets/"+ENDPOINTS[feed],
                                                    max_queue=self.queue_max, max_size=2**20,
                                                    open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                        await self.consume(ws, feed, session_id)
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.errors += 1
                    self.last_error = type(exc).__name__  # never expose auth frames/secrets
                    self.last_error_code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                    failures += 1
                self.store.subscribed.clear()
                await self.status("DISCONNECTED",force=True)
                self.reconnects += 1
                await asyncio.sleep(min(30, 2 ** min(failures, 5)) + random.uniform(0, .25))
        finally:
            self.store.subscribed.clear()
            self.running = False
            await self.status("DISCONNECTED",force=True)
