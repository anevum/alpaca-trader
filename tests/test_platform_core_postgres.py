"""Real PostgreSQL lifecycle tests for ANEVUM Command Platform Core v1."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app.platform_core.reconciliation import BrokerReconciliationResult
from foundation.platform_core_gateway import (
    load_tenant_broker_account,
    record_broker_reconciliation,
    tenant_execution_eligibility,
)


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


def setup_ready_paper_tenant(conn):
    tenant_id = uuid4()
    principal_id = uuid4()
    broker_account_id = uuid4()
    provider_account_id = f"alpaca-{uuid4()}"
    release_id = f"RHEN-BTC-TEST-{uuid4()}"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.tenants(tenant_id,tenant_key,display_name,status)
            values(%s,%s,'Test Tenant','ACTIVE')
            """,
            (tenant_id, f"tenant-{uuid4()}"),
        )
        cur.execute(
            """
            insert into anevum.principals(principal_id,external_subject,email,status)
            values(%s,%s,%s,'ACTIVE')
            """,
            (principal_id, f"subject-{uuid4()}", f"{uuid4()}@example.test"),
        )
        cur.execute(
            """
            insert into anevum.tenant_memberships(tenant_id,principal_id,role,status)
            values(%s,%s,'OWNER','ACTIVE')
            """,
            (tenant_id, principal_id),
        )
        cur.execute(
            """
            insert into anevum.entitlements(
                tenant_id,product_key,status,source,effective_at
            )
            values(%s,'COMMAND','ACTIVE','test',now()-interval '1 minute')
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            insert into anevum.broker_accounts(
                broker_account_id,tenant_id,provider,provider_account_id,
                environment,account_status,crypto_enabled,trading_blocked
            )
            values(%s,%s,'ALPACA',%s,'PAPER','ACTIVE',true,false)
            """,
            (broker_account_id, tenant_id, provider_account_id),
        )
        cur.execute(
            """
            insert into anevum.broker_authorizations(
                broker_account_id,authorization_kind,secret_reference,status
            )
            values(%s,'OAUTH',%s,'ACTIVE')
            """,
            (broker_account_id, f"secret://{tenant_id}/alpaca"),
        )
        cur.execute(
            """
            insert into anevum.capital_allocations(
                tenant_id,broker_account_id,allocation_fraction,absolute_cap,status
            )
            values(%s,%s,0.90,100.00,'ACTIVE')
            """,
            (tenant_id, broker_account_id),
        )
        cur.execute(
            """
            insert into anevum.risk_profiles(
                tenant_id,broker_account_id,max_position_fraction,
                max_gross_exposure_fraction,max_daily_loss_fraction,
                max_drawdown_fraction,max_concurrent_positions,status
            )
            values(%s,%s,0.25,0.90,0.05,0.20,1,'ACTIVE')
            """,
            (tenant_id, broker_account_id),
        )
        cur.execute(
            """
            insert into anevum.strategy_releases(
                strategy_release_id,strategy_key,semantic_version,channel,
                lifecycle_state,source_commit,strategy_hash,configuration_hash,
                risk_policy_version,approved_by,approved_at
            )
            values(%s,'RHEN-BTC',%s,'STABLE','STABLE',%s,%s,%s,'risk-v1','test',now())
            """,
            (
                release_id,
                f"test-{uuid4()}",
                "a" * 40,
                "strategy-hash",
                "configuration-hash",
            ),
        )
        cur.execute(
            """
            insert into anevum.tenant_strategy_assignments(
                tenant_id,broker_account_id,strategy_release_id,status,assigned_by
            )
            values(%s,%s,%s,'ACTIVE','test')
            """,
            (tenant_id, broker_account_id, release_id),
        )
        cur.execute(
            """
            insert into anevum.tenant_trading_controls(
                tenant_id,broker_account_id,bot_enabled,customer_consent_version,
                customer_consented_at,updated_by
            )
            values(%s,%s,true,'paper-consent-v1',now(),'customer')
            """,
            (tenant_id, broker_account_id),
        )
        cur.execute(
            """
            insert into iren.system_state(
                system_key,health,state,revision,observation_key,observed_at
            )
            values('IREN','HEALTHY','{}'::jsonb,1,%s,now())
            on conflict(system_key) do update
            set health='HEALTHY', observed_at=excluded.observed_at
            """,
            (f"platform-core-test-{uuid4()}",),
        )

    reconciliation = BrokerReconciliationResult(
        status="SUCCESS",
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
        environment="PAPER",
        provider="ALPACA",
        provider_account_id_expected=provider_account_id,
        provider_account_id_observed=provider_account_id,
        observed_at=datetime.now(timezone.utc),
        account_snapshot={
            "id": provider_account_id,
            "status": "ACTIVE",
            "trading_blocked": False,
            "equity": "100.00",
        },
        positions_snapshot=[],
        open_orders_snapshot=[],
        recent_orders_snapshot=[],
        snapshot_hash=f"snapshot-{uuid4()}",
        error_code=None,
        error_detail=None,
    )
    record_broker_reconciliation(conn, reconciliation)
    return tenant_id, broker_account_id, provider_account_id


def test_ready_paper_tenant_resolves_to_eligible(conn):
    tenant_id, broker_account_id, provider_account_id = setup_ready_paper_tenant(conn)

    broker = load_tenant_broker_account(
        conn,
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
    )
    assert broker.provider_account_id == provider_account_id
    assert broker.secret_reference.startswith("secret://")

    gate_input, result = tenant_execution_eligibility(
        conn,
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
    )
    assert gate_input.environment == "PAPER"
    assert gate_input.broker_reconciled is True
    assert result.eligible is True
    assert result.reasons == ()


def test_same_facts_are_not_live_eligible_without_protected_authority(conn):
    tenant_id, broker_account_id, _ = setup_ready_paper_tenant(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            update anevum.broker_accounts
            set environment='LIVE'
            where tenant_id=%s and broker_account_id=%s
            """,
            (tenant_id, broker_account_id),
        )

    gate_input, result = tenant_execution_eligibility(
        conn,
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
    )
    assert gate_input.environment == "LIVE"
    assert gate_input.live_customer_authority is False
    assert result.eligible is False
    assert "broker_not_reconciled" in result.reasons
    assert "live_customer_authority_missing" in result.reasons


def test_cross_tenant_broker_reference_is_rejected_by_database(conn):
    tenant_id, broker_account_id, _ = setup_ready_paper_tenant(conn)
    other_tenant = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.tenants(tenant_id,tenant_key,display_name,status)
            values(%s,%s,'Other Tenant','ACTIVE')
            """,
            (other_tenant, f"tenant-{uuid4()}"),
        )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.capital_allocations(
                        tenant_id,broker_account_id,allocation_fraction,absolute_cap,status
                    )
                    values(%s,%s,0.50,10.00,'ACTIVE')
                    """,
                    (other_tenant, broker_account_id),
                )

    gate_input, result = tenant_execution_eligibility(
        conn,
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
    )
    assert gate_input.broker_reconciled is True
    assert result.eligible is True


def test_stale_reconciliation_closes_execution_gate(conn):
    tenant_id, broker_account_id, _ = setup_ready_paper_tenant(conn)
    future = datetime.now(timezone.utc) + timedelta(seconds=5)
    gate_input, result = tenant_execution_eligibility(
        conn,
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
        max_reconciliation_age_seconds=1,
        now=future,
    )

    assert gate_input.broker_reconciled is False
    assert result.eligible is False
    assert "broker_not_reconciled" in result.reasons
