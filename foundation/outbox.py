from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class ClaimedBatch:
    lease_id: str
    events: list[dict[str, Any]]


class DurableEventOutbox:
    """SQLite-backed evidence spool.

    The database is expected to live on persistent storage. WAL + FULL
    synchronous mode trades a small amount of throughput for stronger durability.
    Event keys are unique so replaying a process or broker reconciliation remains
    idempotent.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma synchronous=FULL")
        conn.execute("pragma busy_timeout=10000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists evidence_outbox (
                    event_key text primary key,
                    event_json text not null,
                    attempts integer not null default 0,
                    next_attempt_at real not null default 0,
                    lease_id text,
                    lease_until real,
                    last_error text,
                    created_at real not null,
                    updated_at real not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists evidence_outbox_due_idx
                on evidence_outbox (next_attempt_at, lease_until)
                """
            )

    def enqueue(self, event: dict[str, Any]) -> bool:
        event_key = str(event.get("event_key") or "").strip()
        if not event_key:
            raise ValueError("event_key is required")
        encoded = json.dumps(event, separators=(",", ":"), default=str)
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into evidence_outbox (
                    event_key, event_json, created_at, updated_at
                )
                values (?, ?, ?, ?)
                """,
                (event_key, encoded, now, now),
            )
            return cursor.rowcount == 1

    def enqueue_many(self, events: list[dict[str, Any]]) -> int:
        inserted = 0
        for event in events:
            inserted += int(self.enqueue(event))
        return inserted

    def claim_batch(
        self,
        *,
        limit: int = 50,
        lease_seconds: float = 30.0,
    ) -> ClaimedBatch | None:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        now = time.time()
        lease_id = str(uuid4())
        with self._connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select event_key, event_json
                from evidence_outbox
                where next_attempt_at <= ?
                  and (lease_until is null or lease_until <= ?)
                order by created_at, event_key
                limit ?
                """,
                (now, now, limit),
            ).fetchall()
            if not rows:
                conn.commit()
                return None
            keys = [str(row["event_key"]) for row in rows]
            placeholders = ",".join("?" for _ in keys)
            conn.execute(
                f"""
                update evidence_outbox
                set lease_id = ?,
                    lease_until = ?,
                    updated_at = ?
                where event_key in ({placeholders})
                """,
                (lease_id, now + lease_seconds, now, *keys),
            )
            conn.commit()
        return ClaimedBatch(
            lease_id=lease_id,
            events=[json.loads(str(row["event_json"])) for row in rows],
        )

    def acknowledge(self, batch: ClaimedBatch) -> int:
        keys = [str(event["event_key"]) for event in batch.events]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                delete from evidence_outbox
                where lease_id = ?
                  and event_key in ({placeholders})
                """,
                (batch.lease_id, *keys),
            )
            return cursor.rowcount

    def fail(
        self,
        batch: ClaimedBatch,
        error: str,
        *,
        max_backoff_seconds: float = 300.0,
    ) -> int:
        keys = [str(event["event_key"]) for event in batch.events]
        if not keys:
            return 0
        now = time.time()
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select event_key, attempts
                from evidence_outbox
                where lease_id = ?
                  and event_key in ({placeholders})
                """,
                (batch.lease_id, *keys),
            ).fetchall()
            updated = 0
            for row in rows:
                attempts = int(row["attempts"]) + 1
                backoff = min(max_backoff_seconds, float(2 ** min(attempts, 8)))
                cursor = conn.execute(
                    """
                    update evidence_outbox
                    set attempts = ?,
                        next_attempt_at = ?,
                        lease_id = null,
                        lease_until = null,
                        last_error = ?,
                        updated_at = ?
                    where event_key = ?
                    """,
                    (
                        attempts,
                        now + backoff,
                        error[:1000],
                        now,
                        str(row["event_key"]),
                    ),
                )
                updated += cursor.rowcount
            return updated

    def stats(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                    count(*) as queued,
                    sum(case when lease_until > ? then 1 else 0 end) as leased,
                    max(attempts) as max_attempts,
                    min(created_at) as oldest_created_at
                from evidence_outbox
                """,
                (now,),
            ).fetchone()
        return {
            "queued": int(row["queued"] or 0),
            "leased": int(row["leased"] or 0),
            "max_attempts": int(row["max_attempts"] or 0),
            "oldest_created_at": row["oldest_created_at"],
        }


class FoundationShadowSink:
    """Delivers durable shadow evidence to Foundation ingest.

    Enqueue is synchronous and durable. Network delivery is a separate step, so
    a destination outage cannot silently discard the event.
    """

    def __init__(
        self,
        *,
        outbox: DurableEventOutbox,
        ingest_url: str,
        timeout_seconds: float = 8.0,
    ):
        self.outbox = outbox
        self.ingest_url = ingest_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def enqueue(self, event: dict[str, Any]) -> bool:
        return self.outbox.enqueue(event)

    def enqueue_many(self, events: list[dict[str, Any]]) -> int:
        return self.outbox.enqueue_many(events)

    async def flush_once(self, *, limit: int = 50) -> dict[str, Any]:
        batch = self.outbox.claim_batch(limit=limit)
        if batch is None:
            return {"ok": True, "empty": True, "delivered": 0}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as http:
                response = await http.post(
                    self.ingest_url,
                    json={"events": batch.events},
                )
                response.raise_for_status()
                payload = response.json()
            acknowledged = self.outbox.acknowledge(batch)
            return {
                "ok": True,
                "empty": False,
                "delivered": acknowledged,
                "remote": payload,
            }
        except Exception as exc:
            self.outbox.fail(batch, f"{type(exc).__name__}: {exc}")
            return {
                "ok": False,
                "empty": False,
                "delivered": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
