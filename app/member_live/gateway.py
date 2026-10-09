"""Durable, conservative gateway for member-owned LIVE order intents.

Development journal uses isolated SQLite, NOT the owner RHEN production volume.
A production release requires a durable multi-worker store/leases, audited token
vault, authenticated member gateway, live Connect approval and risk snapshots.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .alpaca_connect import AlpacaConnectLiveBroker
from .contracts import (
    BrokerObservation, LiveAuthority, LiveIntent, LiveOrderDenied, LivePolicy,
    LiveRelease, MemberBinding, validate_live_intent
)


@dataclass(frozen=True)
class LiveReceipt:
    member_id: str
    connection_id: str
    signal_id: str
    client_order_id: str
    state: str
    broker_order_id: str | None = None
    broker_status: str | None = None


def _stable_digest(binding: MemberBinding, release: LiveRelease, intent: LiveIntent) -> str:
    doc = {
        "broker_account_id": binding.broker_account_id,
        "connection_id": binding.connection_id,
        "release": asdict(release),
        "signal": asdict(intent),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _client_id(binding: MemberBinding, intent: LiveIntent) -> str:
    key = json.dumps([binding.member_id, binding.connection_id, intent.signal_id], separators=(",", ":"))
    return "rhcl-" + hashlib.sha256(key.encode()).hexdigest()[:46]


class LiveOrderJournal:
    """Prototype SQLite journal with atomic intent reservation.

    The production counterpart must use PostgreSQL or equivalent durable
    central persistence with idempotency, recovery and fenced leases. This
    journal must NEVER be mounted on or copied into /data/rhen-core.db.
    """

    def __init__(self, db_path: str | Path):
        path = str(db_path)
        if Path(path).name == "rhen-core.db":
            raise LiveOrderDenied("Member journal cannot use owner RHEN database")
        self._db = sqlite3.connect(path, isolation_level=None, timeout=15)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS member_live_intents (
                member_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                broker_account_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                digest TEXT NOT NULL,
                client_order_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN('reserved','uncertain','confirmed','blocked')),
                broker_order_id TEXT,
                broker_status TEXT,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(member_id, connection_id, signal_id)
            );
            CREATE INDEX IF NOT EXISTS member_live_owner_idx
                ON member_live_intents(member_id, connection_id, created_at DESC);
            """
        )

    def close(self) -> None:
        self._db.close()

    def _row(self, binding: MemberBinding, signal_id: str):
        return self._db.execute(
            "SELECT * FROM member_live_intents WHERE member_id = ? AND connection_id = ? AND signal_id = ?",
            (binding.member_id, binding.connection_id, signal_id)
        ).fetchone()

    @staticmethod
    def _receipt(row) -> LiveReceipt:
        return LiveReceipt(
            row["member_id"], row["connection_id"], row["signal_id"], row["client_order_id"],
            row["state"], row["broker_order_id"], row["broker_status"]
        )

    def reserve(self, binding: MemberBinding, release: LiveRelease, intent: LiveIntent, now: int) -> tuple[LiveReceipt, bool]:
        if not isinstance(binding, MemberBinding) or not isinstance(release, LiveRelease) or not isinstance(intent, LiveIntent):
            raise LiveOrderDenied("Trusted member order input required")
        digest = _stable_digest(binding, release, intent)
        client_id = _client_id(binding, intent)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._row(binding, intent.signal_id)
            if existing:
                if existing["digest"] != digest or existing["broker_account_id"] != binding.broker_account_id:
                    raise LiveOrderDenied("Duplicate signal ID with changed live order payload")
                self._db.execute("COMMIT")
                return self._receipt(existing), False
            self._db.execute(
                "INSERT INTO member_live_intents(member_id,connection_id,broker_account_id,"
                "signal_id,digest,client_order_id,state,created_at) VALUES(?,?,?,?,?,?,'reserved',?)",
                (binding.member_id, binding.connection_id, binding.broker_account_id,
                 intent.signal_id, digest, client_id, now)
            )
            row = self._row(binding, intent.signal_id)
            self._db.execute("COMMIT")
            return self._receipt(row), True
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def transition(
        self, binding: MemberBinding, signal_id: str, *,
        from_state: str, to_state: str,
        broker_order_id: str | None = None, broker_status: str | None = None
    ) -> LiveReceipt:
        if to_state not in ("uncertain", "confirmed", "blocked"):
            raise LiveOrderDenied("Invalid order state transition")
        result = self._db.execute(
            "UPDATE member_live_intents SET state=?, broker_order_id=COALESCE(?,broker_order_id),"
            "broker_status=COALESCE(?,broker_status) "
            "WHERE member_id=? AND connection_id=? AND broker_account_id=? AND signal_id=? AND state=?",
            (to_state, broker_order_id, broker_status, binding.member_id, binding.connection_id,
             binding.broker_account_id, signal_id, from_state)
        )
        if result.rowcount != 1:
            raise LiveOrderDenied("Order state changed; manual reconciliation required")
        return self._receipt(self._row(binding, signal_id))

    def read(self, binding: MemberBinding, signal_id: str) -> LiveReceipt | None:
        if not isinstance(binding, MemberBinding):
            raise LiveOrderDenied("Authenticated binding required")
        row = self._row(binding, signal_id)
        return self._receipt(row) if row else None

    def list_owned(self, binding: MemberBinding, limit: int = 25) -> tuple[LiveReceipt, ...]:
        if not isinstance(binding, MemberBinding) or type(limit) is not int or not 1 <= limit <= 100:
            raise LiveOrderDenied("Invalid member scope")
        rows = self._db.execute(
            "SELECT * FROM member_live_intents WHERE member_id=? AND connection_id=?"
            " ORDER BY created_at DESC, client_order_id DESC LIMIT ?",
            (binding.member_id, binding.connection_id, limit)
        ).fetchall()
        return tuple(self._receipt(row) for row in rows)


