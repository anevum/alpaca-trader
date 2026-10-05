"""Financial Gateway lifecycle tests; isolated CI PostgreSQL only."""
import os
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from foundation.financial_gateway import (
    _settle_provider_transfer,
    initialize_customer,
    read_snapshot,
    sandbox_deposit,
    set_rhen_allocation,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="isolated PostgreSQL required",
)


def _subject() -> str:
    return f"finance-test-{uuid4()}@example.invalid"


def test_financial_gateway_double_entry_lifecycle_and_idempotency():
    database_url = os.environ["TEST_DATABASE_URL"]
    subject = _subject()

    initial = initialize_customer(
        database_url,
        command_subject=subject,
        display_label="Finance Test",
    )
    assert initial["initialized"] is True
    assert initial["balances"]["available_cash"] == "0"
    assert initial["balances"]["rhen_allocation"] == "0"
    assert initial["external_money_movement_enabled"] is False
    assert initial["live_execution_authorized"] is False

    deposited = sandbox_deposit(
        database_url,
        command_subject=subject,
        amount="100",
        idempotency_key="deposit-1",
    )
    assert deposited["balances"]["available_cash"] == "100.000000000000"
    assert deposited["balances"]["total"] == "100.000000000000"
    assert deposited["idempotent"] is False

    retried_deposit = sandbox_deposit(
        database_url,
        command_subject=subject,
        amount="100",
        idempotency_key="deposit-1",
    )
    assert retried_deposit["balances"]["available_cash"] == "100.000000000000"
    assert retried_deposit["idempotent"] is True

    allocated = set_rhen_allocation(
        database_url,
        command_subject=subject,
        target_amount="60",
        idempotency_key="allocation-1",
        execution_mode="PAPER",
        max_position_fraction="0.10",
        max_daily_loss_fraction="0.05",
        strategy_version_id="TEST-STRATEGY",
    )
    assert allocated["balances"]["available_cash"] == "40.000000000000"
    assert allocated["balances"]["rhen_allocation"] == "60.000000000000"
    assert allocated["balances"]["total"] == "100.000000000000"
    assert allocated["allocation"]["external_execution_enabled"] is False

    retried_allocation = set_rhen_allocation(
        database_url,
        command_subject=subject,
        target_amount="60",
        idempotency_key="allocation-1",
        execution_mode="PAPER",
        max_position_fraction="0.10",
        max_daily_loss_fraction="0.05",
        strategy_version_id="TEST-STRATEGY",
    )
    assert retried_allocation["balances"]["available_cash"] == "40.000000000000"
    assert retried_allocation["balances"]["rhen_allocation"] == "60.000000000000"
    assert retried_allocation["idempotent"] is True

    released = set_rhen_allocation(
        database_url,
        command_subject=subject,
        target_amount="20",
        idempotency_key="allocation-2",
        execution_mode="PAPER",
        strategy_version_id="TEST-STRATEGY",
    )
    assert released["balances"]["available_cash"] == "80.000000000000"
    assert released["balances"]["rhen_allocation"] == "20.000000000000"
    assert released["balances"]["total"] == "100.000000000000"

    with pytest.raises(ValueError, match="insufficient_available_cash"):
        set_rhen_allocation(
            database_url,
            command_subject=subject,
            target_amount="101",
            idempotency_key="allocation-too-large",
            execution_mode="PAPER",
        )

    final = read_snapshot(database_url, command_subject=subject)
    assert final["balances"]["available_cash"] == "80.000000000000"
    assert final["balances"]["rhen_allocation"] == "20.000000000000"
    assert final["balances"]["total"] == "100.000000000000"


def test_allocation_can_be_paused_at_zero_without_granting_execution():
    database_url = os.environ["TEST_DATABASE_URL"]
    subject = _subject()
    initialize_customer(database_url, command_subject=subject)
    sandbox_deposit(
        database_url,
        command_subject=subject,
        amount="10",
        idempotency_key="deposit",
    )
    set_rhen_allocation(
        database_url,
        command_subject=subject,
        target_amount="10",
        idempotency_key="allocate",
    )
    paused = set_rhen_allocation(
        database_url,
        command_subject=subject,
        target_amount="0",
        idempotency_key="pause",
    )
    assert paused["balances"]["available_cash"] == "10.000000000000"
    assert paused["balances"]["rhen_allocation"] == "0E-12"
    assert paused["allocation"]["status"] == "PAUSED"
    assert paused["allocation"]["max_allocation"] == "0E-12"
    assert paused["allocation"]["external_execution_enabled"] is False


