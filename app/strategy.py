from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


@dataclass
class Signal:
    action: str
    symbol: str = ""
    notional: Decimal = Decimal("0")
    reference_price: Decimal = Decimal("0")
    stop_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class OpeningRangeVwapStrategy:
    """Long-only opening-range breakout strategy with VWAP confirmation.

    Each candidate is evaluated only on completed one-minute regular-session
    bars. The strategy builds the first N minutes as an opening range, requires
    a fresh close above that range and session VWAP, rejects abnormally wide
    opening ranges and overly extended breakouts, and requires the configured
    market confirmation symbols to be constructive.

    Position exits are handled by the execution layer's protective bracket and
    end-of-day flattening. The strategy never shorts or averages down.
    """

    def __init__(
        self,
        opening_range_minutes: int,
        max_opening_range_pct: Decimal,
        max_breakout_extension_pct: Decimal,
        stop_pct: Decimal,
        target_pct: Decimal,
        entry_start: time,
        entry_cutoff: time,
        confirmation_symbols: tuple[str, ...],
    ):
        self.opening_range_minutes = opening_range_minutes
        self.max_opening_range_pct = max_opening_range_pct
        self.max_breakout_extension_pct = max_breakout_extension_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.entry_start = entry_start
        self.entry_cutoff = entry_cutoff
        self.confirmation_symbols = confirmation_symbols

    @staticmethod
    def _d(value: Any) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _timestamp(bar: dict[str, Any]) -> datetime:
        raw = str(bar["t"])
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=NY)
        return stamp.astimezone(NY)

    def _completed_session_bars(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = now.astimezone(NY)
        today = now.date()
        completed: list[dict[str, Any]] = []
        for bar in bars:
            stamp = self._timestamp(bar)
            if stamp.date() != today:
                continue
            if not (MARKET_OPEN <= stamp.time() < MARKET_CLOSE):
                continue
            if stamp + timedelta(minutes=1) > now:
                continue
            completed.append(bar)
        completed.sort(key=self._timestamp)
        return completed

    @staticmethod
    def _vwap(bars: list[dict[str, Any]]) -> Decimal:
        total_volume = Decimal("0")
        weighted = Decimal("0")
        fallback: list[Decimal] = []
        for bar in bars:
            close = Decimal(str(bar["c"]))
            fallback.append(close)
            volume = Decimal(str(bar.get("v", "0") or "0"))
            bar_vwap = Decimal(str(bar.get("vw", bar["c"])))
            if volume > 0:
                total_volume += volume
                weighted += bar_vwap * volume
        if total_volume > 0:
            return weighted / total_volume
        if fallback:
            return sum(fallback) / Decimal(len(fallback))
        return Decimal("0")

    def _opening_range(
        self,
        bars: list[dict[str, Any]],
    ) -> tuple[Decimal, Decimal] | None:
        end_minutes = 9 * 60 + 30 + self.opening_range_minutes
        opening: list[dict[str, Any]] = []
        for bar in bars:
            stamp = self._timestamp(bar)
            minute_of_day = stamp.hour * 60 + stamp.minute
            if 9 * 60 + 30 <= minute_of_day < end_minutes:
                opening.append(bar)
        if len(opening) < self.opening_range_minutes:
            return None
        high = max(self._d(bar["h"]) for bar in opening)
        low = min(self._d(bar["l"]) for bar in opening)
        return high, low

    def _confirmation_ok(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[bool, str, dict[str, str]]:
        session = self._completed_session_bars(bars, now)
        if len(session) < 2:
            return False, "not enough completed confirmation bars", {}

        previous, current = session[-2], session[-1]
        current_close = self._d(current["c"])
        previous_close = self._d(previous["c"])
        current_low = self._d(current["l"])
        previous_low = self._d(previous["l"])
        vwap = self._vwap(session)

        above_vwap = current_close >= vwap
        not_falling = current_close >= previous_close
        no_fresh_low = current_low >= previous_low
        ok = above_vwap and not_falling and no_fresh_low
        details = {
            "close": str(current_close),
            "previous_close": str(previous_close),
            "low": str(current_low),
            "previous_low": str(previous_low),
            "vwap": str(vwap),
        }
        if ok:
            return True, "confirmation passed", details
        failed = []
        if not above_vwap:
            failed.append("below VWAP")
        if not not_falling:
            failed.append("close below previous close")
        if not no_fresh_low:
            failed.append("fresh one-bar low")
        return False, ", ".join(failed), details

    @staticmethod
    def _price(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
                reason="position already open; bracket exits manage risk",
            )

        if now.weekday() >= 5:
            return Signal(action="hold", symbol=symbol, reason="weekend")
        if now.time() < self.entry_start:
            return Signal(action="hold", symbol=symbol, reason="before entry window")
        if now.time() > self.entry_cutoff:
            return Signal(action="hold", symbol=symbol, reason="entry window closed")

        session = self._completed_session_bars(bars, now)
        opening_range = self._opening_range(session)
        if opening_range is None:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="opening range is not complete",
            )
        if len(session) < self.opening_range_minutes + 1:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="waiting for first completed post-range bar",
            )

        opening_high, opening_low = opening_range
        previous, current = session[-2], session[-1]
        previous_close = self._d(previous["c"])
        current_close = self._d(current["c"])
        session_vwap = self._vwap(session)

        if opening_low <= 0 or opening_high <= 0:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="invalid opening-range prices",
            )

        opening_range_pct = (opening_high - opening_low) / opening_low
        breakout_extension_pct = (
            (current_close - opening_high) / opening_high
            if current_close > opening_high
            else Decimal("0")
        )
        fresh_breakout = previous_close <= opening_high and current_close > opening_high
        above_vwap = current_close > session_vwap

        opening_range_ok = opening_range_pct <= self.max_opening_range_pct
        extension_ok = breakout_extension_pct <= self.max_breakout_extension_pct

        metadata: dict[str, Any] = {
            "opening_range_high": str(opening_high),
            "opening_range_low": str(opening_low),
            "opening_range_pct": str(opening_range_pct),
            "session_vwap": str(session_vwap),
            "previous_close": str(previous_close),
            "current_close": str(current_close),
            "breakout_extension_pct": str(breakout_extension_pct),
            "distance_to_breakout_pct": str((current_close - opening_high) / opening_high),
            "bar_time": self._timestamp(current).isoformat(),
            "checks": {
                "opening_range_ok": opening_range_ok,
                "fresh_breakout": fresh_breakout,
                "breakout_extension_ok": extension_ok,
                "above_vwap": above_vwap,
            },
            "confirmations": {},
        }

        independent_confirmations = 0
        confirmations_ok = True
        first_confirmation_failure = ""
        for confirmation_symbol in self.confirmation_symbols:
            confirmation_symbol = confirmation_symbol.upper()
            if confirmation_symbol == symbol:
                metadata["confirmations"][confirmation_symbol] = {
                    "ok": True,
                    "reason": "candidate symbol; self-confirmation skipped",
                }
                continue
            independent_confirmations += 1
            ok, reason, details = self._confirmation_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            metadata["confirmations"][confirmation_symbol] = {
                "ok": ok,
                "reason": reason,
                **details,
            }
            if not ok:
                confirmations_ok = False
                if not first_confirmation_failure:
                    first_confirmation_failure = (
                        f"{confirmation_symbol} confirmation failed: {reason}"
                    )

        metadata["checks"]["confirmations_ok"] = (
            confirmations_ok and independent_confirmations > 0
        )

        if not opening_range_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="opening range is too wide",
                metadata=metadata,
            )
        if not fresh_breakout:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="no fresh close above opening-range high",
                metadata=metadata,
            )
        if not extension_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="breakout is too extended above opening-range high",
                metadata=metadata,
            )
        if not above_vwap:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="breakout is below session VWAP",
                metadata=metadata,
            )
        if independent_confirmations == 0:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="no independent confirmation symbol available",
                metadata=metadata,
            )
        if not confirmations_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason=first_confirmation_failure,
                metadata=metadata,
            )

        effective_stop_pct, stop_model = self._effective_stop_pct(session)
        stop_price = self._price(
            current_close * (Decimal("1") - effective_stop_pct)
        )
        take_profit_price = self._price(
            current_close * (Decimal("1") + self.target_pct)
        )
        metadata["effective_stop_pct"] = str(effective_stop_pct)
        metadata["stop_model"] = stop_model
        metadata["stop_price"] = str(stop_price)
        metadata["take_profit_price"] = str(take_profit_price)

        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=current_close,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            reason="fresh opening-range breakout above VWAP with confirmations",
            metadata=metadata,
        )


