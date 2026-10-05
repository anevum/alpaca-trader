from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"


class SecretResolver(Protocol):
    def resolve(self, secret_reference: str) -> str:
        """Return a secret value for a server-side reference."""


@dataclass(frozen=True)
class TenantBrokerAccount:
    tenant_id: str
    broker_account_id: str
    provider_account_id: str
    environment: str
    authorization_kind: str
    secret_reference: str

    @property
    def base_url(self) -> str:
        environment = self.environment.upper()
        if environment == "PAPER":
            return ALPACA_PAPER_BASE_URL
        if environment == "LIVE":
            return ALPACA_LIVE_BASE_URL
        raise ValueError("unsupported Alpaca environment")


class TenantAlpacaReadClient:
    """Read-only Alpaca trading client for one tenant-scoped account.

    The client deliberately exposes no POST/PUT/PATCH/DELETE methods. Raw
    secrets are resolved immediately before a request and are never returned
    from this object or included in reconciliation payloads.
    """

    def __init__(
        self,
        account: TenantBrokerAccount,
        secret_resolver: SecretResolver,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ):
        if account.authorization_kind.upper() != "OAUTH":
            raise ValueError("tenant read client currently requires OAuth authorization")
        self.account = account
        self.secret_resolver = secret_resolver
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        try:
            token = self.secret_resolver.resolve(self.account.secret_reference).strip()
        except Exception as exc:
            raise RuntimeError(
                f"Alpaca secret resolution failed ({type(exc).__name__})"
            ) from None
        if not token:
            raise RuntimeError("resolved Alpaca OAuth token is empty")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            base_url=self.account.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = await client.get(path, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:1000]
            raise RuntimeError(
                f"Alpaca GET {path} failed ({response.status_code}): {detail}"
            ) from exc
        return response.json()

    async def account_snapshot(self) -> dict[str, Any]:
        result = await self._get("/v2/account")
        if not isinstance(result, dict):
            raise RuntimeError("Alpaca account payload is not an object")
        return result

    async def positions_snapshot(self) -> list[dict[str, Any]]:
        result = await self._get("/v2/positions")
        if not isinstance(result, list):
            raise RuntimeError("Alpaca positions payload is not a list")
        return result

    async def open_orders_snapshot(self) -> list[dict[str, Any]]:
        result = await self._get(
            "/v2/orders",
            params={"status": "open", "nested": "true"},
        )
        if not isinstance(result, list):
            raise RuntimeError("Alpaca open-orders payload is not a list")
        return result

    async def recent_orders_snapshot(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        result = await self._get(
            "/v2/orders",
            params={
                "status": "all",
                "limit": bounded,
                "direction": "desc",
                "nested": "true",
            },
        )
        if not isinstance(result, list):
            raise RuntimeError("Alpaca recent-orders payload is not a list")
        return result



class TenantAlpacaPaperExecutionClient(TenantAlpacaReadClient):
    """Narrow PAPER-only Alpaca execution client for one tenant account.

    This client exists only for the account-isolated RHEN tenant executor. It
    refuses LIVE accounts by construction and exposes only the order operations
    required by the paper executor.
    """

    def __init__(
        self,
        account: TenantBrokerAccount,
        secret_resolver: SecretResolver,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ):
        if account.environment.upper() != "PAPER":
            raise ValueError("tenant execution client is paper-only")
        super().__init__(
            account,
            secret_resolver,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    async def _post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.account.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers={
                **self._headers(),
                "Content-Type": "application/json",
            },
        ) as client:
            response = await client.post(path, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:1000]
            raise RuntimeError(
                f"Alpaca POST {path} failed ({response.status_code}): {detail}"
            ) from exc
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Alpaca order payload is not an object")
        return result

    async def order_by_client_order_id(
        self,
        client_order_id: str,
    ) -> dict[str, Any] | None:
        value = str(client_order_id or "").strip()
        if not value:
            raise ValueError("client_order_id_required")
        async with httpx.AsyncClient(
            base_url=self.account.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = await client.get(
                "/v2/orders:by_client_order_id",
                params={"client_order_id": value},
            )
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:1000]
            raise RuntimeError(
                "Alpaca order lookup failed "
                f"({response.status_code}): {detail}"
            ) from exc
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Alpaca order lookup payload is not an object")
        return result

    async def submit_crypto_market_buy(
        self,
        *,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/v2/orders",
            payload={
                "symbol": str(symbol).upper(),
                "qty": str(qty),
                "side": "buy",
                "type": "market",
                "time_in_force": "gtc",
                "client_order_id": client_order_id,
            },
        )

    async def submit_crypto_market_sell(
        self,
        *,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/v2/orders",
            payload={
                "symbol": str(symbol).upper(),
                "qty": str(qty),
                "side": "sell",
                "type": "market",
                "time_in_force": "gtc",
                "client_order_id": client_order_id,
            },
        )

    async def submit_crypto_stop_limit_sell(
        self,
        *,
        symbol: str,
        qty: str,
        stop_price: str,
        limit_price: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/v2/orders",
            payload={
                "symbol": str(symbol).upper(),
                "qty": str(qty),
                "side": "sell",
                "type": "stop_limit",
                "time_in_force": "gtc",
                "stop_price": str(stop_price),
                "limit_price": str(limit_price),
                "client_order_id": client_order_id,
            },
        )

    async def cancel_order(self, order_id: str) -> None:
        value = str(order_id or "").strip()
        if not value:
            raise ValueError("order_id_required")
        async with httpx.AsyncClient(
            base_url=self.account.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = await client.delete(f"/v2/orders/{value}")
        if response.status_code in {204, 404}:
            return
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:1000]
            raise RuntimeError(
                f"Alpaca DELETE /v2/orders failed ({response.status_code}): {detail}"
            ) from exc
