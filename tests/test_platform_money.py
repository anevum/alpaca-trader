from pathlib import Path

import httpx
import pytest

from app.platform_core.alpaca_broker_sandbox import (
    AlpacaBrokerSandboxProvider,
    SANDBOX_BASE_URL,
)
from app.platform_core.money import DisabledBrokerMoneyProvider


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "db" / "migrations" / "0028_command_broker_money_metadata.sql"
).read_text().lower()


def test_disabled_broker_money_provider_fails_closed():
    provider = DisabledBrokerMoneyProvider()
    with pytest.raises(RuntimeError, match="broker_money_provider_disabled"):
        provider.create_transfer("acct", {"amount": "1"})


def test_transfer_schema_reuses_canonical_tenant_broker_identity():
    assert "anevum.financial_customers" not in MIGRATION
    assert "anevum.financial_provider_accounts" not in MIGRATION
    assert "anevum.rhen_allocations" not in MIGRATION
    assert "financial_ledger" not in MIGRATION
    assert "references anevum.broker_accounts(tenant_id, broker_account_id)" in MIGRATION
    assert "broker_transfer_intents" in MIGRATION
    assert "broker_funding_relationships" in MIGRATION


def test_transfer_schema_is_metadata_not_cash_source_of_truth():
    assert "does not represent an anevum cash balance" in MIGRATION
    assert "amount numeric(38,12) not null" in MIGRATION
    assert "direction in ('deposit','withdrawal')" in MIGRATION
    assert "idempotency_key text not null unique" in MIGRATION


def test_provider_events_are_append_only():
    assert "broker_transfer_events_append_only" in MIGRATION
    assert "broker_provider_events_append_only" in MIGRATION
    assert "broker_event_records_are_append_only" in MIGRATION


def test_alpaca_broker_adapter_is_sandbox_only_and_rejects_raw_bank_credentials():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/ach_relationships"):
            return httpx.Response(200, json={"id": "rel-1"})
        return httpx.Response(200, json={"id": "acct-1"})

    client = httpx.Client(
        base_url=SANDBOX_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    provider = AlpacaBrokerSandboxProvider(
        api_key="test-key",
        api_secret="test-secret",
        client=client,
    )

    with pytest.raises(ValueError, match="raw_bank_credentials_not_accepted"):
        provider.create_funding_relationship(
            "acct-1",
            {"routing_number": "123", "account_number": "456"},
        )

    relationship = provider.create_funding_relationship(
        "acct-1",
        {"processor_token": "processor-token", "bank_account_type": "CHECKING"},
    )
    assert relationship["id"] == "rel-1"
    assert requests[-1].url.host == "broker-api.sandbox.alpaca.markets"


def test_sandbox_adapter_normalizes_positive_transfer_amount():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"id": "transfer-1"})

    client = httpx.Client(
        base_url=SANDBOX_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    provider = AlpacaBrokerSandboxProvider(
        api_key="test-key",
        api_secret="test-secret",
        client=client,
    )
    result = provider.create_transfer(
        "acct-1",
        {
            "relationship_id": "rel-1",
            "direction": "INCOMING",
            "amount": "10.50",
        },
    )
    assert result["id"] == "transfer-1"
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.host == "broker-api.sandbox.alpaca.markets"
