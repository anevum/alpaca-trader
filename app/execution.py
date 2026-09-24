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
from .strategy import OpeningRangeVwapStrategy, Signal


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
    def _entry_orders_today(orders: list[dict[str, Any]]) -> int:
        today = datetime.now(NY).date()
        count = 0
        for order in orders:
            if str(order.get("side", "")).lower() != "buy":
                continue
            client_order_id = str(order.get("client_order_id", ""))
            if not client_order_id.startswith("anevum-") or "-buy-" not in client_order_id:
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
    def _bot_bought_symbol_today(
        orders: list[dict[str, Any]],
        symbol: str,
    ) -> bool:
        today = datetime.now(NY).date()
        prefix = f"anevum-{symbol.lower()}-buy-"
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
                    return True
            except ValueError:
                continue
        return False

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

        remaining_orders = self._orders_for_symbol(
            await self.client.open_orders(),
            symbol,
        )
        if remaining_orders:
            self.state.last_decision = "waiting for protective orders to cancel"
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": self.state.last_decision,
            }

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
            "qty": order.get("qty"),
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

        account = await self.client.account()
        cash = Decimal(str(account.get("cash", "0")))
        equity = Decimal(str(account.get("equity", "0")))
        last_equity = Decimal(str(account.get("last_equity", "0")))
        self.state.last_cash = str(cash)
        self.state.last_buying_power = str(account.get("buying_power", "0"))
        self.state.last_equity = str(equity)
        self.state.last_equity_reference = str(last_equity)
        self.state.last_day_pnl = str(equity - last_equity)
        self.state.funding_ready = cash >= self.settings.min_ready_cash

        clock = await self.client.clock()
        if not bool(clock.get("is_open")):
            self.state.last_decision = "market is closed"
            return {"action": "hold", "reason": "market is closed"}

        positions = await self.client.positions()
        open_orders = await self.client.open_orders()
        recent_orders = await self.client.recent_orders(limit=100)
        now = datetime.now(NY)

        managed_positions = [
            position
            for position in positions
            if Decimal(str(position.get("qty", "0"))) > 0
            and str(position.get("symbol", "")).upper() in self.settings.allowed_symbols
            and self._bot_bought_symbol_today(
                recent_orders,
                str(position.get("symbol", "")).upper(),
            )
        ]

        if managed_positions:
            position = managed_positions[0]
            symbol = str(position.get("symbol", "")).upper()
            if now.time() >= self.settings.force_flat_time:
                return await self._force_flatten(
                    symbol=symbol,
                    account=account,
                    position=position,
                    open_orders=open_orders,
                )
            self.state.last_decision = (
                f"{symbol} position already open; bracket exits manage risk"
            )
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": self.state.last_decision,
            }

        if positions:
            self.state.last_decision = "another account position is already open"
            return {
                "action": "hold",
                "reason": self.state.last_decision,
            }

        if open_orders:
            self.state.last_decision = "an account order is already open"
            return {
                "action": "hold",
                "reason": self.state.last_decision,
            }

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

        self.state.last_scan = scan

        if not buy_signals:
            self.state.last_signal = {
                "action": "hold",
                "symbol": "",
                "reason": (
                    f"scanner watching {len(self.settings.scan_symbols)} symbols; "
                    "no qualified entries"
                ),
                "metadata": {},
            }
            self.state.last_decision = self.state.last_signal["reason"]
            return self.state.last_signal

        # SCAN_SYMBOLS order is the deterministic priority when multiple
        # candidates qualify on the same completed bar.
        signal = buy_signals[0]
        symbol = signal.symbol
        self.state.last_signal = self._signal_payload(signal)

        risk = validate_buy(
            self.settings,
            symbol,
            signal.notional,
            account,
            positions,
            self._entry_orders_today(recent_orders),
        )
        self.state.last_decision = risk.reason
        if not risk.allowed:
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": risk.reason,
                "signal": self.state.last_signal,
            }

        asset = await self.client.asset(symbol)
        if (
            str(asset.get("status", "")).lower() != "active"
            or not bool(asset.get("tradable"))
            or not bool(asset.get("fractionable"))
        ):
            self.state.last_decision = "asset is not active, tradable, and fractionable"
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": self.state.last_decision,
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