def test_provider_transfer_settlement_posts_balanced_ledger_entries():
    database_url = os.environ["TEST_DATABASE_URL"]
    subject = _subject()
    initialized = initialize_customer(database_url, command_subject=subject)
    customer_id = initialized["customer"]["customer_id"]

    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select fa.financial_account_id, la.ledger_account_id
                    from anevum.financial_accounts fa
                    join anevum.financial_ledger_accounts la
                      on la.financial_account_id=fa.financial_account_id
                    where fa.customer_id=%s::uuid
                      and fa.account_kind='AVAILABLE_CASH'
                      and fa.currency='USD'
                    """,
                    (customer_id,),
                )
                available_account_id, available_ledger_id = cur.fetchone()
                cur.execute(
                    """
                    insert into anevum.financial_transfers (
                        idempotency_key, customer_id, financial_account_id,
                        direction, provider, provider_environment, currency,
                        amount, status, provider_reference, metadata
                    )
                    values ('provider-deposit-test',%s::uuid,%s,'DEPOSIT',
                            'ALPACA_BROKER','SANDBOX','USD',50,'PENDING',
                            'provider-transfer-deposit','{}'::jsonb)
                    returning transfer_id
                    """,
                    (customer_id, available_account_id),
                )
                deposit_transfer_id = cur.fetchone()[0]
                _settle_provider_transfer(
                    cur,
                    transfer_id=deposit_transfer_id,
                    transfer_direction="DEPOSIT",
                    amount=Decimal("50"),
                    provider_reference="provider-transfer-deposit",
                    available_ledger_id=available_ledger_id,
                )

    after_deposit = read_snapshot(database_url, command_subject=subject)
    assert after_deposit["balances"]["available_cash"] == "50.000000000000"
    assert after_deposit["balances"]["total"] == "50.000000000000"

    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select fa.financial_account_id, la.ledger_account_id
                    from anevum.financial_accounts fa
                    join anevum.financial_ledger_accounts la
                      on la.financial_account_id=fa.financial_account_id
                    where fa.customer_id=%s::uuid
                      and fa.account_kind='AVAILABLE_CASH'
                      and fa.currency='USD'
                    """,
                    (customer_id,),
                )
                available_account_id, available_ledger_id = cur.fetchone()
                cur.execute(
                    """
                    insert into anevum.financial_transfers (
                        idempotency_key, customer_id, financial_account_id,
                        direction, provider, provider_environment, currency,
                        amount, status, provider_reference, metadata
                    )
                    values ('provider-withdraw-test',%s::uuid,%s,'WITHDRAWAL',
                            'ALPACA_BROKER','SANDBOX','USD',20,'PENDING',
                            'provider-transfer-withdraw','{}'::jsonb)
                    returning transfer_id
                    """,
                    (customer_id, available_account_id),
                )
                withdrawal_transfer_id = cur.fetchone()[0]
                _settle_provider_transfer(
                    cur,
                    transfer_id=withdrawal_transfer_id,
                    transfer_direction="WITHDRAWAL",
                    amount=Decimal("20"),
                    provider_reference="provider-transfer-withdraw",
                    available_ledger_id=available_ledger_id,
                )

    after_withdrawal = read_snapshot(database_url, command_subject=subject)
    assert after_withdrawal["balances"]["available_cash"] == "30.000000000000"
    assert after_withdrawal["balances"]["total"] == "30.000000000000"

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select direction,status,ledger_transaction_id is not null
                from anevum.financial_transfers
                where provider_reference in (
                    'provider-transfer-deposit',
                    'provider-transfer-withdraw'
                )
                order by direction
                """
            )
            rows = cur.fetchall()
    assert rows == [
        ("DEPOSIT", "SETTLED", True),
        ("WITHDRAWAL", "SETTLED", True),
    ]
