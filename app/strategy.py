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
    """Long-only SPY opening-range breakout with VWAP and QQQ/SMH confirmation.

    The strategy evaluates only completed one-minute bars. It builds the first N
    regular-session minutes as the opening range, waits for a fresh close above
    that range and SPY VWAP, and requires each confirmation symbol to be above its
    own VWAP without making a fresh one-bar low.

    Position exits are handled by the execution layer's bracket order and forced
    end-of-day flattening; the strategy itself never shorts or averages down.
    """

    def __init__(
        self,
        opening_range_minutes: int,
        stop_pct: Decimal,
        target_pct: Decimal,
        entry_start: time,
        entry_cutoff: time,
        confirmation_symbols: tuple[str, ...],
    ):
        self.opening_range_minutes = opening_range_minutes
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
        spy_vwap = self._vwap(session)

        fresh_breakout = previous_close <= opening_high and current_close > opening_high
        above_vwap = current_close > spy_vwap

        metadata: dict[str, Any] = {
            "opening_range_high": str(opening_high),
            "opening_range_low": str(opening_low),
            "spy_vwap": str(spy_vwap),
            "previous_close": str(previous_close),
            "current_close": str(current_close),
            "bar_time": self._timestamp(current).isoformat(),
            "confirmations": {},
        }

        if not fresh_breakout:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="no fresh close above opening-range high",
                metadata=metadata,
            )
        if not above_vwap:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="SPY breakout is below session VWAP",
                metadata=metadata,
            )

        for confirmation_symbol in self.confirmation_symbols:
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
                return Signal(
                    action="hold",
                    symbol=symbol,
                    reason=f"{confirmation_symbol} confirmation failed: {reason}",
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
            reason="fresh SPY opening-range breakout above VWAP with confirmations",
            metadata=metadata,
        )
