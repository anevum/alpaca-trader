"""Real PostgreSQL tests for consolidated broker-money metadata."""
import os
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="isolated PostgreSQL required",
)


class RollbackTest(Exception):
    pass


@pytest.fixture(name="conn")
def isolated_conn():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as db:
        try:
            with db.transaction():
                yield db
                raise RollbackTest()
        except RollbackTest:
            pass


def _tenant_and_broker(conn, key: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.tenants(tenant_key,display_name,status)
            values(%s,%s,'REGISTERED')
            returning tenant_id
            """,
            (key, key),
        )
        tenant_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into anevum.broker_accounts(
                tenant_id,provider,provider_account_id,environment,account_status
            )
            values(%s,'ALPACA',%s,'PAPER','ACTIVE')
            returning broker_account_id
            """,
            (tenant_id, f"alpaca-{key}"),
        )
        broker_account_id = cur.fetchone()[0]
    return tenant_id, broker_account_id


def test_transfer_metadata_is_tenant_scoped(conn):
    tenant_a, broker_a = _tenant_and_broker(conn, f"a-{uuid4()}")
    tenant_b, broker_b = _tenant_and_broker(conn, f"b-{uuid4()}")

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_funding_relationships(
                tenant_id,broker_account_id,provider_relationship_id,status
            )
            values(%s,%s,%s,'ACTIVE')
            returning funding_relationship_id
            """,
            (tenant_a, broker_a, f"rel-{uuid4()}"),
        )
        relationship_a = cur.fetchone()[0]

    with pytest.raises(psycopg.IntegrityError):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.broker_transfer_intents(
                        idempotency_key,tenant_id,broker_account_id,
                        funding_relationship_id,direction,amount,initiated_by
                    )
                    values(%s,%s,%s,%s,'DEPOSIT',10,'test')
                    """,
                    (f"x-{uuid4()}", tenant_b, broker_b, relationship_a),
                )


def test_transfer_idempotency_is_database_enforced(conn):
    tenant_id, broker_id = _tenant_and_broker(conn, f"idem-{uuid4()}")
    key = f"transfer-{uuid4()}"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_transfer_intents(
                idempotency_key,tenant_id,broker_account_id,
                direction,amount,initiated_by
            )
            values(%s,%s,%s,'DEPOSIT',10,'test')
            """,
            (key, tenant_id, broker_id),
        )

    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.broker_transfer_intents(
                        idempotency_key,tenant_id,broker_account_id,
                        direction,amount,initiated_by
                    )
                    values(%s,%s,%s,'DEPOSIT',10,'test')
                    """,
                    (key, tenant_id, broker_id),
                )


def test_transfer_events_are_append_only(conn):
    tenant_id, broker_id = _tenant_and_broker(conn, f"event-{uuid4()}")

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_transfer_intents(
                idempotency_key,tenant_id,broker_account_id,
                direction,amount,initiated_by
            )
            values(%s,%s,%s,'DEPOSIT',10,'test')
            returning transfer_intent_id
            """,
            (f"transfer-{uuid4()}", tenant_id, broker_id),
        )
        transfer_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into anevum.broker_transfer_events(
                event_key,tenant_id,transfer_intent_id,event_type,status
            )
            values(%s,%s,%s,'OBSERVED','PENDING')
            returning transfer_event_id
            """,
            (f"event-{uuid4()}", tenant_id, transfer_id),
        )
        event_id = cur.fetchone()[0]

    with pytest.raises(psycopg.errors.RaiseException, match="append_only"):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update anevum.broker_transfer_events
                    set status='SETTLED'
                    where transfer_event_id=%s
                    """,
                    (event_id,),
                )
