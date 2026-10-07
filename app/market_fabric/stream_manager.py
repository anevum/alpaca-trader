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


class MarketStreamManager:
    def __init__(self, store, *, api_key: str, api_secret: str, on_event,
                 bootstrap=None, connect_factory=connect, queue_max=10000):
        validate_symbols(store.symbols)
        self.store = store
        self.api_key, self.api_secret = api_key, api_secret
        self.on_event, self.bootstrap = on_event, bootstrap
        self.connect_factory = connect_factory
        self.queue_max = queue_max
        self.reconnects = 0
        self.errors = 0
        self.last_error = None
        self.running = False
        self.sequence = 0

    async def consume(self, ws, feed, session_id):
        generation = uuid4().hex
        self.store.begin(generation, feed, session_id)
        self.sequence = 0
        self.store.connection = "AUTHENTICATING"
        await ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
        authenticated = False
        subscribe_sent = False
        # Sequential consumption applies transport backpressure; bounded websockets max_queue
        # preserves bars rather than losing them to a quote-only drop policy.
        async for frame in ws:
            messages = json.loads(frame)
            if not isinstance(messages, list):
                raise ValueError("invalid market frame")
            for raw in messages:
                if raw.get("T") == "error":
                    raise RuntimeError(f"market stream error code {raw.get('code')}")
                if raw.get("T") == "success" and raw.get("msg") == "authenticated":
                    authenticated = True
                    self.store.connection = "SUBSCRIBING"
                    await ws.send(json.dumps({"action": "subscribe", "quotes": list(self.store.symbols),
                                              "bars": list(self.store.symbols), "updatedBars": list(self.store.symbols)}))
                    subscribe_sent = True
                elif raw.get("T") == "subscription":
                    if not authenticated or not subscribe_sent:
                        raise ValueError("subscription before authentication")
                    expected = set(self.store.symbols)
                    if any(set(raw.get(k, [])) != expected for k in ("quotes", "bars", "updatedBars")):
                        raise ValueError("subscription coverage mismatch")
                    self.store.subscribed = expected
                    self.store.connection = "WARMING"
                    if self.bootstrap:
                        await self.bootstrap(feed, session_id)
                elif raw.get("T") in {"q", "b", "u", "s"}:
                    if not authenticated or self.store.subscribed != set(self.store.symbols):
                        raise ValueError("data before verified subscriptions")
                    self.sequence += 1
                    now = datetime.now(timezone.utc)
                    event = normalize(raw, generation=generation, sequence=self.sequence, feed=feed,
                                      session=session_id.split("/")[-1], session_id=session_id, received_at=now)
                    if self.store.apply(event):
                        await self.on_event(event)
                    ready = all(self.store.snapshot(s, now)["evaluable"] for s in self.store.symbols)
                    self.store.connection = "HEALTHY" if ready else "WARMING"

    async def run(self, feed, session_id):
        if feed not in ENDPOINTS or not self.api_key or not self.api_secret:
            raise ValueError("valid feed and observation credentials required")
        self.running = True
        failures = 0
        try:
            while self.running:
                self.store.connection = "CONNECTING"
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
                    failures += 1
                self.store.connection = "DISCONNECTED"
                self.store.subscribed.clear()
                self.reconnects += 1
                await asyncio.sleep(min(30, 2 ** min(failures, 5)) + random.uniform(0, .25))
        finally:
            self.store.connection = "DISCONNECTED"
            self.store.subscribed.clear()
            self.running = False
