from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import Settings
from .crypto_layer import (
    CryptoMarketDataClient,
    CryptoRollingMomentumStrategy,
    CryptoUniverse,
    enrich_crypto_signal_market_state,
)
from .persistence import TradingEventSink
from .risk import validate_crypto_buy, validate_crypto_sell_to_flat
from .state import RuntimeState
from .strategy import Signal


NY = ZoneInfo("America/New_York")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


class CryptoExecutionEngine:
    """Independent 24/7 execution engine for Alpaca spot crypto."""

    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        market_data: CryptoMarketDataClient,
        strategy: Any,
        state: RuntimeState,
        universe: CryptoUniverse,
        ledger: TradingEventSink | None = None,
    ):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.strategy = strategy
        self.state = state
        self.universe = universe
        self.ledger = ledger
        self._direct_btc_history: list[dict[str, Any]] = []

    def _direct_btc_mode(self) -> bool:
        return self.settings.crypto_execution_mode == "btc_direct_paper"

    def _merge_direct_btc_history(
        self,
        fresh: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for row in [*self._direct_btc_history, *fresh]:
            stamp = str(row.get("t") or row.get("timestamp") or "")
            if stamp:
                rows[stamp] = row
        ordered = [rows[key] for key in sorted(rows)]
        self._direct_btc_history = ordered[-7000:]
        return self._direct_btc_history

    @staticmethod
    def _is_crypto_symbol(symbol: str) -> bool:
        return "/" in str(symbol or "")

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        return (
            symbol.strip().lower()
            .replace("/", "-")
            .replace(" ", "")
        )

    def _client_order_id(self, symbol: str, action: str) -> str:
        return (
            f"anevum-crypto-{self._safe_symbol(symbol)}-{action}-"
            f"{self.settings.order_owner_tag}-{uuid4().hex[:10]}"
        )[:128]

    @staticmethod
    def _qty_for_notional(notional: Decimal, price: Decimal) -> Decimal:
        if price <= 0:
            raise ValueError("crypto reference price must be positive")
        return (notional / price).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )

    @staticmethod
    def _price(value: Decimal) -> Decimal:
        if value <= 0:
            return Decimal("0")
        return value.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)

    @staticmethod
    def _stamp(order: dict[str, Any]) -> datetime | None:
        raw = (
            order.get("filled_at")
            or order.get("submitted_at")
            or order.get("created_at")
        )
        if not raw:
            return None
        try:
            return datetime.fromisoformat(
                str(raw).replace("Z", "+00:00")
            ).astimezone(NY)
        except ValueError:
            return None

    @classmethod
    def _crypto_positions(cls, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            position
            for position in positions
            if cls._is_crypto_symbol(str(position.get("symbol", "")))
            and _d(position.get("qty")) > 0
        ]

    @classmethod
    def _bot_crypto_orders(cls, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            order
            for order in orders
            if str(order.get("client_order_id", "")).startswith("anevum-crypto-")
        ]

    @classmethod
    def _owned_symbols(
        cls,
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
    ) -> set[str]:
        position_symbols = {
            str(position.get("symbol", "")).upper()
            for position in cls._crypto_positions(positions)
        }
        bought = {
            str(order.get("symbol", "")).upper()
            for order in cls._bot_crypto_orders(recent_orders)
            if str(order.get("side", "")).lower() == "buy"
            and str(order.get("status", "")).lower() in {
                "accepted", "new", "partially_filled", "filled"
            }
        }
        return position_symbols & bought

    @classmethod
    def _entries_24h(
        cls,
        orders: list[dict[str, Any]],
        now: datetime,
    ) -> int:
        floor = now - timedelta(hours=24)
        count = 0
        for order in cls._bot_crypto_orders(orders):
            if str(order.get("side", "")).lower() != "buy":
                continue
            stamp = cls._stamp(order)
            if stamp is not None and stamp >= floor:
                count += 1
        return count

    @classmethod
    def _latest_entry(
        cls,
        orders: list[dict[str, Any]],
        symbol: str,
    ) -> datetime | None:
        normalized = symbol.upper()
        stamps = [
            cls._stamp(order)
            for order in cls._bot_crypto_orders(orders)
            if str(order.get("symbol", "")).upper() == normalized
            and str(order.get("side", "")).lower() == "buy"
        ]
        stamps = [stamp for stamp in stamps if stamp is not None]
        return max(stamps) if stamps else None

    @classmethod
    def _latest_exit(
        cls,
        orders: list[dict[str, Any]],
        symbol: str,
    ) -> datetime | None:
        normalized = symbol.upper()
        stamps = [
            cls._stamp(order)
            for order in cls._bot_crypto_orders(orders)
            if str(order.get("symbol", "")).upper() == normalized
            and str(order.get("side", "")).lower() == "sell"
            and str(order.get("status", "")).lower() == "filled"
        ]
        stamps = [stamp for stamp in stamps if stamp is not None]
        return max(stamps) if stamps else None

    @classmethod
    def _protective_order(
        cls,
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> dict[str, Any] | None:
        normalized = symbol.upper()
        for order in cls._bot_crypto_orders(open_orders):
            if (
                str(order.get("symbol", "")).upper() == normalized
                and str(order.get("side", "")).lower() == "sell"
                and "-hardstop-" in str(order.get("client_order_id", ""))
                and str(order.get("status", "")).lower() not in {
                    "canceled", "expired", "rejected", "filled"
                }
            ):
                return order
        return None

    async def _cancel_protective(
        self,
        open_orders: list[dict[str, Any]],
        symbol: str,
    ) -> None:
        protective = self._protective_order(open_orders, symbol)
        if protective and protective.get("id"):
            await self.client.cancel_order(str(protective["id"]))

    async def _ensure_protection(
        self,
        position: dict[str, Any],
        open_orders: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        symbol = str(position.get("symbol", "")).upper()
        if self._protective_order(open_orders, symbol):
            return {"action": "protected", "symbol": symbol}

        qty = _d(position.get("qty"))
        entry = _d(position.get("avg_entry_price"))
        if qty <= 0 or entry <= 0:
            return {
                "action": "hold",
                "symbol": symbol,
                "reason": "crypto position protection inputs unavailable",
            }

        stop_pct = _d(
            getattr(self.strategy, "hard_stop_pct", self.settings.crypto_stop_pct)
        )
        stop = self._price(entry * (Decimal("1") - stop_pct))
        limit = self._price(
            stop * (Decimal("1") - self.settings.crypto_stop_limit_buffer_pct)
        )
        client_order_id = self._client_order_id(symbol, "hardstop")
        try:
            order = await self.client.submit_crypto_stop_limit_sell(
                symbol=symbol,
                qty=str(qty),
                stop_price=str(stop),
                limit_price=str(limit),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            self.state.crypto_last_error = f"{type(exc).__name__}: {exc}"
            self.state.record_event(
                kind="crypto_protection",
                symbol=symbol,
                action="error",
                message="crypto protective stop-limit submission failed",
                reason=self.state.crypto_last_error,
                at=now,
                payload={
                    "market": "crypto",
                    "stop_price": str(stop),
                    "limit_price": str(limit),
                },
                correlation_id=self.state.crypto_current_correlation_id,
            )
            return {
                "action": "error",
                "symbol": symbol,
                "reason": self.state.crypto_last_error,
            }

        if self.ledger is not None:
            self.ledger.record_broker_order(
                order,
                correlation_id=self.state.crypto_current_correlation_id,
                exit_reason="crypto protective stop-limit",
            )
        self.state.record_event(
            kind="crypto_protection",
            symbol=symbol,
            action="submitted",
            message="crypto protective stop-limit submitted",
            at=now,
            payload={
                "market": "crypto",
                "stop_price": str(stop),
                "limit_price": str(limit),
                "broker_order_id": order.get("id"),
            },
            correlation_id=self.state.crypto_current_correlation_id,
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "order": order,
        }

    async def _exit_position(
        self,
        account: dict[str, Any],
        position: dict[str, Any],
        open_orders: list[dict[str, Any]],
        *,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        symbol = str(position.get("symbol", "")).upper()
        decision = validate_crypto_sell_to_flat(
            self.settings,
            symbol,
            account,
            position,
        )
        if not decision.allowed:
            return {
                "action": "blocked",
                "symbol": symbol,
                "reason": decision.reason,
            }

        qty = _d(position.get("qty"))
        await self._cancel_protective(open_orders, symbol)
        client_order_id = self._client_order_id(symbol, "sell")
        refs = None
        if self.ledger is not None:
            refs = self.ledger.persist_exit_intent(
                symbol=symbol,
                qty=str(qty),
                client_order_id=client_order_id,
                exit_reason=reason,
                correlation_id=self.state.crypto_current_correlation_id,
                intended_at=now,
                exit_metadata={"market": "crypto", "session_model": "24x7"},
            )
        order = await self.client.submit_crypto_market_sell(
            symbol=symbol,
            qty=str(qty),
            client_order_id=client_order_id,
        )
        if self.ledger is not None:
            self.ledger.record_broker_order(
                order,
                intent_id=(refs or {}).get("intent_id"),
                exit_id=(refs or {}).get("exit_id"),
                exit_reason=reason,
                correlation_id=self.state.crypto_current_correlation_id,
            )
        self.state.crypto_last_order = order
        self.state.crypto_last_execution_at = now
        self.state.record_event(
            kind="crypto_execution",
            symbol=symbol,
            action="sell",
            message=reason,
            at=now,
            payload={"market": "crypto", "order": order},
            correlation_id=self.state.crypto_current_correlation_id,
        )
        return {
            "action": "submitted",
            "symbol": symbol,
            "reason": reason,
            "order": order,
        }

    async def _manage_positions(
        self,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        bars: dict[str, list[dict[str, Any]]],
        now: datetime,
        regime_bars: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        owned = self._owned_symbols(positions, recent_orders)
        results: list[dict[str, Any]] = []
        for position in self._crypto_positions(positions):
            symbol = str(position.get("symbol", "")).upper()
            if symbol not in owned:
                continue
            entry = _d(position.get("avg_entry_price"))
            current = _d(position.get("current_price"))
            if current <= 0:
                symbol_bars = bars.get(symbol, [])
                if symbol_bars:
                    current = _d(symbol_bars[-1].get("c"))
            if entry <= 0 or current <= 0:
                continue

            entry_time = self._latest_entry(recent_orders, symbol)
            held_minutes = (
                max((now - entry_time).total_seconds() / 60, 0)
                if entry_time is not None else None
            )
            return_pct = (current - entry) / entry
            stop_pct = _d(
                getattr(self.strategy, "hard_stop_pct", self.settings.crypto_stop_pct)
            )
            target_pct = _d(
                getattr(self.strategy, "take_profit_pct", self.settings.crypto_target_pct)
            )
            strategy_max_hold_minutes = int(
                getattr(
                    self.strategy,
                    "max_hold_minutes",
                    self.settings.crypto_max_hold_minutes,
                )
                or 0
            )
            exit_reason = None
            if return_pct <= -stop_pct:
                exit_reason = (
                    f"crypto software stop triggered at {return_pct:.6f}"
                )
            elif target_pct > 0 and return_pct >= target_pct:
                exit_reason = (
                    f"crypto target triggered at {return_pct:.6f}"
                )
            elif (
                strategy_max_hold_minutes > 0
                and held_minutes is not None
                and held_minutes >= strategy_max_hold_minutes
            ):
                exit_reason = (
                    f"crypto max hold reached at {held_minutes:.1f} minutes"
                )
            elif bool(getattr(self.strategy, "manages_position_exits", False)):
                exit_reason = self.strategy.position_exit_reason(
                    bars.get(symbol, []),
                    (regime_bars or {}).get(symbol, []),
                    now=now,
                )

            if self.ledger is not None:
                self.ledger.record_position_metrics(
                    symbol=symbol,
                    metrics={
                        "market": "crypto",
                        "session_model": "24x7",
                        "entry_price": str(entry),
                        "current_price": str(current),
                        "current_return_pct": str(return_pct),
                        "held_minutes": held_minutes,
                        "risk_stop_pct": str(stop_pct),
                        "target_pct": str(target_pct) if target_pct > 0 else None,
                        "source": "crypto_live_position_snapshot",
                    },
                    correlation_id=self.state.crypto_current_correlation_id,
                    observed_at=now,
                )

            if exit_reason:
                results.append(
                    await self._exit_position(
                        account,
                        position,
                        open_orders,
                        reason=exit_reason,
                        now=now,
                    )
                )
                continue

            protection = await self._ensure_protection(position, open_orders, now)
            results.append(protection)
        return results

    async def run_once(self) -> dict[str, Any]:
        now = datetime.now(NY)
        self.state.crypto_last_error = None
        self.state.crypto_last_execution_context = {}

        if self.state.paused:
            self.state.crypto_last_decision = "runtime paused"
            return {"action": "hold", "reason": self.state.crypto_last_decision}
        if not self.settings.crypto_lane_enabled:
            self.state.crypto_last_decision = "crypto lane disabled"
            return {"action": "hold", "reason": self.state.crypto_last_decision}

        direct_btc = self._direct_btc_mode()
        if direct_btc and not self.settings.btc_direct_paper_authorized:
            self.state.crypto_last_decision = (
                "BTC direct paper execution is not explicitly authorized"
            )
            return {"action": "blocked", "reason": self.state.crypto_last_decision}

        account, positions, open_orders, recent_orders = await asyncio.gather(
            self.client.account(),
            self.client.positions(),
            self.client.open_orders(),
            self.client.recent_orders(limit=100),
        )

        active_symbols = (
            ["BTC/USD"]
            if direct_btc
            else list(await self.universe.active_symbols(now=now))
        )
        owned_symbols = self._owned_symbols(positions, recent_orders)
        crypto_positions = self._crypto_positions(positions)
        self.state.crypto_active_positions = len(crypto_positions)
        self.state.crypto_aggregate_exposure = str(sum(
            (abs(_d(position.get("market_value"))) for position in crypto_positions),
            Decimal("0"),
        ))
        self.state.crypto_recent_orders = self._bot_crypto_orders(recent_orders)[:20]
        self.state.crypto_execution_healthy = True
        data_symbols = list(dict.fromkeys([
            *active_symbols,
            *([] if direct_btc else self.settings.crypto_confirmation_symbols),
            *owned_symbols,
        ]))
        if direct_btc:
            bars_request = self.market_data.bars_many(
                data_symbols,
                timeframe=getattr(self.strategy, "timeframe", "4Hour"),
                lookback_minutes=int(
                    getattr(
                        self.strategy,
                        "required_history_minutes",
                        14 * 24 * 60,
                    )
                ),
            )
            regime_request = self.market_data.bars_many(
                ["BTC/USD"],
                timeframe=getattr(self.strategy, "regime_timeframe", "1Day"),
                lookback_minutes=int(
                    getattr(
                        self.strategy,
                        "regime_history_minutes",
                        270 * 24 * 60,
                    )
                ),
            )
            bars, quotes, regime_bars = await asyncio.gather(
                bars_request,
                self.market_data.latest_quotes(active_symbols),
                regime_request,
            )
        else:
            bars, quotes = await asyncio.gather(
                self.market_data.bars_many(data_symbols),
                self.market_data.latest_quotes(active_symbols),
            )
            regime_bars = {}

        managed = await self._manage_positions(
            account,
            positions,
            open_orders,
            recent_orders,
            bars,
            now,
            regime_bars=regime_bars,
        )
        if any(item.get("action") == "submitted" and item.get("order") for item in managed):
            self.state.crypto_last_decision = "crypto position management submitted an order"
            return {
                "action": "submitted",
                "reason": self.state.crypto_last_decision,
                "results": managed,
            }

        scan: dict[str, dict[str, Any]] = {}
        buy_signals: list[Signal] = []
        for symbol in active_symbols:
            signal = self.strategy.evaluate(
                bars=bars.get(symbol, []),
                confirmation_bars=(
                    regime_bars
                    if direct_btc
                    else {
                        confirmation: bars.get(confirmation, [])
                        for confirmation in self.settings.crypto_confirmation_symbols
                    }
                ),
                symbol=symbol,
                has_position=symbol in owned_symbols,
                order_notional=self.settings.crypto_order_notional,
                now=now,
            )
            quote = quotes.get(symbol, {})
            if not direct_btc:
                signal = enrich_crypto_signal_market_state(
                    signal,
                    bars=bars.get(symbol, []),
                    quote=quote,
                    now=now,
                    settings=self.settings,
                )
            self.state.crypto_last_market_data_at = now
            bid = _d(quote.get("bp"))
            ask = _d(quote.get("ap"))
            midpoint = (bid + ask) / Decimal("2") if bid > 0 and ask > 0 else Decimal("0")
            spread_pct = (
                (ask - bid) / midpoint
                if midpoint > 0 and ask >= bid
                else Decimal("999")
            )
            signal.metadata["market"] = "crypto"
            signal.metadata["session_model"] = "24x7"
            if direct_btc:
                signal.metadata["execution_class"] = "BTC_DIRECT_PAPER"
                signal.metadata["live_execution_authorized"] = False
            raw_features = dict((signal.metadata.get("feature_state") or {}).get("raw") or {})
            if direct_btc:
                quote_stamp = quote.get("t")
                quote_age_ms = None
                if quote_stamp:
                    try:
                        quote_at = datetime.fromisoformat(
                            str(quote_stamp).replace("Z", "+00:00")
                        ).astimezone(NY)
                        quote_age_ms = max(
                            (now - quote_at).total_seconds() * 1000,
                            0,
                        )
                    except ValueError:
                        quote_age_ms = None
                raw_features = {
                    "quote_age_ms": quote_age_ms,
                    "available_depth": str(
                        min(_d(quote.get("bs")), _d(quote.get("as")))
                    ),
                }
            signal.metadata["market_quality"] = {
                "bid": str(bid) if bid > 0 else None,
                "ask": str(ask) if ask > 0 else None,
                "midpoint": str(midpoint) if midpoint > 0 else None,
                "spread_pct": str(spread_pct),
                "spread_bps": raw_features.get("spread_bps"),
                "available_depth": raw_features.get("available_depth"),
                "trade_volume": raw_features.get("trade_volume"),
                "trade_count": raw_features.get("trade_count"),
                "quote_age_ms": raw_features.get("quote_age_ms"),
                "quote_timestamp": quote.get("t"),
            }
            quote_age_ms = raw_features.get("quote_age_ms")
            available_depth = _d(raw_features.get("available_depth"))
            trade_activity = _d(
                raw_features.get("trade_count")
                if raw_features.get("trade_count") is not None
                else raw_features.get("trade_volume")
            )
            if (
                signal.action == "buy"
                and (
                    quote_age_ms is None
                    or float(quote_age_ms) > self.settings.crypto_max_quote_age_seconds * 1000
                )
            ):
                signal = Signal(
                    action="hold",
                    symbol=symbol,
                    reason="crypto quote is stale or timestamp unavailable",
                    metadata=signal.metadata,
                )
            if (
                signal.action == "buy"
                and self.settings.crypto_min_quoted_depth > 0
                and available_depth < self.settings.crypto_min_quoted_depth
            ):
                signal = Signal(
                    action="hold",
                    symbol=symbol,
                    reason="crypto quoted depth is below execution threshold",
                    metadata=signal.metadata,
                )
            if (
                signal.action == "buy"
                and self.settings.crypto_min_trade_activity > 0
                and trade_activity < self.settings.crypto_min_trade_activity
            ):
                signal = Signal(
                    action="hold",
                    symbol=symbol,
                    reason="crypto trade activity is below execution threshold",
                    metadata=signal.metadata,
                )
            if signal.action == "buy" and spread_pct > self.settings.crypto_max_spread_pct:
                signal = Signal(
                    action="hold",
                    symbol=symbol,
                    reason="crypto spread exceeds execution threshold",
                    metadata=signal.metadata,
                )
            payload = {
                "action": signal.action,
                "symbol": signal.symbol,
                "notional": str(signal.notional),
                "reference_price": str(signal.reference_price),
                "stop_price": str(signal.stop_price),
                "take_profit_price": str(signal.take_profit_price),
                "reason": signal.reason,
                "metadata": signal.metadata,
            }
            scan[symbol] = payload
            if signal.action == "buy":
                buy_signals.append(signal)

        self.state.record_crypto_scan(scan, at=now)
        entries_24h = self._entries_24h(recent_orders, now)
        prepared: list[tuple[Signal, Decimal, str, dict[str, str] | None]] = []
        errors: list[dict[str, str]] = []
        for signal in buy_signals:
            if not direct_btc:
                if not bool(self.state.crypto_graen_promotion.get("promotion_ready")):
                    errors.append({
                        "symbol": signal.symbol,
                        "reason": "crypto GRAEN promotion gate not satisfied",
                    })
                    continue
                if not self.settings.crypto_calibration_promoted:
                    errors.append({
                        "symbol": signal.symbol,
                        "reason": "crypto ADS calibration has not been promoted",
                    })
                    continue
                ads_state = dict((signal.metadata or {}).get("ads_crypto") or {})
                ads_score = ads_state.get("score")
                if ads_score is None or Decimal(str(ads_score)) < self.settings.crypto_ads_threshold:
                    errors.append({
                        "symbol": signal.symbol,
                        "reason": "crypto ADS threshold not satisfied",
                    })
                    continue
            latest_exit = self._latest_exit(recent_orders, signal.symbol)
            if (
                latest_exit is not None
                and (now - latest_exit).total_seconds()
                < self.settings.crypto_reentry_cooldown_minutes * 60
            ):
                continue

            decision = validate_crypto_buy(
                self.settings,
                signal.symbol,
                self.settings.crypto_order_notional,
                account,
                positions,
                entries_24h + len(prepared),
                entry_symbols=set(active_symbols),
            )
            if not decision.allowed:
                errors.append({"symbol": signal.symbol, "reason": decision.reason})
                continue

            qty = self._qty_for_notional(
                self.settings.crypto_order_notional,
                signal.reference_price,
            )
            if qty <= 0:
                errors.append({"symbol": signal.symbol, "reason": "crypto qty rounded to zero"})
                continue
            client_order_id = self._client_order_id(signal.symbol, "buy")
            refs = None
            if self.ledger is not None:
                try:
                    setattr(signal, "_evidence_decision_scan", scan)
                    refs = await self.ledger.persist_entry_intent(
                        signal=signal,
                        qty=str(qty),
                        client_order_id=client_order_id,
                        correlation_id=self.state.crypto_current_correlation_id,
                        intended_at=now,
                    )
                finally:
                    if hasattr(signal, "_evidence_decision_scan"):
                        delattr(signal, "_evidence_decision_scan")
                if refs is None:
                    errors.append({
                        "symbol": signal.symbol,
                        "reason": "durable crypto entry intent persistence unavailable",
                    })
                    continue
            prepared.append((signal, qty, client_order_id, refs))
            if len(prepared) >= 1:
                break

        result: dict[str, Any]
        if not prepared:
            self.state.crypto_last_decision = (
                "crypto scan completed; no executable entries"
            )
            result = {
                "action": "hold",
                "reason": self.state.crypto_last_decision,
                "errors": errors,
            }
        else:
            signal, qty, client_order_id, refs = prepared[0]
            try:
                order = await self.client.submit_crypto_market_buy(
                    symbol=signal.symbol,
                    qty=str(qty),
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
                    self.state.crypto_last_error = f"{type(exc).__name__}: {exc}"
                    self.state.crypto_last_decision = (
                        "crypto broker submission ambiguous; no additional entry submitted"
                    )
                    self.state.record_event(
                        kind="crypto_execution",
                        symbol=signal.symbol,
                        action="blocked",
                        message=self.state.crypto_last_decision,
                        reason=self.state.crypto_last_error,
                        at=now,
                        payload={"market": "crypto", "client_order_id": client_order_id},
                        correlation_id=self.state.crypto_current_correlation_id,
                    )
                    return {
                        "action": "blocked",
                        "reason": self.state.crypto_last_decision,
                        "error": self.state.crypto_last_error,
                    }
                order = recovered

            if self.ledger is not None:
                self.ledger.record_broker_order(
                    order,
                    intent_id=(refs or {}).get("intent_id"),
                    position_id=(refs or {}).get("position_id"),
                    correlation_id=self.state.crypto_current_correlation_id,
                )
            self.state.crypto_last_order = order
            self.state.crypto_last_execution_at = now
            self.state.crypto_last_decision = (
                f"crypto buy submitted for {signal.symbol}"
            )
            self.state.record_event(
                kind="crypto_execution",
                symbol=signal.symbol,
                action="buy",
                message=self.state.crypto_last_decision,
                at=now,
                payload={"market": "crypto", "order": order},
                correlation_id=self.state.crypto_current_correlation_id,
            )
            result = {
                "action": "submitted",
                "symbol": signal.symbol,
                "reason": self.state.crypto_last_decision,
                "order": order,
            }

        self.state.crypto_last_execution_context = {
            "decision_at": now.isoformat(),
            "execution_mode": self.settings.crypto_execution_mode,
            "execution_class": "BTC_DIRECT_PAPER" if direct_btc else "VALIDATED",
            "active_universe": active_symbols,
            "owned_symbols": sorted(owned_symbols),
            "entries_24h": entries_24h,
            "scan": scan,
            "result": result,
        }
        self.state.crypto_last_execution_at = now

        if self.ledger is not None and self.state.crypto_current_correlation_id:
            self.ledger.record_decision_cycle(
                correlation_id=self.state.crypto_current_correlation_id,
                cycle_started_at=now,
                cycle_ended_at=datetime.now(NY),
                market_is_open=True,
                active_universe=active_symbols,
                scan=scan,
                cycle_outcome=str(result.get("reason") or result.get("action") or ""),
                data_status="ok" if not self.state.crypto_last_error else "degraded",
                degraded=bool(self.state.crypto_last_error),
                error=self.state.crypto_last_error,
                runtime={"market": "crypto", "session_model": "24x7"},
                comparison_context={"market": "crypto"},
                execution_result=result,
            )

        return result
