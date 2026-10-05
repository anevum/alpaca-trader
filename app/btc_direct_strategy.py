from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .strategy import Signal


class BtcDirectSwingStrategy:
    """Direct BTC/USD swing execution strategy.

    The strategy deliberately uses completed 4-hour bars and has no dependency on
    GRAEN, NOSTRA, ADS, promotion state, or external confirmation symbols.
    """

    strategy_family = "btc_direct_swing"
    strategy_version_id = "RHEN-BTC-DIRECT-001"
    timeframe = "1Hour"
    signal_timeframe = "4Hour"
    required_history_minutes = 270 * 24 * 60
    manages_position_exits = True

    momentum_lookback_bars = 180 * 6
    sma_window_bars = 250 * 6
    entry_lookback_bars = 42
    exit_lookback_bars = 15
    hard_stop_pct = Decimal("0.05")

    @staticmethod
    def _d(value: Any) -> Decimal:
        try:
            return Decimal(str(value or "0"))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _timestamp(bar: dict[str, Any]) -> datetime:
        raw = bar.get("t", bar.get("timestamp"))
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)

    def _completed(self, bars: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        """Aggregate hourly Alpaca bars into completed UTC-aligned 4-hour bars."""
        now_utc = now.astimezone(timezone.utc)
        buckets: dict[datetime, dict[str, Any]] = {}
        for bar in bars:
            try:
                stamp = self._timestamp(bar)
            except (TypeError, ValueError):
                continue
            open_ = self._d(bar.get("o", bar.get("open")))
            high = self._d(bar.get("h", bar.get("high")))
            low = self._d(bar.get("l", bar.get("low")))
            close = self._d(bar.get("c", bar.get("close")))
            if min(open_, high, low, close) <= 0:
                continue
            if high < low or high < max(open_, close) or low > min(open_, close):
                continue

            bucket = stamp.replace(
                hour=stamp.hour - (stamp.hour % 4),
                minute=0,
                second=0,
                microsecond=0,
            )
            current = buckets.get(bucket)
            if current is None:
                buckets[bucket] = {
                    "t": bucket,
                    "o": open_,
                    "h": high,
                    "l": low,
                    "c": close,
                    "_last": stamp,
                }
            else:
                current["h"] = max(current["h"], high)
                current["l"] = min(current["l"], low)
                if stamp >= current["_last"]:
                    current["c"] = close
                    current["_last"] = stamp

        completed: list[dict[str, Any]] = []
        for bucket in sorted(buckets):
            if bucket + timedelta(hours=4) > now_utc:
                continue
            row = dict(buckets[bucket])
            row.pop("_last", None)
            completed.append(row)
        return completed

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values))

    def _state(
        self,
        bars: list[dict[str, Any]],
        regime_bars: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        completed = self._completed(bars, now, bar_hours=4)
        regime_completed = self._completed(regime_bars, now, bar_hours=24)
        minimum_4h = max(self.entry_lookback_bars, self.exit_lookback_bars) + 1
        minimum_daily = max(self.momentum_lookback_bars, self.sma_window_bars) + 1
        if len(completed) < minimum_4h:
            return {
                "ready": False,
                "reason": (
                    f"BTC direct 4-hour warmup: {len(completed)}/{minimum_4h} "
                    "completed bars"
                ),
                "completed_4h_bars": len(completed),
                "completed_daily_bars": len(regime_completed),
            }
        if len(regime_completed) < minimum_daily:
            return {
                "ready": False,
                "reason": (
                    f"BTC direct daily regime warmup: "
                    f"{len(regime_completed)}/{minimum_daily} completed bars"
                ),
                "completed_4h_bars": len(completed),
                "completed_daily_bars": len(regime_completed),
            }

        signal = completed[-1]
        history = completed[:-1]
        daily_signal = regime_completed[-1]
        daily_history = regime_completed[:-1]
        close = signal["c"]
        daily_close = daily_signal["c"]
        momentum_anchor = daily_history[-self.momentum_lookback_bars]["c"]
        sma = self._mean(
            [row["c"] for row in regime_completed[-self.sma_window_bars:]]
        )
        momentum_return = (
            (daily_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        regime_long = bool(momentum_return > 0 or daily_close > sma)
        entry_high = max(
            row["h"] for row in history[-self.entry_lookback_bars:]
        )
        exit_low = min(
            row["l"] for row in history[-self.exit_lookback_bars:]
        )
        breakout = close > entry_high
        breakdown = close < exit_low
        return {
            "ready": True,
            "bar_time": signal["t"].isoformat(),
            "daily_regime_bar_time": daily_signal["t"].isoformat(),
            "close": close,
            "daily_close": daily_close,
            "momentum_anchor": momentum_anchor,
            "momentum_return": momentum_return,
            "sma": sma,
            "regime_long": regime_long,
            "entry_high": entry_high,
            "exit_low": exit_low,
            "breakout": breakout,
            "breakdown": breakdown,
            "completed_4h_bars": len(completed),
            "completed_daily_bars": len(regime_completed),
        }

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
        now: datetime | None = None,
    ) -> Signal:
        now = now or datetime.now(timezone.utc)
        symbol = symbol.upper()
        if symbol != "BTC/USD":
            return Signal(action="hold", symbol=symbol, reason="BTC direct strategy is BTC/USD only")
        if has_position:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct position already open; channel exit manages risk",
            )

        state = self._state(
            bars,
            confirmation_bars.get("BTC/USD", []),
            now,
        )
        metadata = {
            "market": "crypto",
            "session_model": "24x7",
            "strategy_family": self.strategy_family,
            "strategy_version_id": self.strategy_version_id,
            "timeframe": self.signal_timeframe,
            "source_timeframe": self.timeframe,
            "execution_model": "completed_4h_signal_then_next_available_market_execution",
            "research_dependency": False,
            "state": {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in state.items()
            },
        }
        if not state.get("ready"):
            return Signal(
                action="hold",
                symbol=symbol,
                reason=str(state.get("reason") or "BTC direct strategy not ready"),
                metadata=metadata,
            )
        if not state["regime_long"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct regime is flat",
                metadata=metadata,
            )
        if not state["breakout"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct 42-bar breakout not confirmed",
                metadata=metadata,
            )

        close = state["close"]
        stop_price = close * (Decimal("1") - self.hard_stop_pct)
        metadata["stop_price"] = str(stop_price)
        metadata["entry_reference"] = str(close)
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=close,
            stop_price=stop_price,
            take_profit_price=Decimal("0"),
            reason="BTC direct 4-hour regime breakout confirmed",
            metadata=metadata,
        )

    def position_exit_reason(
        self,
        bars: list[dict[str, Any]],
        regime_bars: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> str | None:
        state = self._state(
            bars,
            regime_bars or [],
            now or datetime.now(timezone.utc),
        )
        if not state.get("ready"):
            return None
        if not state["regime_long"]:
            return "BTC direct regime turned flat on completed 4-hour bar"
        if state["breakdown"]:
            return "BTC direct 15-bar channel exit confirmed on completed 4-hour bar"
        return None
