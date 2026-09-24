from __future__ import annotations

import asyncio
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
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy, Signal


NY = ZoneInfo("America/New_York")


class ExecutionEngine:
    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: MarketDataClient,
        strategy: OpeningRangeVwapStrategy | RollingMomentumVwapStrategy,
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
    def _latest_bot_buy_today(
        orders: list[dict[str, Any]],
        symbol: str,
    ) -> datetime | None:
        today = datetime.now(NY).date()
        prefix = f"anevum-{symbol.lower()}-buy-"
        latest: datetime | None = None
        for order in orders:
            if str(order.get("side", "")).lower() != "buy":
                continue
            if not str(order.get("client_order_id", "")).startswith(prefix):
                continue
            submitted = order.get("submitted_at")
            if not submitted:
                continue
            try:
                stamp = datetime.fromisoformat(str(submitted).replace("Z", "+00:00")).astimezone(NY)
            except ValueError:
                continue
            if stamp.date() != today:
                continue
            if latest is None or stamp > latest:
                latest = stamp
        return latest

    @staticmethod
    def _client_order_id(symbol: str, action: str) -> str:
        suffix = uuid4().hex[:20]
        return f"anevum-{symbol.lower()}-{action}-{suffix}"

    @staticmethod
    def _spread_pct(quote: dict[str, Any]) -> Decimal | None:
        bid = Decimal(str(quote.get("bp", "0") or "0"))
        ask = Decimal(str(quote.get("ap", "0") or "0"))
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        midpoint = (bid + ask) / Decimal("2")
        if midpoint <= 0:
            return None
        return (ask - bid) / midpoint

    @staticmethod
    def _fractional_qty(notional: Decimal, reference_price: Decimal) -> Decimal:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        return (notional / reference_price).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )

    @staticmethod
    def _managed_price_exit(
        position: dict[str, Any],
        stop_pct: Decimal,
        target_pct: Decimal,
    ) -> tuple[str, str] | None:
        entry_price = Decimal(str(position.get("avg_entry_price", "0") or "0"))
        current_price = Decimal(str(position.get("current_price", "0") or "0"))
        if entry_price <= 0 or current_price <= 0:
            return None

        stop_price = entry_price * (Decimal("1") - stop_pct)
        target_price = entry_price * (Decimal("1") + target_pct)

        if current_price <= stop_price:
            return (
                "stop",
                f"bot-managed stop loss triggered at {current_price} "
                f"(entry {entry_price})",
            )
        if current_price >= target_price:
            return (
                "target",
                f"bot-managed take profit triggered at {current_price} "
                f"(entry {entry_price})",
            )
        return None

    @staticmethod
    def _signal_rank(signal: Signal) -> tuple[Decimal, Decimal, int]:
        metadata = signal.metadata or {}
        return (
            Decimal(str(metadata.get("momentum_pct", "0"))),
            Decimal(str(metadata.get("vwap_edge_pct", "0"))),
            int(metadata.get("confirmation_passes", 0) or 0),
        )

    def _same_symbol_lockout_minutes(self) -> int:
        if self.settings.reentry_cooldown_minutes <= 0:
            return 0
        return (
            self.settings.max_hold_minutes
            + self.settings.reentry_cooldown_minutes
            if self.settings.max_hold_minutes > 0
            else self.settings.reentry_cooldown_minutes
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
        *,
        exit_reason: str = "forced end-of-day flatten",
        action_tag: str = "eod",
    ) -> dict[str, Any]:
        if action_tag == "eod" and self._has_eod_exit_order(open_orders, symbol):
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

        remaining_orders: list[dict[str, Any]] = []
        for _ in range(6):
            remaining_orders = self._orders_for_symbol(
                await self.client.open_orders(),
                symbol,
            )
            if not remaining_orders:
                break
            await asyncio.sleep(0.25)

        if remaining_orders:
            self.state.last_decision = "protective orders are still open; close deferred"
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
            client_order_id=self._client_order_id(symbol, action_tag),
        )
        self.state.last_order = {
            "id": order.get("id"),
            "client_order_id": order.get("client_order_id"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "qty": order.get("qty"),
            "status": order.get("status"),
            "submitted_at": order.get("submitted_at"),
            "reason": exit_reason,
        }
        self.state.last_decision = f"{exit_reason} order submitted"
        return {"action": "submitted", "order": self.state.last_order}

    async def cancel_pending_bot_orders(self) -> dict[str, Any]:
        positions = await self.client.positions()
        if positions:
            return {
                "action": "blocked",
                "reason": "cannot cancel protective orders while a position is open; close the position instead",
            }

        open_orders = await self.client.open_orders()
        bot_orders = [
            order
            for order in open_orders
            if str(order.get("client_order_id", "")).startswith("anevum-")
        ]
        for order in bot_orders:
            order_id = order.get("id")
            if order_id:
                await self.client.cancel_order(str(order_id))

        self.state.record_event(
            kind="control",
            action="cancel_orders",
            message=f"cancel requested for {len(bot_orders)} ANEVUM open orders",
        )
        return {
            "action": "cancel_requested",
            "count": len(bot_orders),
        }

    async def close_managed_position(self) -> dict[str, Any]:
        account = await self.client.account()
        positions = await self.client.positions()
        recent_orders = await self.client.recent_orders(limit=100)
        open_orders = await self.client.open_orders()

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
        if not managed_positions:
            return {
                "action": "hold",
                "reason": "no bot-managed long position is open",
            }

        position = managed_positions[0]
        symbol = str(position.get("symbol", "")).upper()
        result = await self._force_flatten(
            symbol=symbol,
            account=account,
            position=position,
            open_orders=open_orders,
        )
        self.state.record_event(
            kind="control",
            symbol=symbol,
            action=str(result.get("action") or ""),
            message=str(result.get("reason") or "manual close requested"),
        )
        return result

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

            price_exit = self._managed_price_exit(
                position,
                self.settings.stop_pct,
                self.settings.target_pct,
            )
            if price_exit is not None:
                action_tag, exit_reason = price_exit
                return await self._force_flatten(
                    symbol=symbol,
                    account=account,
                    position=position,
                    open_orders=open_orders,
                    exit_reason=exit_reason,
                    action_tag=action_tag,
                )

            if self.settings.max_hold_minutes > 0:
                entry_time = self._latest_bot_buy_today(recent_orders, symbol)
                if entry_time is not None:
                    held_minutes = (now - entry_time).total_seconds() / 60
                    if held_minutes >= self.settings.max_hold_minutes:
                        return await self._force_flatten(
                            symbol=symbol,
                            account=account,
                            position=position,
                            open_orders=open_orders,
                            exit_reason=f"max hold {self.settings.max_hold_minutes} minutes",
                            action_tag="time",
                        )

            self.state.last_decision = (
                f"{symbol} position already open; bot-managed stop/target/time exits active"
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

        self.state.record_scan(scan, at=now)
        hold_reasons: dict[str, int] = {}
        for payload in scan.values():
            if payload.get("action") == "buy":
                continue
            reason = str(payload.get("reason") or "unknown")
            hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
        print(
            "LIVE_SCAN_CYCLE",
            {
                "at": now.isoformat(),
                "symbols": len(self.settings.scan_symbols),
                "ready": [signal.symbol for signal in buy_signals],
                "hold_reasons": hold_reasons,
            },
            flush=True,
        )

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

        # When multiple candidates qualify, prefer the strongest rolling
        # momentum/VWAP setup. Equal scores preserve SCAN_SYMBOLS order.
        signal = max(buy_signals, key=self._signal_rank)
        symbol = signal.symbol
        self.state.last_signal = self._signal_payload(signal)

        if not self.state.entries_enabled:
            self.state.last_decision = "qualified entry blocked because new entries are disabled"
            self.state.record_event(
                kind="control",
                symbol=symbol,
                action="blocked",
                message=self.state.last_decision,
                at=now,
            )
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": self.state.last_decision,
                "signal": self.state.last_signal,
            }

        lockout_minutes = self._same_symbol_lockout_minutes()
        if lockout_minutes > 0:
            latest_entry = self._latest_bot_buy_today(recent_orders, symbol)
            if latest_entry is not None:
                minutes_since_entry = (now - latest_entry).total_seconds() / 60
                if minutes_since_entry < lockout_minutes:
                    self.state.last_decision = (
                        f"{symbol} same-symbol lockout active "
                        f"({lockout_minutes} minutes from prior entry)"
                    )
                    return {
                        "action": "hold",
                        "symbol": symbol,
                        "reason": self.state.last_decision,
                        "signal": self.state.last_signal,
                    }

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

        if self.settings.max_spread_pct > 0:
            quote = await self.market_data.latest_quote(symbol)
            spread_pct = self._spread_pct(quote)
            if spread_pct is None:
                self.state.last_decision = "entry blocked because a valid bid/ask quote is unavailable"
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": self.state.last_decision,
                    "signal": self.state.last_signal,
                }
            if spread_pct > self.settings.max_spread_pct:
                self.state.last_decision = (
                    f"entry blocked because spread {spread_pct:.6f} exceeds "
                    f"limit {self.settings.max_spread_pct:.6f}"
                )
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": self.state.last_decision,
                    "signal": self.state.last_signal,
                }
            self.state.last_signal.setdefault("metadata", {})["execution_quote"] = {
                "bid": str(quote.get("bp")),
                "ask": str(quote.get("ap")),
                "spread_pct": str(spread_pct),
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

        order = await self.client.submit_market_buy(
            symbol=symbol,
            qty=str(qty),
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
            "exit_management": "bot",
        }
        self.state.last_decision = (
            "fractional-compatible market entry submitted; "
            "bot-managed stop/target/time exits active"
        )
        return {"action": "submitted", "order": self.state.last_order}
