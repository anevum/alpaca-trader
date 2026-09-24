from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .market_data import MarketDataClient
from .state import RuntimeState
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy, Signal


NY = ZoneInfo("America/New_York")


class ReadOnlyScanner:
    """Market-data-only strategy evaluator.

    This class intentionally has no order, position, or account-management
    methods. It reads the market clock and bars, evaluates configured symbols,
    and records BUY/HOLD telemetry only.
    """

    def __init__(
        self,
        settings: Settings,
        clock_client: Any,
        market_data: MarketDataClient,
        strategy: OpeningRangeVwapStrategy | RollingMomentumVwapStrategy,
        state: RuntimeState,
    ):
        self.settings = settings
        self.clock_client = clock_client
        self.market_data = market_data
        self.strategy = strategy
        self.state = state

    @staticmethod
    def _signal_payload(signal: Signal) -> dict[str, Any]:
        return {
            "action": signal.action,
            "symbol": signal.symbol,
            "notional": str(signal.notional),
            "reference_price": str(signal.reference_price),
            "stop_price": str(signal.stop_price),
            "take_profit_price": str(signal.take_profit_price),
            "reason": signal.reason,
            "metadata": signal.metadata,
        }

    async def scan_once(self) -> dict[str, Any]:
        self.state.mark_strategy()

        if self.state.paused:
            self.state.last_decision = "runtime paused"
            return {"action": "hold", "reason": self.state.last_decision}

        clock = await self.clock_client.clock()
        if not bool(clock.get("is_open")):
            self.state.last_decision = "market is closed"
            return {"action": "hold", "reason": self.state.last_decision}

        now = datetime.now(NY)
        symbols = list(
            dict.fromkeys(
                [*self.settings.scan_symbols, *self.settings.confirmation_symbols]
            )
        )
        market_bars = await self.market_data.bars_many(symbols)

        buy_signals: list[Signal] = []
        scan: dict[str, Any] = {}
        for symbol in self.settings.scan_symbols:
            signal = self.strategy.evaluate(
                bars=market_bars.get(symbol, []),
                confirmation_bars={
                    confirmation_symbol: market_bars.get(confirmation_symbol, [])
                    for confirmation_symbol in self.settings.confirmation_symbols
                },
                symbol=symbol,
                has_position=False,
                order_notional=self.settings.order_notional,
                now=now,
            )
            scan[symbol] = self._signal_payload(signal)
            if signal.action == "buy":
                buy_signals.append(signal)

        self.state.record_scan(scan, at=now)
        print(
            "SAFE_SCAN_CYCLE",
            {
                "at": now.isoformat(),
                "symbols": len(self.settings.scan_symbols),
                "ready": [signal.symbol for signal in buy_signals],
            },
            flush=True,
        )

        if not buy_signals:
            self.state.last_signal = {
                "action": "hold",
                "symbol": "",
                "reason": (
                    f"scan-only watching {len(self.settings.scan_symbols)} symbols; "
                    "no qualified entries"
                ),
                "metadata": {},
            }
            self.state.last_decision = self.state.last_signal["reason"]
            return self.state.last_signal

        signal = buy_signals[0]
        self.state.last_signal = self._signal_payload(signal)
        self.state.last_decision = (
            f"scan-only qualified {signal.symbol}; execution disabled"
        )
        self.state.record_event(
            kind="signal",
            symbol=signal.symbol,
            action="shadow_buy",
            message=self.state.last_decision,
            reason=signal.reason,
            at=now,
        )
        return {
            "action": "shadow_buy",
            "symbol": signal.symbol,
            "reason": self.state.last_decision,
            "signal": self.state.last_signal,
        }
