from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .strategy import Signal


class BtcDirectSwingStrategy:
    """Direct BTC/USD pullback/reclaim execution strategy.

    The strategy uses completed 4-hour bars for entries/exits and completed
    daily bars for the broad regime. It has no GRAEN, NOSTRA, ADS, promotion,
    or external-confirmation dependency.
    """

    strategy_family = "btc_direct_pullback"
    strategy_version_id = "RHEN-BTC-DIRECT-002"

    timeframe = "4Hour"
    signal_timeframe = "4Hour"
    required_history_minutes = 14 * 24 * 60

    regime_timeframe = "1Day"
    regime_history_minutes = 100 * 24 * 60

    manages_position_exits = True

    momentum_lookback_bars = 60
    sma_window_bars = 90

    fast_window_bars = 8
    slow_window_bars = 24
    dip_window_bars = 4
    dip_threshold_pct = Decimal("0.012")

    hard_stop_pct = Decimal("0.03")
    take_profit_pct = Decimal("0.05")
    max_hold_minutes = 18 * 4 * 60

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

    def _completed(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
        *,
        bar_hours: int,
    ) -> list[dict[str, Any]]:
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

            if bar_hours == 24:
                bucket = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                bucket = stamp.replace(
                    hour=stamp.hour - (stamp.hour % bar_hours),
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
            if bucket + timedelta(hours=bar_hours) > now_utc:
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
        regime_completed = self._completed(regime_bars or bars, now, bar_hours=24)

        minimum_4h = self.slow_window_bars + 2
        minimum_daily = max(
            self.momentum_lookback_bars,
            self.sma_window_bars,
        ) + 1

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
        previous = completed[-2]

        daily_signal = regime_completed[-1]
        daily_history = regime_completed[:-1]
        daily_close = daily_signal["c"]
        momentum_anchor = daily_history[-self.momentum_lookback_bars]["c"]
        daily_sma = self._mean(
            [row["c"] for row in regime_completed[-self.sma_window_bars:]]
        )
        momentum_return = (
            (daily_close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        regime_long = bool(momentum_return > 0 and daily_close > daily_sma)

        fast = self._mean(
            [row["c"] for row in completed[-self.fast_window_bars:]]
        )
        slow = self._mean(
            [row["c"] for row in completed[-self.slow_window_bars:]]
        )
        previous_fast = self._mean(
            [
                row["c"]
                for row in completed[-self.fast_window_bars - 1 : -1]
            ]
        )

        recent_low = min(
            row["l"] for row in completed[-self.dip_window_bars:]
        )
        dip_threshold = fast * (Decimal("1") - self.dip_threshold_pct)
        recent_dip = recent_low <= dip_threshold

        bullish_structure = fast > slow
        reclaim = bool(
            signal["c"] > fast
            and previous["c"] <= previous_fast
            and signal["c"] > signal["o"]
        )
        entry_ready = bool(
            regime_long
            and bullish_structure
            and recent_dip
            and reclaim
        )

        return {
            "ready": True,
            "bar_time": signal["t"].isoformat(),
            "daily_regime_bar_time": daily_signal["t"].isoformat(),
            "close": signal["c"],
            "open": signal["o"],
            "daily_close": daily_close,
            "momentum_anchor": momentum_anchor,
            "momentum_return": momentum_return,
            "daily_sma": daily_sma,
            "regime_long": regime_long,
            "fast": fast,
            "slow": slow,
            "previous_fast": previous_fast,
            "recent_low": recent_low,
            "dip_threshold": dip_threshold,
            "recent_dip": recent_dip,
            "bullish_structure": bullish_structure,
            "reclaim": reclaim,
            "entry_ready": entry_ready,
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
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct strategy is BTC/USD only",
            )

        if has_position:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct position already open",
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
            "regime_timeframe": self.regime_timeframe,
            "execution_model": (
                "completed_4h_pullback_reclaim_then_next_available_market_execution"
            ),
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
                reason="BTC direct daily bull regime is not active",
                metadata=metadata,
            )

        if not state["bullish_structure"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct 4-hour trend structure is not bullish",
                metadata=metadata,
            )

        if not state["recent_dip"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct pullback threshold has not been reached",
                metadata=metadata,
            )

        if not state["reclaim"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct 4-hour reclaim is not confirmed",
                metadata=metadata,
            )

        close = state["close"]
        stop_price = close * (Decimal("1") - self.hard_stop_pct)
        target_price = close * (Decimal("1") + self.take_profit_pct)
        metadata["stop_price"] = str(stop_price)
        metadata["target_price"] = str(target_price)
        metadata["entry_reference"] = str(close)

        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=close,
            stop_price=stop_price,
            take_profit_price=target_price,
            reason="BTC direct 4-hour bull-regime pullback reclaim confirmed",
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
            regime_bars or bars,
            now or datetime.now(timezone.utc),
        )
        if not state.get("ready"):
            return None
        if not state["regime_long"]:
            return "BTC direct daily bull regime ended"
        if not state["bullish_structure"]:
            return "BTC direct 4-hour fast trend crossed below slow trend"
        return None
