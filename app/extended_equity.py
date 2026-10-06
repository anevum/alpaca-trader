from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .equity_sessions import EquitySession, EquitySessionContext, EquitySessionResolver
from .market_data import MarketDataClient
from .persistence import TradingEventSink
from .risk import validate_extended_buy
from .sizing import calculate_entry_notional, sizing_snapshot
from .state import RuntimeState
from .strategy import Signal


NY = ZoneInfo("America/New_York")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=NY)
    return stamp.astimezone(NY)


def _price(value: Decimal, *, side: str) -> Decimal:
    increment = Decimal("0.0001") if value < Decimal("1") else Decimal("0.01")
    rounding = ROUND_UP if side == "buy" else ROUND_DOWN
    return value.quantize(increment, rounding=rounding)


class ExtendedEquityEngine:
    """Session-aware RHEN executor for Alpaca premarket/after-hours/overnight.

    This engine never changes the regular-session strategy. It owns only
    extended-session entries and positions whose most recent RHEN extended buy
    has not been followed by an extended sell.

    Safety invariants:
    - no extended-hours market or stop orders;
    - entry and exit orders are DAY limit orders with extended_hours=true;
    - regular-session handoff positions are flattened, never silently adopted;
    - Friday positions are flattened before the 20:00 ET weekend close;
    - all entries share RHEN's account-level cash, gross exposure, stop-risk,
      daily-loss, reconciliation, and entry-enable gates;
    - the live lane requires its own explicit live acknowledgement.
    """

    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: MarketDataClient,
        state: RuntimeState,
        *,
        ledger: TradingEventSink | None = None,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.state = state
        self.ledger = ledger
        self.session_resolver = EquitySessionResolver(market_data)

        self._bars: dict[str, list[dict[str, Any]]] = {}
        self._cache_key: tuple[str, str | None, str] | None = None
        self._universe: list[str] = []
        self._eligible_count = 0
        self._universe_updated_at: datetime | None = None
        self._universe_session: str | None = None

        self.last_cycle_at: datetime | None = None
        self.last_session: dict[str, Any] | None = None
        self.last_scan: dict[str, dict[str, Any]] = {}
        self.last_decision: str = "awaiting first extended-equity cycle"
        self.last_error: str | None = None
        self.last_order: dict[str, Any] | None = None
        self.last_exit_results: list[dict[str, Any]] = []

    @property
    def execution_authorized(self) -> bool:
        return self.settings.extended_equity_execution_authorized

    def snapshot(self) -> dict[str, Any]:
        return {
            "lane": "extended_equity",
            "enabled": self.settings.extended_equity_lane_enabled,
            "execution_enabled": self.settings.extended_equity_execution_enabled,
            "execution_authorized": self.execution_authorized,
            "strategy_version_id": self.settings.extended_equity_strategy_version_id,
            "observed_at": (
                self.last_cycle_at.isoformat() if self.last_cycle_at else None
            ),
            "session": self.last_session,
            "universe": {
                "active_symbols": list(self._universe),
                "active_count": len(self._universe),
                "eligible_count": self._eligible_count,
                "updated_at": (
                    self._universe_updated_at.isoformat()
                    if self._universe_updated_at else None
                ),
            },
            "scanner": self.last_scan,
            "last_decision": self.last_decision,
            "last_error": self.last_error,
            "last_order": self.last_order,
            "last_exits": self.last_exit_results,
            "data_cache": {
                "symbols": len(self._bars),
                "bars": sum(len(rows) for rows in self._bars.values()),
            },
        }

    def _event(
        self,
        *,
        action: str,
        message: str,
        symbol: str = "",
        reason: str = "",
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.state.record_event(
            kind="extended_equity",
            action=action,
            message=message,
            symbol=symbol,
            reason=reason or message,
            at=now,
            payload={
                "market": "us_equity_extended",
                **(payload or {}),
            },
        )

    def _client_order_id(self, symbol: str, action: str) -> str:
        return (
            f"anevum-{symbol.lower()}-{action}-ext-"
            f"{self.settings.order_owner_tag}-{uuid4().hex[:12]}"
        )[:128]

    @staticmethod
    def _is_extended_order(order: dict[str, Any], *, side: str | None = None) -> bool:
        client_order_id = str(order.get("client_order_id") or "")
        if not (
            client_order_id.startswith("anevum-")
            and "-ext-" in client_order_id
        ):
            return False
        if side is None:
            return True
        return str(order.get("side") or "").lower() == side.lower()

    @staticmethod
    def _filled_stamp(order: dict[str, Any]) -> datetime | None:
        return _timestamp(order.get("filled_at") or order.get("submitted_at"))

    def _position_owned_by_lane(
        self,
        position: dict[str, Any],
        recent_orders: list[dict[str, Any]],
    ) -> bool:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol or "/" in symbol or _d(position.get("qty")) <= 0:
            return False

        buys: list[datetime] = []
        sells: list[datetime] = []
        for order in recent_orders:
            if str(order.get("symbol") or "").upper() != symbol:
                continue
            if not self._is_extended_order(order):
                continue
            stamp = self._filled_stamp(order)
            if stamp is None:
                continue
            side = str(order.get("side") or "").lower()
            filled_qty = _d(order.get("filled_qty"))
            status = str(order.get("status") or "").lower()
            if filled_qty <= 0 and status != "filled":
                continue
            if side == "buy":
                buys.append(stamp)
            elif side == "sell":
                sells.append(stamp)

        if not buys:
            return False
        latest_buy = max(buys)
        latest_sell = max(sells) if sells else None
        return latest_sell is None or latest_buy > latest_sell

    def _managed_positions(
        self,
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            position
            for position in positions
            if self._position_owned_by_lane(position, recent_orders)
        ]

    def _latest_lane_buy(
        self,
        recent_orders: list[dict[str, Any]],
        symbol: str,
    ) -> datetime | None:
        stamps = [
            self._filled_stamp(order)
            for order in recent_orders
            if str(order.get("symbol") or "").upper() == symbol.upper()
            and self._is_extended_order(order, side="buy")
            and (
                _d(order.get("filled_qty")) > 0
                or str(order.get("status") or "").lower() == "filled"
            )
        ]
        return max((stamp for stamp in stamps if stamp is not None), default=None)

    @staticmethod
    def _active_order_for_symbol(
        open_orders: list[dict[str, Any]],
        symbol: str,
        side: str,
    ) -> dict[str, Any] | None:
        upper = symbol.upper()
        for order in open_orders:
            if str(order.get("symbol") or "").upper() != upper:
                continue
            if str(order.get("side") or "").lower() != side.lower():
                continue
            client_order_id = str(order.get("client_order_id") or "")
            if client_order_id.startswith("anevum-") and "-ext-" in client_order_id:
                return order
        return None

    async def _cancel_stale_entry_orders(
        self,
        open_orders: list[dict[str, Any]],
        now: datetime,
        *,
        force: bool = False,
    ) -> int:
        if not self.execution_authorized:
            return 0
        ttl = max(60, self.settings.extended_equity_poll_seconds * 3)
        canceled = 0
        for order in open_orders:
            if not self._is_extended_order(order, side="buy"):
                continue
            submitted = _timestamp(order.get("submitted_at"))
            stale = (
                submitted is None
                or (now - submitted).total_seconds() >= ttl
            )
            if not (force or stale):
                continue
            order_id = str(order.get("id") or "")
            if not order_id:
                continue
            try:
                await self.client.cancel_order(order_id)
                canceled += 1
            except Exception as exc:
                self._event(
                    action="warning",
                    symbol=str(order.get("symbol") or "").upper(),
                    message="stale extended entry cancel failed",
                    reason=f"{type(exc).__name__}: {exc}",
                    now=now,
                )
        return canceled

    def _feed_for(self, context: EquitySessionContext) -> str:
        if context.session == EquitySession.OVERNIGHT:
            return self.settings.overnight_data_feed
        return self.settings.extended_equity_data_feed

    def _reset_bar_cache_if_needed(
        self,
        context: EquitySessionContext,
        feed: str,
    ) -> None:
        key = (
            context.session.value,
            (
                context.target_session_date.isoformat()
                if context.target_session_date is not None else None
            ),
            feed,
        )
        if key != self._cache_key:
            self._bars = {}
            self._cache_key = key

    def _append_latest_bars(
        self,
        latest: dict[str, dict[str, Any]],
    ) -> None:
        max_rows = max(self.settings.extended_equity_slow_window * 8, 120)
        for symbol, bar in latest.items():
            if not isinstance(bar, dict) or not bar.get("t"):
                continue
            upper = symbol.upper()
            rows = self._bars.setdefault(upper, [])
            stamp = str(bar.get("t"))
            existing = next(
                (index for index, row in enumerate(rows) if str(row.get("t")) == stamp),
                None,
            )
            if existing is None:
                rows.append(dict(bar))
            else:
                rows[existing] = dict(bar)
            rows.sort(key=lambda row: str(row.get("t") or ""))
            if len(rows) > max_rows:
                del rows[:-max_rows]

    async def _refresh_universe(
        self,
        context: EquitySessionContext,
        now: datetime,
    ) -> list[str]:
        refresh_due = (
            not self._universe
            or self._universe_updated_at is None
            or self._universe_session != context.session.value
            or (
                now - self._universe_updated_at
            ).total_seconds() >= self.settings.extended_equity_universe_refresh_seconds
        )
        if not refresh_due:
            return list(self._universe)

        requested = list(
            dict.fromkeys(
                [
                    *self.settings.extended_equity_symbols,
                    *self.state.universe_active_symbols,
                    *self.settings.extended_equity_confirmation_symbols,
                ]
            )
        )
        requested_set = set(requested)
        assets = await self.client.assets(
            status="active",
            asset_class="us_equity",
            attributes=(
                "overnight_tradable"
                if context.session == EquitySession.OVERNIGHT
                else None
            ),
        )

        eligible: list[str] = []
        by_symbol = {
            str(asset.get("symbol") or "").upper(): asset
            for asset in assets
            if asset.get("symbol")
        }
        for symbol in requested:
            asset = by_symbol.get(symbol)
            if asset is None:
                continue
            attrs = {
                str(value).lower()
                for value in (asset.get("attributes") or [])
            }
            if str(asset.get("status") or "").lower() != "active":
                continue
            if not bool(asset.get("tradable")):
                continue
            if not bool(asset.get("fractionable")):
                continue
            if (
                str(asset.get("exchange") or "").upper()
                not in self.settings.universe_exchanges
            ):
                continue
            if context.session == EquitySession.OVERNIGHT:
                if "overnight_tradable" not in attrs:
                    continue
                if (
                    "overnight_halted" in attrs
                    or bool(asset.get("overnight_halted"))
                ):
                    continue
            eligible.append(symbol)

        # Preserve configured priority, then live regular-universe additions.
        confirmation_set = set(self.settings.extended_equity_confirmation_symbols)
        tradable = [
            symbol for symbol in eligible
            if symbol not in confirmation_set
        ][: self.settings.extended_equity_universe_size]
        for confirmation in self.settings.extended_equity_confirmation_symbols:
            if confirmation in eligible and confirmation not in tradable:
                tradable.append(confirmation)

        self._universe = tradable
        self._eligible_count = sum(
            1 for symbol in requested_set if symbol in by_symbol
        )
        self._universe_updated_at = now
        self._universe_session = context.session.value
        self._event(
            action="universe",
            message=(
                f"extended-equity universe refreshed: {len(tradable)} active"
            ),
            payload={
                "session": context.session.value,
                "symbols": tradable,
                "requested_count": len(requested),
                "eligible_count": self._eligible_count,
            },
            now=now,
        )
        return list(tradable)

    def _spread_limit(self, context: EquitySessionContext) -> Decimal:
        if context.session == EquitySession.OVERNIGHT:
            return self.settings.overnight_max_spread_pct
        return self.settings.extended_equity_max_spread_pct

    def _quote_quality(
        self,
        quote: dict[str, Any],
        context: EquitySessionContext,
        now: datetime,
    ) -> tuple[bool, str, dict[str, Any]]:
        bid = _d(quote.get("bp"))
        ask = _d(quote.get("ap"))
        stamp = _timestamp(quote.get("t"))
        midpoint = (bid + ask) / Decimal("2") if bid > 0 and ask > 0 else Decimal("0")
        spread_pct = (
            (ask - bid) / midpoint
            if midpoint > 0 and ask >= bid
            else Decimal("999")
        )
        age = (
            (now - stamp).total_seconds()
            if stamp is not None
            else None
        )
        detail = {
            "bid": str(bid),
            "ask": str(ask),
            "midpoint": str(midpoint),
            "spread_pct": str(spread_pct),
            "quote_timestamp": stamp.isoformat() if stamp else None,
            "quote_age_seconds": age,
            "spread_limit": str(self._spread_limit(context)),
        }
        if bid <= 0 or ask <= 0 or ask < bid:
            return False, "extended quote unavailable or crossed", detail
        if stamp is None or age is None:
            return False, "extended quote timestamp unavailable", detail
        if age < -5 or age > self.settings.extended_equity_max_quote_age_seconds:
            return False, "extended quote is stale", detail
        if spread_pct > self._spread_limit(context):
            return False, "extended spread exceeds session limit", detail
        return True, "extended quote quality passed", detail

    @staticmethod
    def _vwap(bars: list[dict[str, Any]]) -> Decimal:
        weighted = Decimal("0")
        volume = Decimal("0")
        fallback: list[Decimal] = []
        for bar in bars:
            close = _d(bar.get("c"))
            if close <= 0:
                continue
            fallback.append(close)
            bar_volume = _d(bar.get("v"))
            bar_vwap = _d(bar.get("vw")) or close
            if bar_volume > 0:
                weighted += bar_vwap * bar_volume
                volume += bar_volume
        if volume > 0:
            return weighted / volume
        if fallback:
            return sum(fallback, Decimal("0")) / Decimal(len(fallback))
        return Decimal("0")

    def _confirmation_ok(self, symbol: str) -> bool:
        rows = self._bars.get(symbol.upper(), [])
        needed = self.settings.extended_equity_fast_window + 1
        if len(rows) < needed:
            return False
        closes = [_d(row.get("c")) for row in rows[-needed:]]
        if any(value <= 0 for value in closes):
            return False
        fast = (
            sum(closes[-self.settings.extended_equity_fast_window:], Decimal("0"))
            / Decimal(self.settings.extended_equity_fast_window)
        )
        return closes[-1] >= fast and closes[-1] >= closes[-2]

    def _evaluate(
        self,
        symbol: str,
        quote: dict[str, Any],
        context: EquitySessionContext,
        now: datetime,
        *,
        has_position: bool,
        has_open_order: bool,
    ) -> Signal:
        upper = symbol.upper()
        if has_position:
            return Signal(action="hold", symbol=upper, reason="position already open")
        if has_open_order:
            return Signal(action="hold", symbol=upper, reason="extended order already open")

        rows = self._bars.get(upper, [])
        needed = self.settings.extended_equity_slow_window + 1
        if len(rows) < needed:
            return Signal(
                action="hold",
                symbol=upper,
                reason=(
                    "extended tape warming "
                    f"({len(rows)}/{needed} completed observations)"
                ),
                metadata={
                    "market": "us_equity_extended",
                    "equity_session": context.session.value,
                },
            )

        latest_bar_stamp = _timestamp(rows[-1].get("t"))
        bar_age = (
            (now - latest_bar_stamp).total_seconds()
            if latest_bar_stamp is not None
            else None
        )
        if (
            latest_bar_stamp is None
            or bar_age is None
            or bar_age < -5
            or bar_age > self.settings.extended_equity_max_bar_age_seconds
        ):
            return Signal(
                action="hold",
                symbol=upper,
                reason="extended latest bar is stale",
                metadata={
                    "market": "us_equity_extended",
                    "equity_session": context.session.value,
                    "bar_age_seconds": bar_age,
                },
            )

        quality_ok, quality_reason, quality = self._quote_quality(
            quote,
            context,
            now,
        )
        if not quality_ok:
            return Signal(
                action="hold",
                symbol=upper,
                reason=quality_reason,
                metadata={
                    "market": "us_equity_extended",
                    "equity_session": context.session.value,
                    "market_quality": quality,
                },
            )

        closes = [_d(row.get("c")) for row in rows]
        current = closes[-1]
        previous = closes[-2]
        if current < self.settings.extended_equity_min_price:
            return Signal(
                action="hold",
                symbol=upper,
                reason="price below extended-equity minimum",
            )

        fast = (
            sum(
                closes[-self.settings.extended_equity_fast_window:],
                Decimal("0"),
            )
            / Decimal(self.settings.extended_equity_fast_window)
        )
        slow = (
            sum(
                closes[-self.settings.extended_equity_slow_window:],
                Decimal("0"),
            )
            / Decimal(self.settings.extended_equity_slow_window)
        )
        anchor = closes[-(self.settings.extended_equity_fast_window + 1)]
        momentum = (
            (current - anchor) / anchor
            if anchor > 0 else Decimal("0")
        )
        rolling_vwap = self._vwap(
            rows[-max(self.settings.extended_equity_slow_window * 3, 20):]
        )
        vwap_edge = (
            (current - rolling_vwap) / rolling_vwap
            if rolling_vwap > 0 else Decimal("0")
        )

        confirmations: dict[str, bool] = {}
        for confirmation in self.settings.extended_equity_confirmation_symbols:
            if confirmation.upper() == upper:
                continue
            confirmations[confirmation.upper()] = self._confirmation_ok(confirmation)
        required = min(
            max(self.settings.min_confirmations, 0),
            len(confirmations),
        )
        confirmation_passes = sum(confirmations.values())
        confirmation_ok = confirmation_passes >= required

        checks = {
            "fast_above_slow": fast > slow,
            "rising": current > previous,
            "momentum_ok": momentum >= self.settings.extended_equity_min_momentum_pct,
            "above_rolling_vwap": current >= rolling_vwap,
            "vwap_extension_ok": (
                vwap_edge <= self.settings.extended_equity_max_vwap_extension_pct
            ),
            "confirmations_ok": confirmation_ok,
        }

        metadata = {
            "market": "us_equity_extended",
            "market_lane": "extended_equity",
            "equity_session": context.session.value,
            "strategy_version_id": self.settings.extended_equity_strategy_version_id,
            "execution_order_type": "limit",
            "time_in_force": "day",
            "extended_hours": True,
            "data_feed": self._feed_for(context),
            "bar_time": latest_bar_stamp.isoformat(),
            "bar_age_seconds": bar_age,
            "current_close": str(current),
            "previous_close": str(previous),
            "fast_average": str(fast),
            "slow_average": str(slow),
            "rolling_vwap": str(rolling_vwap),
            "momentum_pct": str(momentum),
            "vwap_edge_pct": str(vwap_edge),
            "confirmation_passes": confirmation_passes,
            "confirmations": confirmations,
            "checks": checks,
            "market_quality": quality,
            "effective_stop_pct": str(self.settings.extended_equity_stop_pct),
        }

        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            return Signal(
                action="hold",
                symbol=upper,
                reason="extended signal failed: " + ", ".join(failed),
                metadata=metadata,
            )

        ask = _d(quote.get("ap"))
        stop = ask * (Decimal("1") - self.settings.extended_equity_stop_pct)
        target = ask * (Decimal("1") + self.settings.extended_equity_target_pct)
        return Signal(
            action="buy",
            symbol=upper,
            reference_price=ask,
            stop_price=_price(stop, side="sell"),
            take_profit_price=_price(target, side="sell"),
            reason="extended rolling momentum qualified",
            metadata=metadata,
        )

    def _entry_count(
        self,
        recent_orders: list[dict[str, Any]],
        context: EquitySessionContext,
        now: datetime,
    ) -> int:
        if context.starts_at is None:
            return 0
        return sum(
            1
            for order in recent_orders
            if self._is_extended_order(order, side="buy")
            and (
                (stamp := _timestamp(order.get("submitted_at"))) is not None
                and context.starts_at <= stamp <= now
            )
        )

    def _entry_block_reason(
        self,
        context: EquitySessionContext,
        now: datetime,
    ) -> str | None:
        if context.session == EquitySession.PREMARKET and context.regular_open:
            if now >= (
                context.regular_open
                - timedelta(minutes=self.settings.extended_equity_handoff_flat_minutes)
            ):
                return "regular-session handoff window"
        if (
            context.session == EquitySession.AFTER_HOURS
            and now.weekday() == 4
            and now.time() >= self.settings.extended_equity_weekend_flat_time
        ):
            return "Friday weekend flatten window"
        return None

    async def _exit_position(
        self,
        position: dict[str, Any],
        quote: dict[str, Any],
        context: EquitySessionContext,
        now: datetime,
        open_orders: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        symbol = str(position.get("symbol") or "").upper()
        pending = self._active_order_for_symbol(open_orders, symbol, "sell")
        bid = _d(quote.get("bp"))
        current = bid if bid > 0 else _d(position.get("current_price"))
        if current <= 0:
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "exit price unavailable",
            }

        if pending is not None:
            if context.session == EquitySession.REGULAR:
                order_id = str(pending.get("id") or "")
                if order_id:
                    try:
                        await self.client.cancel_order(order_id)
                    except Exception:
                        pass
                pending = None
            else:
                limit_price = _price(
                    current
                    * (Decimal("1") - self.settings.extended_equity_limit_buffer_pct),
                    side="sell",
                )
                order_id = str(pending.get("id") or "")
                if order_id:
                    try:
                        replaced = await self.client.replace_limit_order(
                            order_id,
                            str(limit_price),
                        )
                        return {
                            "action": "replaced",
                            "symbol": symbol,
                            "reason": reason,
                            "order": replaced,
                        }
                    except Exception as exc:
                        return {
                            "action": "hold",
                            "symbol": symbol,
                            "reason": (
                                "existing extended exit retained after reprice failure: "
                                f"{type(exc).__name__}"
                            ),
                        }

        client_order_id = self._client_order_id(symbol, "sell")
        exit_metadata = {
            "market": "us_equity_extended",
            "market_lane": "extended_equity",
            "equity_session": context.session.value,
            "execution_order_type": (
                "market"
                if context.session == EquitySession.REGULAR
                else "limit"
            ),
            "time_in_force": "day",
            "extended_hours": context.session != EquitySession.REGULAR,
            "data_feed": (
                self.settings.data_feed
                if context.session == EquitySession.REGULAR
                else self._feed_for(context)
            ),
        }
        ledger_refs: dict[str, str] | None = None
        if self.ledger is not None:
            ledger_refs = self.ledger.persist_exit_intent(
                symbol=symbol,
                qty=str(position.get("qty") or "0"),
                client_order_id=client_order_id,
                exit_reason=reason,
                correlation_id=self.state.current_correlation_id,
                intended_at=now,
                exit_metadata=exit_metadata,
            )

        try:
            if context.session == EquitySession.REGULAR:
                order = await self.client.submit_market_sell(
                    symbol=symbol,
                    qty=str(position.get("qty") or "0"),
                    client_order_id=client_order_id,
                )
            else:
                limit_price = _price(
                    current
                    * (Decimal("1") - self.settings.extended_equity_limit_buffer_pct),
                    side="sell",
                )
                order = await self.client.submit_extended_limit_sell(
                    symbol=symbol,
                    qty=str(position.get("qty") or "0"),
                    limit_price=str(limit_price),
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
                self.state.reconciliation_safe = False
                self.state.last_reconciliation = {
                    "safe_to_enter": False,
                    "reason": "ambiguous extended-equity exit submission",
                    "client_order_id": client_order_id,
                    "symbol": symbol,
                }
                return {
                    "action": "error",
                    "symbol": symbol,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            order = recovered

        if self.ledger is not None:
            self.ledger.record_broker_order(
                order,
                intent_id=(ledger_refs or {}).get("intent_id"),
                exit_id=(ledger_refs or {}).get("exit_id"),
                exit_reason=reason,
                correlation_id=self.state.current_correlation_id,
            )
        self.last_order = order
        self._event(
            action="sell",
            symbol=symbol,
            message=f"extended-equity exit submitted: {reason}",
            payload={"order": order, "session": context.session.value},
            now=now,
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "reason": reason,
            "order": order,
        }

    async def _manage_positions(
        self,
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
        context: EquitySessionContext,
        now: datetime,
    ) -> list[dict[str, Any]]:
        managed = self._managed_positions(positions, recent_orders)
        if not managed:
            return []
        if not self.execution_authorized:
            return [
                {
                    "action": "hold",
                    "symbol": str(position.get("symbol") or "").upper(),
                    "reason": "extended position present but execution is not authorized",
                }
                for position in managed
            ]

        results: list[dict[str, Any]] = []
        handoff = self._entry_block_reason(context, now)
        for position in managed:
            symbol = str(position.get("symbol") or "").upper()
            quote = quotes.get(symbol, {})
            bid = _d(quote.get("bp"))
            current = bid if bid > 0 else _d(position.get("current_price"))
            entry = _d(position.get("avg_entry_price"))
            entry_time = self._latest_lane_buy(recent_orders, symbol)

            reason: str | None = None
            if context.session == EquitySession.REGULAR:
                reason = "regular-session emergency handoff flatten"
            elif handoff is not None:
                reason = handoff
            elif entry > 0 and current > 0:
                return_pct = (current - entry) / entry
                if return_pct <= -self.settings.extended_equity_stop_pct:
                    reason = "extended software stop"
                elif return_pct >= self.settings.extended_equity_target_pct:
                    reason = "extended profit target"
            if (
                reason is None
                and entry_time is not None
                and (now - entry_time).total_seconds() / 60
                >= self.settings.extended_equity_max_hold_minutes
            ):
                reason = (
                    f"extended max hold "
                    f"{self.settings.extended_equity_max_hold_minutes} minutes"
                )

            if reason is None:
                continue
            results.append(
                await self._exit_position(
                    position,
                    quote,
                    context,
                    now,
                    open_orders,
                    reason,
                )
            )
        return results

    async def _submit_entry(
        self,
        signal: Signal,
        qty: Decimal,
        context: EquitySessionContext,
        now: datetime,
    ) -> dict[str, Any]:
        symbol = signal.symbol.upper()
        ask = signal.reference_price
        limit_price = _price(
            ask * (Decimal("1") + self.settings.extended_equity_limit_buffer_pct),
            side="buy",
        )
        client_order_id = self._client_order_id(symbol, "buy")
        signal.metadata = dict(signal.metadata or {})
        signal.metadata.update(
            {
                "execution_order_type": "limit",
                "time_in_force": "day",
                "extended_hours": True,
                "entry_limit_price": str(limit_price),
            }
        )

        ledger_refs: dict[str, str] | None = None
        if self.ledger is not None:
            ledger_refs = await self.ledger.persist_entry_intent(
                signal=signal,
                qty=str(qty),
                client_order_id=client_order_id,
                correlation_id=self.state.current_correlation_id,
                intended_at=now,
            )
            if ledger_refs is None:
                return {
                    "action": "blocked",
                    "symbol": symbol,
                    "reason": "durable extended entry intent unavailable",
                }

        try:
            order = await self.client.submit_extended_limit_buy(
                symbol=symbol,
                qty=str(qty),
                limit_price=str(limit_price),
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
                self.state.reconciliation_safe = False
                self.state.last_reconciliation = {
                    "safe_to_enter": False,
                    "reason": "ambiguous extended-equity entry submission",
                    "client_order_id": client_order_id,
                    "symbol": symbol,
                }
                return {
                    "action": "error",
                    "symbol": symbol,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            order = recovered

        if self.ledger is not None:
            self.ledger.record_broker_order(
                order,
                intent_id=(ledger_refs or {}).get("intent_id"),
                position_id=(ledger_refs or {}).get("position_id"),
                correlation_id=self.state.current_correlation_id,
            )
        self.last_order = order
        self._event(
            action="buy",
            symbol=symbol,
            message=(
                f"{context.session.value} limit entry submitted at {limit_price}"
            ),
            payload={"order": order, "signal": signal.metadata},
            now=now,
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "order": order,
            "limit_price": str(limit_price),
        }

    async def run_once(self) -> dict[str, Any]:
        now = datetime.now(NY)
        self.last_cycle_at = now
        self.last_error = None

        context = await self.session_resolver.classify(now)
        self.last_session = context.as_dict()

        if not self.settings.extended_equity_lane_enabled:
            self.last_decision = "extended-equity lane disabled"
            return {"action": "hold", "reason": self.last_decision}

        account, positions, open_orders, recent_orders = await asyncio.gather(
            self.client.account(),
            self.client.positions(),
            self.client.open_orders(),
            self.client.recent_orders(limit=500),
        )

        # During regular hours this lane performs only emergency cleanup of
        # positions it owns, then yields all new entries to the regular engine.
        if context.session == EquitySession.REGULAR:
            managed = self._managed_positions(positions, recent_orders)
            if not managed:
                self.last_decision = "regular session owned by primary equity engine"
                return {"action": "hold", "reason": self.last_decision}
            quotes = await self.market_data.latest_quotes_many(
                [str(position.get("symbol") or "").upper() for position in managed],
                feed=self.settings.data_feed,
            )
            exits = await self._manage_positions(
                positions,
                recent_orders,
                open_orders,
                quotes,
                context,
                now,
            )
            self.last_exit_results = exits
            self.last_decision = "regular-session handoff cleanup"
            return {
                "action": "submitted" if any(
                    item.get("action") in {"submitted", "replaced"}
                    for item in exits
                ) else "hold",
                "reason": self.last_decision,
                "exits": exits,
            }

        if context.session == EquitySession.CLOSED or not context.tradable:
            managed = self._managed_positions(positions, recent_orders)
            self.last_decision = (
                "extended equity market closed"
                if not managed
                else "extended equity market closed with managed position awaiting reopen"
            )
            if managed:
                self._event(
                    action="warning",
                    message=self.last_decision,
                    payload={
                        "positions": [
                            str(position.get("symbol") or "").upper()
                            for position in managed
                        ]
                    },
                    now=now,
                )
            return {"action": "hold", "reason": self.last_decision}

        feed = self._feed_for(context)
        self._reset_bar_cache_if_needed(context, feed)
        universe = await self._refresh_universe(context, now)
        managed = self._managed_positions(positions, recent_orders)
        managed_symbols = [
            str(position.get("symbol") or "").upper()
            for position in managed
        ]
        symbols = list(
            dict.fromkeys(
                [
                    *universe,
                    *self.settings.extended_equity_confirmation_symbols,
                    *managed_symbols,
                ]
            )
        )
        if not symbols:
            self.last_decision = "extended-equity universe empty"
            return {"action": "hold", "reason": self.last_decision}

        latest_bars, quotes = await asyncio.gather(
            self.market_data.latest_bars_many(symbols, feed=feed),
            self.market_data.latest_quotes_many(symbols, feed=feed),
        )
        self._append_latest_bars(latest_bars)

        block_reason = self._entry_block_reason(context, now)
        await self._cancel_stale_entry_orders(
            open_orders,
            now,
            force=block_reason is not None,
        )
        if block_reason is not None:
            open_orders = await self.client.open_orders()

        exits = await self._manage_positions(
            positions,
            recent_orders,
            open_orders,
            quotes,
            context,
            now,
        )
        self.last_exit_results = exits
        if any(
            item.get("action") in {"submitted", "replaced"}
            for item in exits
        ):
            self.last_decision = (
                f"extended position management submitted {len(exits)} exit action(s)"
            )
            return {
                "action": "submitted",
                "reason": self.last_decision,
                "exits": exits,
            }

        scan: dict[str, dict[str, Any]] = {}
        candidates: list[Signal] = []
        position_symbols = {
            str(position.get("symbol") or "").upper()
            for position in positions
            if _d(position.get("qty")) > 0
        }
        open_order_symbols = {
            str(order.get("symbol") or "").upper()
            for order in open_orders
            if self._is_extended_order(order)
        }
        confirmation_set = set(self.settings.extended_equity_confirmation_symbols)
        for symbol in universe:
            if symbol in confirmation_set and len(universe) > len(confirmation_set):
                # Benchmarks may still trade when explicitly configured, but
                # prefer independent candidates when the universe has them.
                pass
            signal = self._evaluate(
                symbol,
                quotes.get(symbol, {}),
                context,
                now,
                has_position=symbol in position_symbols,
                has_open_order=symbol in open_order_symbols,
            )
            scan[symbol] = {
                "action": signal.action,
                "symbol": signal.symbol,
                "reference_price": str(signal.reference_price),
                "stop_price": str(signal.stop_price),
                "take_profit_price": str(signal.take_profit_price),
                "reason": signal.reason,
                "metadata": signal.metadata,
            }
            if signal.action == "buy":
                candidates.append(signal)

        self.last_scan = scan
        self._event(
            action="scan",
            message=(
                f"{context.session.value} scan: {len(scan)} observed, "
                f"{len(candidates)} qualified"
            ),
            payload={
                "session": context.session.value,
                "qualified_symbols": [signal.symbol for signal in candidates],
            },
            now=now,
        )

        if block_reason is not None:
            self.last_decision = f"new entries blocked: {block_reason}"
            return {
                "action": "hold",
                "reason": self.last_decision,
                "qualified_symbols": [signal.symbol for signal in candidates],
            }

        if not candidates:
            self.last_decision = (
                f"{context.session.value} scanner found no qualified entries"
            )
            return {"action": "hold", "reason": self.last_decision}

        candidates.sort(
            key=lambda signal: (
                _d((signal.metadata or {}).get("momentum_pct"))
                - _d(
                    ((signal.metadata or {}).get("market_quality") or {}).get(
                        "spread_pct"
                    )
                )
            ),
            reverse=True,
        )

        if not self.execution_authorized:
            self.last_decision = (
                f"{len(candidates)} extended candidate(s) qualified; "
                "execution remains gated"
            )
            return {
                "action": "qualified",
                "reason": self.last_decision,
                "qualified_symbols": [signal.symbol for signal in candidates],
            }

        if not self.state.startup_reconciled:
            self.last_decision = "extended entries blocked pending startup reconciliation"
            return {"action": "blocked", "reason": self.last_decision}
        if not self.state.reconciliation_safe:
            self.last_decision = "extended entries blocked by reconciliation state"
            return {"action": "blocked", "reason": self.last_decision}
        if not self.state.entries_enabled:
            self.last_decision = "extended entries blocked because entries are disabled"
            return {"action": "blocked", "reason": self.last_decision}
        if self.state.paused:
            self.last_decision = "extended entries blocked because RHEN is paused"
            return {"action": "blocked", "reason": self.last_decision}

        entries_session = self._entry_count(recent_orders, context, now)
        simulated_positions = list(positions)
        simulated_account = dict(account)
        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        for signal in candidates:
            signal.notional = calculate_entry_notional(
                self.settings,
                simulated_account,
                simulated_positions,
                stop_pct_override=self.settings.extended_equity_stop_pct,
            )
            if signal.notional <= 0:
                skipped.append(
                    {
                        "symbol": signal.symbol,
                        "reason": "shared capital allocator produced no eligible notional",
                    }
                )
                continue
            signal.metadata = dict(signal.metadata or {})
            signal.metadata["sizing"] = sizing_snapshot(
                self.settings,
                simulated_account,
                simulated_positions,
                stop_pct_override=self.settings.extended_equity_stop_pct,
            )
            decision = validate_extended_buy(
                self.settings,
                signal.symbol,
                signal.notional,
                simulated_account,
                simulated_positions,
                entries_session + len(submitted),
                entry_symbols=set(universe),
                stop_pct_override=self.settings.extended_equity_stop_pct,
            )
            if not decision.allowed:
                skipped.append({"symbol": signal.symbol, "reason": decision.reason})
                continue

            qty = (
                signal.notional / signal.reference_price
                if signal.reference_price > 0 else Decimal("0")
            ).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            if qty <= 0:
                skipped.append(
                    {"symbol": signal.symbol, "reason": "calculated quantity is zero"}
                )
                continue

            result = await self._submit_entry(
                signal,
                qty,
                context,
                now,
            )
            if result.get("action") == "submitted":
                submitted.append(result)
                simulated_positions.append(
                    {
                        "symbol": signal.symbol,
                        "qty": str(qty),
                        "market_value": str(signal.notional),
                        "risk_stop_pct": str(self.settings.extended_equity_stop_pct),
                    }
                )
                simulated_cash = _d(simulated_account.get("cash")) - signal.notional
                simulated_account["cash"] = str(max(simulated_cash, Decimal("0")))
                # Submit at most one new extended entry per cycle. The normal
                # 30-second cadence can admit another only after broker state
                # and portfolio risk have been re-read.
                break
            skipped.append(
                {
                    "symbol": signal.symbol,
                    "reason": str(result.get("reason") or "entry submission failed"),
                }
            )

        if submitted:
            self.last_decision = (
                f"submitted extended entry for {submitted[0]['symbol']} "
                f"during {context.session.value}"
            )
            return {
                "action": "submitted",
                "reason": self.last_decision,
                "orders": submitted,
                "skipped": skipped,
            }

        self.last_decision = "qualified extended signals found but none were eligible"
        return {
            "action": "hold",
            "reason": self.last_decision,
            "skipped": skipped,
        }
