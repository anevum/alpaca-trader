from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .strategy import Signal


class BtcDirectSwingStrategy:
    """Direct BTC/USD 1-hour breakout strategy.

    RHEN-BTC-DIRECT-003 is intentionally slower than a 15-minute day-trading
    loop because current small-account crypto fees make high turnover
    structurally expensive. Entries use completed hourly bars only and require
    both a multi-horizon bull trend and a material breakout. Position exits are
    handled by the execution engine through the hard stop, target, and maximum
    hold window.
    """

    strategy_family = "btc_direct_intraday_breakout"
    strategy_version_id = "RHEN-BTC-DIRECT-003"

    timeframe = "1Hour"
    signal_timeframe = "1Hour"
    required_history_minutes = 35 * 24 * 60

    # The execution engine can reuse the same hourly history as regime history.
    regime_timeframe = "1Hour"
    regime_history_minutes = required_history_minutes

    manages_position_exits = False

    fast_trend_bars = 24
    medium_trend_bars = 72
    slow_trend_bars = 168
    long_trend_bars = 720

    breakout_lookback_bars = 48
    momentum_lookback_bars = 24
    min_momentum_pct = Decimal("0.01")

    hard_stop_pct = Decimal("0.025")
    take_profit_pct = Decimal("0.03")
    max_hold_minutes = 24 * 60

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

            bucket = stamp.replace(minute=0, second=0, microsecond=0)
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
            if bucket + timedelta(hours=1) > now_utc:
                continue
            row = dict(buckets[bucket])
            row.pop("_last", None)
            completed.append(row)
        return completed

    @staticmethod
    def _ema(values: list[Decimal], window: int) -> Decimal:
        if not values:
            return Decimal("0")
        alpha = Decimal("2") / Decimal(window + 1)
        value = values[0]
        for item in values[1:]:
            value = (item * alpha) + (value * (Decimal("1") - alpha))
        return value

    def _state(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        completed = self._completed(bars, now)
        minimum = max(
            self.long_trend_bars,
            self.breakout_lookback_bars + 1,
            self.momentum_lookback_bars + 1,
        ) + 1
        if len(completed) < minimum:
            return {
                "ready": False,
                "reason": (
                    f"BTC direct hourly warmup: {len(completed)}/{minimum} "
                    "completed bars"
                ),
                "completed_hourly_bars": len(completed),
            }

        signal = completed[-1]
        history = completed[:-1]
        closes = [row["c"] for row in completed]

        fast = self._ema(closes[-self.fast_trend_bars :], self.fast_trend_bars)
        medium = self._ema(
            closes[-self.medium_trend_bars :],
            self.medium_trend_bars,
        )
        slow = self._ema(closes[-self.slow_trend_bars :], self.slow_trend_bars)
        long = self._ema(closes[-self.long_trend_bars :], self.long_trend_bars)

        trend_long = bool(fast > medium > slow > long)

        momentum_anchor = completed[-(self.momentum_lookback_bars + 1)]["c"]
        momentum_return = (
            (signal["c"] - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        momentum_ready = bool(momentum_return > self.min_momentum_pct)

        prior_high = max(
            row["h"] for row in history[-self.breakout_lookback_bars :]
        )
        breakout = bool(signal["c"] > prior_high)
        bullish_bar = bool(signal["c"] > signal["o"])
        entry_ready = bool(
            trend_long
            and momentum_ready
            and breakout
            and bullish_bar
        )

        return {
            "ready": True,
            "bar_time": signal["t"].isoformat(),
            "close": signal["c"],
            "open": signal["o"],
            "fast_ema": fast,
            "medium_ema": medium,
            "slow_ema": slow,
            "long_ema": long,
            "trend_long": trend_long,
            "momentum_anchor": momentum_anchor,
            "momentum_return": momentum_return,
            "momentum_ready": momentum_ready,
            "prior_48h_high": prior_high,
            "breakout": breakout,
            "bullish_bar": bullish_bar,
            "entry_ready": entry_ready,
            "completed_hourly_bars": len(completed),
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

        state = self._state(bars, now)
        metadata = {
            "market": "crypto",
            "session_model": "24x7",
            "strategy_family": self.strategy_family,
            "strategy_version_id": self.strategy_version_id,
            "timeframe": self.signal_timeframe,
            "source_timeframe": self.timeframe,
            "regime_timeframe": self.regime_timeframe,
            "execution_model": (
                "completed_1h_breakout_then_next_available_market_execution"
            ),
            "research_dependency": False,
            "fee_aware": True,
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

        if not state["trend_long"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct multi-horizon bull trend is not active",
                metadata=metadata,
            )

        if not state["momentum_ready"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct 24-hour momentum is below threshold",
                metadata=metadata,
            )

        if not state["breakout"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct 48-hour breakout is not confirmed",
                metadata=metadata,
            )

        if not state["bullish_bar"]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="BTC direct breakout bar is not bullish",
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
            reason="BTC direct 1-hour trend breakout confirmed",
            metadata=metadata,
        )

    def position_exit_reason(
        self,
        bars: list[dict[str, Any]],
        regime_bars: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> str | None:
        return None
