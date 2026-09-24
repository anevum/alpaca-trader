from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .market_data import MarketDataClient
from .risk import validate_buy, validate_sell_to_flat
from .state import RuntimeState
from .strategy import OpeningRangeVwapStrategy


NY = ZoneInfo("America/New_York")


class ExecutionEngine:
    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: MarketDataClient,
        strategy: OpeningRangeVwapStrategy,
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
    def _orders_for_symbol(
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> list[dict[str, Any]]:
        return [
            order
            for order in open_orders
            if str(order.get("symbol", "")).upper() == symbol.upper()
        ]

    @staticmethod
    def _has_eod_exit_order(open_orders: list[dict[str, Any]], symbol: str) -> bool:
        prefix = f"anevum-{symbol.lower()}-eod-"
        return any(
            str(order.get("client_order_id", "")).startswith(prefix)
            for order in open_orders
        )

    @staticmethod
    def _entry_orders_today(orders: list[dict[str, Any]], symbol: str) -> int:
        today = datetime.now(NY).date()
        prefix = f"anevum-{symbol.lower()}-buy-"
        count = 0
        for order in orders:
            if str(order.get("side", "")).lower() != "buy":
                continue
            if not str(order.get("client_order_id", "")).startswith(prefix):
                continue
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

    @staticmethod
    def _fractional_qty(notional: Decimal, reference_price: Decimal) -> Decimal:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        return (notional / reference_price).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )

    async def _force_flatten(
        self,
        symbol: str,
        account: dict[str, Any],
        position: dict[str, Any],
        open_orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._has_eod_exit_order(open_orders, symbol):
            self.state.last_decision = "end-of-day exit order already open"
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "end-of-day exit order already open",
            }

        for order in self._orders_for_symbol(open_orders, symbol):
            order_id = order.get("id")
            if order_id:
                await self.client.cancel_order(str(order_id))

        positions = await self.client.positions()
        position = self._position_for_symbol(positions, symbol)
        if not position:
            self.state.last_decision = "position closed while canceling protective orders"
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": self.state.last_decision,
            }

        risk = validate_sell_to_flat(self.settings, symbol, account, position)
        self.state.last_decision = risk.reason
        if not risk.allowed:
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": risk.reason,
            }

        order = await self.client.submit_market_sell(
            symbol=symbol,
            qty=str(position.get("qty")),
            client_order_id=self._client_order_id(symbol, "eod"),
        )
        self.state.last_order = {
            "id": order.get("id"),
            "client_order_id": order.get("client_order_id"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "status": order.get("status"),
            "submitted_at": order.get("submitted_at"),
            "reason": "forced end-of-day flatten",
        }
        self.state.last_decision = "end-of-day flatten order submitted"
        return {"action": "submitted", "order": self.state.last_order}

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
        now = datetime.now(NY)

        if position and now.time() >= self.settings.force_flat_time:
            return await self._force_flatten(
                symbol=symbol,
                account=account,
                position=position,
                open_orders=open_orders,
            )

        if self._orders_for_symbol(open_orders, symbol):
            self.state.last_decision = "open order already exists for strategy symbol"
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "open order already exists for strategy symbol",
            }

        symbols = [symbol, *self.settings.confirmation_symbols]
        market_bars = await self.market_data.bars_many(symbols)
        signal = self.strategy.evaluate(
            bars=market_bars[symbol],
            confirmation_bars={
                confirmation_symbol: market_bars[confirmation_symbol]
                for confirmation_symbol in self.settings.confirmation_symbols
            },
            symbol=symbol,
            has_position=position is not None
            and Decimal(str(position.get("qty", "0"))) > 0,
            order_notional=self.settings.order_notional,
            now=now,
        )
        self.state.last_signal = {
            "action": signal.action,
            "symbol": signal.symbol,
            "notional": str(signal.notional),
            "reference_price": str(signal.reference_price),
            "stop_price": str(signal.stop_price),
            "take_profit_price": str(signal.take_profit_price),
            "reason": signal.reason,
            "metadata": signal.metadata,
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
                self._entry_orders_today(recent_orders, symbol),
            )
            self.state.last_decision = risk.reason
            if not risk.allowed:
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": risk.reason,
                    "signal": self.state.last_signal,
                }

            qty = self._fractional_qty(signal.notional, signal.reference_price)
            if qty <= 0:
                self.state.last_decision = "calculated quantity is zero"
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": self.state.last_decision,
                    "signal": self.state.last_signal,
                }

            order = await self.client.submit_bracket_market_buy(
                symbol=symbol,
                qty=str(qty),
                take_profit_price=str(signal.take_profit_price),
                stop_price=str(signal.stop_price),
                client_order_id=self._client_order_id(symbol, "buy"),
            )
            self.state.last_order = {
                "id": order.get("id"),
                "client_order_id": order.get("client_order_id"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "qty": order.get("qty"),
                "status": order.get("status"),
                "order_class": order.get("order_class"),
                "submitted_at": order.get("submitted_at"),
                "stop_price": str(signal.stop_price),
                "take_profit_price": str(signal.take_profit_price),
            }
            self.state.last_decision = "bracket entry submitted"
            return {"action": "submitted", "order": self.state.last_order}

        self.state.last_decision = "unsupported signal"
        return {"action": "hold", "reason": "unsupported signal"}
