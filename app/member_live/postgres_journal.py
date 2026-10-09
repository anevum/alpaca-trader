"""PostgreSQL durable member-live journal; NOT the founder's RHEN database.

Connections MUST point to a dedicated RHEN Cloud database, with autocommit=True.
No network broker calls occur here. The account advisory transaction lock
serializes reservations, and per-member uniqueness makes retries idempotent.
This alone does NOT provide complete live risk reservations or worker fencing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .contracts import LiveOrderDenied, LiveRelease, LiveIntent, MemberBinding
from .gateway import LiveReceipt, _client_id, _stable_digest

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "db" / "member_live" / "0001_member_live_journal.sql"
TABLE = "member_live.order_intents"


def _account_lock_key(binding: MemberBinding) -> int:
    seed = "\x1f".join((binding.member_id, binding.connection_id, binding.broker_account_id))
    return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big", signed=True)


class PostgresLiveOrderJournal:
    """Single-worker connection handle; open one connection per concurrent worker.

    No migration is applied by the class. No global connection pool is assumed.
    The transaction lock is *per bound account*, not a database-wide lock.
    """

    def __init__(self, conn: psycopg.Connection):
        if not isinstance(conn, psycopg.Connection) or not conn.autocommit or conn.closed:
            raise LiveOrderDenied("Dedicated autocommit PostgreSQL connection required")
        self._db = conn
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('member_live.order_intents')")
            if cur.fetchone()[0] != TABLE:
                raise LiveOrderDenied("Isolated member-live order migration is not installed")

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _receipt(row: dict) -> LiveReceipt:
        return LiveReceipt(
            row["member_id"], row["connection_id"], row["signal_id"],
            row["client_order_id"], row["state"], row["broker_order_id"],
            row["broker_status"]
        )

    @staticmethod
    def _check_binding(binding: MemberBinding, signal_id: str | None = None) -> None:
        if not isinstance(binding, MemberBinding):
            raise LiveOrderDenied("Server-verified member binding is required")
        if signal_id is not None and (not isinstance(signal_id, str) or len(signal_id) > 128 or not signal_id):
            raise LiveOrderDenied("Valid signal ID required")

    def reserve(
        self, binding: MemberBinding, release: LiveRelease,
        intent: LiveIntent, now: int
    ) -> tuple[LiveReceipt, bool]:
        self._check_binding(binding)
        if not isinstance(release, LiveRelease) or not isinstance(intent, LiveIntent) or type(now) is not int or now <= 0:
            raise LiveOrderDenied("Trusted member order intent required")
        digest = _stable_digest(binding, release, intent)
        client_order_id = _client_id(binding, intent)
        with self._db.transaction():
            with self._db.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_account_lock_key(binding),))
                cur.execute(
                    f"SELECT * FROM {TABLE} WHERE member_id=%s AND connection_id=%s"
                    " AND broker_account_id=%s AND signal_id=%s FOR UPDATE",
                    (binding.member_id, binding.connection_id, binding.broker_account_id, intent.signal_id)
                )
                existing = cur.fetchone()
                if existing:
                    if existing["digest"] != digest:
                        raise LiveOrderDenied("Duplicate signal ID with changed live order payload")
                    return self._receipt(existing), False
                # The connection may have been rebound to a different brokerage
                # account. Never allow outstanding intents to silently cross it.
                cur.execute(
                    f"SELECT 1 FROM {TABLE} WHERE member_id=%s AND connection_id=%s"
                    " AND broker_account_id<>%s LIMIT 1",
                    (binding.member_id, binding.connection_id, binding.broker_account_id)
                )
                if cur.fetchone():
                    raise LiveOrderDenied("Connection ID is bound to a different brokerage account")
                cur.execute(
                    f"INSERT INTO {TABLE}("
                    "member_id,connection_id,broker_account_id,signal_id,digest,"
                    "client_order_id,state,created_at) VALUES (%s,%s,%s,%s,%s,%s,'reserved',%s)"
                    " RETURNING *",
                    (binding.member_id, binding.connection_id, binding.broker_account_id,
                     intent.signal_id, digest, client_order_id, now)
                )
                return self._receipt(cur.fetchone()), True

    def transition(
        self, binding: MemberBinding, signal_id: str, *,
        from_state: str, to_state: str,
        broker_order_id: str | None = None, broker_status: str | None = None
    ) -> LiveReceipt:
        self._check_binding(binding, signal_id)
        valid_edges = {
            ("reserved", "uncertain"),
            ("reserved", "blocked"),
            ("uncertain", "confirmed")
        }
        if (from_state, to_state) not in valid_edges:
            raise LiveOrderDenied("Invalid or unsafe journal state transition")
        if broker_order_id is not None and (not isinstance(broker_order_id, str) or len(broker_order_id) > 128):
            raise LiveOrderDenied("Invalid broker order ID")
        if broker_status is not None and (not isinstance(broker_status, str) or len(broker_status) > 128):
            raise LiveOrderDenied("Invalid broker status")
        with self._db.transaction():
            with self._db.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"UPDATE {TABLE} SET state=%s, broker_order_id=COALESCE(%s,broker_order_id),"
                    " broker_status=COALESCE(%s,broker_status), updated_at=now()"
                    " WHERE member_id=%s AND connection_id=%s AND broker_account_id=%s"
                    " AND signal_id=%s AND state=%s RETURNING *",
                    (to_state, broker_order_id, broker_status,
                     binding.member_id, binding.connection_id, binding.broker_account_id,
                     signal_id, from_state)
                )
                row = cur.fetchone()
                if row is None:
                    raise LiveOrderDenied("Journal state changed; manual reconciliation required")
                return self._receipt(row)

    def read(self, binding: MemberBinding, signal_id: str) -> LiveReceipt | None:
        self._check_binding(binding, signal_id)
        with self._db.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM {TABLE} WHERE member_id=%s AND connection_id=%s"
                " AND broker_account_id=%s AND signal_id=%s",
                (binding.member_id, binding.connection_id, binding.broker_account_id, signal_id)
            )
            row = cur.fetchone()
            return self._receipt(row) if row else None

    def list_owned(self, binding: MemberBinding, limit: int = 25) -> tuple[LiveReceipt, ...]:
        self._check_binding(binding)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise LiveOrderDenied("Invalid order-history page size")
        with self._db.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM {TABLE} WHERE member_id=%s AND connection_id=%s"
                " AND broker_account_id=%s ORDER BY created_at DESC, client_order_id DESC LIMIT %s",
                (binding.member_id, binding.connection_id, binding.broker_account_id, limit)
            )
            return tuple(self._receipt(row) for row in cur.fetchall())
