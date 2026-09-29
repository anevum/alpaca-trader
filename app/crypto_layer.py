from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import log1p
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .alpaca_client import AlpacaClient
from .config import Settings
from .state import RuntimeState
from .strategy import RollingMomentumVwapStrategy, Signal


NY = ZoneInfo("America/New_York")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


class CryptoMarketDataClient:
    """Read-only Alpaca crypto market-data adapter for the 24/7 RHEN lane."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_api_secret,
        }

    @staticmethod
    def _normalize(symbols: list[str] | tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol and symbol.strip()
        ))

    def _batches(self, symbols: list[str] | tuple[str, ...]) -> list[list[str]]:
        normalized = self._normalize(symbols)
        size = max(1, min(self.settings.market_data_batch_size, 100))
        return [normalized[i:i + size] for i in range(0, len(normalized), size)]

    async def bars_many(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        timeframe: str = "1Min",
        lookback_minutes: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")

        batches = self._batches(symbols)
        output = {
            symbol: []
            for batch in batches
            for symbol in batch
        }
        if not batches:
            return output

        minutes = lookback_minutes or self.settings.crypto_lookback_minutes
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=max(minutes, 2))

        async with httpx.AsyncClient(timeout=12.0) as http:
            for batch in batches:
                page_token: str | None = None
                pages = 0
                while True:
                    params: dict[str, Any] = {
                        "symbols": ",".join(batch),
                        "timeframe": timeframe,
                        "start": start.isoformat().replace("+00:00", "Z"),
                        "end": end.isoformat().replace("+00:00", "Z"),
                        "limit": 10000,
                        "sort": "asc",
                    }
                    if page_token:
                        params["page_token"] = page_token
                    response = await http.get(
                        f"{self.settings.data_base_url}/v1beta3/crypto/"
                        f"{self.settings.crypto_location}/bars",
                        headers=self.headers,
                        params=params,
                    )
                    response.raise_for_status()
                    data = response.json()
                    for symbol, bars in (data.get("bars") or {}).items():
                        output.setdefault(symbol.upper(), []).extend(bars or [])
                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
                    pages += 1
                    if pages >= 20:
                        raise RuntimeError("crypto market-data pagination exceeded safety limit")
        return output


class CryptoUniverse:
    """Discovers and ranks Alpaca's active tradable USD crypto pairs."""

    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: CryptoMarketDataClient,
        state: RuntimeState,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.state = state
        self._active_symbols: list[str] = []
        self._eligible_count = 0
        self._last_refresh: datetime | None = None

    async def _eligible(self) -> list[str]:
        assets = await self.client.assets(status="active", asset_class="crypto")
        quotes = self.settings.crypto_quote_currencies
        excluded_bases = self.settings.crypto_excluded_bases
        symbols: list[str] = []
        for asset in assets:
            symbol = str(asset.get("symbol", "")).upper().strip()
            if "/" not in symbol:
                continue
            base, quote = symbol.split("/", 1)
            if (
                quote in quotes
                and base not in excluded_bases
                and str(asset.get("status", "")).lower() == "active"
                and bool(asset.get("tradable"))
                and bool(asset.get("fractionable"))
            ):
                symbols.append(symbol)
        return list(dict.fromkeys(symbols))

    @staticmethod
    def _score(bars: list[dict[str, Any]]) -> float:
        if len(bars) < 2:
            return -1.0
        first = _d(bars[0].get("o"))
        latest = _d(bars[-1].get("c"))
        if first <= 0 or latest <= 0:
            return -1.0
        volume = sum((_d(bar.get("v")) for bar in bars), Decimal("0"))
        dollar_volume = latest * volume
        high = max((_d(bar.get("h")) for bar in bars), default=latest)
        low = min((_d(bar.get("l")) for bar in bars), default=latest)
        move = abs((latest - first) / first)
        price_range = (high - low) / latest if latest > 0 else Decimal("0")
        return (
            log1p(float(max(dollar_volume, Decimal("0"))))
            + min(float(move), 0.30) * 35.0
            + min(max(float(price_range), 0.0), 0.40) * 20.0
        )

    def _refresh_due(self, now: datetime) -> bool:
        if not self._active_symbols or self._last_refresh is None:
            return True
        return (
            now - self._last_refresh
        ).total_seconds() >= self.settings.crypto_universe_refresh_seconds

    async def active_symbols(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        current = (now or datetime.now(NY)).astimezone(NY)
        if not self._refresh_due(current):
            return tuple(self._active_symbols)

        try:
            eligible = await self._eligible()
            self._eligible_count = len(eligible)
            if not eligible:
                raise RuntimeError("Alpaca returned no eligible crypto pairs")

            ranking_bars = await self.market_data.bars_many(
                eligible,
                timeframe="1Hour",
                lookback_minutes=24 * 60,
            )
            ranked = sorted(
                (
                    (self._score(ranking_bars.get(symbol, [])), symbol)
                    for symbol in eligible
                ),
                reverse=True,
            )
            active = [
                symbol
                for score, symbol in ranked
                if score >= 0
            ][: self.settings.crypto_universe_size]
            active = list(dict.fromkeys([
                *self.settings.crypto_always_include,
                *active,
            ]))[: self.settings.crypto_universe_size]
            if not active:
                raise RuntimeError("crypto active universe is empty")

            self._active_symbols = active
            self._last_refresh = current
            self.state.set_universe(
                symbols=active,
                candidate_count=len(eligible),
                eligible_count=len(eligible),
                source="crypto_dynamic",
                at=current,
                error=None,
            )
            return tuple(active)
        except Exception as exc:
            fallback = self._active_symbols or list(self.settings.crypto_always_include)
            self.state.set_universe(
                symbols=fallback,
                candidate_count=self._eligible_count,
                eligible_count=self._eligible_count,
                source="crypto_fallback",
                at=current,
                error=f"{type(exc).__name__}: {exc}",
            )
            return tuple(fallback)


class CryptoScanner:
    """Always-on read-only strategy evaluator for RHEN's crypto lane."""

    def __init__(
        self,
        settings: Settings,
        market_data: CryptoMarketDataClient,
        strategy: RollingMomentumVwapStrategy,
        state: RuntimeState,
        universe: CryptoUniverse,
    ):
        self.settings = settings
        self.market_data = market_data
        self.strategy = strategy
        self.state = state
        self.universe = universe

    @staticmethod
    def _signal_payload(signal: Signal) -> dict[str, Any]:
        metadata = dict(signal.metadata or {})
        metadata["market"] = "crypto"
        metadata["session_model"] = "24x7"
        return {
            "action": signal.action,
            "symbol": signal.symbol,
            "notional": str(signal.notional),
            "reference_price": str(signal.reference_price),
            "stop_price": str(signal.stop_price),
            "take_profit_price": str(signal.take_profit_price),
            "reason": signal.reason,
            "metadata": metadata,
        }

    async def scan_once(self) -> dict[str, Any]:
        self.state.mark_strategy()
        if self.state.paused:
            self.state.last_decision = "crypto lane paused"
            return {"action": "hold", "reason": self.state.last_decision}

        now = datetime.now(NY)
        entry_symbols = list(await self.universe.active_symbols(now=now))
        symbols = list(dict.fromkeys([
            *entry_symbols,
            *self.settings.crypto_confirmation_symbols,
        ]))
        market_bars = await self.market_data.bars_many(symbols)

        buy_signals: list[Signal] = []
        scan: dict[str, Any] = {}
        for symbol in entry_symbols:
            signal = self.strategy.evaluate(
                bars=market_bars.get(symbol, []),
                confirmation_bars={
                    confirmation: market_bars.get(confirmation, [])
                    for confirmation in self.settings.crypto_confirmation_symbols
                },
                symbol=symbol,
                has_position=False,
                order_notional=self.settings.order_notional,
                now=now,
            )
            payload = self._signal_payload(signal)
            scan[symbol] = payload
            if signal.action == "buy":
                buy_signals.append(signal)

        self.state.record_scan(scan, at=now)
        self.state.record_event(
            kind="crypto_scan_cycle",
            action="qualified" if buy_signals else "hold",
            message="24/7 crypto scan cycle completed",
            at=now,
            payload={
                "market": "crypto",
                "active_universe": entry_symbols,
                "qualified_count": len(buy_signals),
            },
        )
        print(
            "CRYPTO_SCAN_CYCLE",
            {
                "at": now.isoformat(),
                "symbols": len(entry_symbols),
                "ready": [signal.symbol for signal in buy_signals],
            },
            flush=True,
        )

        if not buy_signals:
            self.state.last_signal = {
                "action": "hold",
                "symbol": "",
                "reason": (
                    f"crypto 24/7 watching {len(entry_symbols)} symbols; "
                    "no qualified entries"
                ),
                "metadata": {"market": "crypto", "session_model": "24x7"},
            }
            self.state.last_decision = self.state.last_signal["reason"]
            return self.state.last_signal

        signal = buy_signals[0]
        self.state.last_signal = self._signal_payload(signal)
        self.state.last_decision = (
            f"crypto 24/7 qualified {signal.symbol}; shadow execution only"
        )
        self.state.record_event(
            kind="crypto_signal",
            symbol=signal.symbol,
            action="shadow_buy",
            message=self.state.last_decision,
            reason=signal.reason,
            at=now,
            payload={"market": "crypto", "signal": self.state.last_signal},
        )
        return {
            "action": "shadow_buy",
            "symbol": signal.symbol,
            "reason": self.state.last_decision,
            "signal": self.state.last_signal,
        }
