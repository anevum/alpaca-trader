from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import PreOpenSettings


class MarketDataError(RuntimeError):
    pass


class AlpacaReadOnlyMarketData:
    """Market-data-only Alpaca client. Contains no broker/order methods."""

    def __init__(self, settings: PreOpenSettings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_api_secret,
        }

    async def bars_many(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.settings.credentials_configured:
            raise MarketDataError("Alpaca market-data credentials are not configured")
        if end <= start:
            raise ValueError("end must be after start")
        normalized = tuple(dict.fromkeys(s.upper() for s in symbols if s))
        output = {symbol: [] for symbol in normalized}
        if not normalized:
            return output

        params: dict[str, Any] = {
            "symbols": ",".join(normalized),
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "sort": "asc",
            "feed": self.settings.data_feed,
            "adjustment": "raw",
        }
        token: str | None = None
        async with httpx.AsyncClient(timeout=30.0) as http:
            for _ in range(50):
                request = dict(params)
                if token:
                    request["page_token"] = token
                response = await http.get(
                    f"{self.settings.data_base_url.rstrip('/')}/v2/stocks/bars",
                    headers=self.headers,
                    params=request,
                )
                if response.status_code in {401, 403}:
                    raise MarketDataError(
                        f"market-data authorization failed for feed={self.settings.data_feed}"
                    )
                response.raise_for_status()
                payload = response.json()
                for symbol, bars in (payload.get("bars") or {}).items():
                    output.setdefault(symbol.upper(), []).extend(bars or [])
                token = payload.get("next_page_token")
                if not token:
                    return output
        raise MarketDataError("market-data pagination exceeded safety limit")

    async def daily_bars_many(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        return await self.bars_many(
            symbols,
            start=start,
            end=end,
            timeframe="1Day",
        )
