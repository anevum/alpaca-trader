"""Durable broker observation inbox; canonical reconciliation remains 4.3-owned.

trade_updates are retained before projection. This cannot submit/cancel orders,
infer position quantity from prices, or replace canonical broker reconciliation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from websockets.asyncio.client import connect

from .contracts import utc, number


class BrokerInbox:
    def __init__(self, path, *, max_pending=10000, max_delivered=2400):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS broker_inbox (id TEXT PRIMARY KEY, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)")
        self.max_pending, self.max_delivered = max_pending, max_delivered

    def retain(self, data):
        order = data.get("order") or {}
        if not order.get("id") or not order.get("symbol") or not data.get("event"):
            raise ValueError("broker identity missing")
        timestamp = utc(data.get("timestamp") or order.get("updated_at")).isoformat()
        identity = {"order_id": order["id"], "event": data["event"], "timestamp": timestamp,
                    "execution_id": data.get("execution_id"), "qty": data.get("qty"), "filled_qty": order.get("filled_qty")}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self.db:
            if self.db.execute("SELECT 1 FROM broker_inbox WHERE id=?", (key,)).fetchone():
                return key
            pending = self.db.execute("SELECT count(*) FROM broker_inbox WHERE delivered=0").fetchone()[0]
            if pending >= self.max_pending:
                raise RuntimeError("broker inbox overloaded; observation blocked; reconcile required")
            self.db.execute("INSERT INTO broker_inbox(id,body) VALUES (?,?)", (key, json.dumps(data, allow_nan=False)))
        return key

    async def deliver(self, callback):
        for key, body in self.db.execute("SELECT id,body FROM broker_inbox WHERE delivered=0 ORDER BY rowid").fetchall():
            data = json.loads(body)
            if await callback(key, data) is not True:
                raise RuntimeError("canonical observation sink not acknowledged")
            with self.db:
                self.db.execute("UPDATE broker_inbox SET delivered=1 WHERE id=?", (key,))
        with self.db:
            self.db.execute("DELETE FROM broker_inbox WHERE delivered=1 AND rowid NOT IN (SELECT rowid FROM broker_inbox WHERE delivered=1 ORDER BY rowid DESC LIMIT ?)", (self.max_delivered,))

    def close(self):
        self.db.close()


def execution_marker(event_id, data):
    kind = {"new": "ACCEPTED", "fill": "FILL", "partial_fill": "PARTIAL_FILL", "canceled": "CANCEL", "rejected": "REJECT"}.get(data.get("event"))
    if not kind:
        return None
    order = data["order"]
    # Price is the broker's reported fill price, never a market-price inference.
    return {"event_id": event_id, "timestamp": utc(data.get("timestamp") or order.get("updated_at")).isoformat(),
            "symbol": order["symbol"], "event_type": kind, "side": order.get("side"),
            "quantity": number(data["qty"]) if data.get("qty") is not None else None,
            "price": number(data["price"], positive=True) if data.get("price") is not None else None,
            "order_ref": order["id"], "provenance": "OBSERVED", "source": "ALPACA/trade_updates", "quality_state": "LIVE"}


class BrokerUpdateStream:
    def __init__(self, *, api_key, api_secret, paper, inbox, callback, connect_factory=connect):
        self.api_key, self.api_secret, self.paper = api_key, api_secret, paper
        self.inbox, self.callback, self.connect_factory = inbox, callback, connect_factory
        self.state = "DISCONNECTED"
        self.last_error = None

    async def consume(self, ws):
        await ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
        authenticated = False
        async for raw in ws:
            message = json.loads(raw)
            stream, data = message.get("stream"), message.get("data") or {}
            if stream == "authorization":
                if data.get("status") != "authorized":
                    raise ValueError("broker stream unauthorized")
                authenticated = True
                await self.inbox.deliver(self.callback)
                await ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            elif stream == "listening":
                if not authenticated or set(data.get("streams", [])) != {"trade_updates"}:
                    raise ValueError("broker subscription mismatch")
                self.state = "HEALTHY"
            elif stream == "trade_updates":
                if self.state != "HEALTHY":
                    raise ValueError("broker update before authorization")
                self.inbox.retain(data)
                await self.inbox.deliver(self.callback)

    async def run(self):
        endpoint = "wss://paper-api.alpaca.markets/stream" if self.paper else "wss://api.alpaca.markets/stream"
        failures = 0
        try:
            while True:
                self.state = "CONNECTING"
                try:
                    async with self.connect_factory(endpoint, max_queue=10000, open_timeout=10) as ws:
                        await self.consume(ws)
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    self.last_error = type(exc).__name__
                self.state = "DISCONNECTED"
                await asyncio.sleep(min(30, 2**min(failures, 5)))
        finally:
            self.state = "DISCONNECTED"
