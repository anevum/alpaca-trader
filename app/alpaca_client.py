from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class AlpacaClient:
    """Thin Alpaca Trading API client.

    Execution is exposed here, but all automated order submission must pass through
    ExecutionEngine, which enforces the configured paper/live gates and risk checks.
    """

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
            if response.status_code == 204:
                return None
            return response.json()

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/account")

    async def clock(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/clock")

    async def positions(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/positions")

    async def open_orders(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/orders", params={"status": "open"})

    async def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/v2/orders",
            params={"status": "all", "limit": limit, "direction": "desc"},
        )

    async def submit_market_buy(
        self,
        symbol: str,
        notional: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "notional": notional,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": client_order_id,
            },
        )

    async def submit_market_sell(
        self,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": client_order_id,
            },
        )
