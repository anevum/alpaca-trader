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
    timeframe = "4Hour"
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
        now_utc = now.astimezone(timezone.utc)
        by_stamp: dict[datetime, dict[str, Any]] = {}
        for bar in bars:
            try:
                stamp = self._timestamp(bar)
            except (TypeError, ValueError):
                continue
            if stamp + timedelta(hours=4) > now_utc:
                continue
            open_ = self._d(bar.get("o", bar.get("open")))
            high = self._d(bar.get("h", bar.get("high")))
            low = self._d(bar.get("l", bar.get("low")))
            close = self._d(bar.get("c", bar.get("close")))
            if min(open_, high, low, close) <= 0:
                continue
            if high < low or high < max(open_, close) or low > min(open_, close):
                continue
            by_stamp[stamp] = {
                "t": stamp,
                "o": open_,
                "h": high,
                "l": low,
                "c": close,
            }
        return [by_stamp[key] for key in sorted(by_stamp)]

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values))

    def _state(self, bars: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
        completed = self._completed(bars, now)
        minimum = max(
            self.momentum_lookback_bars,
            self.sma_window_bars,
            self.entry_lookback_bars,
            self.exit_lookback_bars,
        )
        if len(completed) < minimum + 1:
            return {
                "ready": False,
                "reason": (
                    f"BTC direct strategy warming up: {len(completed)}/{minimum + 1} "
                    "completed 4-hour bars"
                ),
                "completed_bars": len(completed),
            }

        signal = completed[-1]
        history = completed[:-1]
        close = signal["c"]
        momentum_anchor = history[-self.momentum_lookback_bars]["c"]
        sma = self._mean([row["c"] for row in completed[-self.sma_window_bars:]])
        momentum_return = (
            (close - momentum_anchor) / momentum_anchor
            if momentum_anchor > 0
            else Decimal("0")
        )
        regime_long = bool(momentum_return > 0 or close > sma)
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
            "close": close,
            "momentum_anchor": momentum_anchor,
            "momentum_return": momentum_return,
            "sma": sma,
            "regime_long": regime_long,
            "entry_high": entry_high,
            "exit_low": exit_low,
            "breakout": breakout,
            "breakdown": breakdown,
            "completed_bars": len(completed),
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
        del confirmation_bars
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

        state = self._state(bars, now)
        metadata = {
            "market": "crypto",
            "session_model": "24x7",
            "strategy_family": self.strategy_family,
            "strategy_version_id": self.strategy_version_id,
            "timeframe": self.timeframe,
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
        now: datetime | None = None,
    ) -> str | None:
        state = self._state(bars, now or datetime.now(timezone.utc))
        if not state.get("ready"):
            return None
        if not state["regime_long"]:
            return "BTC direct regime turned flat on completed 4-hour bar"
        if state["breakdown"]:
            return "BTC direct 15-bar channel exit confirmed on completed 4-hour bar"
        return None
