from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .market_data import MarketDataClient
from .persistence import TradingEventSink
from .risk import validate_buy, validate_sell_to_flat
from .sizing import calculate_entry_notional, sizing_snapshot
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
        ledger: TradingEventSink | None = None,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.strategy = strategy
        self.state = state
        self.ledger = ledger

    @staticmethod
    def _position_for_symbol(
        positions: list[dict[str, Any]],
        symbol: str,
    ) -> dict[str, Any] | None:
        symbol = symbol.upper()
        for position in positions:
            if str(position.get("symbol", "")).upper() == symbol:
                return position
        return None

    @staticmethod
    def _orders_for_symbol(
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        return [
            order
            for order in open_orders
            if str(order.get("symbol", "")).upper() == symbol
        ]

    @staticmethod
    def _has_bot_exit_order(open_orders: list[dict[str, Any]], symbol: str) -> bool:
        prefix = f"anevum-{symbol.lower()}-"
        return any(
            str(order.get("side", "")).lower() == "sell"
            and str(order.get("client_order_id", "")).startswith(prefix)
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
            except ValueError:
                continue
            if stamp.astimezone(NY).date() == today:
                count += 1
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
            except ValueError:
                continue
            if stamp.astimezone(NY).date() == today:
                return True
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
            raw_stamp = order.get("filled_at") or order.get("submitted_at")
            if not raw_stamp:
                continue
            try:
                stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00")).astimezone(NY)
            except ValueError:
                continue
            if stamp.date() != today:
                continue
            if latest is None or stamp > latest:
                latest = stamp
        return latest

    @staticmethod
    def _latest_bot_exit_today(
        orders: list[dict[str, Any]],
        symbol: str,
    ) -> datetime | None:
        today = datetime.now(NY).date()
        prefix = f"anevum-{symbol.lower()}-"
        latest: datetime | None = None
        for order in orders:
            if str(order.get("side", "")).lower() != "sell":
                continue
            if not str(order.get("client_order_id", "")).startswith(prefix):
                continue
            raw_stamp = order.get("filled_at")
            if not raw_stamp:
                continue
            try:
                stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00")).astimezone(NY)
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

    @staticmethod
    def _timestamp(raw: Any) -> datetime | None:
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=NY)
        return stamp.astimezone(NY)

    def _latest_completed_bar(
        self,
        bars: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        latest_stamp: datetime | None = None
        for bar in bars:
            stamp = self._timestamp(bar.get("t"))
            if stamp is None:
                continue
            if stamp.date() != now.date():
                continue
            if stamp + timedelta(minutes=1) > now:
                continue
            if latest_stamp is None or stamp > latest_stamp:
                latest = bar
                latest_stamp = stamp
        return latest

    def _market_quality(
        self,
        signal: Signal,
        bars: list[dict[str, Any]],
        quote: dict[str, Any],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        now: datetime,
    ) -> tuple[bool, str, dict[str, Any]]:
        details: dict[str, Any] = {}

        latest = self._latest_completed_bar(bars, now)
        if latest is None:
            return False, "no recent completed bar", details

        bar_stamp = self._timestamp(latest.get("t"))
        if bar_stamp is None:
            return False, "latest bar timestamp is invalid", details

        completed_at = bar_stamp + timedelta(minutes=1)
        bar_age_seconds = max((now - completed_at).total_seconds(), 0)
        details["bar_age_seconds"] = round(bar_age_seconds, 3)
        if bar_age_seconds > self.settings.max_bar_age_seconds:
            return (
                False,
                f"latest completed bar is stale ({bar_age_seconds:.0f}s old)",
                details,
            )

        bid = Decimal(str(quote.get("bp", quote.get("bid_price", "0")) or "0"))
        ask = Decimal(str(quote.get("ap", quote.get("ask_price", "0")) or "0"))
        if bid <= 0 or ask <= 0 or ask < bid:
            return False, "latest quote is missing or invalid", details

        midpoint = (bid + ask) / Decimal("2")
        spread_pct = (ask - bid) / midpoint if midpoint > 0 else Decimal("1")
        details["bid"] = str(bid)
        details["ask"] = str(ask)
        details["spread_pct"] = str(spread_pct)
        if spread_pct > self.settings.max_spread_pct:
            return (
                False,
                f"spread {spread_pct:.4%} exceeds limit "
                f"{self.settings.max_spread_pct:.4%}",
                details,
            )

        quote_stamp = self._timestamp(quote.get("t", quote.get("timestamp")))
        if quote_stamp is not None:
            quote_age_seconds = max((now - quote_stamp).total_seconds(), 0)
            details["quote_age_seconds"] = round(quote_age_seconds, 3)
            if quote_age_seconds > self.settings.max_bar_age_seconds:
                return (
                    False,
                    f"latest quote is stale ({quote_age_seconds:.0f}s old)",
                    details,
                )

        fresh_confirmation_passes = 0
        confirmations = (signal.metadata or {}).get("confirmations") or {}
        for confirmation_symbol, payload in confirmations.items():
            if confirmation_symbol.upper() == signal.symbol.upper():
                continue
            if not bool((payload or {}).get("ok")):
                continue
            latest_confirmation = self._latest_completed_bar(
                confirmation_bars.get(confirmation_symbol, []),
                now,
            )
            if latest_confirmation is None:
                continue
            confirmation_stamp = self._timestamp(latest_confirmation.get("t"))
            if confirmation_stamp is None:
                continue
            age = max(
                (now - (confirmation_stamp + timedelta(minutes=1))).total_seconds(),
                0,
            )
            if age <= self.settings.max_bar_age_seconds:
                fresh_confirmation_passes += 1

        details["fresh_confirmation_passes"] = fresh_confirmation_passes
        if fresh_confirmation_passes < self.settings.min_confirmations:
            return False, "not enough fresh market confirmations passed", details

        return True, "market quality checks passed", details

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

    def _managed_positions(
        self,
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            position
            for position in positions
            if Decimal(str(position.get("qty", "0") or "0")) > 0
            and str(position.get("symbol", "")).upper() in self.settings.allowed_symbols
            and self._bot_bought_symbol_today(
                recent_orders,
                str(position.get("symbol", "")).upper(),
            )
        ]

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
        if self._has_bot_exit_order(open_orders, symbol):
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "bot exit order already open",
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
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "orders are still open; close deferred",
            }

        positions = await self.client.positions()
        position = self._position_for_symbol(positions, symbol)
        if not position:
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "position closed before exit submission",
            }

        risk = validate_sell_to_flat(self.settings, symbol, account, position)
        if not risk.allowed:
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": risk.reason,
            }

        client_order_id = self._client_order_id(symbol, action_tag)
        exit_refs: dict[str, str] | None = None
        if self.ledger is not None:
            try:
                exit_refs = self.ledger.persist_exit_intent(
                    symbol=symbol,
                    qty=str(position.get("qty")),
                    client_order_id=client_order_id,
                    exit_reason=exit_reason,
                    correlation_id=self.state.current_correlation_id,
                    intended_at=datetime.now(NY),
                )
            except Exception as exc:
                self.state.record_event(
                    kind="persistence",
                    symbol=symbol,
                    action="warning",
                    message="exit persistence failed open; protective sell continues",
                    reason=f"{type(exc).__name__}: {exc}",
                )

        try:
            order = await self.client.submit_market_sell(
                symbol=symbol,
                qty=str(position.get("qty")),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            recovered = None
            for attempt in range(3):
                try:
                    recovered = await self.client.order_by_client_order_id(
                        client_order_id
                    )
                except Exception:
                    recovered = None
                if recovered is not None:
                    break
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

            if recovered is None:
                self.state.reconciliation_safe = False
                self.state.last_reconciliation = {
                    "safe_to_enter": False,
                    "reason": "ambiguous protective exit submission",
                    "client_order_id": client_order_id,
                    "symbol": symbol,
                }
                self.state.record_event(
                    kind="execution",
                    symbol=symbol,
                    action="warning",
                    message=(
                        "protective exit outcome ambiguous; duplicate sell "
                        "suppressed pending reconciliation"
                    ),
                    reason=f"{type(exc).__name__}: {exc}",
                    payload={
                        "client_order_id": client_order_id,
                        "exit_reason": exit_reason,
                    },
                )
                return {
                    "action": "hold",
                    "symbol": symbol,
                    "reason": (
                        "protective exit submission outcome ambiguous; "
                        "reconciliation required before retry"
                    ),
                }

            order = recovered
            if self.ledger is not None:
                try:
                    persisted = await self.ledger.persist_recovered_order(
                        recovered,
                        intent_id=(exit_refs or {}).get("intent_id"),
                        exit_id=(exit_refs or {}).get("exit_id"),
                        exit_reason=exit_reason,
                        correlation_id=self.state.current_correlation_id,
                    )
                    if not persisted:
                        self.state.record_event(
                            kind="persistence",
                            symbol=symbol,
                            action="warning",
                            message=(
                                "protective exit recovered by client ID but "
                                "synchronous persistence failed"
                            ),
                            reason=exit_reason,
                        )
                except Exception as persist_exc:
                    self.state.record_event(
                        kind="persistence",
                        symbol=symbol,
                        action="warning",
                        message=(
                            "protective exit recovered by client ID but "
                            "persistence raised an exception"
                        ),
                        reason=f"{type(persist_exc).__name__}: {persist_exc}",
                    )

            self.state.record_event(
                kind="execution",
                symbol=symbol,
                action="recovered",
                message="ambiguous protective exit recovered by client order ID",
                reason=exit_reason,
                payload={"client_order_id": client_order_id},
            )

        if self.ledger is not None:
            try:
                self.ledger.record_broker_order(
                    order,
                    intent_id=(exit_refs or {}).get("intent_id"),
                    exit_id=(exit_refs or {}).get("exit_id"),
                    exit_reason=exit_reason,
                    correlation_id=self.state.current_correlation_id,
                )
            except Exception as exc:
                self.state.record_event(
                    kind="persistence",
                    symbol=symbol,
                    action="warning",
                    message="sell submitted but broker-order persistence failed",
                    reason=f"{type(exc).__name__}: {exc}",
                )
        order_payload = {
            "id": order.get("id"),
            "client_order_id": order.get("client_order_id"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "qty": order.get("qty"),
            "status": order.get("status"),
            "submitted_at": order.get("submitted_at"),
            "reason": exit_reason,
        }
        self.state.last_order = order_payload
        self.state.record_event(
            kind="execution",
            symbol=symbol,
            action="sell",
            message=f"{exit_reason} order submitted",
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "reason": f"{exit_reason} order submitted",
            "order": order_payload,
        }

    async def cancel_pending_bot_orders(self) -> dict[str, Any]:
        positions = await self.client.positions()
        if positions:
            return {
                "action": "blocked",
                "reason": (
                    "cannot cancel bot orders while positions are open; "
                    "close positions instead"
                ),
            }

        open_orders = await self.client.open_orders()
        bot_orders = [
            order
            for order in open_orders
            if str(order.get("client_order_id", "")).startswith("anevum-")
        ]
        await asyncio.gather(
            *[
                self.client.cancel_order(str(order.get("id")))
                for order in bot_orders
                if order.get("id")
            ]
        )

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

        managed_positions = self._managed_positions(positions, recent_orders)
        if not managed_positions:
            return {
                "action": "hold",
                "reason": "no bot-managed long position is open",
            }

        results = await asyncio.gather(
            *[
                self._force_flatten(
                    symbol=str(position.get("symbol", "")).upper(),
                    account=account,
                    position=position,
                    open_orders=open_orders,
                    exit_reason="manual close requested",
                    action_tag="manual",
                )
                for position in managed_positions
            ],
            return_exceptions=True,
        )

        normalized: list[dict[str, Any]] = []
        for position, result in zip(managed_positions, results):
            symbol = str(position.get("symbol", "")).upper()
            if isinstance(result, Exception):
                normalized.append(
                    {
                        "action": "error",
                        "symbol": symbol,
                        "reason": f"{type(result).__name__}: {result}",
                    }
                )
            else:
                normalized.append(result)

        submitted = sum(item.get("action") == "submitted" for item in normalized)
        return {
            "action": "submitted" if submitted else "hold",
            "reason": f"manual close submitted for {submitted} managed position(s)",
            "results": normalized,
        }

    async def _exit_managed_positions(
        self,
        account: dict[str, Any],
        managed_positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        exit_specs: list[tuple[dict[str, Any], str, str]] = []

        for position in managed_positions:
            symbol = str(position.get("symbol", "")).upper()
            if self._has_bot_exit_order(open_orders, symbol):
                continue

            if now.time() >= self.settings.force_flat_time:
                exit_specs.append(
                    (position, "eod", "forced end-of-day flatten")
                )
                continue

            price_exit = self._managed_price_exit(
                position,
                self.settings.stop_pct,
                self.settings.target_pct,
            )
            if price_exit is not None:
                action_tag, exit_reason = price_exit
                exit_specs.append((position, action_tag, exit_reason))
                continue

            if self.settings.max_hold_minutes > 0:
                entry_time = self._latest_bot_buy_today(recent_orders, symbol)
                if entry_time is not None:
                    held_minutes = (now - entry_time).total_seconds() / 60
                    if held_minutes >= self.settings.max_hold_minutes:
                        exit_specs.append(
                            (
                                position,
                                "time",
                                f"max hold {self.settings.max_hold_minutes} minutes",
                            )
                        )

        if not exit_specs:
            return []

        results = await asyncio.gather(
            *[
                self._force_flatten(
                    symbol=str(position.get("symbol", "")).upper(),
                    account=account,
                    position=position,
                    open_orders=open_orders,
                    exit_reason=reason,
                    action_tag=tag,
                )
                for position, tag, reason in exit_specs
            ],
            return_exceptions=True,
        )

        normalized: list[dict[str, Any]] = []
        for (position, _tag, reason), result in zip(exit_specs, results):
            symbol = str(position.get("symbol", "")).upper()
            if isinstance(result, Exception):
                normalized.append(
                    {
                        "action": "error",
                        "symbol": symbol,
                        "reason": f"{type(result).__name__}: {result}",
                    }
                )
                self.state.record_event(
                    kind="execution",
                    symbol=symbol,
                    action="error",
                    message=f"exit failed: {reason}",
                    reason=str(result),
                )
            else:
                normalized.append(result)
        return normalized

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

        managed_positions = self._managed_positions(positions, recent_orders)
        exit_results = await self._exit_managed_positions(
            account,
            managed_positions,
            open_orders,
            recent_orders,
            now,
        )
        if exit_results:
            submitted = sum(
                item.get("action") == "submitted"
                for item in exit_results
            )
            self.state.last_decision = (
                f"managed {len(exit_results)} exit condition(s); "
                f"submitted {submitted} sell order(s)"
            )
            return {
                "action": "submitted" if submitted else "hold",
                "reason": self.state.last_decision,
                "results": exit_results,
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
            has_position = self._position_for_symbol(positions, symbol) is not None
            signal = self.strategy.evaluate(
                bars=market_bars.get(symbol, []),
                confirmation_bars={
                    confirmation_symbol: market_bars.get(confirmation_symbol, [])
                    for confirmation_symbol in self.settings.confirmation_symbols
                },
                symbol=symbol,
                has_position=has_position,
                order_notional=self.settings.order_notional,
                now=now,
            )
            scan[symbol] = self._signal_payload(signal)
            if signal.action == "buy":
                buy_signals.append(signal)

        if buy_signals:
            latest_quotes = await self.market_data.latest_quotes_many(
                [signal.symbol for signal in buy_signals]
            )
            quality_signals: list[Signal] = []
            confirmation_bars = {
                confirmation_symbol: market_bars.get(confirmation_symbol, [])
                for confirmation_symbol in self.settings.confirmation_symbols
            }
            for signal in buy_signals:
                allowed, reason, quality = self._market_quality(
                    signal,
                    market_bars.get(signal.symbol, []),
                    latest_quotes.get(signal.symbol, {}),
                    confirmation_bars,
                    now,
                )
                signal.metadata = dict(signal.metadata or {})
                signal.metadata["market_quality"] = quality
                if allowed:
                    quality_signals.append(signal)
                    scan[signal.symbol] = self._signal_payload(signal)
                else:
                    payload = self._signal_payload(signal)
                    payload["action"] = "hold"
                    payload["reason"] = reason
                    scan[signal.symbol] = payload
            buy_signals = quality_signals

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
                "open_positions": [
                    str(position.get("symbol", "")).upper()
                    for position in positions
                    if Decimal(str(position.get("qty", "0") or "0")) > 0
                ],
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

        ranked = sorted(
            buy_signals,
            key=self._signal_rank,
            reverse=True,
        )
        self.state.last_signal = self._signal_payload(ranked[0])

        if not self.state.startup_reconciled:
            self.state.last_decision = (
                "qualified entries blocked until startup reconciliation completes"
            )
            return {
                "action": "blocked",
                "reason": self.state.last_decision,
                "qualified_symbols": [signal.symbol for signal in ranked],
            }

        if not self.state.reconciliation_safe:
            self.state.last_decision = (
                "qualified entries blocked by broker/canonical reconciliation"
            )
            return {
                "action": "blocked",
                "reason": self.state.last_decision,
                "qualified_symbols": [signal.symbol for signal in ranked],
                "reconciliation": self.state.last_reconciliation,
            }

        if not self.state.entries_enabled:
            self.state.last_decision = (
                "qualified entries blocked because new entries are disabled"
            )
            return {
                "action": "blocked",
                "reason": self.state.last_decision,
                "qualified_symbols": [signal.symbol for signal in ranked],
            }

        open_order_symbols = {
            str(order.get("symbol", "")).upper()
            for order in open_orders
        }
        entry_count = self._entry_orders_today(recent_orders)
        cycle_limit = min(
            self.settings.max_new_entries_per_cycle,
            self.settings.max_concurrent_positions,
        )

        planned: list[tuple[Signal, Decimal]] = []
        skipped: list[dict[str, str]] = []
        simulated_positions = list(positions)
        simulated_account = dict(account)
        simulated_cash = Decimal(str(account.get("cash", "0")))

        for signal in ranked:
            if len(planned) >= cycle_limit:
                break

            symbol = signal.symbol.upper()
            if symbol in open_order_symbols:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "open order already exists for symbol",
                    }
                )
                continue

            if self.settings.reentry_cooldown_minutes > 0:
                latest_exit = self._latest_bot_exit_today(recent_orders, symbol)
                if latest_exit is not None:
                    minutes_since_exit = (now - latest_exit).total_seconds() / 60
                    if minutes_since_exit < self.settings.reentry_cooldown_minutes:
                        skipped.append(
                            {
                                "symbol": symbol,
                                "reason": (
                                    "same-symbol cooldown active "
                                    f"({minutes_since_exit:.1f}/"
                                    f"{self.settings.reentry_cooldown_minutes} min)"
                                ),
                            }
                        )
                        continue

            simulated_account["cash"] = str(simulated_cash)
            entry_notional = calculate_entry_notional(
                self.settings,
                simulated_account,
                simulated_positions,
            )
            if entry_notional <= 0:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "capital allocator produced no eligible notional",
                    }
                )
                continue

            signal.notional = entry_notional
            signal.metadata = dict(signal.metadata or {})
            signal.metadata["sizing"] = sizing_snapshot(
                self.settings,
                simulated_account,
                simulated_positions,
            )

            risk = validate_buy(
                self.settings,
                symbol,
                signal.notional,
                simulated_account,
                simulated_positions,
                entry_count + len(planned),
            )
            if not risk.allowed:
                skipped.append({"symbol": symbol, "reason": risk.reason})
                continue

            asset = await self.client.asset(symbol)
            if (
                str(asset.get("status", "")).lower() != "active"
                or not bool(asset.get("tradable"))
                or not bool(asset.get("fractionable"))
            ):
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "asset is not active, tradable, and fractionable",
                    }
                )
                continue

            qty = self._fractional_qty(signal.notional, signal.reference_price)
            if qty <= 0:
                skipped.append(
                    {"symbol": symbol, "reason": "calculated quantity is zero"}
                )
                continue

            planned.append((signal, qty))
            if len(planned) == 1:
                self.state.last_signal = self._signal_payload(signal)
            simulated_cash -= signal.notional
            simulated_positions.append(
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "market_value": str(signal.notional),
                }
            )

        if not planned:
            reason = skipped[0]["reason"] if skipped else "no eligible candidates"
            self.state.last_decision = (
                f"qualified signals found but none eligible: {reason}"
            )
            return {
                "action": "hold",
                "reason": self.state.last_decision,
                "skipped": skipped,
            }

        prepared: list[tuple[Signal, Decimal, str, dict[str, str] | None]] = []
        errors: list[dict[str, str]] = []
        for signal, qty in planned:
            client_order_id = self._client_order_id(signal.symbol, "buy")
            ledger_refs: dict[str, str] | None = None
            if self.ledger is not None:
                try:
                    ledger_refs = await self.ledger.persist_entry_intent(
                        signal=signal,
                        qty=str(qty),
                        client_order_id=client_order_id,
                        correlation_id=self.state.current_correlation_id,
                        intended_at=now,
                    )
                except Exception as exc:
                    ledger_refs = None
                    self.state.record_event(
                        kind="persistence",
                        symbol=signal.symbol.upper(),
                        action="error",
                        message="entry intent persistence raised an exception",
                        reason=f"{type(exc).__name__}: {exc}",
                        at=now,
                    )
                if ledger_refs is None:
                    errors.append(
                        {
                            "symbol": signal.symbol.upper(),
                            "reason": "durable entry intent persistence unavailable",
                        }
                    )
                    self.state.record_event(
                        kind="persistence",
                        symbol=signal.symbol.upper(),
                        action="blocked",
                        message="entry blocked because durable intent was not acknowledged",
                        at=now,
                    )
                    continue
            prepared.append((signal, qty, client_order_id, ledger_refs))

        if not prepared:
            self.state.last_decision = "all planned entries blocked before broker submission"
            return {
                "action": "blocked",
                "reason": self.state.last_decision,
                "errors": errors,
                "skipped": skipped,
            }

        order_results = await asyncio.gather(
            *[
                self.client.submit_market_buy(
                    symbol=signal.symbol,
                    qty=str(qty),
                    client_order_id=client_order_id,
                )
                for signal, qty, client_order_id, _ledger_refs in prepared
            ],
            return_exceptions=True,
        )

        submitted_orders: list[dict[str, Any]] = []
        for (signal, _qty, _client_order_id, ledger_refs), result in zip(
            prepared,
            order_results,
        ):
            symbol = signal.symbol.upper()
            if isinstance(result, Exception):
                recovered = None
                for attempt in range(3):
                    try:
                        recovered = await self.client.order_by_client_order_id(
                            _client_order_id
                        )
                    except Exception:
                        recovered = None
                    if recovered is not None:
                        break
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))

                if recovered is None:
                    self.state.reconciliation_safe = False
                    self.state.last_reconciliation = {
                        "safe_to_enter": False,
                        "reason": "ambiguous broker submission",
                        "client_order_id": _client_order_id,
                        "symbol": symbol,
                    }
                    errors.append(
                        {
                            "symbol": symbol,
                            "reason": (
                                f"{type(result).__name__}: {result}; "
                                "broker submission remains ambiguous"
                            ),
                        }
                    )
                    self.state.record_event(
                        kind="execution",
                        symbol=symbol,
                        action="blocked",
                        message=(
                            "entry submission result ambiguous; "
                            "future entries blocked pending reconciliation"
                        ),
                        reason=str(result),
                        at=now,
                        payload={"client_order_id": _client_order_id},
                    )
                    continue

                result = recovered
                if self.ledger is not None:
                    persisted = await self.ledger.persist_recovered_order(
                        recovered,
                        correlation_id=self.state.current_correlation_id,
                    )
                    if not persisted:
                        self.state.reconciliation_safe = False
                        errors.append(
                            {
                                "symbol": symbol,
                                "reason": (
                                    "broker order recovered by client ID but "
                                    "durable persistence failed"
                                ),
                            }
                        )
                        continue
                self.state.record_event(
                    kind="execution",
                    symbol=symbol,
                    action="recovered",
                    message="ambiguous entry recovered by client order ID",
                    at=now,
                    payload={"client_order_id": _client_order_id},
                )

            order_payload = {
                "id": result.get("id"),
                "client_order_id": result.get("client_order_id"),
                "symbol": result.get("symbol"),
                "side": result.get("side"),
                "qty": result.get("qty"),
                "status": result.get("status"),
                "order_class": result.get("order_class"),
                "submitted_at": result.get("submitted_at"),
                "stop_price": str(signal.stop_price),
                "take_profit_price": str(signal.take_profit_price),
                "exit_management": "bot",
            }
            submitted_orders.append(order_payload)
            if self.ledger is not None:
                try:
                    self.ledger.record_broker_order(
                        result,
                        intent_id=(ledger_refs or {}).get("intent_id"),
                        position_id=(ledger_refs or {}).get("position_id"),
                        correlation_id=self.state.current_correlation_id,
                    )
                except Exception as exc:
                    self.state.record_event(
                        kind="persistence",
                        symbol=symbol,
                        action="warning",
                        message="buy submitted but broker-order persistence failed",
                        reason=f"{type(exc).__name__}: {exc}",
                        at=now,
                    )
            self.state.last_order = order_payload
            self.state.record_event(
                kind="execution",
                symbol=symbol,
                action="buy",
                message=(
                    "fractional-compatible market entry submitted; "
                    "bot-managed stop/target/time exits active"
                ),
                at=now,
            )

        if submitted_orders:
            symbols_text = ",".join(
                str(order.get("symbol") or "")
                for order in submitted_orders
            )
            self.state.last_decision = (
                f"submitted {len(submitted_orders)} concurrent entry order(s): "
                f"{symbols_text}"
            )
            return {
                "action": "submitted",
                "symbol": symbols_text,
                "reason": self.state.last_decision,
                "orders": submitted_orders,
                "skipped": skipped,
                "errors": errors,
            }

        self.state.last_decision = "all planned entry submissions failed"
        return {
            "action": "error",
            "reason": self.state.last_decision,
            "errors": errors,
            "skipped": skipped,
        }
