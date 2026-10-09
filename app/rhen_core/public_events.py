"""Bounded read-only push bus for RHEN's existing aggregate public projection.

The event ingest path only schedules a tiny notification on the Core event loop.
Costly SQLite projections happen on-demand, off-loop, and are shared across
subscribers for two seconds. No raw events or broker secrets leave this bus.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable


class PublicEventBus:
    MAX_CLIENTS = 24
    MAX_CACHE_SECONDS = 2.0

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.revision = 0
        self.clients: set[asyncio.Queue[int]] = set()
        self._cache: dict[str, Any] | None = None
        self._cache_revision = -1
        self._cache_until = 0.0
        self._cache_lock = asyncio.Lock()

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()

    def stop(self) -> None:
        self.loop = None
        self.clients.clear()
        self._cache = None
        self._cache_revision = -1

    def signal(self) -> None:
        """Safe from the synchronous FastAPI ingress thread; never fail ingest."""
        loop = self.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._notify)
        except RuntimeError:
            # Shutdown raced the final canonical write. Persistence is unchanged.
            return

    def _notify(self) -> None:
        self.revision += 1
        for client in tuple(self.clients):
            if client.full():
                client.get_nowait()
            client.put_nowait(self.revision)

    def subscribe(self) -> asyncio.Queue[int]:
        if self.loop is None:
            raise RuntimeError("public stream is not running")
        if len(self.clients) >= self.MAX_CLIENTS:
            raise RuntimeError("public stream client capacity reached")
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[int]) -> None:
        self.clients.discard(queue)

    async def snapshot(
        self,
        read_public_projection: Callable[[], dict[str, Any]],
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """One validated aggregate-only projection per revision/cadence."""
        now = asyncio.get_running_loop().time()
        if (
            not refresh and self._cache is not None
            and self._cache_revision == self.revision
            and now < self._cache_until
        ):
            return self._cache
        async with self._cache_lock:
            now = asyncio.get_running_loop().time()
            if (
                not refresh and self._cache is not None
                and self._cache_revision == self.revision
                and now < self._cache_until
            ):
                return self._cache
            revision = self.revision
            snapshot = await asyncio.to_thread(read_public_projection)
            if not (
                isinstance(snapshot, dict)
                and snapshot.get("ok") is True
                and snapshot.get("source") == "rhen-core-sqlite"
                and (snapshot.get("disclosure") or {}).get("level")
                == "aggregate_only"
            ):
                raise ValueError("unsafe or unavailable public projection")
            self._cache = snapshot
            self._cache_revision = revision
            self._cache_until = asyncio.get_running_loop().time() + self.MAX_CACHE_SECONDS
            return snapshot

    @staticmethod
    def frame(snapshot: dict[str, Any]) -> str:
        if (
            snapshot.get("ok") is not True
            or snapshot.get("source") != "rhen-core-sqlite"
            or (snapshot.get("disclosure") or {}).get("level") != "aggregate_only"
        ):
            raise ValueError("private data cannot enter public event stream")
        return "event: snapshot\ndata:" + json.dumps(
            snapshot, allow_nan=False, separators=(",", ":"),
        ) + "\n\n"
