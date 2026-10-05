from __future__ import annotations

import json

import httpx
import pytest

from foundation.alpaca_broker_sandbox import (
    SANDBOX_BASE_URL,
    AlpacaBrokerSandboxProvider,
    sandbox_provider_status,
)


def _provider(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        base_url=SANDBOX_BASE_URL,
        transport=transport,
    )
    return AlpacaBrokerSandboxProvider(
        api_key="sandbox-key",
        api_secret="sandbox-secret",
        client=client,
    )


def test_sandbox_provider_base_url_is_hard_locked():
    assert SANDBOX_BASE_URL == "https://broker-api.sandbox.alpaca.markets"


def test_create_account_posts_only_to_sandbox_account_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "acct-sandbox-1", "status": "SUBMITTED", "account_type": "trading"},
        )

    provider = _provider(handler)
    result = provider.create_customer_account({
        "contact": {"email_address": "test@example.invalid"},
        "identity": {"given_name": "Test", "family_name": "User"},
        "disclosures": {},
        "agreements": [],
    })

    assert result["id"] == "acct-sandbox-1"
    assert seen["method"] == "POST"
    assert seen["url"] == SANDBOX_BASE_URL + "/v1/accounts"
    assert seen["body"]["account_type"] == "trading"


def test_bank_link_rejects_raw_account_and_routing_numbers_before_network():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    provider = _provider(handler)
    with pytest.raises(ValueError, match="raw_bank_credentials_not_accepted"):
        provider.create_bank_link(
            "acct-sandbox-1",
            {
                "routing_number": "000000000",
                "account_number": "1234",
            },
        )
    assert calls == []


def test_bank_link_uses_processor_token_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "ach-1",
                "status": "APPROVED",
                "account_type": "CHECKING",
            },
        )

    provider = _provider(handler)
    result = provider.create_bank_link(
        "acct-sandbox-1",
        {
            "processor_token": "processor-sandbox-token",
            "bank_account_type": "CHECKING",
            "nickname": "Test bank",
        },
    )

    assert result["id"] == "ach-1"
    assert seen["body"]["processor_token"] == "processor-sandbox-token"
    assert "routing_number" not in seen["body"]
    assert "account_number" not in seen["body"]


def test_transfer_is_ach_and_virtual_sandbox_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "transfer-1", "status": "QUEUED"},
        )

    provider = _provider(handler)
    result = provider.create_transfer(
        "acct-sandbox-1",
        {
            "relationship_id": "ach-1",
            "amount": "25.00",
            "direction": "INCOMING",
        },
    )

    assert result["id"] == "transfer-1"
    assert seen["url"] == SANDBOX_BASE_URL + "/v1/accounts/acct-sandbox-1/transfers"
    assert seen["body"] == {
        "transfer_type": "ach",
        "relationship_id": "ach-1",
        "amount": "25.00",
        "direction": "INCOMING",
    }


def test_get_transfer_uses_account_transfer_list_and_selects_requested_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/accounts/acct-sandbox-1/transfers"
        return httpx.Response(
            200,
            json=[
                {"id": "transfer-other", "status": "COMPLETE"},
                {"id": "transfer-1", "status": "QUEUED"},
            ],
        )

    provider = _provider(handler)
    result = provider.get_transfer("acct-sandbox-1", "transfer-1")
    assert result["status"] == "QUEUED"


def test_provider_status_never_claims_real_money_enabled(monkeypatch):
    monkeypatch.setenv("ALPACA_BROKER_SANDBOX_KEY", "configured")
    monkeypatch.setenv("ALPACA_BROKER_SANDBOX_SECRET", "configured")
    status = sandbox_provider_status()
    assert status["configured"] is True
    assert status["environment"] == "SANDBOX"
    assert status["external_money_movement"] is False
    assert status["virtual_funds_only"] is True
    assert status["tokenized_ach_only"] is True
