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
from .opportunity import correlation_checks, score_opportunity
from .risk import validate_buy, validate_sell_to_flat
from .sizing import calculate_entry_notional, sizing_snapshot
from .state import RuntimeState
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy, Signal
from .universe import DynamicUniverse


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
        universe: DynamicUniverse | None = None,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.strategy = strategy
        self.state = state
        self.ledger = ledger
        self.universe = universe

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
    def _is_standing_protective_stop(order: dict[str, Any]) -> bool:
        return "-hardstop-" in str(order.get("client_order_id", ""))

    @classmethod
    def _protective_stop_for_symbol(
        cls,
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> dict[str, Any] | None:
        prefix = f"anevum-{symbol.lower()}-"
        for order in open_orders:
            if (
                str(order.get("side", "")).lower() == "sell"
                and str(order.get("client_order_id", "")).startswith(prefix)
                and cls._is_standing_protective_stop(order)
            ):
                return order
        return None

    @classmethod
    def _has_bot_exit_order(
        cls,
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> bool:
        prefix = f"anevum-{symbol.lower()}-"
        return any(
            str(order.get("side", "")).lower() == "sell"
            and str(order.get("client_order_id", "")).startswith(prefix)
            and not cls._is_standing_protective_stop(order)
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

    def _client_order_id(self, symbol: str, action: str) -> str:
        suffix = uuid4().hex[:12]
        return (
            f"anevum-{symbol.lower()}-{action}-"
            f"{self.settings.order_owner_tag}-{suffix}"
        )

    def _loss_streak_gate(
        self,
        orders: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[bool, str, dict[str, Any]]:
        limit = self.settings.loss_streak_limit
        cooldown = self.settings.loss_streak_cooldown_minutes
        if limit <= 0 or cooldown <= 0:
            return True, "", {"streak": 0, "cooldown_minutes": cooldown}

        def filled_stamp(order: dict[str, Any]) -> datetime | None:
            raw = order.get("filled_at")
            if not raw:
                return None
            try:
                return datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00")
                ).astimezone(NY)
            except ValueError:
                return None

        buy_fills: dict[str, list[tuple[datetime, Decimal]]] = {}
        for order in orders:
            if str(order.get("side", "")).lower() != "buy":
                continue
            client_order_id = str(order.get("client_order_id", ""))
            if not client_order_id.startswith("anevum-"):
                continue
            stamp = filled_stamp(order)
            price = Decimal(str(order.get("filled_avg_price") or "0"))
            if stamp is None or price <= 0 or stamp.date() != now.date():
                continue
            symbol = str(order.get("symbol", "")).upper()
            buy_fills.setdefault(symbol, []).append((stamp, price))

        for fills in buy_fills.values():
            fills.sort(key=lambda item: item[0])

        exits: list[tuple[datetime, str, bool]] = []
        for order in orders:
            if str(order.get("side", "")).lower() != "sell":
                continue
            client_order_id = str(order.get("client_order_id", ""))
            if not client_order_id.startswith("anevum-"):
                continue
            status = str(order.get("status", "")).lower()
            filled_qty = Decimal(str(order.get("filled_qty") or "0"))
            if not order.get("filled_at") and not (
                status == "filled" and filled_qty > 0
            ):
                continue
            stamp = filled_stamp(order)
            if stamp is None or stamp.date() != now.date():
                continue

            stop_like = (
                "-stop-" in client_order_id
                or "-hardstop-" in client_order_id
            )
            is_loss = stop_like
            if stop_like:
                symbol = str(order.get("symbol", "")).upper()
                exit_price = Decimal(
                    str(order.get("filled_avg_price") or "0")
                )
                prior_buys = [
                    item for item in buy_fills.get(symbol, [])
                    if item[0] <= stamp
                ]
                if exit_price > 0 and prior_buys:
                    entry_price = prior_buys[-1][1]
                    is_loss = exit_price < entry_price

            exits.append((stamp, client_order_id, is_loss))

        exits.sort(key=lambda item: item[0], reverse=True)
        streak = 0
        latest_stop: datetime | None = None
        for stamp, client_order_id, is_loss in exits:
            stop_like = (
                "-stop-" in client_order_id
                or "-hardstop-" in client_order_id
            )
            if not stop_like or not is_loss:
                break
            streak += 1
            if latest_stop is None:
                latest_stop = stamp

        detail = {
            "streak": streak,
            "limit": limit,
            "cooldown_minutes": cooldown,
            "latest_stop_at": latest_stop.isoformat() if latest_stop else None,
        }
        if streak < limit or latest_stop is None:
            return True, "", detail

        minutes_since_stop = (now - latest_stop).total_seconds() / 60
        detail["minutes_since_stop"] = minutes_since_stop
        if minutes_since_stop >= cooldown:
            return True, "", detail

        return (
            False,
            (
                f"loss-streak cooldown active after {streak} consecutive stop exits "
                f"({minutes_since_stop:.1f}/{cooldown} min)"
            ),
            detail,
        )

    @staticmethod
    def _fractional_qty(notional: Decimal, reference_price: Decimal) -> Decimal:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        return (notional / reference_price).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )

    @staticmethod
    def _price_for_order(value: Decimal) -> Decimal:
        increment = Decimal("0.0001") if value < Decimal("1") else Decimal("0.01")
        return value.quantize(increment, rounding=ROUND_DOWN)

    def _prune_exit_states(self, positions: list[dict[str, Any]]) -> None:
        active = {
            str(position.get("symbol", "")).upper()
            for position in positions
            if Decimal(str(position.get("qty", "0") or "0")) > 0
        }
        for symbol in list(self.state.exit_states):
            if symbol not in active:
                del self.state.exit_states[symbol]

    def _exit_state_for_position(
        self,
        position: dict[str, Any],
        *,
        bars: list[dict[str, Any]] | None = None,
        entry_time: datetime | None = None,
    ) -> dict[str, Any]:
        symbol = str(position.get("symbol", "")).upper()
        entry_price = Decimal(str(position.get("avg_entry_price", "0") or "0"))
        current_price = Decimal(str(position.get("current_price", "0") or "0"))
        state = self.state.exit_states.setdefault(
            symbol,
            {
                "symbol": symbol,
                "risk_stop_pct": str(self.settings.stop_pct),
                "peak_return_pct": "0",
                "trough_return_pct": "0",
                "protected_floor_pct": None,
                "profit_protection_active": False,
                "thesis_failure_count": 0,
            },
        )
        if entry_price <= 0 or current_price <= 0:
            return state

        current_return = (current_price - entry_price) / entry_price
        observed_peak = current_return
        observed_trough = current_return
        if bars and entry_time is not None:
            for bar in bars:
                stamp = self._timestamp(bar.get("t"))
                if stamp is None or stamp < entry_time:
                    continue
                high = Decimal(str(bar.get("h", "0") or "0"))
                low = Decimal(str(bar.get("l", "0") or "0"))
                if high > 0:
                    observed_peak = max(
                        observed_peak,
                        (high - entry_price) / entry_price,
                    )
                if low > 0:
                    observed_trough = min(
                        observed_trough,
                        (low - entry_price) / entry_price,
                    )

        peak = max(
            Decimal(str(state.get("peak_return_pct") or "0")),
            observed_peak,
        )
        trough = min(
            Decimal(str(state.get("trough_return_pct") or "0")),
            observed_trough,
        )
        state["entry_price"] = str(entry_price)
        state["current_price"] = str(current_price)
        state["current_return_pct"] = str(current_return)
        state["peak_return_pct"] = str(peak)
        state["trough_return_pct"] = str(trough)
        state["observed_peak_return_pct"] = str(observed_peak)
        state["observed_trough_return_pct"] = str(observed_trough)

        if (
            self.settings.profit_protect_enabled
            and peak >= self.settings.profit_protect_activation_pct
        ):
            floor = max(
                self.settings.profit_protect_min_pct,
                peak * self.settings.profit_protect_retain_fraction,
            )
            old_floor_raw = state.get("protected_floor_pct")
            old_floor = (
                Decimal(str(old_floor_raw))
                if old_floor_raw not in {None, ""}
                else Decimal("-1")
            )
            floor = max(floor, old_floor)
            state["profit_protection_active"] = True
            state["protected_floor_pct"] = str(floor)

        risk_stop_pct = Decimal(
            str(state.get("risk_stop_pct") or self.settings.stop_pct)
        )
        stop_floor_pct = -risk_stop_pct
        if (
            self.settings.profit_protect_enabled
            and state.get("profit_protection_active")
        ):
            stop_floor_pct = max(
                stop_floor_pct,
                Decimal(str(state.get("protected_floor_pct") or "0")),
            )
        desired_stop = entry_price * (Decimal("1") + stop_floor_pct)
        state["desired_stop_price"] = str(self._price_for_order(desired_stop))
        return state

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

    def _stateful_price_exit(
        self,
        position: dict[str, Any],
        exit_state: dict[str, Any] | None = None,
    ) -> tuple[str, str] | None:
        entry_price = Decimal(str(position.get("avg_entry_price", "0") or "0"))
        current_price = Decimal(str(position.get("current_price", "0") or "0"))
        if entry_price <= 0 or current_price <= 0:
            return None

        state = exit_state or self._exit_state_for_position(position)
        risk_stop_pct = Decimal(
            str(state.get("risk_stop_pct") or self.settings.stop_pct)
        )
        hard_stop_price = entry_price * (Decimal("1") - risk_stop_pct)
        target_price = entry_price * (Decimal("1") + self.settings.target_pct)

        if current_price <= hard_stop_price:
            return (
                "stop",
                f"software fallback stop triggered at {current_price} "
                f"(entry {entry_price}, risk {risk_stop_pct:.4%})",
            )

        if state.get("profit_protection_active"):
            protected_floor_pct = Decimal(
                str(state.get("protected_floor_pct") or "0")
            )
            protected_price = entry_price * (
                Decimal("1") + protected_floor_pct
            )
            if current_price <= protected_price:
                return (
                    "protect",
                    f"profit-protection floor triggered at {current_price} "
                    f"(peak {Decimal(str(state.get('peak_return_pct') or '0')):.4%}, "
                    f"floor {protected_floor_pct:.4%})",
                )

        if current_price >= target_price:
            return (
                "target",
                f"bot-managed take profit triggered at {current_price} "
                f"(entry {entry_price})",
            )
        return None

    @staticmethod
    def _signal_rank(signal: Signal) -> tuple[Decimal, Decimal, Decimal, int]:
        metadata = signal.metadata or {}
        return (
            Decimal(str(metadata.get("quality_score", "0"))),
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
            and (
                self.settings.dynamic_universe_enabled
                or str(position.get("symbol", "")).upper() in self.settings.allowed_symbols
            )
            and self._bot_bought_symbol_today(
                recent_orders,
                str(position.get("symbol", "")).upper(),
            )
        ]

    async def _ensure_protective_stop(
        self,
        position: dict[str, Any],
        open_orders: list[dict[str, Any]],
        now: datetime,
        *,
        bars: list[dict[str, Any]] | None = None,
        entry_time: datetime | None = None,
    ) -> dict[str, Any]:
        symbol = str(position.get("symbol", "")).upper()
        if not self.settings.broker_protective_stop_enabled:
            return {"action": "disabled", "symbol": symbol}

        state = self._exit_state_for_position(
            position,
            bars=bars,
            entry_time=entry_time,
        )
        desired_stop = Decimal(str(state.get("desired_stop_price") or "0"))
        entry_price = Decimal(str(position.get("avg_entry_price", "0") or "0"))
        current_price = Decimal(str(position.get("current_price", "0") or "0"))
        if desired_stop <= 0 or entry_price <= 0 or current_price <= 0:
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "protective stop price unavailable",
            }
        if current_price <= desired_stop:
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": (
                    "current price is at or below desired protection; "
                    "software exit will handle"
                ),
            }

        pending_id = str(
            state.get("protective_stop_pending_client_order_id") or ""
        )
        if pending_id:
            try:
                recovered = await self.client.order_by_client_order_id(pending_id)
            except Exception:
                recovered = None
            if recovered is None:
                return {
                    "action": "hold",
                    "symbol": symbol,
                    "reason": "protective stop submission remains ambiguous",
                }
            state.pop("protective_stop_pending_client_order_id", None)
            state["broker_stop_order_id"] = str(recovered.get("id") or "")
            state["broker_stop_price"] = str(recovered.get("stop_price") or "")
            self.state.record_event(
                kind="protection",
                symbol=symbol,
                action="recovered",
                message="protective stop recovered by client order ID",
                at=now,
                payload={"order": recovered, "exit_state": dict(state)},
            )
            return {
                "action": "recovered",
                "symbol": symbol,
                "order": recovered,
            }

        existing = self._protective_stop_for_symbol(open_orders, symbol)
        if existing is not None:
            current_stop = Decimal(str(existing.get("stop_price") or "0"))
            state["broker_stop_order_id"] = str(existing.get("id") or "")
            state["broker_stop_price"] = str(current_stop)
            improvement = (
                (desired_stop - current_stop) / entry_price
                if entry_price > 0
                else Decimal("0")
            )
            if (
                desired_stop > current_stop
                and improvement >= self.settings.profit_stop_step_pct
                and existing.get("id")
            ):
                try:
                    replaced = await self.client.replace_stop_order(
                        str(existing["id"]),
                        str(desired_stop),
                    )
                except Exception as exc:
                    self.state.record_event(
                        kind="protection",
                        symbol=symbol,
                        action="warning",
                        message="protective stop ratchet failed; existing stop retained",
                        reason=f"{type(exc).__name__}: {exc}",
                        at=now,
                        payload={"exit_state": dict(state)},
                    )
                    return {
                        "action": "hold",
                        "symbol": symbol,
                        "reason": "existing protective stop retained after replace failure",
                    }

                state["broker_stop_order_id"] = str(replaced.get("id") or "")
                state["broker_stop_price"] = str(
                    replaced.get("stop_price") or desired_stop
                )
                self.state.record_event(
                    kind="protection",
                    symbol=symbol,
                    action="ratchet",
                    message=(
                        f"protective stop raised from {current_stop} "
                        f"to {desired_stop}"
                    ),
                    at=now,
                    payload={"order": replaced, "exit_state": dict(state)},
                )
                return {
                    "action": "replaced",
                    "symbol": symbol,
                    "order": replaced,
                }

            return {
                "action": "present",
                "symbol": symbol,
                "order": existing,
            }

        client_order_id = self._client_order_id(symbol, "hardstop")
        try:
            order = await self.client.submit_stop_sell(
                symbol=symbol,
                qty=str(position.get("qty")),
                stop_price=str(desired_stop),
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
                    await asyncio.sleep(0.25 * (attempt + 1))

            if recovered is None:
                state["protective_stop_pending_client_order_id"] = client_order_id
                self.state.reconciliation_safe = False
                self.state.last_reconciliation = {
                    "safe_to_enter": False,
                    "reason": "ambiguous broker protective stop submission",
                    "client_order_id": client_order_id,
                    "symbol": symbol,
                }
                self.state.record_event(
                    kind="protection",
                    symbol=symbol,
                    action="warning",
                    message=(
                        "protective stop outcome ambiguous; new entries blocked "
                        "until reconciled"
                    ),
                    reason=f"{type(exc).__name__}: {exc}",
                    at=now,
                    payload={"exit_state": dict(state)},
                )
                return {
                    "action": "hold",
                    "symbol": symbol,
                    "reason": "protective stop outcome ambiguous",
                }
            order = recovered

        state["broker_stop_order_id"] = str(order.get("id") or "")
        state["broker_stop_price"] = str(order.get("stop_price") or desired_stop)
        self.state.record_event(
            kind="protection",
            symbol=symbol,
            action="stop",
            message=f"broker protective stop active at {desired_stop}",
            at=now,
            payload={"order": order, "exit_state": dict(state)},
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "order": order,
        }

    async def _ensure_protective_stops(
        self,
        managed_positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        monitoring_bars: dict[str, list[dict[str, Any]]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        if not self.settings.broker_protective_stop_enabled:
            return []
        results = await asyncio.gather(
            *[
                self._ensure_protective_stop(
                    position,
                    open_orders,
                    now,
                    bars=monitoring_bars.get(
                        str(position.get("symbol", "")).upper(),
                        [],
                    ),
                    entry_time=self._latest_bot_buy_today(
                        recent_orders,
                        str(position.get("symbol", "")).upper(),
                    ),
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
                self.state.record_event(
                    kind="protection",
                    symbol=symbol,
                    action="error",
                    message="protective stop management failed",
                    reason=f"{type(result).__name__}: {result}",
                    at=now,
                )
            else:
                normalized.append(result)
        return normalized

    async def _force_flatten(
        self,
        symbol: str,
        account: dict[str, Any],
        position: dict[str, Any],
        open_orders: list[dict[str, Any]],
        *,
        exit_reason: str = "forced end-of-day flatten",
        action_tag: str = "eod",
        exit_metadata: dict[str, Any] | None = None,
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
                    exit_metadata=exit_metadata,
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
            "exit_metadata": exit_metadata or {},
        }
        self.state.last_order = order_payload
        self.state.record_event(
            kind="execution",
            symbol=symbol,
            action="sell",
            message=f"{exit_reason} order submitted",
            payload={"exit_state": exit_metadata or {}},
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
        monitoring_bars: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        exit_specs: list[tuple[dict[str, Any], str, str, dict[str, Any]]] = []

        health_bars: dict[str, list[dict[str, Any]]] = monitoring_bars or {}
        if (
            self.settings.thesis_exit_enabled
            and managed_positions
            and isinstance(self.strategy, RollingMomentumVwapStrategy)
            and not health_bars
        ):
            health_symbols = {
                str(position.get("symbol", "")).upper()
                for position in managed_positions
                if str(position.get("symbol", "")).strip()
            }
            health_symbols.update(self.settings.confirmation_symbols)
            try:
                health_bars = await self.market_data.bars_many(
                    sorted(health_symbols)
                )
            except Exception as exc:
                self.state.record_event(
                    kind="exit_health",
                    action="warning",
                    message="position-health market data unavailable",
                    reason=f"{type(exc).__name__}: {exc}",
                    at=now,
                )

        for position in managed_positions:
            symbol = str(position.get("symbol", "")).upper()
            if self._has_bot_exit_order(open_orders, symbol):
                continue

            entry_time = self._latest_bot_buy_today(recent_orders, symbol)
            exit_state = self._exit_state_for_position(
                position,
                bars=health_bars.get(symbol, []),
                entry_time=entry_time,
            )
            if (
                exit_state.get("profit_protection_active")
                and not exit_state.get("profit_activation_emitted")
            ):
                exit_state["profit_activation_emitted"] = True
                self.state.record_event(
                    kind="protection",
                    symbol=symbol,
                    action="activated",
                    message=(
                        "profit protection activated at "
                        f"{Decimal(str(exit_state.get('peak_return_pct') or '0')):.4%}"
                    ),
                    at=now,
                    payload={"exit_state": dict(exit_state)},
                )

            if now.time() >= self.settings.force_flat_time:
                exit_specs.append(
                    (
                        position,
                        "eod",
                        "forced end-of-day flatten",
                        dict(exit_state),
                    )
                )
                continue

            price_exit = self._stateful_price_exit(position, exit_state)
            if price_exit is not None:
                action_tag, exit_reason = price_exit
                exit_specs.append(
                    (position, action_tag, exit_reason, dict(exit_state))
                )
                continue

            if (
                self.settings.thesis_exit_enabled
                and isinstance(self.strategy, RollingMomentumVwapStrategy)
                and health_bars
            ):
                confirmation_bars = {
                    confirmation_symbol: health_bars.get(
                        confirmation_symbol,
                        [],
                    )
                    for confirmation_symbol in self.settings.confirmation_symbols
                }
                health = self.strategy.position_health(
                    bars=health_bars.get(symbol, []),
                    confirmation_bars=confirmation_bars,
                    symbol=symbol,
                    now=now,
                )
                exit_state["position_health"] = health
                current_return = Decimal(
                    str(exit_state.get("current_return_pct") or "0")
                )
                prior_failures = int(
                    exit_state.get("thesis_failure_count") or 0
                )
                thesis_failure = (
                    bool(health.get("strong_failure"))
                    and current_return
                    <= self.settings.thesis_exit_max_return_pct
                )
                health_bar_time = str(health.get("bar_time") or "")
                prior_failure_bar = str(
                    exit_state.get("last_thesis_failure_bar_time") or ""
                )
                counted_new_failure = False
                if thesis_failure:
                    if health_bar_time and health_bar_time != prior_failure_bar:
                        exit_state["thesis_failure_count"] = prior_failures + 1
                        exit_state["last_thesis_failure_bar_time"] = health_bar_time
                        counted_new_failure = True
                    elif not health_bar_time and prior_failures == 0:
                        exit_state["thesis_failure_count"] = 1
                        counted_new_failure = True
                else:
                    exit_state["thesis_failure_count"] = 0
                    exit_state.pop("last_thesis_failure_bar_time", None)

                if thesis_failure and counted_new_failure:
                    self.state.record_event(
                        kind="exit_health",
                        symbol=symbol,
                        action="failure",
                        message=(
                            "thesis deterioration observed "
                            f"({exit_state['thesis_failure_count']}/"
                            f"{self.settings.thesis_failure_cycles})"
                        ),
                        reason=str(health.get("reason") or ""),
                        at=now,
                        payload={"exit_state": dict(exit_state)},
                    )

                if (
                    int(exit_state.get("thesis_failure_count") or 0)
                    >= self.settings.thesis_failure_cycles
                ):
                    exit_specs.append(
                        (
                            position,
                            "thesis",
                            (
                                "position thesis failed for "
                                f"{self.settings.thesis_failure_cycles} "
                                "consecutive evaluations"
                            ),
                            dict(exit_state),
                        )
                    )
                    continue

            if self.settings.max_hold_minutes > 0:
                if entry_time is not None:
                    held_minutes = (now - entry_time).total_seconds() / 60
                    exit_state["held_minutes"] = held_minutes
                    if held_minutes >= self.settings.max_hold_minutes:
                        exit_specs.append(
                            (
                                position,
                                "time",
                                f"max hold {self.settings.max_hold_minutes} minutes",
                                dict(exit_state),
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
                    exit_metadata=metadata,
                )
                for position, tag, reason, metadata in exit_specs
            ],
            return_exceptions=True,
        )

        normalized: list[dict[str, Any]] = []
        for (position, _tag, reason, metadata), result in zip(
            exit_specs,
            results,
        ):
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
                    payload={"exit_state": metadata},
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

        self._prune_exit_states(positions)
        managed_positions = self._managed_positions(positions, recent_orders)

        exit_monitoring_bars: dict[str, list[dict[str, Any]]] = {}
        if managed_positions and (
            self.settings.broker_protective_stop_enabled
            or self.settings.profit_protect_enabled
            or self.settings.thesis_exit_enabled
        ):
            monitoring_symbols = {
                str(position.get("symbol", "")).upper()
                for position in managed_positions
                if str(position.get("symbol", "")).strip()
            }
            monitoring_symbols.update(self.settings.confirmation_symbols)
            try:
                exit_monitoring_bars = await self.market_data.bars_many(
                    sorted(monitoring_symbols)
                )
            except Exception as exc:
                self.state.record_event(
                    kind="exit_health",
                    action="warning",
                    message="exit-monitoring market data unavailable",
                    reason=f"{type(exc).__name__}: {exc}",
                    at=now,
                )

        protection_results = await self._ensure_protective_stops(
            managed_positions,
            open_orders,
            recent_orders,
            exit_monitoring_bars,
            now,
        )
        if any(
            result.get("action") in {"submitted", "replaced", "recovered"}
            for result in protection_results
        ):
            open_orders = await self.client.open_orders()

        exit_results = await self._exit_managed_positions(
            account,
            managed_positions,
            open_orders,
            recent_orders,
            now,
            monitoring_bars=exit_monitoring_bars,
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

        position_symbols = [
            str(position.get("symbol", "")).upper()
            for position in positions
            if str(position.get("symbol", "")).strip()
        ]
        if self.universe is not None:
            entry_symbols = list(
                await self.universe.active_symbols(
                    position_symbols=position_symbols,
                    now=now,
                )
            )
        else:
            entry_symbols = list(self.settings.scan_symbols)

        symbols = list(
            dict.fromkeys(
                [
                    *entry_symbols,
                    *self.settings.confirmation_symbols,
                    *position_symbols,
                ]
            )
        )
        market_bars = await self.market_data.bars_many(symbols)

        buy_signals: list[Signal] = []
        scan: dict[str, Any] = {}
        for symbol in entry_symbols:
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
                    ranking = score_opportunity(
                        self.settings,
                        signal,
                        market_bars.get(signal.symbol, []),
                        quality,
                    )
                    signal.metadata["quality_score"] = ranking["score"]
                    signal.metadata["quality_components"] = ranking["components"]
                    signal.metadata["relative_volume_ratio"] = ranking["relative_volume_ratio"]
                    signal.metadata["trend_persistence"] = ranking["trend_persistence"]
                    if Decimal(str(ranking["score"])) < self.settings.min_quality_score:
                        gate_reason = (
                            f"quality score {ranking['score']:.2f} below "
                            f"MIN_QUALITY_SCORE {self.settings.min_quality_score}"
                        )
                        payload = self._signal_payload(signal)
                        payload["action"] = "hold"
                        payload["reason"] = gate_reason
                        scan[signal.symbol] = payload
                        self.state.record_event(
                            kind="allocation",
                            symbol=signal.symbol,
                            action="hold",
                            message=gate_reason,
                            reason=gate_reason,
                            at=now,
                            payload={
                                "quality_score": ranking["score"],
                                "min_quality_score": str(self.settings.min_quality_score),
                            },
                        )
                        continue
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
                "symbols": len(entry_symbols),
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
                    f"scanner watching {len(entry_symbols)} symbols; "
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

        loss_gate_ok, loss_gate_reason, loss_gate_detail = self._loss_streak_gate(
            recent_orders,
            now,
        )
        if not loss_gate_ok:
            self.state.last_decision = loss_gate_reason
            self.state.record_event(
                kind="risk",
                action="hold",
                message=loss_gate_reason,
                reason=loss_gate_reason,
                at=now,
                payload={"loss_streak": loss_gate_detail},
            )
            return {
                "action": "hold",
                "reason": loss_gate_reason,
                "qualified_symbols": [signal.symbol for signal in ranked],
                "loss_streak": loss_gate_detail,
            }

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
        if self.settings.portfolio_limit_mode == "risk":
            cycle_limit: int | None = (
                self.settings.max_new_entries_per_cycle
                if self.settings.max_new_entries_per_cycle > 0
                else None
            )
        else:
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
            if cycle_limit is not None and len(planned) >= cycle_limit:
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

            exposure_symbols = [
                str(position.get("symbol", "")).upper()
                for position in simulated_positions
                if Decimal(str(position.get("qty", "0") or "0")) > 0
            ]
            correlation_allowed, correlation_reason, correlation_detail = correlation_checks(
                self.settings,
                symbol,
                exposure_symbols,
                market_bars,
            )
            signal.metadata = dict(signal.metadata or {})
            signal.metadata["correlation"] = {
                "threshold": str(self.settings.max_pairwise_correlation),
                "lookback_bars": self.settings.correlation_lookback_bars,
                "min_observations": self.settings.correlation_min_observations,
                "checks": correlation_detail,
            }
            scan[symbol] = self._signal_payload(signal)
            if not correlation_allowed:
                skipped.append({"symbol": symbol, "reason": correlation_reason})
                scan[symbol]["action"] = "hold"
                scan[symbol]["reason"] = correlation_reason
                self.state.record_event(
                    kind="allocation",
                    symbol=symbol,
                    action="hold",
                    message=correlation_reason,
                    reason=correlation_reason,
                    at=now,
                    payload={"correlation": correlation_detail},
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
            effective_stop_pct = Decimal(
                str(
                    (signal.metadata or {}).get(
                        "effective_stop_pct",
                        self.settings.stop_pct,
                    )
                )
            )
            entry_notional = calculate_entry_notional(
                self.settings,
                simulated_account,
                simulated_positions,
                stop_pct_override=effective_stop_pct,
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
                stop_pct_override=effective_stop_pct,
            )

            risk = validate_buy(
                self.settings,
                symbol,
                signal.notional,
                simulated_account,
                simulated_positions,
                entry_count + len(planned),
                entry_symbols=set(entry_symbols),
                stop_pct_override=effective_stop_pct,
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
                    "risk_stop_pct": str(effective_stop_pct),
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

            effective_stop_pct = Decimal(
                str(
                    (signal.metadata or {}).get(
                        "effective_stop_pct",
                        self.settings.stop_pct,
                    )
                )
            )
            self.state.exit_states[symbol] = {
                "symbol": symbol,
                "risk_stop_pct": str(effective_stop_pct),
                "peak_return_pct": "0",
                "trough_return_pct": "0",
                "protected_floor_pct": None,
                "profit_protection_active": False,
                "thesis_failure_count": 0,
                "entry_client_order_id": _client_order_id,
            }

            protection_result: dict[str, Any] | None = None
            filled_qty = Decimal(str(result.get("filled_qty") or "0"))
            filled_price = Decimal(str(result.get("filled_avg_price") or "0"))
            if (
                self.settings.broker_protective_stop_enabled
                and filled_qty > 0
                and filled_price > 0
            ):
                synthetic_position = {
                    "symbol": symbol,
                    "qty": str(filled_qty),
                    "avg_entry_price": str(filled_price),
                    "current_price": str(filled_price),
                }
                try:
                    protection_result = await self._ensure_protective_stop(
                        synthetic_position,
                        [],
                        now,
                    )
                except Exception as exc:
                    protection_result = {
                        "action": "error",
                        "symbol": symbol,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                    self.state.record_event(
                        kind="protection",
                        symbol=symbol,
                        action="error",
                        message="immediate post-fill protective stop failed",
                        reason=str(exc),
                        at=now,
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
                "effective_stop_pct": str(effective_stop_pct),
                "exit_management": "exit_engine_v2",
                "protection": protection_result,
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
                    "Exit Engine v2 protection active"
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