class LiveMemberGateway:
    """The *only* entry to live order writes from a verified member backend.

    Deliberately fails closed by default. In production, network_writes_enabled
    MUST be server/operator-controlled, not a setting on the member profile.
    """

    def __init__(self, journal: LiveOrderJournal, *, network_writes_enabled: bool = False):
        if not isinstance(journal, LiveOrderJournal) or type(network_writes_enabled) is not bool:
            raise LiveOrderDenied("Isolated live order journal required")
        self._journal = journal
        self._enabled = network_writes_enabled

    @staticmethod
    def _require_adapter(binding: MemberBinding, broker: AlpacaConnectLiveBroker):
        if not isinstance(broker, AlpacaConnectLiveBroker) or broker._binding != binding:
            raise LiveOrderDenied("An isolated, account-bound Alpaca Connect adapter is required")

    def submit(
        self, binding: MemberBinding, authority: LiveAuthority, release: LiveRelease,
        policy: LivePolicy, intent: LiveIntent, observation: BrokerObservation,
        broker: AlpacaConnectLiveBroker, *, now: int | None = None
    ) -> LiveReceipt:
        if not self._enabled:
            raise LiveOrderDenied("RHEN Cloud member LIVE broker-write path is OFF")
        epoch = int(time.time()) if now is None else now
        validate_live_intent(binding, authority, release, policy, intent, observation, now=epoch)
        self._require_adapter(binding, broker)

        receipt, first = self._journal.reserve(binding, release, intent, epoch)
        if not first:
            # DO NOT automatically retry a reserved or uncertain network operation:
            # the broker may already have received an order.
            return receipt
        try:
            broker.verify_account()
        except Exception:
            return self._journal.transition(
                binding, intent.signal_id, from_state="reserved", to_state="blocked"
            )
        # Mark ambiguous BEFORE the network call to tolerate a crash or timeout.
        uncertain = self._journal.transition(
            binding, intent.signal_id, from_state="reserved", to_state="uncertain"
        )
        try:
            order = broker.submit_limit_day(intent, uncertain.client_order_id)
            return self._journal.transition(
                binding, intent.signal_id, from_state="uncertain", to_state="confirmed",
                broker_order_id=str(order["broker_order_id"]), broker_status=str(order["status"])
            )
        except Exception:
            # Any timeout/exception is ambiguous. Reconcile by client_order_id, NEVER blindly resend.
            return uncertain

    def reconcile(
        self, binding: MemberBinding, authority: LiveAuthority, signal_id: str,
        broker: AlpacaConnectLiveBroker
    ) -> LiveReceipt:
        if not isinstance(authority, LiveAuthority) or not authority.session_verified or (
            not authority.broker_grant_valid or authority.revoked
        ):
            raise LiveOrderDenied("Broker reconciliation requires a current server-verified grant")
        self._require_adapter(binding, broker)
        receipt = self._journal.read(binding, signal_id)
        if not receipt:
            raise LiveOrderDenied("No order exists for this member and connection")
        if receipt.state == "confirmed":
            return receipt
        if receipt.state != "uncertain":
            return receipt
        broker.verify_account()
        order = broker.find_by_client_order_id(receipt.client_order_id)
        if order is None:
            return receipt
        return self._journal.transition(
            binding, signal_id, from_state="uncertain", to_state="confirmed",
            broker_order_id=str(order["broker_order_id"]), broker_status=str(order["status"])
        )
