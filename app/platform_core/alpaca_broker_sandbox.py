from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


SANDBOX_BASE_URL = "https://broker-api.sandbox.alpaca.markets"


class AlpacaBrokerSandboxError(RuntimeError):
    def __init__(self, operation: str, status_code: int, request_id: str | None = None):
        self.operation = operation
        self.status_code = status_code
        self.request_id = request_id
        suffix = f":request_id={request_id}" if request_id else ""
        super().__init__(f"alpaca_broker_sandbox_{operation}_failed:{status_code}{suffix}")


class AlpacaBrokerSandboxProvider:
    """Alpaca Broker API sandbox adapter for Platform Core.

    The adapter is sandbox-only by construction. It cannot accept a production base
    URL and it rejects raw bank account/routing numbers. It is intentionally not wired
    to any customer Command mutation in Platform Core v1.
    """

    provider_name = "ALPACA_BROKER"
    environment = "SANDBOX"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        client: httpx.Client | None = None,
    ):
        key = str(api_key or "").strip()
        secret = str(api_secret or "").strip()
        if not key or not secret:
            raise ValueError("alpaca_broker_sandbox_credentials_required")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=SANDBOX_BASE_URL,
            auth=httpx.BasicAuth(key, secret),
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    @classmethod
    def from_env(cls) -> "AlpacaBrokerSandboxProvider":
        return cls(
            api_key=os.environ.get("ALPACA_BROKER_SANDBOX_KEY", ""),
            api_secret=os.environ.get("ALPACA_BROKER_SANDBOX_SECRET", ""),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        operation: str,
    ) -> Any:
        response = self.client.request(method, path, json=json_body, params=params)
        request_id = response.headers.get("x-request-id")
        if not response.is_success:
            raise AlpacaBrokerSandboxError(
                operation,
                response.status_code,
                request_id=request_id,
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    @staticmethod
    def _account_ref(value: str) -> str:
        ref = str(value or "").strip()
        if not ref or len(ref) > 100:
            raise ValueError("invalid_provider_account_ref")
        return ref

    @staticmethod
    def _positive_amount(value: Any) -> str:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid_transfer_amount") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("invalid_transfer_amount")
        return format(amount, "f")

    def create_broker_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("invalid_account_application")
        if str(payload.get("account_type") or "trading").lower() != "trading":
            raise ValueError("unsupported_account_type")
        body = dict(payload)
        body["account_type"] = "trading"
        result = self._request(
            "POST",
            "/v1/accounts",
            json_body=body,
            operation="create_account",
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("alpaca_broker_sandbox_invalid_account_response")
        return result

    def get_account(self, provider_account_ref: str) -> dict[str, Any]:
        account_id = self._account_ref(provider_account_ref)
        result = self._request(
            "GET",
            f"/v1/accounts/{account_id}",
            operation="get_account",
        )
        if not isinstance(result, dict):
            raise RuntimeError("alpaca_broker_sandbox_invalid_account_response")
        return result

    def create_funding_relationship(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        account_id = self._account_ref(provider_account_ref)
        if not isinstance(payload, dict):
            raise ValueError("invalid_funding_relationship")
        forbidden = {
            "bank_account_number",
            "bank_routing_number",
            "routing_number",
            "account_number",
        }
        if forbidden.intersection(payload):
            raise ValueError("raw_bank_credentials_not_accepted")
        processor_token = str(payload.get("processor_token") or "").strip()
        if not processor_token:
            raise ValueError("processor_token_required")
        account_type = str(payload.get("bank_account_type") or "CHECKING").upper()
        if account_type not in {"CHECKING", "SAVINGS"}:
            raise ValueError("invalid_bank_account_type")
        body: dict[str, Any] = {
            "processor_token": processor_token,
            "bank_account_type": account_type,
        }
        for field in ("account_owner_name", "nickname"):
            value = str(payload.get(field) or "").strip()
            if value:
                body[field] = value
        if "instant" in payload:
            body["instant"] = bool(payload["instant"])
        result = self._request(
            "POST",
            f"/v1/accounts/{account_id}/ach_relationships",
            json_body=body,
            operation="create_ach_relationship",
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("alpaca_broker_sandbox_invalid_ach_response")
        return result

    def list_funding_relationships(
        self,
        provider_account_ref: str,
    ) -> list[dict[str, Any]]:
        account_id = self._account_ref(provider_account_ref)
        result = self._request(
            "GET",
            f"/v1/accounts/{account_id}/ach_relationships",
            operation="list_ach_relationships",
        )
        if not isinstance(result, list):
            raise RuntimeError("alpaca_broker_sandbox_invalid_ach_response")
        return [row for row in result if isinstance(row, dict)]

    def create_transfer(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        account_id = self._account_ref(provider_account_ref)
        if not isinstance(payload, dict):
            raise ValueError("invalid_transfer")
        relationship_id = str(payload.get("relationship_id") or "").strip()
        if not relationship_id:
            raise ValueError("relationship_id_required")
        direction = str(payload.get("direction") or "").upper()
        if direction not in {"INCOMING", "OUTGOING"}:
            raise ValueError("invalid_transfer_direction")
        body = {
            "transfer_type": "ach",
            "relationship_id": relationship_id,
            "amount": self._positive_amount(payload.get("amount")),
            "direction": direction,
        }
        result = self._request(
            "POST",
            f"/v1/accounts/{account_id}/transfers",
            json_body=body,
            operation="create_transfer",
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("alpaca_broker_sandbox_invalid_transfer_response")
        return result

    def get_transfer(
        self,
        provider_account_ref: str,
        transfer_ref: str,
    ) -> dict[str, Any]:
        account_id = self._account_ref(provider_account_ref)
        ref = str(transfer_ref or "").strip()
        if not ref:
            raise ValueError("invalid_transfer_ref")
        result = self._request(
            "GET",
            f"/v1/accounts/{account_id}/transfers",
            params={"limit": 100},
            operation="list_transfers",
        )
        if not isinstance(result, list):
            raise RuntimeError("alpaca_broker_sandbox_invalid_transfer_response")
        for row in result:
            if isinstance(row, dict) and str(row.get("id") or "") == ref:
                return row
        raise ValueError("transfer_not_found")

    def list_activities(self, provider_account_ref: str) -> list[dict[str, Any]]:
        account_id = self._account_ref(provider_account_ref)
        result = self._request(
            "GET",
            "/v1/accounts/activities",
            params={"account_id": account_id, "page_size": 100, "direction": "desc"},
            operation="list_activities",
        )
        if not isinstance(result, list):
            raise RuntimeError("alpaca_broker_sandbox_invalid_activity_response")
        return [row for row in result if isinstance(row, dict)]

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        raise RuntimeError("alpaca_broker_sandbox_webhook_verification_not_configured")


def sandbox_provider_status() -> dict[str, Any]:
    configured = bool(
        os.environ.get("ALPACA_BROKER_SANDBOX_KEY", "").strip()
        and os.environ.get("ALPACA_BROKER_SANDBOX_SECRET", "").strip()
    )
    return {
        "provider": "ALPACA_BROKER",
        "environment": "SANDBOX",
        "base_url": SANDBOX_BASE_URL,
        "configured": configured,
        "wired_to_customer_mutations": False,
        "external_money_movement": False,
        "virtual_funds_only": True,
        "tokenized_ach_only": True,
    }
