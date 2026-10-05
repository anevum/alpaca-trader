"""Financial Gateway lifecycle tests; isolated CI PostgreSQL only."""
import os
from uuid import uuid4

import pytest

from foundation.financial_gateway import (
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
