from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import log1p
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .market_data import MarketDataClient
from .state import RuntimeState


NY = ZoneInfo("America/New_York")


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


class DynamicUniverse:
    """Selects a liquid, moving intraday universe from Alpaca's eligible equities."""

    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: MarketDataClient,
        state: RuntimeState,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.state = state
        self._eligible_symbols: list[str] = []
        self._candidate_symbols: list[str] = []
        self._candidate_day: date | None = None
        self._candidate_source = "uninitialized"
        self._active_symbols: list[str] = []
        self._last_refresh: datetime | None = None

    def _static_fallback(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self.settings.scan_symbols,
                    *self.settings.universe_always_include,
                ]
            )
        )

    @staticmethod
    def _clean_symbol(symbol: str) -> bool:
        if not symbol or "/" in symbol or " " in symbol:
            return False
        return all(character.isalnum() or character in {".", "-"} for character in symbol)

    async def _eligible(self) -> list[str]:
        assets = await self.client.assets(status="active", asset_class="us_equity")
        exchanges = self.settings.universe_exchanges
        symbols: list[str] = []
        for asset in assets:
            symbol = str(asset.get("symbol", "")).upper().strip()
            exchange = str(asset.get("exchange", "")).upper().strip()
            if (
                symbol
                and self._clean_symbol(symbol)
                and exchange in exchanges
                and str(asset.get("status", "")).lower() == "active"
                and bool(asset.get("tradable"))
                and bool(asset.get("fractionable"))
            ):
                symbols.append(symbol)
        return list(dict.fromkeys(symbols))

    @staticmethod
    def _daily_metrics(
        bars: list[dict[str, Any]],
    ) -> tuple[float, float, float, float] | None:
        if not bars:
            return None
        closes: list[Decimal] = []
        volumes: list[Decimal] = []
        ranges: list[Decimal] = []
        for bar in bars:
            close = d(bar.get("c"))
            volume = d(bar.get("v"))
            high = d(bar.get("h"))
            low = d(bar.get("l"))
            if close <= 0 or volume <= 0:
                continue
            closes.append(close)
            volumes.append(volume)
            ranges.append((high - low) / close if high > 0 and low > 0 else Decimal("0"))
        if not closes:
            return None
        observations = Decimal(len(closes))
        avg_volume = sum(volumes, Decimal("0")) / observations
        avg_dollar_volume = sum(
            (close * volume for close, volume in zip(closes, volumes)),
            Decimal("0"),
        ) / observations
        avg_range = sum(ranges, Decimal("0")) / Decimal(len(ranges)) if ranges else Decimal("0")
        return float(closes[-1]), float(avg_volume), float(avg_dollar_volume), float(avg_range)

    async def _refresh_candidates(self, now: datetime) -> None:
        eligible = await self._eligible()
        if not eligible:
            raise RuntimeError("dynamic universe returned no eligible equities")

        eligible_set = set(eligible)
        screened = [
            symbol
            for symbol in await self.market_data.stock_screener_symbols(top=100)
            if symbol in eligible_set
        ]
        always = [
            symbol
            for symbol in self.settings.universe_always_include
            if symbol in eligible_set
        ]
        if screened:
            seed_symbols = list(
                dict.fromkeys([*always, *screened])
            )[: self.settings.universe_candidate_pool_size]
            self._candidate_source = "hierarchical_screener"
        else:
            # Permission/feed failures do not make the universe unavailable.
            # Fall back to the proven full-catalog daily ranking once per day.
            seed_symbols = eligible
            self._candidate_source = "full_market_fallback"

        lookback_days = max(self.settings.universe_daily_lookback * 2 + 3, 10)
        end = datetime.combine(
            now.date(),
            time(0, 0),
            tzinfo=NY,
        ).astimezone(timezone.utc)
        start = (end - timedelta(days=lookback_days)).astimezone(timezone.utc)
        daily = await self.market_data.daily_bars_many(
            seed_symbols,
            start=start,
            end=end,
            batch_size=self.settings.universe_data_batch_size,
        )

        ranked: list[tuple[float, str]] = []
        for symbol in seed_symbols:
            bars = daily.get(symbol, [])[-self.settings.universe_daily_lookback:]
            metrics = self._daily_metrics(bars)
            if metrics is None:
                continue
            price, avg_volume, avg_dollar_volume, avg_range = metrics
            if price < float(self.settings.universe_min_price):
                continue
            if avg_volume < float(self.settings.universe_min_avg_volume):
                continue
            if avg_dollar_volume < float(self.settings.universe_min_avg_dollar_volume):
                continue

            liquidity = log1p(avg_dollar_volume)
            usable_volatility = min(max(avg_range, 0.0), 0.20)
            score = liquidity + usable_volatility * 30.0
            ranked.append((score, symbol))

        ranked.sort(reverse=True)
        candidates = [
            symbol
            for _score, symbol in ranked[: self.settings.universe_candidate_pool_size]
        ]
        self._eligible_symbols = eligible
        self._candidate_symbols = list(dict.fromkeys([*always, *candidates]))
        self._candidate_day = now.date()

        if not self._candidate_symbols:
            raise RuntimeError("dynamic universe candidate pool is empty")

    @staticmethod
    def _intraday_score(bars: list[dict[str, Any]]) -> float:
        if len(bars) < 2:
            return -1.0
        first = bars[0]
        latest = bars[-1]
        open_price = d(first.get("o"))
        close = d(latest.get("c"))
        if open_price <= 0 or close <= 0:
            return -1.0

        volume = sum((d(bar.get("v")) for bar in bars), Decimal("0"))
        session_dollar_volume = close * volume
        session_return = (close - open_price) / open_price
        high = max((d(bar.get("h")) for bar in bars), default=close)
        low = min((d(bar.get("l")) for bar in bars), default=close)
        session_range = (high - low) / close if close > 0 else Decimal("0")

        return (
            log1p(float(max(session_dollar_volume, Decimal("0"))))
            + max(float(session_return), 0.0) * 100.0
            + min(max(float(session_range), 0.0), 0.20) * 25.0
        )

    def _refresh_due(self, now: datetime) -> bool:
        if not self._active_symbols or self._last_refresh is None:
            return True
        return (
            now - self._last_refresh
        ).total_seconds() >= self.settings.universe_refresh_seconds

    async def active_symbols(
        self,
        *,
        position_symbols: list[str] | None = None,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        if not self.settings.dynamic_universe_enabled:
            symbols = self._static_fallback()
            self.state.set_universe(
                symbols=symbols,
                candidate_count=len(symbols),
                eligible_count=len(symbols),
                source="static",
                error=None,
            )
            return tuple(symbols)

        current = (now or datetime.now(NY)).astimezone(NY)
        if not self._refresh_due(current):
            return tuple(self._active_symbols)

        try:
            if self._candidate_day != current.date() or not self._candidate_symbols:
                await self._refresh_candidates(current)

            intraday = await self.market_data.bars_many(self._candidate_symbols)
            ranked = sorted(
                (
                    (self._intraday_score(intraday.get(symbol, [])), symbol)
                    for symbol in self._candidate_symbols
                ),
                reverse=True,
            )
            active = [
                symbol
                for score, symbol in ranked
                if score >= 0
            ][: self.settings.universe_size]

            active = list(
                dict.fromkeys(
                    [
                        *self.settings.universe_always_include,
                        *active,
                    ]
                )
            )[: self.settings.universe_size]

            if not active:
                raise RuntimeError("dynamic universe active set is empty")

            self._active_symbols = active
            self._last_refresh = current
            self.state.set_universe(
                symbols=active,
                candidate_count=len(self._candidate_symbols),
                eligible_count=len(self._eligible_symbols),
                source=self._candidate_source,
                at=current,
                error=None,
            )
            return tuple(active)
        except Exception as exc:
            fallback = self._active_symbols or self._static_fallback()
            self.state.set_universe(
                symbols=fallback,
                candidate_count=len(self._candidate_symbols),
                eligible_count=len(self._eligible_symbols),
                source="fallback",
                at=current,
                error=f"{type(exc).__name__}: {exc}",
            )
            return tuple(fallback)
