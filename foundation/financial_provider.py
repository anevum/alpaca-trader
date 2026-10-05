from __future__ import annotations

from typing import Any, Protocol


class FinancialProvider(Protocol):
    """Provider boundary for custody/account/money-movement integrations."""

    provider_name: str
    environment: str

    def create_customer_account(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_account(self, provider_account_ref: str) -> dict[str, Any]: ...
    def create_bank_link(self, provider_account_ref: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_transfer(self, provider_account_ref: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_transfer(self, provider_account_ref: str, transfer_ref: str) -> dict[str, Any]: ...
    def list_activities(self, provider_account_ref: str) -> list[dict[str, Any]]: ...
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]: ...


class DisabledExternalFinancialProvider:
    """Fail-closed adapter used until a sandbox provider is explicitly configured."""

    provider_name = "DISABLED"
    environment = "SANDBOX"

    @staticmethod
    def _disabled() -> None:
        raise RuntimeError("external_financial_provider_disabled")

    def create_customer_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._disabled()

    def get_account(self, provider_account_ref: str) -> dict[str, Any]:
        self._disabled()

    def create_bank_link(self, provider_account_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._disabled()

    def create_transfer(self, provider_account_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._disabled()

    def get_transfer(self, provider_account_ref: str, transfer_ref: str) -> dict[str, Any]:
        self._disabled()

    def list_activities(self, provider_account_ref: str) -> list[dict[str, Any]]:
        self._disabled()

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        self._disabled()
