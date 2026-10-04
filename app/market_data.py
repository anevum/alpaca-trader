from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import asyncio

import httpx

from .config import Settings


NY = ZoneInfo("America/New_York")


class MarketDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _batches(self, symbols: list[str]) -> list[list[str]]:
        normalized = list(
            dict.fromkeys(s.strip().upper() for s in symbols if s.strip())
        )
        size = self.settings.market_data_batch_size
        return [
            normalized[index:index + size]
            for index in range(0, len(normalized), size)
        ]

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

        batches = self._batches(symbols)
        if not batches:
            return {}

        now_ny = datetime.now(NY)
        session_start_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        start = session_start_ny.astimezone(timezone.utc)
        end = datetime.now(timezone.utc)
        output: dict[str, list[dict[str, Any]]] = {
            symbol: [] for batch in batches for symbol in batch
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            for batch in batches:
                params = {
                    "symbols": ",".join(batch),
                    "timeframe": self.settings.bar_timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "limit": 10000,
                    "sort": "asc",
                    "feed": self.settings.data_feed,
                }
                page_token: str | None = None
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


    async def market_calendar_details(
        self,
        *,
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        """Return Alpaca session dates and published open/close boundaries."""
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        if end < start:
            raise ValueError("calendar end must not be before start")

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.settings.base_url}/v2/calendar",
                headers=self.headers,
                params={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
            response.raise_for_status()
            payload = response.json()

        details: list[dict[str, Any]] = []
        for item in payload:
            if not item.get("date"):
                continue
            details.append(
                {
                    "date": date.fromisoformat(str(item["date"])),
                    "open": item.get("open"),
                    "close": item.get("close"),
                    "session_open": item.get("session_open"),
                    "session_close": item.get("session_close"),
                }
            )
        return sorted(details, key=lambda item: item["date"])

    async def market_calendar(
        self,
        *,
        start: date,
        end: date,
    ) -> list[date]:
        """Return Alpaca trading-session dates for an inclusive date range."""
        return [
            item["date"]
            for item in await self.market_calendar_details(start=start, end=end)
        ]

    async def historical_bars_many(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch time-bounded bars for replay/research. This method is read-only."""
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        if end <= start:
            raise ValueError("historical bar end must be after start")

        batches = self._batches(symbols)
        if not batches:
            return {}

        output: dict[str, list[dict[str, Any]]] = {
            symbol: [] for batch in batches for symbol in batch
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch in batches:
                selected_timeframe = str(
                    timeframe or self.settings.bar_timeframe
                ).strip()
                if not selected_timeframe:
                    raise ValueError("historical crypto bar timeframe is required")
                params = {
                    "symbols": ",".join(batch),
                    "timeframe": selected_timeframe,
                    "start": start.astimezone(timezone.utc).isoformat(),
                    "end": end.astimezone(timezone.utc).isoformat(),
                    "limit": 10000,
                    "sort": "asc",
                    "feed": self.settings.data_feed,
                }
                page_token: str | None = None
                for _ in range(50):
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
                    raise RuntimeError(
                        "historical replay pagination exceeded safety limit"
                    )

        return output


    async def historical_crypto_bars_many(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
        timeframe: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch Alpaca US crypto bars for VELUM. Read-only; no broker access."""
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        if end <= start:
            raise ValueError("historical crypto bar end must be after start")

        batches = self._batches(symbols)
        if not batches:
            return {}

        output: dict[str, list[dict[str, Any]]] = {
            symbol: [] for batch in batches for symbol in batch
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch in batches:
                params = {
                    "symbols": ",".join(batch),
                    "timeframe": self.settings.bar_timeframe,
                    "start": start.astimezone(timezone.utc).isoformat(),
                    "end": end.astimezone(timezone.utc).isoformat(),
                    "limit": 10000,
                    "sort": "asc",
                }
                page_token: str | None = None
                for _ in range(50):
                    request_params = dict(params)
                    if page_token:
                        request_params["page_token"] = page_token
                    response = await client.get(
                        f"{self.settings.data_base_url}/v1beta3/crypto/"
                        f"{self.settings.crypto_location}/bars",
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
                    raise RuntimeError(
                        "historical crypto replay pagination exceeded safety limit"
                    )

        return output


    async def daily_bars_many(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
        batch_size: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch daily bars in explicit batches for dynamic-universe ranking."""
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        if end <= start:
            raise ValueError("daily bar end must be after start")

        normalized = list(
            dict.fromkeys(s.strip().upper() for s in symbols if s.strip())
        )
        if not normalized:
            return {}

        size = batch_size or self.settings.universe_data_batch_size
        batches = [
            normalized[index:index + size]
            for index in range(0, len(normalized), size)
        ]
        output: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in normalized
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            semaphore = asyncio.Semaphore(4)

            async def fetch_batch(batch: list[str]) -> dict[str, list[dict[str, Any]]]:
                async with semaphore:
                    batch_output: dict[str, list[dict[str, Any]]] = {
                        symbol: [] for symbol in batch
                    }
                    params = {
                        "symbols": ",".join(batch),
                        "timeframe": "1Day",
                        "start": start.astimezone(timezone.utc).isoformat(),
                        "end": end.astimezone(timezone.utc).isoformat(),
                        "limit": 10000,
                        "sort": "asc",
                        "feed": self.settings.data_feed,
                    }
                    page_token: str | None = None
                    for _ in range(10):
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
                            batch_output.setdefault(symbol.upper(), []).extend(bars or [])
                        page_token = data.get("next_page_token")
                        if not page_token:
                            break
                    else:
                        raise RuntimeError(
                            "dynamic-universe daily-bar pagination exceeded safety limit"
                        )
                    return batch_output

            batch_results = await asyncio.gather(
                *(fetch_batch(batch) for batch in batches)
            )

        for batch_output in batch_results:
            for symbol, bars in batch_output.items():
                output.setdefault(symbol, []).extend(bars)

        return output


    async def latest_quotes_many(
        self,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")

        batches = self._batches(symbols)
        if not batches:
            return {}

        output: dict[str, dict[str, Any]] = {
            symbol: {} for batch in batches for symbol in batch
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            for batch in batches:
                response = await client.get(
                    f"{self.settings.data_base_url}/v2/stocks/quotes/latest",
                    headers=self.headers,
                    params={
                        "symbols": ",".join(batch),
                        "feed": self.settings.data_feed,
                    },
                )
                response.raise_for_status()
                quotes = (response.json().get("quotes") or {})
                for symbol in batch:
                    output[symbol] = quotes.get(symbol, {})
        return output
