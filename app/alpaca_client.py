from __future__ import annotations

from typing import Any
import httpx

from .config import Settings


class AlpacaClient:
    """Read-only Alpaca Trading API client for the bootstrap stage."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_api_secret,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if method.upper() != "GET":
            raise RuntimeError("bootstrap Alpaca client is read-only")
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"{self.settings.base_url}{path}",
                headers=self.headers,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/account")

    async def positions(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/positions")

    async def open_orders(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/orders", params={"status": "open"})

    async def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/v2/orders", params={"status": "all", "limit": limit, "direction": "desc"}
        )
