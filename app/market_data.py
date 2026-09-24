from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import Settings


NY = ZoneInfo("America/New_York")


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
        return (await self.bars_many([symbol])).get(symbol.upper(), [])

    async def bars_many(self, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")

        normalized = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not normalized:
            return {}

        now_ny = datetime.now(NY)
        session_start_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        start = session_start_ny.astimezone(timezone.utc)
        end = datetime.now(timezone.utc)

        params = {
            "symbols": ",".join(normalized),
            "timeframe": self.settings.bar_timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "sort": "asc",
            "feed": self.settings.data_feed,
        }

        output: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized}
        page_token: str | None = None

        async with httpx.AsyncClient(timeout=20.0) as client:
            for _ in range(5):
                request_params = dict(params)
                if page_token:
                    request_params["page_token"] = page_token
                response = await client.get(
                    f"{self.settings.data_base_url}/v2/stocks/bars",
                    headers=self.headers,
                    params=request_params,
                )
                response.raise_for_status()
                data = response.json()
                for symbol, bars in (data.get("bars") or {}).items():
                    output.setdefault(symbol.upper(), []).extend(bars or [])
                page_token = data.get("next_page_token")
                if not page_token:
                    break
            else:
                raise RuntimeError("market-data pagination exceeded safety limit")

        return output


    async def latest_quotes_many(
        self,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")

        normalized = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not normalized:
            return {}

        params = {
            "symbols": ",".join(normalized),
            "feed": self.settings.data_feed,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.settings.data_base_url}/v2/stocks/quotes/latest",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        quotes = data.get("quotes") or {}
        return {
            symbol: quotes.get(symbol, {})
            for symbol in normalized
        }
