"""One ordered protected client stream. Slow clients must re-snapshot."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4


class LivePublisher:
    def __init__(self, snapshot, *, flush_ms=125, client_capacity=64):
        if not 100 <= flush_ms <= 250:
            raise ValueError("Command cadence outside contract")
        self.snapshot = snapshot
        self.flush_ms, self.client_capacity = flush_ms, client_capacity
        self.generation = uuid4().hex
        self.sequence = 0
        self.clients = set()
        self.dirty = {}
        self.slow_clients = 0
        self.max_tick_lag_ms = 0.0

    def envelope(self, kind, payload):
        self.sequence += 1
        return {"schema_version": "command-live.v1", "message_type": kind,
                "server_time": datetime.now(timezone.utc).isoformat(), "stream_generation": self.generation,
                "sequence": self.sequence, "payload": payload}

    def subscribe(self):
        queue = asyncio.Queue(maxsize=self.client_capacity)
        # Synchronous subscription ensures the snapshot is queued before any delta.
        queue.put_nowait(self.envelope("snapshot", self.snapshot()))
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.clients.discard(queue)

    def send(self, kind, payload):
        message = self.envelope(kind, payload)
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.clients.discard(queue)
                # Client transport is ephemeral. Disconnect and require a snapshot,
                # never claim a missing broker event was successfully delivered.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
                self.slow_clients += 1

    def stage(self, key, kind, payload):
        if len(self.dirty) >= 10000 and key not in self.dirty:
            self.flush()
        self.dirty[key] = {"message_type": kind, "payload": payload}

    def flush(self):
        if self.dirty:
            batch, self.dirty = list(self.dirty.values()), {}
            self.send("delta_batch", {"events": batch})

    async def run(self):
        last_heartbeat = asyncio.get_running_loop().time()
        last_tick = last_heartbeat
        while True:
            await asyncio.sleep(self.flush_ms/1000)
            now = asyncio.get_running_loop().time()
            self.max_tick_lag_ms = max(self.max_tick_lag_ms, (now-last_tick)*1000-self.flush_ms)
            last_tick = now
            self.flush()
            if now-last_heartbeat >= 2:
                self.send("heartbeat", self.snapshot()["system"])
                last_heartbeat = now
