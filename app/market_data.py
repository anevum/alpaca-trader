from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings


class MarketDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_api_secret,
        }

    async def bars(self, symbol: str) -> list[dict[str, Any]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=self.settings.lookback_days)
        params = {
            "timeframe": self.settings.bar_timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": self.settings.lookback_bars,
            "sort": "desc",
            "feed": self.settings.data_feed,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.settings.data_base_url}/v2/stocks/{symbol}/bars",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        bars = data.get("bars", [])
        bars.reverse()
        return bars

    async def bars_many(self, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        results = await asyncio.gather(*(self.bars(symbol) for symbol in symbols))
        return dict(zip(symbols, results, strict=True))
