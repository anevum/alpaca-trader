"""Integration verification of the isolated RHEN Cloud PostgreSQL order journal.

Runs ONLY when TEST_DATABASE_URL points at the temporary CI Postgres instance.
No member OAuth credentials, live Alpaca calls or founder RHEN DB are involved.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.member_live import (
    LiveMemberGateway, LiveOrderDenied, LiveRelease, LiveIntent,
    MemberBinding, PostgresLiveOrderJournal
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="isolated CI PostgreSQL required"
)

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "db" / "member_live" / "0001_member_live_journal.sql"
NOW = 1800000000


def migrate():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def binding(member: str | None = None, *, connection="connection-a", account="broker-a"):
    return MemberBinding(member or "member-" + uuid4().hex, connection, account, "live")


def release():
    return LiveRelease("member-live-v1", "a" * 64, ("SPY",))


def signal(signal_id="signal-a", *, quantity=2):
    return LiveIntent(signal_id, "member-live-v1", "SPY", "buy", quantity, 1000, NOW)


def test_isolated_schema_and_idempotent_replay_survive_restart():
    migrate()
    b = binding()
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        journal = PostgresLiveOrderJournal(conn)
        # Default-off live gateway accepts the storage but does not dispatch.
        with pytest.raises(LiveOrderDenied, match="OFF"):
            LiveMemberGateway(journal).submit(
                b, None, None, None, signal(), None, None, now=NOW
            )
        receipt, fresh = journal.reserve(b, release(), signal(), NOW)
        assert fresh and receipt.state == "reserved"
        same, fresh = journal.reserve(b, release(), signal(), NOW)
        assert not fresh and same == receipt
        with pytest.raises(LiveOrderDenied, match="Duplicate signal ID"):
            journal.reserve(b, release(), signal(quantity=3), NOW)
        assert journal.list_owned(b) == (receipt,)
        assert journal.read(binding("member-other", connection=b.connection_id, account=b.broker_account_id), "signal-a") is None
        assert journal.transition(b, "signal-a", from_state="reserved", to_state="uncertain").state == "uncertain"
        with pytest.raises(LiveOrderDenied):
            journal.transition(b, "signal-a", from_state="uncertain", to_state="blocked")
        confirmed = journal.transition(
            b, "signal-a", from_state="uncertain", to_state="confirmed",
            broker_order_id="broker-order-1", broker_status="accepted"
        )
        assert confirmed.broker_order_id == "broker-order-1"
        assert confirmed.state == "confirmed"
        with pytest.raises(LiveOrderDenied):
            journal.transition(b, "signal-a", from_state="confirmed", to_state="uncertain")

    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        journal = PostgresLiveOrderJournal(conn)
        repeat, fresh = journal.reserve(b, release(), signal(), NOW)
        assert not fresh
        assert repeat == confirmed
        assert journal.list_owned(b)[0] == confirmed


def test_two_members_with_identical_signal_ids_remain_isolated():
    migrate()
    first = binding("test-a-" + uuid4().hex)
    second = binding("test-b-" + uuid4().hex, connection="connection-b", account="broker-b")
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        journal = PostgresLiveOrderJournal(conn)
        a, new_a = journal.reserve(first, release(), signal("both-members"), NOW)
        b, new_b = journal.reserve(second, release(), signal("both-members"), NOW)
        assert new_a and new_b
        assert a.client_order_id != b.client_order_id
        assert journal.read(first, "both-members") == a
        assert journal.read(second, "both-members") == b
        assert journal.list_owned(first) == (a,)
        assert journal.list_owned(second) == (b,)
        mismatched = MemberBinding(first.member_id, first.connection_id, "different-broker", "live")
        assert journal.read(mismatched, "both-members") is None
        with pytest.raises(LiveOrderDenied, match="different brokerage"):
            journal.reserve(mismatched, release(), signal("new-signal"), NOW)


def test_concurrent_workers_observe_exactly_one_first_reservation():
    migrate()
    b = binding("concurrent-" + uuid4().hex)
    def run(worker_id: int):
        # Separate connections represent independent RHEN Cloud workers.
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
            journal = PostgresLiveOrderJournal(conn)
            return journal.reserve(b, release(), signal("same-id"), NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, range(2)))
    assert sorted(is_new for _, is_new in results) == [False, True]
    assert results[0][0] == results[1][0]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        journal = PostgresLiveOrderJournal(conn)
        assert len(journal.list_owned(b)) == 1


def test_requires_isolated_autocommit_database_and_valid_state_transition():
    migrate()
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as non_auto:
        with pytest.raises(LiveOrderDenied, match="autocommit"):
            PostgresLiveOrderJournal(non_auto)
    b = binding("invalid-" + uuid4().hex)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        journal = PostgresLiveOrderJournal(conn)
        with pytest.raises(LiveOrderDenied):
            journal.transition(b, "missing", from_state="reserved", to_state="confirmed")
        with pytest.raises(LiveOrderDenied):
            journal.list_owned(b, limit=0)
        with pytest.raises(LiveOrderDenied):
            journal.reserve(b, release(), signal(), now=0)
