from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class AlpacaClient:
    """Thin Alpaca Trading API client.

    Automated order submission is exposed here, but every entry/exit is gated by
    ExecutionEngine and the risk layer before this client is called.
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
        return await self._request(
            "GET",
            "/v2/orders",
            params={"status": "open", "nested": "true"},
        )

    async def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/v2/orders",
            params={
                "status": "all",
                "limit": limit,
                "direction": "desc",
                "nested": "true",
            },
        )

    async def submit_bracket_market_buy(
        self,
        symbol: str,
        qty: str,
        take_profit_price: str,
        stop_price: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "order_class": "bracket",
                "take_profit": {"limit_price": take_profit_price},
                "stop_loss": {"stop_price": stop_price},
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

    async def cancel_order(self, order_id: str) -> None:
        try:
            await self._request("DELETE", f"/v2/orders/{order_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
