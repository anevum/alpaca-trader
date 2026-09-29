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


class CryptoRollingMomentumStrategy(RollingMomentumVwapStrategy):
    """Rolling momentum/VWAP strategy with continuous 24/7 bar semantics."""

    def _completed_session_bars(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = now.astimezone(NY)
        completed: list[dict[str, Any]] = []
        for bar in bars:
            stamp = self._timestamp(bar)
            if stamp + timedelta(minutes=1) > now:
                continue
            completed.append(bar)
        completed.sort(key=self._timestamp)
        return completed

    @staticmethod
    def _crypto_price(value: Decimal) -> Decimal:
        if value < Decimal("1"):
            increment = Decimal("0.00000001")
        elif value < Decimal("100"):
            increment = Decimal("0.0001")
        else:
            increment = Decimal("0.01")
        return value.quantize(increment)

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
        now: datetime | None = None,
    ) -> Signal:
        now = (now or datetime.now(NY)).astimezone(NY)
        symbol = symbol.upper()
        if has_position:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="crypto position already open; exit layer manages risk",
            )

        session = self._completed_session_bars(bars, now)
        if len(session) < self.slow_window + 1:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="not enough completed crypto bars for rolling signal",
            )

        closes = [self._d(bar["c"]) for bar in session]
        current_close = closes[-1]
        previous_close = closes[-2]
        fast_average = self._mean(closes[-self.fast_window:])
        slow_average = self._mean(closes[-self.slow_window:])
        rolling_vwap = self._vwap(session)
        momentum_anchor = closes[-(self.fast_window + 1)]
        momentum_pct = (
            (current_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0 else Decimal("0")
        )
        vwap_edge_pct = (
            (current_close - rolling_vwap) / rolling_vwap
            if rolling_vwap > 0 else Decimal("0")
        )

        checks = {
            "fast_above_slow": fast_average > slow_average,
            "rising": current_close > previous_close,
            "momentum_ok": momentum_pct >= self.min_momentum_pct,
            "vwap_ok": (
                current_close > rolling_vwap
                and vwap_edge_pct >= self.min_vwap_edge_pct
            ),
            "vwap_extension_ok": vwap_edge_pct <= self.max_vwap_extension_pct,
            "confirmations_ok": False,
            "regime_ok": False,
        }
        metadata: dict[str, Any] = {
            "market": "crypto",
            "session_model": "24x7",
            "bar_time": self._timestamp(session[-1]).isoformat(),
            "current_close": str(current_close),
            "previous_close": str(previous_close),
            "fast_average": str(fast_average),
            "slow_average": str(slow_average),
            "rolling_vwap": str(rolling_vwap),
            "momentum_pct": str(momentum_pct),
            "vwap_edge_pct": str(vwap_edge_pct),
            "checks": checks,
            "confirmations": {},
            "regime_confirmations": {},
        }

        confirmation_passes = 0
        regime_passes = 0
        independent = 0
        for confirmation_symbol in self.confirmation_symbols:
            confirmation_symbol = confirmation_symbol.upper()
            if confirmation_symbol == symbol:
                continue
            independent += 1
            ok, reason, details = self._confirmation_ok(
                confirmation_bars.get(confirmation_symbol, []), now
            )
            metadata["confirmations"][confirmation_symbol] = {
                "ok": ok, "reason": reason, **details
            }
            confirmation_passes += int(ok)
            rok, rreason, rdetails = self._regime_ok(
                confirmation_bars.get(confirmation_symbol, []), now
            )
            metadata["regime_confirmations"][confirmation_symbol] = {
                "ok": rok, "reason": rreason, **rdetails
            }
            regime_passes += int(rok)

        checks["confirmations_ok"] = (
            independent >= self.min_confirmations
            and confirmation_passes >= self.min_confirmations
        )
        checks["regime_ok"] = (
            independent >= self.regime_min_confirmations
            and regime_passes >= self.regime_min_confirmations
        )
        metadata["confirmation_passes"] = confirmation_passes
        metadata["regime_passes"] = regime_passes

        failures = [
            ("fast_above_slow", "fast trend is not above slow trend"),
            ("rising", "latest completed crypto bar is not rising"),
            ("momentum_ok", "short-term crypto momentum is below threshold"),
            ("vwap_ok", "crypto price does not have required rolling VWAP edge"),
            ("vwap_extension_ok", "crypto price is too extended above rolling VWAP"),
            ("confirmations_ok", "not enough crypto market confirmations passed"),
            ("regime_ok", "crypto market regime is not constructive"),
        ]
        for key, reason in failures:
            if not checks[key]:
                return Signal(
                    action="hold",
                    symbol=symbol,
                    reason=reason,
                    metadata=metadata,
                )

        effective_stop_pct, stop_model = self._effective_stop_pct(session)
        stop_price = self._crypto_price(
            current_close * (Decimal("1") - effective_stop_pct)
        )
        target_price = self._crypto_price(
            current_close * (Decimal("1") + self.target_pct)
        )
        metadata["effective_stop_pct"] = str(effective_stop_pct)
        metadata["stop_model"] = stop_model
        metadata["stop_price"] = str(stop_price)
        metadata["take_profit_price"] = str(target_price)
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=current_close,
            stop_price=stop_price,
            take_profit_price=target_price,
            reason="24/7 crypto momentum above rolling VWAP with constructive regime",
            metadata=metadata,
        )


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

    async def latest_quotes(
        self,
        symbols: list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        output: dict[str, dict[str, Any]] = {}
        batches = self._batches(symbols)
        async with httpx.AsyncClient(timeout=8.0) as http:
            for batch in batches:
                response = await http.get(
                    f"{self.settings.data_base_url}/v1beta3/crypto/"
                    f"{self.settings.crypto_location}/latest/quotes",
                    headers=self.headers,
                    params={"symbols": ",".join(batch)},
                )
                response.raise_for_status()
                data = response.json()
                for symbol, quote in (data.get("quotes") or {}).items():
                    output[symbol.upper()] = quote or {}
        return output

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
            self.state.set_crypto_universe(
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
            self.state.set_crypto_universe(
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
        strategy: CryptoRollingMomentumStrategy,
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
            self.state.crypto_last_decision = "crypto lane paused"
            return {"action": "hold", "reason": self.state.crypto_last_decision}

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
                order_notional=getattr(
                    self.settings,
                    "crypto_order_notional",
                    self.settings.order_notional,
                ),
                now=now,
            )
            payload = self._signal_payload(signal)
            scan[symbol] = payload
            if signal.action == "buy":
                buy_signals.append(signal)

        self.state.record_crypto_scan(scan, at=now)
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
            self.state.crypto_last_signal = {
                "action": "hold",
                "symbol": "",
                "reason": (
                    f"crypto 24/7 watching {len(entry_symbols)} symbols; "
                    "no qualified entries"
                ),
                "metadata": {"market": "crypto", "session_model": "24x7"},
            }
            self.state.crypto_last_decision = self.state.crypto_last_signal["reason"]
            return self.state.crypto_last_signal

        signal = buy_signals[0]
        self.state.crypto_last_signal = self._signal_payload(signal)
        self.state.crypto_last_decision = (
            f"crypto 24/7 qualified {signal.symbol}; shadow execution only"
        )
        self.state.record_event(
            kind="crypto_signal",
            symbol=signal.symbol,
            action="shadow_buy",
            message=self.state.crypto_last_decision,
            reason=signal.reason,
            at=now,
            payload={"market": "crypto", "signal": self.state.crypto_last_signal},
        )
        return {
            "action": "shadow_buy",
            "symbol": signal.symbol,
            "reason": self.state.crypto_last_decision,
            "signal": self.state.crypto_last_signal,
        }
