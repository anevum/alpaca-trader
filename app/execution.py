from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .market_data import MarketDataClient
from .risk import validate_buy, validate_sell_to_flat
from .state import RuntimeState
from .strategy import SmaCrossStrategy


NY = ZoneInfo("America/New_York")


class ExecutionEngine:
    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: MarketDataClient,
        strategy: SmaCrossStrategy,
        state: RuntimeState,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.strategy = strategy
        self.state = state

    @staticmethod
    def _position_for_symbol(
        positions: list[dict[str, Any]],
        symbol: str,
    ) -> dict[str, Any] | None:
        for position in positions:
            if str(position.get("symbol", "")).upper() == symbol.upper():
                return position
        return None

    @staticmethod
    def _has_open_order(open_orders: list[dict[str, Any]], symbol: str) -> bool:
        return any(
            str(order.get("symbol", "")).upper() == symbol.upper()
            for order in open_orders
        )

    @staticmethod
    def _orders_today(orders: list[dict[str, Any]]) -> int:
        today = datetime.now(NY).date()
        count = 0
        for order in orders:
            submitted = order.get("submitted_at")
            if not submitted:
                continue
            try:
                stamp = datetime.fromisoformat(str(submitted).replace("Z", "+00:00"))
                if stamp.astimezone(NY).date() == today:
                    count += 1
            except ValueError:
                continue
        return count

    @staticmethod
    def _client_order_id(symbol: str, action: str) -> str:
        suffix = uuid4().hex[:20]
        return f"anevum-{symbol.lower()}-{action}-{suffix}"

    async def run_once(self) -> dict[str, Any]:
        self.state.mark_strategy()

        if self.state.paused:
            self.state.last_decision = "runtime paused"
            return {"action": "hold", "reason": "runtime paused"}

        symbol = self.settings.normalized_strategy_symbol
        if not symbol:
            self.state.last_decision = "STRATEGY_SYMBOL is empty"
            return {"action": "hold", "reason": "STRATEGY_SYMBOL is empty"}

        account = await self.client.account()
        cash = Decimal(str(account.get("cash", "0")))
        self.state.last_cash = str(cash)
        self.state.last_buying_power = str(account.get("buying_power", "0"))
        self.state.funding_ready = cash >= self.settings.min_ready_cash

        clock = await self.client.clock()
        if not bool(clock.get("is_open")):
            self.state.last_decision = "market is closed"
            return {"action": "hold", "reason": "market is closed"}

        positions = await self.client.positions()
        open_orders = await self.client.open_orders()
        recent_orders = await self.client.recent_orders(limit=100)
        position = self._position_for_symbol(positions, symbol)

        if self._has_open_order(open_orders, symbol):
            self.state.last_decision = "open order already exists for strategy symbol"
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "open order already exists for strategy symbol",
            }

        bars = await self.market_data.bars(symbol)
        signal = self.strategy.evaluate(
            bars=bars,
            symbol=symbol,
            has_position=position is not None and Decimal(str(position.get("qty", "0"))) > 0,
            order_notional=self.settings.order_notional,
        )
        self.state.last_signal = {
            "action": signal.action,
            "symbol": signal.symbol,
            "notional": str(signal.notional),
            "reason": signal.reason,
        }

        if signal.action == "hold":
            self.state.last_decision = signal.reason
            return self.state.last_signal

        if signal.action == "buy":
            risk = validate_buy(
                self.settings,
                symbol,
                signal.notional,
                account,
                positions,
                self._orders_today(recent_orders),
            )
            self.state.last_decision = risk.reason
            if not risk.allowed:
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": risk.reason,
                    "signal": self.state.last_signal,
                }

            order = await self.client.submit_market_buy(
                symbol=symbol,
                notional=str(signal.notional),
                client_order_id=self._client_order_id(symbol, "buy"),
            )
            self.state.last_order = {
                "id": order.get("id"),
                "client_order_id": order.get("client_order_id"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "status": order.get("status"),
                "submitted_at": order.get("submitted_at"),
            }
            self.state.last_decision = "buy order submitted"
            return {"action": "submitted", "order": self.state.last_order}

        if signal.action == "sell":
            risk = validate_sell_to_flat(
                self.settings,
                symbol,
                account,
                position,
            )
            self.state.last_decision = risk.reason
            if not risk.allowed:
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": risk.reason,
                    "signal": self.state.last_signal,
                }

            qty = str(position.get("qty"))
            order = await self.client.submit_market_sell(
                symbol=symbol,
                qty=qty,
                client_order_id=self._client_order_id(symbol, "sell"),
            )
            self.state.last_order = {
                "id": order.get("id"),
                "client_order_id": order.get("client_order_id"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "status": order.get("status"),
                "submitted_at": order.get("submitted_at"),
            }
            self.state.last_decision = "sell-to-flat order submitted"
            return {"action": "submitted", "order": self.state.last_order}

        self.state.last_decision = "unsupported signal"
        return {"action": "hold", "reason": "unsupported signal"}
