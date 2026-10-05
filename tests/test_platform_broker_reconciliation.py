import asyncio
import json

import httpx

from app.platform_core.broker import TenantAlpacaReadClient, TenantBrokerAccount
from app.platform_core.reconciliation import TenantBrokerReconciler


class FakeSecretResolver:
    def __init__(self, value: str):
        self.value = value
        self.references: list[str] = []

    def resolve(self, secret_reference: str) -> str:
        self.references.append(secret_reference)
        return self.value


def account_config(provider_account_id: str = "alpaca-account-1") -> TenantBrokerAccount:
    return TenantBrokerAccount(
        tenant_id="tenant-1",
        broker_account_id="broker-1",
        provider_account_id=provider_account_id,
        environment="PAPER",
        authorization_kind="OAUTH",
        secret_reference="secret://tenant-1/alpaca",
    )


def run(value):
    return asyncio.run(value)


def test_tenant_alpaca_client_uses_bearer_secret_without_exposing_it_in_results():
    token = "super-secret-oauth-token"
    resolver = FakeSecretResolver(token)
    seen_authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("Authorization", ""))
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"id": "alpaca-account-1", "status": "ACTIVE"})
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[])
        if request.url.path == "/v2/orders":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = TenantAlpacaReadClient(
        account_config(),
        resolver,
        transport=httpx.MockTransport(handler),
    )
    result = run(TenantBrokerReconciler(account_config(), client).reconcile())

    assert result.ready is True
    assert resolver.references
    assert all(value == f"Bearer {token}" for value in seen_authorization)
    encoded = json.dumps(result.to_record(), default=str)
    assert token not in encoded
    assert "secret://tenant-1/alpaca" not in encoded


def test_reconciliation_fails_closed_on_broker_account_identity_mismatch():
    resolver = FakeSecretResolver("token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"id": "different-account", "status": "ACTIVE"})
        raise AssertionError("reconciler must stop before positions/orders on identity mismatch")

    client = TenantAlpacaReadClient(
        account_config(),
        resolver,
        transport=httpx.MockTransport(handler),
    )
    result = run(TenantBrokerReconciler(account_config(), client).reconcile())

    assert result.ready is False
    assert result.status == "IDENTITY_MISMATCH"
    assert result.error_code == "broker_account_identity_mismatch"
    assert result.account_snapshot is None
    assert result.snapshot_hash is None


def test_read_client_exposes_no_order_or_transfer_mutation_methods():
    client = TenantAlpacaReadClient(
        account_config(),
        FakeSecretResolver("token"),
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    forbidden = (
        "submit_order",
        "cancel_order",
        "replace_order",
        "create_transfer",
        "withdraw",
        "deposit",
        "post",
        "delete",
        "patch",
        "put",
    )
    for name in forbidden:
        assert not hasattr(client, name)


def test_successful_snapshot_hash_is_deterministic_for_same_broker_state():
    resolver = FakeSecretResolver("token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": "alpaca-account-1",
                    "status": "ACTIVE",
                    "trading_blocked": False,
                    "equity": "100.00",
                },
            )
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": "BTCUSD", "qty": "0.01"}])
        if request.url.path == "/v2/orders":
            status = request.url.params.get("status")
            if status == "open":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": "order-1", "status": "filled"}])
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    first_client = TenantAlpacaReadClient(account_config(), resolver, transport=transport)
    second_client = TenantAlpacaReadClient(account_config(), resolver, transport=transport)

    first = run(TenantBrokerReconciler(account_config(), first_client).reconcile())
    second = run(TenantBrokerReconciler(account_config(), second_client).reconcile())

    assert first.ready and second.ready
    assert first.snapshot_hash == second.snapshot_hash


def test_http_failure_becomes_non_ready_reconciliation_evidence():
    resolver = FakeSecretResolver("token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="broker temporarily unavailable")

    client = TenantAlpacaReadClient(
        account_config(),
        resolver,
        transport=httpx.MockTransport(handler),
    )
    result = run(TenantBrokerReconciler(account_config(), client).reconcile())

    assert result.ready is False
    assert result.status == "FAILED"
    assert result.error_code == "broker_reconciliation_failed"
    assert "503" in (result.error_detail or "")
