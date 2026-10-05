from decimal import Decimal
from pathlib import Path

import pytest

from foundation.financial_gateway import (
    EXTERNAL_MONEY_MOVEMENT_ENABLED,
    LIVE_EXECUTION_AUTHORIZED,
    canonical_payload_hash,
    handle_financial_action,
    money,
)
from foundation.financial_provider import DisabledExternalFinancialProvider


ROOT = Path(__file__).resolve().parents[1]


def test_financial_gateway_is_fail_closed_for_external_money_and_live_execution():
    assert EXTERNAL_MONEY_MOVEMENT_ENABLED is False
    assert LIVE_EXECUTION_AUTHORIZED is False


def test_canonical_payload_hash_is_order_independent():
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash({"b": 2, "a": 1})


def test_money_normalizes_precision_and_rejects_negative_values():
    assert money("10.5") == Decimal("10.500000000000")
    assert money("0", allow_zero=True) == Decimal("0E-12")
    with pytest.raises(ValueError, match="amount_must_be_positive"):
        money("0")
    with pytest.raises(ValueError, match="amount_must_be_positive"):
        money("-1")


def test_disabled_provider_fails_closed():
    provider = DisabledExternalFinancialProvider()
    with pytest.raises(RuntimeError, match="external_financial_provider_disabled"):
        provider.create_transfer("acct", {"amount": "1"})


def test_invalid_financial_action_is_rejected_before_database_use():
    with pytest.raises(ValueError, match="invalid_financial_action"):
        handle_financial_action(
            "postgresql://unused",
            command_subject="devon@example.com",
            action="send_wire",
            body={},
        )


def test_financial_schema_enforces_append_only_balanced_ledger_and_no_external_execution():
    sql = (ROOT / "db" / "migrations" / "0024_anevum_financial_gateway_v1.sql").read_text()
    assert "financial_ledger_transactions_append_only" in sql
    assert "financial_ledger_entries_append_only" in sql
    assert "financial_transaction_unbalanced" in sql
    assert "external_execution_enabled boolean not null default false" in sql
    assert "check (external_execution_enabled is false)" in sql
    assert "max_allocation >= 0" in sql


def test_command_finance_api_uses_verified_command_identity_boundary():
    source = (ROOT / "foundation" / "ingest" / "service.py").read_text()
    assert '@app.get("/v1/command/finance")' in source
    assert '@app.post("/v1/command/finance")' in source
    assert "identity = await require_command_access" in source
    assert "command_subject=subject" in source
