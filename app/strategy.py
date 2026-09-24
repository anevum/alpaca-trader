from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Signal:
    action: str
    symbol: str = ""
    notional: Decimal = Decimal("0")
    reason: str = ""


class DisabledStrategy:
    """Default strategy. It intentionally never trades."""

    async def evaluate(self) -> Signal:
        return Signal(action="hold", reason="strategy is disabled")
