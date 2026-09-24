from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class Signal:
    action: str
    symbol: str = ""
    notional: Decimal = Decimal("0")
    reason: str = ""


class SmaCrossStrategy:
    """Long-only SMA crossover strategy.

    It enters only on a fresh bullish crossover and exits only on a fresh bearish
    crossover. It never shorts and does not buy merely because the bot started while
    the fast average was already above the slow average.
    """

    def __init__(self, fast_window: int, slow_window: int):
        self.fast_window = fast_window
        self.slow_window = slow_window

    @staticmethod
    def _close(bar: dict[str, Any]) -> Decimal:
        return Decimal(str(bar["c"]))

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
    ) -> Signal:
        required = self.slow_window + 1
        if len(bars) < required:
            return Signal(
                action="hold",
                symbol=symbol,
                reason=f"need {required} bars, received {len(bars)}",
            )

        closes = [self._close(bar) for bar in bars]
        previous = closes[:-1]
        current = closes

        prev_fast = sum(previous[-self.fast_window:]) / Decimal(self.fast_window)
        prev_slow = sum(previous[-self.slow_window:]) / Decimal(self.slow_window)
        curr_fast = sum(current[-self.fast_window:]) / Decimal(self.fast_window)
        curr_slow = sum(current[-self.slow_window:]) / Decimal(self.slow_window)

        if not has_position and prev_fast <= prev_slow and curr_fast > curr_slow:
            return Signal(
                action="buy",
                symbol=symbol,
                notional=order_notional,
                reason=f"bullish SMA crossover: fast={curr_fast} slow={curr_slow}",
            )

        if has_position and prev_fast >= prev_slow and curr_fast < curr_slow:
            return Signal(
                action="sell",
                symbol=symbol,
                reason=f"bearish SMA crossover: fast={curr_fast} slow={curr_slow}",
            )

        return Signal(
            action="hold",
            symbol=symbol,
            reason=f"no fresh crossover: fast={curr_fast} slow={curr_slow}",
        )
