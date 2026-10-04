from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .crypto_canary import (
    BTC_CANARY_FAMILY,
    BTC_CANARY_SOURCE_CANDIDATE_ID,
    BTC_CANARY_STRATEGY_VERSION_ID,
    BTC_CANARY_SYMBOL,
    BTC_CANARY_TIMEFRAME,
    evaluate_btc_canary_state,
    signal_from_canary_state,
)
from .crypto_execution import CryptoExecutionEngine, NY, _d
from .risk import validate_btc_canary_buy


class BtcCanaryExecutionEngine(CryptoExecutionEngine):
    """BTC-only forward paper canary for the frozen R2H signal.

    This engine deliberately bypasses crypto research-promotion and ADS entry
    gates only inside an explicitly acknowledged paper-only execution mode.
    It never authorizes live-account execution.
    """

    def __init__(self, settings, client, market_data, state, ledger=None):
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.state = state
        self.ledger = ledger
        self.strategy = None
        self.universe = None
        self._circuit_open_reason: str | None = None

    @staticmethod
    def _quote_quality(
        quote: dict[str, Any],
        *,
        now: datetime,
    ) -> tuple[Decimal, Decimal, float | None, dict[str, Any]]:
        bid = _d(quote.get("bp"))
        ask = _d(quote.get("ap"))
        midpoint = (
            (bid + ask) / Decimal("2")
            if bid > 0 and ask > 0 and ask >= bid
            else Decimal("0")
        )
        spread_pct = (
            (ask - bid) / midpoint
            if midpoint > 0
            else Decimal("999")
        )
        age_seconds: float | None = None
        raw_stamp = quote.get("t")
        if raw_stamp:
            try:
                stamp = datetime.fromisoformat(
                    str(raw_stamp).replace("Z", "+00:00")
                )
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age_seconds = max(
                    (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc))
                    .total_seconds(),
                    0.0,
                )
            except ValueError:
                age_seconds = None
        metadata = {
            "bid": str(bid) if bid > 0 else None,
            "ask": str(ask) if ask > 0 else None,
            "midpoint": str(midpoint) if midpoint > 0 else None,
            "spread_pct": str(spread_pct),
            "quote_age_seconds": age_seconds,
            "quote_timestamp": raw_stamp,
        }
        return midpoint, spread_pct, age_seconds, metadata

    async def _ensure_canary_protection(
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
                "action": "error",
                "symbol": symbol,
                "reason": "BTC canary protection inputs unavailable",
            }

        stop = self._price(
            entry * (Decimal("1") - self.settings.btc_canary_stop_pct)
        )
        limit = self._price(
            stop * (Decimal("1") - self.settings.crypto_stop_limit_buffer_pct)
        )
        client_order_id = self._client_order_id(symbol, "canary-hardstop")
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
                kind="btc_canary_protection",
                symbol=symbol,
                action="error",
                message="BTC canary protective stop submission failed",
                reason=self.state.crypto_last_error,
                at=now,
                payload={
                    "market": "crypto",
                    "execution_class": "EXPERIMENTAL_PAPER",
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
                exit_reason="BTC canary protective stop-limit",
            )
        self.state.record_event(
            kind="btc_canary_protection",
            symbol=symbol,
            action="submitted",
            message="BTC canary protective stop submitted",
            at=now,
            payload={
                "market": "crypto",
                "execution_class": "EXPERIMENTAL_PAPER",
                "stop_price": str(stop),
                "limit_price": str(limit),
                "broker_order_id": order.get("id"),
            },
            correlation_id=self.state.crypto_current_correlation_id,
        )
        return {"action": "submitted", "symbol": symbol, "order": order}

    def _record_cycle(
        self,
        *,
        now: datetime,
        scan: dict[str, dict[str, Any]],
        entries_24h: int,
        result: dict[str, Any],
        canary_state: Any,
    ) -> None:
        self.state.crypto_last_execution_context = {
            "decision_at": now.isoformat(),
            "execution_class": "EXPERIMENTAL_PAPER",
            "strategy_version_id": "BTC-CANARY-001",
            "active_universe": [BTC_CANARY_SYMBOL],
            "entries_24h": entries_24h,
            "circuit_open_reason": self._circuit_open_reason,
            "canary_state": canary_state.metadata(),
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
                active_universe=[BTC_CANARY_SYMBOL],
                scan=scan,
                cycle_outcome=str(result.get("reason") or result.get("action") or ""),
                data_status="ok" if not self.state.crypto_last_error else "degraded",
                degraded=bool(self.state.crypto_last_error),
                error=self.state.crypto_last_error,
                runtime={
                    "market": "crypto",
                    "session_model": "24x7",
                    "execution_class": "EXPERIMENTAL_PAPER",
                },
                comparison_context={
                    "market": "crypto",
                    "strategy_family": BTC_CANARY_FAMILY,
                    "strategy_version_id": BTC_CANARY_STRATEGY_VERSION_ID,
                    "source_candidate_id": BTC_CANARY_SOURCE_CANDIDATE_ID,
                    "bar_timeframe": BTC_CANARY_TIMEFRAME,
                },
                execution_result=result,
            )

    async def run_once(self) -> dict[str, Any]:
        now = datetime.now(NY)
        self.state.crypto_last_error = None
        self.state.crypto_last_execution_context = {}

        if self.state.paused:
            return {"action": "hold", "reason": "runtime paused"}
        if not self.settings.crypto_lane_enabled:
            return {"action": "hold", "reason": "crypto lane disabled"}
        if self.settings.trading_mode != "paper":
            return {
                "action": "blocked",
                "reason": "BTC canary is hard-gated to Alpaca paper trading",
            }

        account, positions, open_orders, recent_orders = await asyncio.gather(
            self.client.account(),
            self.client.positions(),
            self.client.open_orders(),
            self.client.recent_orders(limit=100),
        )
        crypto_positions = self._crypto_positions(positions)
        owned_symbols = self._owned_symbols(positions, recent_orders)
        self.state.crypto_active_positions = len(crypto_positions)
        self.state.crypto_aggregate_exposure = str(
            sum(
                (abs(_d(position.get("market_value"))) for position in crypto_positions),
                Decimal("0"),
            )
        )
        self.state.crypto_recent_orders = self._bot_crypto_orders(recent_orders)[:20]
        self.state.crypto_execution_healthy = True

        end = now.astimezone(timezone.utc)
        start = end - timedelta(days=self.settings.btc_canary_history_days)
        bars_by_symbol, quotes = await asyncio.gather(
            self.market_data.historical_bars_many(
                [BTC_CANARY_SYMBOL],
                start=start,
                end=end,
                timeframe="4Hour",
            ),
            self.market_data.latest_quotes([BTC_CANARY_SYMBOL]),
        )
        rows = bars_by_symbol.get(BTC_CANARY_SYMBOL, [])
        quote = quotes.get(BTC_CANARY_SYMBOL, {})
        canary_state = evaluate_btc_canary_state(rows, now=now)
        midpoint, spread_pct, quote_age_seconds, market_quality = self._quote_quality(
            quote,
            now=now,
        )
        self.state.crypto_last_market_data_at = now

        signal = signal_from_canary_state(
            canary_state,
            reference_price=midpoint,
            order_notional=self.settings.btc_canary_order_notional,
            stop_pct=self.settings.btc_canary_stop_pct,
        )
        signal.metadata["market_quality"] = market_quality
        scan_payload = {
            "action": signal.action,
            "symbol": signal.symbol,
            "notional": str(signal.notional),
            "reference_price": str(signal.reference_price),
            "stop_price": str(signal.stop_price),
            "take_profit_price": str(signal.take_profit_price),
            "reason": signal.reason,
            "metadata": signal.metadata,
        }
        scan = {BTC_CANARY_SYMBOL: scan_payload}
        self.state.record_crypto_scan(scan, at=now)
        entries_24h = self._entries_24h(recent_orders, now)

        btc_position = next(
            (
                position
                for position in crypto_positions
                if str(position.get("symbol", "")).upper() == BTC_CANARY_SYMBOL
            ),
            None,
        )
        if btc_position is not None:
            if BTC_CANARY_SYMBOL not in owned_symbols:
                result = {
                    "action": "blocked",
                    "symbol": BTC_CANARY_SYMBOL,
                    "reason": (
                        "BTC position is not owned by RHEN crypto order lineage; "
                        "canary will not manage it"
                    ),
                }
                self._record_cycle(
                    now=now,
                    scan=scan,
                    entries_24h=entries_24h,
                    result=result,
                    canary_state=canary_state,
                )
                return result

            entry = _d(btc_position.get("avg_entry_price"))
            current = _d(btc_position.get("current_price"))
            if current <= 0:
                current = midpoint if midpoint > 0 else canary_state.signal_close
            return_pct = (
                (current - entry) / entry
                if entry > 0 and current > 0
                else Decimal("0")
            )
            if self.ledger is not None:
                self.ledger.record_position_metrics(
                    symbol=BTC_CANARY_SYMBOL,
                    metrics={
                        "market": "crypto",
                        "execution_class": "EXPERIMENTAL_PAPER",
                        "strategy_version_id": "BTC-CANARY-001",
                        "source_candidate_id": (
                            "V14-R2H-BTC-4H-CONSENSUS-1080-1500"
                        ),
                        "entry_price": str(entry),
                        "current_price": str(current),
                        "current_return_pct": str(return_pct),
                        "risk_stop_pct": str(self.settings.btc_canary_stop_pct),
                        "signal_desired_long": canary_state.desired_long,
                        "signal_available": canary_state.available,
                    },
                    correlation_id=self.state.crypto_current_correlation_id,
                    observed_at=now,
                )

            exit_reason = None
            if self._circuit_open_reason:
                exit_reason = (
                    "BTC canary fail-safe flatten: circuit open "
                    f"({self._circuit_open_reason})"
                )
            elif (
                entry > 0
                and current > 0
                and return_pct <= -self.settings.btc_canary_stop_pct
            ):
                exit_reason = (
                    f"BTC canary software catastrophe stop at {return_pct:.6f}"
                )
            elif canary_state.available and not canary_state.desired_long:
                exit_reason = "BTC canary frozen R2H signal returned flat"

            if exit_reason:
                result = await self._exit_position(
                    account,
                    btc_position,
                    open_orders,
                    reason=exit_reason,
                    now=now,
                )
                self._record_cycle(
                    now=now,
                    scan=scan,
                    entries_24h=entries_24h,
                    result=result,
                    canary_state=canary_state,
                )
                return result

            protection = await self._ensure_canary_protection(
                btc_position,
                open_orders,
                now,
            )
            if protection.get("action") == "error":
                self._circuit_open_reason = "protective_stop_unavailable"
                result = await self._exit_position(
                    account,
                    btc_position,
                    open_orders,
                    reason=(
                        "BTC canary fail-safe flatten: protective stop unavailable"
                    ),
                    now=now,
                )
                result["circuit_open_reason"] = self._circuit_open_reason
                self._record_cycle(
                    now=now,
                    scan=scan,
                    entries_24h=entries_24h,
                    result=result,
                    canary_state=canary_state,
                )
                return result

            result = {
                "action": "hold",
                "symbol": BTC_CANARY_SYMBOL,
                "reason": (
                    "BTC canary position protected; waiting for frozen R2H exit"
                    if canary_state.available
                    else "BTC canary signal unavailable; existing position remains protected"
                ),
                "protection": protection,
            }
            self._record_cycle(
                now=now,
                scan=scan,
                entries_24h=entries_24h,
                result=result,
                canary_state=canary_state,
            )
            return result

        if self._circuit_open_reason:
            result = {
                "action": "blocked",
                "reason": f"BTC canary circuit open: {self._circuit_open_reason}",
            }
        elif not self.settings.btc_canary_execution_authorized:
            result = {
                "action": "hold",
                "reason": "BTC canary paper execution is not authorized",
            }
        elif not canary_state.available:
            result = {"action": "hold", "reason": canary_state.reason}
        elif not canary_state.desired_long:
            result = {"action": "hold", "reason": canary_state.reason}
        elif (
            quote_age_seconds is None
            or quote_age_seconds > self.settings.crypto_max_quote_age_seconds
        ):
            result = {
                "action": "blocked",
                "reason": "BTC canary quote is stale or timestamp unavailable",
            }
        elif spread_pct > self.settings.btc_canary_max_spread_pct:
            result = {
                "action": "blocked",
                "reason": "BTC canary spread exceeds experimental threshold",
            }
        else:
            decision = validate_btc_canary_buy(
                self.settings,
                BTC_CANARY_SYMBOL,
                self.settings.btc_canary_order_notional,
                account,
                positions,
                entries_24h,
            )
            if not decision.allowed:
                result = {"action": "blocked", "reason": decision.reason}
            else:
                qty = self._qty_for_notional(
                    self.settings.btc_canary_order_notional,
                    midpoint,
                )
                if qty <= 0:
                    result = {
                        "action": "blocked",
                        "reason": "BTC canary quantity rounded to zero",
                    }
                else:
                    if self.ledger is None or not bool(
                        getattr(self.ledger, "enabled", False)
                    ):
                        result = {
                            "action": "blocked",
                            "reason": (
                                "durable BTC canary entry persistence is not configured"
                            ),
                        }
                        self._record_cycle(
                            now=now,
                            scan=scan,
                            entries_24h=entries_24h,
                            result=result,
                            canary_state=canary_state,
                        )
                        return result

                    client_order_id = self._client_order_id(
                        BTC_CANARY_SYMBOL,
                        "canary-buy",
                    )
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
                        result = {
                            "action": "blocked",
                            "reason": (
                                "durable BTC canary entry intent persistence unavailable"
                            ),
                        }
                        self._record_cycle(
                            now=now,
                            scan=scan,
                            entries_24h=entries_24h,
                            result=result,
                            canary_state=canary_state,
                        )
                        return result

                    try:
                        order = await self.client.submit_crypto_market_buy(
                            symbol=BTC_CANARY_SYMBOL,
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
                                await asyncio.sleep(0.25 * (attempt + 1))
                        if recovered is None:
                            self._circuit_open_reason = "ambiguous_broker_submission"
                            self.state.crypto_last_error = f"{type(exc).__name__}: {exc}"
                            result = {
                                "action": "blocked",
                                "reason": (
                                    "BTC canary broker submission ambiguous; circuit opened"
                                ),
                                "error": self.state.crypto_last_error,
                                "circuit_open_reason": self._circuit_open_reason,
                            }
                            self._record_cycle(
                                now=now,
                                scan=scan,
                                entries_24h=entries_24h,
                                result=result,
                                canary_state=canary_state,
                            )
                            return result
                        order = recovered

                    if self.ledger is not None:
                        self.ledger.record_broker_order(
                            order,
                            intent_id=(refs or {}).get("intent_id"),
                            position_id=(refs or {}).get("position_id"),
                            correlation_id=self.state.crypto_current_correlation_id,
                        )
                    self.state.crypto_last_order = order

                    filled_price = _d(order.get("filled_avg_price"))
                    slippage_pct = (
                        (filled_price - midpoint) / midpoint
                        if filled_price > 0 and midpoint > 0
                        else None
                    )
                    if (
                        slippage_pct is not None
                        and slippage_pct > self.settings.btc_canary_max_slippage_pct
                    ):
                        self._circuit_open_reason = "entry_slippage_threshold_exceeded"

                    self.state.record_event(
                        kind="btc_canary_execution",
                        symbol=BTC_CANARY_SYMBOL,
                        action="buy",
                        message="BTC canary paper buy submitted",
                        at=now,
                        payload={
                            "market": "crypto",
                            "execution_class": "EXPERIMENTAL_PAPER",
                            "strategy_version_id": "BTC-CANARY-001",
                            "source_candidate_id": (
                                "V14-R2H-BTC-4H-CONSENSUS-1080-1500"
                            ),
                            "order": order,
                            "reference_price": str(midpoint),
                            "filled_avg_price": (
                                str(filled_price) if filled_price > 0 else None
                            ),
                            "slippage_pct": (
                                str(slippage_pct) if slippage_pct is not None else None
                            ),
                            "circuit_open_reason": self._circuit_open_reason,
                        },
                        correlation_id=self.state.crypto_current_correlation_id,
                    )
                    result = {
                        "action": "submitted",
                        "symbol": BTC_CANARY_SYMBOL,
                        "reason": "BTC canary paper buy submitted",
                        "order": order,
                        "slippage_pct": (
                            str(slippage_pct) if slippage_pct is not None else None
                        ),
                        "circuit_open_reason": self._circuit_open_reason,
                    }

        self._record_cycle(
            now=now,
            scan=scan,
            entries_24h=entries_24h,
            result=result,
            canary_state=canary_state,
        )
        return result
