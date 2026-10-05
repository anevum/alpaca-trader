from __future__ import annotations

from typing import Any, Protocol


class BrokerMoneyProvider(Protocol):
    """Fail-closed provider boundary for broker-owned funding and transfer rails.

    Platform Core owns tenant authorization and intent. The external broker/custodian
    remains the source of truth for account cash and transfer settlement.
    """

    provider_name: str
    environment: str

    def create_broker_account(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_account(self, provider_account_ref: str) -> dict[str, Any]: ...
    def create_funding_relationship(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...
    def list_funding_relationships(
        self,
        provider_account_ref: str,
    ) -> list[dict[str, Any]]: ...
    def create_transfer(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...
    def get_transfer(
        self,
        provider_account_ref: str,
        transfer_ref: str,
    ) -> dict[str, Any]: ...
    def list_activities(
        self,
        provider_account_ref: str,
    ) -> list[dict[str, Any]]: ...
    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]: ...


class DisabledBrokerMoneyProvider:
    """Default adapter until a sandbox/production provider is explicitly selected."""

    provider_name = "DISABLED"
    environment = "DISABLED"

    @staticmethod
    def _disabled() -> None:
        raise RuntimeError("broker_money_provider_disabled")

    def create_broker_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._disabled()

    def get_account(self, provider_account_ref: str) -> dict[str, Any]:
        self._disabled()

    def create_funding_relationship(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._disabled()

    def list_funding_relationships(
        self,
        provider_account_ref: str,
    ) -> list[dict[str, Any]]:
        self._disabled()

    def create_transfer(
        self,
        provider_account_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._disabled()

    def get_transfer(
        self,
        provider_account_ref: str,
        transfer_ref: str,
    ) -> dict[str, Any]:
        self._disabled()

    def list_activities(
        self,
        provider_account_ref: str,
    ) -> list[dict[str, Any]]:
        self._disabled()

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        self._disabled()