class RollingMomentumVwapStrategy(OpeningRangeVwapStrategy):
    """Intraday rolling momentum strategy for repeated short-duration entries.

    The strategy uses completed one-minute bars, compares short and slow moving
    averages, requires positive short-term momentum and price above session
    VWAP, and accepts a configurable minimum number of constructive market
    confirmations. Protective exits remain the execution layer's responsibility.
    """

    def __init__(
        self,
        fast_window: int,
        slow_window: int,
        min_momentum_pct: Decimal,
        min_vwap_edge_pct: Decimal,
        stop_pct: Decimal,
        target_pct: Decimal,
        entry_start: time,
        entry_cutoff: time,
        confirmation_symbols: tuple[str, ...],
        min_confirmations: int,
        regime_window: int = 5,
        regime_min_confirmations: int = 1,
        regime_min_return_pct: Decimal = Decimal("0"),
        max_vwap_extension_pct: Decimal = Decimal("0.008"),
        volatility_stop_enabled: bool = False,
        volatility_stop_multiplier: Decimal = Decimal("2.0"),
        volatility_stop_lookback_bars: int = 8,
        max_dynamic_stop_pct: Decimal = Decimal("0.006"),
    ):
        super().__init__(
            opening_range_minutes=1,
            max_opening_range_pct=Decimal("0.099"),
            max_breakout_extension_pct=Decimal("0.049"),
            stop_pct=stop_pct,
            target_pct=target_pct,
            entry_start=entry_start,
            entry_cutoff=entry_cutoff,
            confirmation_symbols=confirmation_symbols,
        )
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.min_momentum_pct = min_momentum_pct
        self.min_vwap_edge_pct = min_vwap_edge_pct
        self.min_confirmations = min_confirmations
        self.regime_window = regime_window
        self.regime_min_confirmations = regime_min_confirmations
        self.regime_min_return_pct = regime_min_return_pct
        self.max_vwap_extension_pct = max_vwap_extension_pct
        self.volatility_stop_enabled = volatility_stop_enabled
        self.volatility_stop_multiplier = volatility_stop_multiplier
        self.volatility_stop_lookback_bars = volatility_stop_lookback_bars
        self.max_dynamic_stop_pct = max_dynamic_stop_pct

    def _effective_stop_pct(
        self,
        session: list[dict[str, Any]],
    ) -> tuple[Decimal, dict[str, str]]:
        if not self.volatility_stop_enabled or not session:
            return self.stop_pct, {
                "mode": "fixed",
                "base_stop_pct": str(self.stop_pct),
                "effective_stop_pct": str(self.stop_pct),
            }

        bars = session[-self.volatility_stop_lookback_bars:]
        true_ranges: list[Decimal] = []
        previous_close: Decimal | None = None
        for bar in bars:
            high = self._d(bar["h"])
            low = self._d(bar["l"])
            close = self._d(bar["c"])
            true_range = high - low
            if previous_close is not None:
                true_range = max(
                    true_range,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            if true_range > 0:
                true_ranges.append(true_range)
            previous_close = close

        current_close = self._d(session[-1]["c"])
        if not true_ranges or current_close <= 0:
            return self.stop_pct, {
                "mode": "fixed_fallback",
                "base_stop_pct": str(self.stop_pct),
                "effective_stop_pct": str(self.stop_pct),
            }

        atr = self._mean(true_ranges)
        atr_pct = atr / current_close
        volatility_stop_pct = atr_pct * self.volatility_stop_multiplier
        effective_stop_pct = min(
            self.max_dynamic_stop_pct,
            max(self.stop_pct, volatility_stop_pct),
        )
        return effective_stop_pct, {
            "mode": "volatility",
            "atr": str(atr),
            "atr_pct": str(atr_pct),
            "multiplier": str(self.volatility_stop_multiplier),
            "base_stop_pct": str(self.stop_pct),
            "max_dynamic_stop_pct": str(self.max_dynamic_stop_pct),
            "effective_stop_pct": str(effective_stop_pct),
        }

    def position_health(
        self,
        *,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        now: datetime,
    ) -> dict[str, Any]:
        now = now.astimezone(NY)
        session = self._completed_session_bars(bars, now)
        if len(session) < self.slow_window + 1:
            return {
                "data_ready": False,
                "strong_failure": False,
                "reason": "not enough completed bars for position health",
            }

        closes = [self._d(bar["c"]) for bar in session]
        current_close = closes[-1]
        fast_average = self._mean(closes[-self.fast_window:])
        slow_average = self._mean(closes[-self.slow_window:])
        momentum_anchor = closes[-(self.fast_window + 1)]
        momentum_pct = (
            (current_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )

        candidate_checks = {
            "fast_above_slow": fast_average > slow_average,
            "above_fast_average": current_close >= fast_average,
            "positive_momentum": momentum_pct > 0,
        }
        candidate_failure_count = sum(
            1 for ok in candidate_checks.values() if not ok
        )

        regime_passes = 0
        regime_details: dict[str, Any] = {}
        independent_confirmations = 0
        for confirmation_symbol in self.confirmation_symbols:
            confirmation_symbol = confirmation_symbol.upper()
            if confirmation_symbol == symbol.upper():
                continue
            independent_confirmations += 1
            ok, reason, details = self._regime_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            regime_details[confirmation_symbol] = {
                "ok": ok,
                "reason": reason,
                **details,
            }
            if ok:
                regime_passes += 1

        regime_ok = (
            independent_confirmations >= self.regime_min_confirmations
            and regime_passes >= self.regime_min_confirmations
        )
        strong_failure = (not regime_ok) and candidate_failure_count >= 2
        return {
            "data_ready": True,
            "strong_failure": strong_failure,
            "reason": (
                "market regime and position momentum both deteriorated"
                if strong_failure
                else "position thesis remains viable"
            ),
            "current_close": str(current_close),
            "fast_average": str(fast_average),
            "slow_average": str(slow_average),
            "momentum_pct": str(momentum_pct),
            "candidate_checks": candidate_checks,
            "candidate_failure_count": candidate_failure_count,
            "regime_ok": regime_ok,
            "regime_passes": regime_passes,
            "regime_min_confirmations": self.regime_min_confirmations,
            "regime": regime_details,
        }

    def _regime_ok(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[bool, str, dict[str, str]]:
        session = self._completed_session_bars(bars, now)
        needed = self.regime_window + 1
        if len(session) < needed:
            return False, "not enough completed bars for regime check", {}

        closes = [self._d(bar["c"]) for bar in session]
        current_close = closes[-1]
        anchor_close = closes[-needed]
        recent_mean = self._mean(closes[-self.regime_window:])
        session_vwap = self._vwap(session)
        window_return_pct = (
            (current_close - anchor_close) / anchor_close
            if anchor_close > 0
            else Decimal("0")
        )
        above_recent_mean = current_close >= recent_mean
        above_vwap = current_close >= session_vwap
        return_ok = window_return_pct >= self.regime_min_return_pct
        ok = above_recent_mean and above_vwap and return_ok
        details = {
            "close": str(current_close),
            "anchor_close": str(anchor_close),
            "recent_mean": str(recent_mean),
            "session_vwap": str(session_vwap),
            "window_return_pct": str(window_return_pct),
        }
        if ok:
            return True, "regime constructive", details
        failed: list[str] = []
        if not above_recent_mean:
            failed.append("below recent mean")
        if not above_vwap:
            failed.append("below VWAP")
        if not return_ok:
            failed.append("window return below threshold")
        return False, ", ".join(failed), details

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal:
        return sum(values) / Decimal(len(values))

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
                reason="position already open; bracket/time exits manage risk",
            )
        if now.weekday() >= 5:
            return Signal(action="hold", symbol=symbol, reason="weekend")
        if now.time() < self.entry_start:
            return Signal(action="hold", symbol=symbol, reason="before entry window")
        if now.time() > self.entry_cutoff:
            return Signal(action="hold", symbol=symbol, reason="entry window closed")

        session = self._completed_session_bars(bars, now)
        if len(session) < self.slow_window + 1:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="not enough completed bars for rolling signal",
            )

        closes = [self._d(bar["c"]) for bar in session]
        current_close = closes[-1]
        previous_close = closes[-2]
        fast_average = self._mean(closes[-self.fast_window:])
        slow_average = self._mean(closes[-self.slow_window:])
        session_vwap = self._vwap(session)

        momentum_anchor = closes[-(self.fast_window + 1)]
        momentum_pct = (
            (current_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        vwap_edge_pct = (
            (current_close - session_vwap) / session_vwap
            if session_vwap > 0
            else Decimal("0")
        )

        fast_above_slow = fast_average > slow_average
        rising = current_close > previous_close
        momentum_ok = momentum_pct >= self.min_momentum_pct
        vwap_ok = current_close > session_vwap and vwap_edge_pct >= self.min_vwap_edge_pct
        vwap_extension_ok = vwap_edge_pct <= self.max_vwap_extension_pct

        metadata: dict[str, Any] = {
            "bar_time": self._timestamp(session[-1]).isoformat(),
            "current_close": str(current_close),
            "previous_close": str(previous_close),
            "fast_average": str(fast_average),
            "slow_average": str(slow_average),
            "session_vwap": str(session_vwap),
            "momentum_pct": str(momentum_pct),
            "vwap_edge_pct": str(vwap_edge_pct),
            "checks": {
                "fast_above_slow": fast_above_slow,
                "rising": rising,
                "momentum_ok": momentum_ok,
                "vwap_ok": vwap_ok,
                "vwap_extension_ok": vwap_extension_ok,
                "confirmations_ok": False,
                "regime_ok": False,
            },
            "confirmations": {},
            "regime_confirmations": {},
            "max_vwap_extension_pct": str(self.max_vwap_extension_pct),
        }

        confirmation_passes = 0
        regime_passes = 0
        independent_confirmations = 0
        for confirmation_symbol in self.confirmation_symbols:
            confirmation_symbol = confirmation_symbol.upper()
            if confirmation_symbol == symbol:
                metadata["confirmations"][confirmation_symbol] = {
                    "ok": True,
                    "reason": "candidate symbol; self-confirmation skipped",
                }
                continue
            independent_confirmations += 1
            ok, reason, details = self._confirmation_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            metadata["confirmations"][confirmation_symbol] = {
                "ok": ok,
                "reason": reason,
                **details,
            }
            if ok:
                confirmation_passes += 1
            regime_ok, regime_reason, regime_details = self._regime_ok(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            metadata["regime_confirmations"][confirmation_symbol] = {
                "ok": regime_ok,
                "reason": regime_reason,
                **regime_details,
            }
            if regime_ok:
                regime_passes += 1

        confirmations_ok = (
            independent_confirmations >= self.min_confirmations
            and confirmation_passes >= self.min_confirmations
        )
        regime_ok = (
            independent_confirmations >= self.regime_min_confirmations
            and regime_passes >= self.regime_min_confirmations
        )
        metadata["checks"]["confirmations_ok"] = confirmations_ok
        metadata["checks"]["regime_ok"] = regime_ok
        metadata["confirmation_passes"] = confirmation_passes
        metadata["min_confirmations"] = self.min_confirmations
        metadata["regime_passes"] = regime_passes
        metadata["regime_min_confirmations"] = self.regime_min_confirmations
        metadata["regime_window"] = self.regime_window
        metadata["regime_min_return_pct"] = str(self.regime_min_return_pct)

        if not fast_above_slow:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="fast trend is not above slow trend",
                metadata=metadata,
            )
        if not rising:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="latest completed bar is not rising",
                metadata=metadata,
            )
        if not momentum_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="short-term momentum is below threshold",
                metadata=metadata,
            )
        if not vwap_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="price does not have required VWAP edge",
                metadata=metadata,
            )
        if not vwap_extension_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="price is too extended above session VWAP",
                metadata=metadata,
            )
        if not confirmations_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="not enough market confirmations passed",
                metadata=metadata,
            )
        if not regime_ok:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="market regime is not constructive",
                metadata=metadata,
            )

        stop_price = self._price(current_close * (Decimal("1") - self.stop_pct))
        take_profit_price = self._price(
            current_close * (Decimal("1") + self.target_pct)
        )
        metadata["stop_price"] = str(stop_price)
        metadata["take_profit_price"] = str(take_profit_price)

        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=current_close,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            reason="rolling momentum above VWAP with constructive market regime",
            metadata=metadata,
        )
