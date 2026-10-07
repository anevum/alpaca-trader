from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from foundation.outbox import DurableEventOutbox, FoundationShadowSink

from .config import Settings
from .cash_flow import day_pnl, risk_reference_equity
from .shadow_economics import candidate_shadow_economics
from .shadow_allocation import selected_entry_shadow_allocation
from .research_agent.ads002 import (
    METHODOLOGY_VERSION as ADS002_METHODOLOGY_VERSION,
    pretrade_composite as ads002_pretrade_composite,
    score_attention as ads002_score_attention,
    score_qualification as ads002_score_qualification,
    score_timing as ads002_score_timing,
)
from .research_agent.ads002_v2 import (
    FEATURE_SCHEMA_VERSION as ADS002_V2_FEATURE_SCHEMA_VERSION,
    METHODOLOGY_VERSION as ADS002_V2_METHODOLOGY_VERSION,
    NORMALIZATION_VERSION as ADS002_V2_NORMALIZATION_VERSION,
    score_cycle_v2,
)


class TradingEventSink:
    """Durable, idempotent trading telemetry and canonical-ledger transport."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_sent_at: datetime | None = None
        self.last_error: str | None = None
        self.last_reconcile_at: datetime | None = None
        self.sent_count = 0
        self.shed_count = 0
        self.storage_analytics_shedding = False
        self.dropped_count = 0
        self.foundation_sink: FoundationShadowSink | None = None
        self.foundation_task: asyncio.Task | None = None
        self.foundation_last_error: str | None = None
        self.foundation_delivered_count = 0
        if bool(getattr(settings, "foundation_shadow_enabled", False)):
            self.foundation_sink = FoundationShadowSink(
                outbox=DurableEventOutbox(settings.foundation_outbox_path),
                ingest_url=settings.foundation_ingest_url,
                ingest_token=getattr(settings, "foundation_ingest_token", ""),
            )

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.trading_ingest_url
            and self.settings.trading_ingest_token
            and self.settings.trading_run_id
            and self.settings.strategy_version_id
        )

    @property
    def foundation_enabled(self) -> bool:
        return self.foundation_sink is not None

    def _owns_broker_order(self, order: dict[str, Any]) -> bool:
        client_order_id = str(order.get("client_order_id") or "")
        if not client_order_id.startswith("anevum-"):
            return False
        owner_tag = str(getattr(self.settings, "order_owner_tag", "") or "")
        if not owner_tag:
            return True
        return f"-{owner_tag}-" in client_order_id

    def _owned_order_ids_from_snapshot(
        self,
        orders: list[dict[str, Any]],
    ) -> set[str]:
        owned_ids = {
            str(order.get("id") or "")
            for order in orders
            if order.get("id") and self._owns_broker_order(order)
        }

        changed = True
        while changed:
            changed = False
            for order in orders:
                order_id = str(order.get("id") or "")
                if not order_id or order_id in owned_ids:
                    continue
                replaces = str(order.get("replaces") or "")
                replaced_by = str(order.get("replaced_by") or "")
                if (
                    (replaces and replaces in owned_ids)
                    or (replaced_by and replaced_by in owned_ids)
                ):
                    owned_ids.add(order_id)
                    changed = True

        return owned_ids

    def _protective_stop_lineage_ids(
        self,
        orders: list[dict[str, Any]],
    ) -> set[str]:
        protective_ids = {
            str(order.get("id") or "")
            for order in orders
            if order.get("id")
            and "-hardstop-" in str(order.get("client_order_id") or "")
        }

        changed = True
        while changed:
            changed = False
            for order in orders:
                order_id = str(order.get("id") or "")
                if not order_id or order_id in protective_ids:
                    continue
                replaces = str(order.get("replaces") or "")
                replaced_by = str(order.get("replaced_by") or "")
                if (
                    (replaces and replaces in protective_ids)
                    or (replaced_by and replaced_by in protective_ids)
                ):
                    protective_ids.add(order_id)
                    changed = True

        return protective_ids

    def managed_symbols_from_snapshot(
        self,
        *,
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[str]:
        managed = {
            str(symbol).upper()
            for symbol in (getattr(self.settings, "allowed_symbols", set()) or set())
            if str(symbol).strip()
        }
        all_orders = [*orders, *open_orders]
        owned_order_ids = self._owned_order_ids_from_snapshot(all_orders)
        owned_orders = [
            order
            for order in all_orders
            if str(order.get("id") or "") in owned_order_ids
        ]
        owned_order_ids = {
            str(order.get("id") or "")
            for order in owned_orders
            if order.get("id")
        }
        for order in owned_orders:
            symbol = str(order.get("symbol") or "").upper()
            if symbol:
                managed.add(symbol)
        for activity in fills:
            order_id = str(activity.get("order_id") or "")
            if order_id not in owned_order_ids:
                continue
            symbol = str(activity.get("symbol") or "").upper()
            if symbol:
                managed.add(symbol)
        return sorted(managed)

    @staticmethod
    def _is_standing_protective_stop(
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> bool:
        client_order_id = str(order.get("client_order_id") or "")
        order_id = str(order.get("id") or "")
        return (
            "-hardstop-" in client_order_id
            or bool(protective_order_ids and order_id in protective_order_ids)
        )

    @classmethod
    def _projectable_order(
        cls,
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> bool:
        if not cls._is_standing_protective_stop(order, protective_order_ids):
            return True
        filled_qty = Decimal(str(order.get("filled_qty") or "0"))
        status = str(order.get("status") or "").lower()
        return filled_qty > 0 or status == "filled"

    @classmethod
    def _inferred_exit_reason(
        cls,
        order: dict[str, Any],
        protective_order_ids: set[str] | None = None,
    ) -> str | None:
        if cls._is_standing_protective_stop(order, protective_order_ids):
            return "broker protective stop filled"
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "queued": self.queue.qsize(),
            "sent_count": self.sent_count,
            "shed_count": self.shed_count,
            "storage_analytics_shedding": self.storage_analytics_shedding,
            "dropped_count": self.dropped_count,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "last_reconcile_at": (
                self.last_reconcile_at.isoformat() if self.last_reconcile_at else None
            ),
            "last_error": self.last_error,
            "run_id": self.settings.trading_run_id or None,
            "strategy_version_id": self.settings.strategy_version_id or None,
            "foundation": {
                "enabled": self.foundation_enabled,
                "delivered_count": self.foundation_delivered_count,
                "last_error": self.foundation_last_error,
                "outbox": (
                    self.foundation_sink.outbox.stats()
                    if self.foundation_sink is not None
                    else {
                        "queued": 0,
                        "leased": 0,
                        "max_attempts": 0,
                        "oldest_created_at": None,
                    }
                ),
            },
            "run_started_at": (
                self.settings.trading_run_started_at.isoformat()
                if self.settings.trading_run_started_at
                else None
            ),
        }

    async def start(self) -> None:
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self._run())
        if self.foundation_enabled and self.foundation_task is None:
            self.foundation_task = asyncio.create_task(self._run_foundation())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            self.task = None
        if self.foundation_task is not None:
            try:
                await asyncio.wait_for(self.foundation_task, timeout=5)
            except asyncio.TimeoutError:
                self.foundation_task.cancel()
            self.foundation_task = None

    def _event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
        strategy_version_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event_key": event_key
            or f"{self.settings.trading_run_id}:{event_type}:{uuid4().hex}",
            "run_id": self.settings.trading_run_id,
            "strategy_version_id": (
                strategy_version_id
                if strategy_version_id is not None
                else self.settings.strategy_version_id
            ),
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "symbol": symbol or None,
            "correlation_id": correlation_id,
            "source": "alpaca-trader",
            "payload": payload or {},
        }

    def _mirror_foundation(self, event: dict[str, Any]) -> None:
        if self.foundation_sink is None:
            return
        try:
            self.foundation_sink.enqueue(event)
            self.foundation_last_error = None
        except Exception as exc:
            # Shadow persistence must never alter live execution behavior.
            self.foundation_last_error = f"{type(exc).__name__}: {exc}"

    def emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
        strategy_version_id: str | None = None,
    ) -> None:
        if not self.enabled and not self.foundation_enabled:
            return
        event = self._event(
            event_type=event_type,
            payload=payload,
            symbol=symbol,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            event_key=event_key,
            strategy_version_id=strategy_version_id,
        )
        self._mirror_foundation(event)
        if not self.enabled:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_count += 1
            self.last_error = "event queue full; telemetry event dropped"

    async def emit_critical(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        symbol: str = "",
        correlation_id: str | None = None,
        occurred_at: str | None = None,
        event_key: str | None = None,
        strategy_version_id: str | None = None,
    ) -> bool:
        """Persist before a new entry. Protective exits never depend on this path."""
        if not self.enabled and not self.foundation_enabled:
            return True
        event = self._event(
            event_type=event_type,
            payload=payload,
            symbol=symbol,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            event_key=event_key,
            strategy_version_id=strategy_version_id,
        )
        self._mirror_foundation(event)
        if not self.enabled:
            return True
        async with httpx.AsyncClient(timeout=5.0) as http:
            for attempt in range(3):
                if await self._send_batch(http, [event], require_all=event_type in {
                    "research_daily_report", "research_weekly_report", "extended_research_snapshot"
                }):
                    return True
                await asyncio.sleep(0.25 * (2 ** attempt))
        return False

    def _comparison_configuration(self) -> dict[str, Any]:
        """Non-secret values required to reconstruct the live decision later."""
        s = self.settings

        def value(name: str, default: Any = None) -> Any:
            raw = getattr(s, name, default)
            if isinstance(raw, Decimal):
                return str(raw)
            if isinstance(raw, (tuple, set)):
                return list(raw)
            return raw

        return {
            "strategy_name": value("strategy_name"),
            "fast_window": value("fast_window"),
            "slow_window": value("slow_window"),
            "min_momentum_pct": value("min_momentum_pct"),
            "min_vwap_edge_pct": value("min_vwap_edge_pct"),
            "stop_pct": value("stop_pct"),
            "target_pct": value("target_pct"),
            "entry_start": value("entry_start_raw"),
            "entry_cutoff": value("entry_cutoff_raw"),
            "confirmation_symbols": list(value("confirmation_symbols", ()) or ()),
            "min_confirmations": value("min_confirmations"),
            "regime_window": value("regime_window"),
            "regime_min_confirmations": value("regime_min_confirmations"),
            "regime_min_return_pct": value("regime_min_return_pct"),
            "max_vwap_extension_pct": value("max_vwap_extension_pct"),
            "volatility_stop_enabled": value("volatility_stop_enabled"),
            "volatility_stop_multiplier": value("volatility_stop_multiplier"),
            "volatility_stop_lookback_bars": value("volatility_stop_lookback_bars"),
            "max_dynamic_stop_pct": value("max_dynamic_stop_pct"),
            "max_bar_age_seconds": value("max_bar_age_seconds"),
            "max_spread_pct": value("max_spread_pct"),
            "min_quality_score": value("min_quality_score"),
            "max_pairwise_correlation": value("max_pairwise_correlation"),
            "correlation_lookback_bars": value("correlation_lookback_bars"),
            "correlation_min_observations": value("correlation_min_observations"),
            "loss_streak_limit": value("loss_streak_limit"),
            "loss_streak_cooldown_minutes": value("loss_streak_cooldown_minutes"),
            "reentry_cooldown_minutes": value("reentry_cooldown_minutes"),
            "order_notional": value("order_notional"),
            "sizing_mode": value("sizing_mode"),
            "risk_per_trade_pct": value("risk_per_trade_pct"),
            "max_gross_exposure_pct": value("max_gross_exposure_pct"),
            "min_order_notional": value("min_order_notional"),
            "max_order_notional": value("max_order_notional"),
            "max_position_notional": value("max_position_notional"),
            "max_total_position_notional": value("max_total_position_notional"),
            "portfolio_limit_mode": value("portfolio_limit_mode"),
            "max_position_gross_pct": value("max_position_gross_pct"),
            "max_portfolio_stop_risk_pct": value("max_portfolio_stop_risk_pct"),
            "max_concurrent_positions": value("max_concurrent_positions"),
            "max_new_entries_per_cycle": value("max_new_entries_per_cycle"),
            "max_daily_orders": value("max_daily_orders"),
            "max_daily_loss": value("max_daily_loss"),
            "session_cash_flow_adjustment_raw": value("session_cash_flow_adjustment_raw", ""),
            "dynamic_universe_enabled": value("dynamic_universe_enabled"),
            "allowed_symbols": sorted(value("allowed_symbols", set()) or []),
            "execution_authorized": value("execution_authorized", False),
            "data_feed": value("data_feed"),
            "bar_timeframe": value("bar_timeframe"),
        }

    def _ads002_shadow_candidate(
        self,
        *,
        symbol: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute research-only pretrade components from already-observed inputs.

        This method performs no I/O and its output is never consumed by execution.
        Missing source observations remain explicit instead of being imputed into a
        research-eligible score.
        """
        quality = dict(metadata.get("market_quality") or {})
        confirmations = dict(metadata.get("confirmations") or {})
        regime_confirmations = dict(metadata.get("regime_confirmations") or {})
        independent_count = sum(
            1
            for peer in confirmations
            if str(peer).upper() != symbol.upper()
        )
        regime_count = len(regime_confirmations)

        quote_age_seconds = quality.get("quote_age_seconds")
        quote_age_ms = (
            float(quote_age_seconds) * 1000
            if quote_age_seconds not in {None, ""}
            else None
        )
        feature_vector = {
            "relative_volume_ratio": metadata.get("relative_volume_ratio"),
            "momentum_pct": metadata.get("momentum_pct"),
            "vwap_edge_pct": metadata.get("vwap_edge_pct"),
            "trend_persistence": metadata.get("trend_persistence"),
            "fresh_confirmation_passes": quality.get(
                "fresh_confirmation_passes",
                metadata.get("confirmation_passes"),
            ),
            "independent_confirmation_count": independent_count,
            "regime_confirmation_passes": metadata.get(
                "regime_passes",
                sum(
                    bool((payload or {}).get("ok"))
                    for payload in regime_confirmations.values()
                ),
            ),
            "regime_confirmation_count": regime_count,
            "spread_pct": quality.get("spread_pct"),
            "bar_age_seconds": quality.get("bar_age_seconds"),
            "quote_age_ms": quote_age_ms,
        }
        configuration = self._comparison_configuration()

        attention_ready = all(
            feature_vector.get(key) not in {None, ""}
            for key in (
                "relative_volume_ratio",
                "momentum_pct",
                "trend_persistence",
            )
        )
        qualification_ready = all(
            feature_vector.get(key) not in {None, ""}
            for key in ("momentum_pct", "vwap_edge_pct")
        ) and bool(confirmations or regime_confirmations)
        timing_ready = all(
            feature_vector.get(key) not in {None, ""}
            for key in ("spread_pct", "bar_age_seconds")
        )

        attention = (
            ads002_score_attention(feature_vector, configuration)
            if attention_ready
            else None
        )
        qualification = (
            ads002_score_qualification(feature_vector, configuration)
            if qualification_ready
            else None
        )
        timing = (
            ads002_score_timing(feature_vector, configuration)
            if timing_ready
            else None
        )
        component_scores = {
            "attention": attention,
            "qualification": qualification,
            "timing": timing,
        }
        complete_pretrade = all(
            row is not None for row in component_scores.values()
        )
        composite = (
            ads002_pretrade_composite(
                attention=attention["score"],
                qualification=qualification["score"],
                timing=timing["score"],
            )
            if complete_pretrade
            else None
        )
        missing: list[str] = []
        if not attention_ready:
            missing.append("ATTENTION_INPUTS")
        if not qualification_ready:
            missing.append("QUALIFICATION_INPUTS")
        if not timing_ready:
            missing.append("TIMING_INPUTS")

        return {
            "methodology_version": ADS002_METHODOLOGY_VERSION,
            "research_only": True,
            "feature_vector": feature_vector,
            "source_completeness": {
                "attention": attention_ready,
                "qualification": qualification_ready,
                "timing": timing_ready,
                "pretrade_complete": complete_pretrade,
                "missing_requirements": missing,
            },
            "attention": attention,
            "qualification": qualification,
            "timing": timing,
            "pretrade_composite": composite,
            "legacy_quality_score": metadata.get("quality_score"),
        }

    @staticmethod
    def _ads002_v1_missing_state(
        *,
        metadata: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Preserve an explicit research-only ADS v1 failure without imputing data."""
        quality = dict(metadata.get("market_quality") or {})
        return {
            "methodology_version": ADS002_METHODOLOGY_VERSION,
            "research_only": True,
            "feature_vector": {
                "relative_volume_ratio": metadata.get("relative_volume_ratio"),
                "momentum_pct": metadata.get("momentum_pct"),
                "vwap_edge_pct": metadata.get("vwap_edge_pct"),
                "trend_persistence": metadata.get("trend_persistence"),
                "spread_pct": quality.get("spread_pct"),
                "bar_age_seconds": quality.get("bar_age_seconds"),
                "quote_age_ms": None,
            },
            "source_completeness": {
                "attention": False,
                "qualification": False,
                "timing": False,
                "pretrade_complete": False,
                "missing_requirements": [reason],
            },
            "attention": None,
            "qualification": None,
            "timing": None,
            "pretrade_composite": None,
            "legacy_quality_score": metadata.get("quality_score"),
        }

    def _ads002_shadow_candidate_safe(
        self,
        *,
        symbol: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep research scoring failures from ever influencing live execution."""
        try:
            return self._ads002_shadow_candidate(symbol=symbol, metadata=metadata)
        except Exception as exc:
            return self._ads002_v1_missing_state(
                metadata=metadata,
                reason=f"SCORING_ERROR:{type(exc).__name__}",
            )

    @staticmethod
    def _ads002_v2_missing_state(
        *,
        symbol: str,
        metadata: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Represent unavailable v2 shadow state without inventing ranks or scores."""
        raw_features = dict(metadata.get("ads002_v2_raw_features") or {})
        raw_features["symbol"] = symbol.upper()
        return {
            "methodology_version": ADS002_V2_METHODOLOGY_VERSION,
            "feature_schema_version": ADS002_V2_FEATURE_SCHEMA_VERSION,
            "normalization_version": ADS002_V2_NORMALIZATION_VERSION,
            "research_only": True,
            "execution_authority": False,
            "raw_features": raw_features,
            "attention": {"score": None, "reason": reason},
            "qualification": {"score": None, "reason": reason},
            "timing": {"score": None, "reason": reason},
            "component_ranks": {
                "attention": None,
                "qualification": None,
                "timing": None,
            },
            "confidence": {
                "score": None,
                "state": "UNAVAILABLE",
                "reason": reason,
            },
            "challengers": {},
            "source_completeness": {
                "pretrade_complete": False,
                "historical_time_of_day_baseline_available": any(
                    key.startswith("z_") and value not in {None, ""}
                    for key, value in raw_features.items()
                ),
                "relative_family_complete": False,
                "missing_requirements": [reason],
            },
        }

    def _ads002_v2_shadow_cycle(
        self,
        scan: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Score ADS v2 from the full frozen decision cycle.

        ADS v2 contains cross-sectional ranks, so entry snapshots must use the
        complete decision-time scan rather than scoring an entry in isolation.
        This path is telemetry-only and never changes execution inputs.
        """
        ordered: list[tuple[str, dict[str, Any]]] = []
        inputs: list[dict[str, Any]] = []
        for scan_symbol, payload in scan.items():
            if not isinstance(payload, dict):
                continue
            symbol = str(payload.get("symbol") or scan_symbol or "").upper()
            if not symbol:
                continue
            metadata = dict(payload.get("metadata") or {})
            ordered.append((symbol, metadata))
            inputs.append(
                {
                    "symbol": symbol,
                    "raw_features": dict(
                        metadata.get("ads002_v2_raw_features") or {}
                    ),
                }
            )

        if not inputs:
            return {}

        try:
            scores = score_cycle_v2(
                inputs,
                self._comparison_configuration(),
            )
        except Exception as exc:
            reason = f"SCORING_ERROR:{type(exc).__name__}"
            return {
                symbol: self._ads002_v2_missing_state(
                    symbol=symbol,
                    metadata=metadata,
                    reason=reason,
                )
                for symbol, metadata in ordered
            }

        by_symbol: dict[str, dict[str, Any]] = {}
        for (symbol, metadata), raw_score in zip(ordered, scores):
            score = dict(raw_score or {})
            completeness = dict(score.get("source_completeness") or {})
            missing: list[str] = []
            if completeness.get("pretrade_complete") is not True:
                for component_name in ("attention", "qualification", "timing"):
                    component = score.get(component_name) or {}
                    if component.get("score") is None:
                        missing.append(
                            str(component.get("reason") or f"{component_name.upper()}_INPUTS")
                        )
                if not missing:
                    missing.append("PRETRADE_INPUTS")
            completeness["missing_requirements"] = missing
            score["source_completeness"] = completeness
            by_symbol[symbol] = score or self._ads002_v2_missing_state(
                symbol=symbol,
                metadata=metadata,
                reason="SCORING_RESULT_UNAVAILABLE",
            )
        return by_symbol

    def _shadow_economics_safe(
        self,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep shadow-economics failures isolated from live execution/evidence."""
        try:
            return candidate_shadow_economics(
                target_pct=self.settings.target_pct,
                metadata=metadata,
            )
        except Exception as exc:
            return {
                "schema_version": "shadow_opportunity_economics.v1",
                "methodology_version": "rhen-shadow-economics-v1",
                "research_only": True,
                "execution_authority": False,
                "changes_live_decision": False,
                "error": f"{type(exc).__name__}: {exc}",
                "shadow_admission": {
                    "would_admit": None,
                    "reason": "shadow_economics_unavailable",
                },
            }

    def _shadow_allocation_safe(
        self,
        *,
        signal: Any,
        shadow_economics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Isolate counterfactual allocation from the live execution path."""
        try:
            return selected_entry_shadow_allocation(
                symbol=signal.symbol,
                live_safe_notional=signal.notional,
                min_order_notional=self.settings.min_order_notional,
                expected_holding_minutes=self.settings.max_hold_minutes,
                shadow_economics=shadow_economics,
            )
        except Exception as exc:
            return {
                "schema_version": "shadow_capital_allocation.v1",
                "methodology_version": "rhen-shadow-allocation-v1",
                "research_only": True,
                "execution_authority": False,
                "changes_live_decision": False,
                "bounded_by_live_safe_notional": True,
                "would_allocate": None,
                "reason": "shadow_allocation_unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _candidate_final_decision(
        symbol: str,
        qualified: bool,
        execution_result: dict[str, Any] | None,
    ) -> str:
        if not qualified:
            return "rejected"
        result = execution_result or {}
        normalized = symbol.upper()
        for order in result.get("orders") or []:
            if str(order.get("symbol") or "").upper() == normalized:
                return "submitted"
        for skipped in result.get("skipped") or []:
            if str(skipped.get("symbol") or "").upper() == normalized:
                return "qualified_not_selected"
        action = str(result.get("action") or "").lower()
        if action in {"blocked", "hold", "error"}:
            return action
        return "qualified"

    def record_decision_cycle(
        self,
        *,
        correlation_id: str,
        cycle_started_at: datetime,
        cycle_ended_at: datetime,
        market_is_open: bool | None,
        active_universe: list[str],
        scan: dict[str, dict[str, Any]],
        cycle_outcome: str,
        data_status: str = "ok",
        degraded: bool = False,
        error: str | None = None,
        runtime: dict[str, Any] | None = None,
        comparison_context: dict[str, Any] | None = None,
        execution_result: dict[str, Any] | None = None,
    ) -> None:
        """Persist one complete strategy-evaluation cycle without affecting execution."""
        duration_ms = max(
            int((cycle_ended_at - cycle_started_at).total_seconds() * 1000),
            0,
        )
        if market_is_open is None:
            market_is_open = (
                cycle_outcome != "market is closed"
                if cycle_outcome
                else None
            )
        cycle_key = f"{self.settings.trading_run_id}:{correlation_id}"
        candidates: list[dict[str, Any]] = []
        embedded_comparison_context: dict[str, Any] = {}
        qualified_count = 0
        ads002_v2_by_symbol = self._ads002_v2_shadow_cycle(scan)
        for rank, (symbol, signal) in enumerate(scan.items(), start=1):
            metadata = dict(signal.get("metadata") or {})
            candidate_strategy_version_id = self.settings.strategy_version_id
            candidate_data_feed = getattr(self.settings, "data_feed", None)
            candidate_comparison_context = metadata.pop("_comparison_context", None)
            if isinstance(candidate_comparison_context, dict) and not embedded_comparison_context:
                embedded_comparison_context = candidate_comparison_context
            quality = dict(metadata.get("market_quality") or {})
            bid = quality.get("bid")
            ask = quality.get("ask")
            midpoint = None
            try:
                if bid not in {None, ""} and ask not in {None, ""}:
                    midpoint = str((Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2"))
            except Exception:
                midpoint = None
            qualified = str(signal.get("action") or "").lower() == "buy"
            qualified_count += int(qualified)
            reason = str(signal.get("reason") or "")
            ads002_shadow = self._ads002_shadow_candidate_safe(
                symbol=symbol,
                metadata=metadata,
            )
            shadow_economics = self._shadow_economics_safe(metadata)
            candidates.append(
                {
                    "symbol": symbol.upper(),
                    "candidate_key": f"{cycle_key}:{symbol.upper()}",
                    "observed_at": cycle_ended_at.isoformat(),
                    "action": str(signal.get("action") or "hold"),
                    "qualified": qualified,
                    "candidate_state": "qualified" if qualified else "rejected",
                    "qualification_status": "qualified" if qualified else "rejected",
                    "final_decision": self._candidate_final_decision(
                        symbol,
                        qualified,
                        execution_result,
                    ),
                    "reason": reason or None,
                    "rejection_reasons": [] if qualified else ([reason] if reason else []),
                    "rejection_reason_codes": [] if qualified else ([reason] if reason else []),
                    "candidate_rank": rank if qualified else None,
                    "data_quality_state": (
                        "unavailable"
                        if "missing" in reason.lower() or "not enough" in reason.lower()
                        else ("degraded" if "stale" in reason.lower() else "available")
                    ),
                    "decision_reference_price": (
                        str(signal.get("reference_price"))
                        if signal.get("reference_price") not in {None, "", "0", 0}
                        else (
                            str(metadata.get("current_close"))
                            if metadata.get("current_close") not in {None, "", "0", 0}
                            else None
                        )
                    ),
                    "quote": {
                        "bid": bid,
                        "ask": ask,
                        "midpoint": midpoint,
                        "spread_pct": quality.get("spread_pct"),
                        "observed_at": quality.get("quote_timestamp"),
                    },
                    "features": metadata,
                    "checks": {
                        "strategy": metadata.get("checks") or {},
                        "confirmations": metadata.get("confirmations") or {},
                        "regime_confirmations": metadata.get("regime_confirmations") or {},
                        "market_quality": quality,
                        "correlation": metadata.get("correlation") or {},
                    },
                    "stop_price": str(signal.get("stop_price") or "") or None,
                    "target_price": str(signal.get("take_profit_price") or "") or None,
                    "forward_outcomes_status": "pending",
                    "market_context": {
                        "confirmations": metadata.get("confirmations") or {},
                        "regime_confirmations": metadata.get("regime_confirmations") or {},
                    },
                    "eligibility": {
                        "active_universe": symbol.upper() in {item.upper() for item in active_universe},
                    },
                    "constraints": {
                        "correlation": metadata.get("correlation") or {},
                        "sizing": metadata.get("sizing") or {},
                    },
                    "methodology_version": "live-decision-v1",
                    "strategy_version_id": candidate_strategy_version_id,
                    "market_lane": "us_equity",
                    "strategy_family": getattr(self.settings, "strategy_name", None),
                    "model_version": candidate_strategy_version_id,
                    "calibration_version": None,
                    "regime_version": None,
                    "execution_adapter_version": "alpaca-equity-execution-v1",
                    "data_source": "alpaca",
                    "data_feed": candidate_data_feed,
                    "bar_interval": getattr(self.settings, "bar_timeframe", None),
                    "confirmation_state": {
                        "passes": metadata.get("confirmation_passes"),
                        "confirmations": metadata.get("confirmations") or {},
                    },
                    "regime_state": {
                        "passes": metadata.get("regime_passes"),
                        "confirmations": metadata.get("regime_confirmations") or {},
                    },
                    "research_attribution": {
                        "live_strategy_version": candidate_strategy_version_id,
                        "market": "us_equity",
                    },
                    "ads002": ads002_shadow,
                    "shadow_economics": shadow_economics,
                    "ads002_v2": (
                        ads002_v2_by_symbol.get(symbol.upper())
                        or self._ads002_v2_missing_state(
                            symbol=symbol,
                            metadata=metadata,
                            reason="DECISION_CYCLE_CONTEXT_UNAVAILABLE",
                        )
                    ),
                }
            )

        replay_context = {
            "configuration": self._comparison_configuration(),
            "execution_context": comparison_context or embedded_comparison_context,
            "execution_result": execution_result or {},
        }
        self.emit(
            event_type="decision_cycle",
            event_key=f"{self.settings.trading_run_id}:decision-cycle:{correlation_id}",
            correlation_id=correlation_id,
            occurred_at=cycle_ended_at.isoformat(),
            strategy_version_id=self.settings.strategy_version_id,
            payload={
                "cycle_key": cycle_key,
                "cycle_started_at": cycle_started_at.isoformat(),
                "cycle_ended_at": cycle_ended_at.isoformat(),
                "market_is_open": market_is_open,
                "active_universe_size": len(active_universe),
                "active_universe": list(active_universe),
                "symbols_expected": list(active_universe),
                "symbols_evaluated": list(scan),
                "execution_mode": getattr(self.settings, "trading_mode", None),
                "market_session": "regular" if market_is_open else "closed",
                "data_source": "alpaca",
                "data_feed": getattr(self.settings, "data_feed", None),
                "bar_interval": getattr(self.settings, "bar_timeframe", None),
                "methodology_version": "live-decision-v1",
                "market_lane": "us_equity",
                "strategy_family": getattr(self.settings, "strategy_name", None),
                "model_version": getattr(self.settings, "strategy_version_id", None),
                "calibration_version": None,
                "regime_version": None,
                "execution_adapter_version": "alpaca-equity-execution-v1",
                "candidate_count": len(candidates),
                "qualified_count": qualified_count,
                "rejected_count": len(candidates) - qualified_count,
                "cycle_outcome": cycle_outcome,
                "data_status": data_status,
                "degraded": degraded,
                "error": error,
                "cycle_duration_ms": duration_ms,
                "runtime": runtime or {},
                "comparison_context": replay_context,
                "candidates": candidates,
            },
        )

    def record_position_metrics(
        self,
        *,
        symbol: str,
        metrics: dict[str, Any],
        correlation_id: str | None,
        observed_at: datetime,
    ) -> None:
        if not metrics:
            return
        bucket = observed_at.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
        self.emit(
            event_type="position_metrics",
            event_key=f"{self.settings.trading_run_id}:position-metrics:{symbol.upper()}:{bucket}",
            symbol=symbol.upper(),
            correlation_id=correlation_id,
            occurred_at=observed_at.isoformat(),
            strategy_version_id=self.settings.strategy_version_id,
            payload=metrics,
        )

    async def persist_entry_intent(
        self,
        *,
        signal: Any,
        qty: str,
        client_order_id: str,
        correlation_id: str | None,
        intended_at: datetime,
    ) -> dict[str, str] | None:
        signal_id = str(uuid4())
        intent_id = str(uuid4())
        position_id = str(uuid4())
        metadata = dict(signal.metadata or {})
        market = str(metadata.get("market") or "").lower()
        is_extended_equity = market == "us_equity_extended"
        time_in_force = str(metadata.get("time_in_force") or "day")
        order_type = str(metadata.get("execution_order_type") or "market")
        strategy_version_id = (
            self.settings.extended_equity_strategy_version_id
            if is_extended_equity
            else self.settings.strategy_version_id
        )
        cycle_key = (
            f"{self.settings.trading_run_id}:{correlation_id}"
            if correlation_id else None
        )
        market_quality = dict(metadata.get("market_quality") or {})
        candidate_key = (
            f"{cycle_key}:{signal.symbol.upper()}"
            if cycle_key
            else None
        )
        decision_quote = {
            "bid": market_quality.get("bid"),
            "ask": market_quality.get("ask"),
            "midpoint": market_quality.get("midpoint"),
            "spread_pct": market_quality.get("spread_pct"),
            "observed_at": market_quality.get("quote_timestamp"),
        }
        ads002_shadow = self._ads002_shadow_candidate_safe(
            symbol=signal.symbol,
            metadata=metadata,
        )
        shadow_economics = self._shadow_economics_safe(metadata)
        shadow_allocation = (
            None
            if is_extended_equity
            else self._shadow_allocation_safe(
                signal=signal,
                shadow_economics=shadow_economics,
            )
        )
        decision_scan = getattr(signal, "_evidence_decision_scan", None)
        ads002_v2_by_symbol = self._ads002_v2_shadow_cycle(
            decision_scan if isinstance(decision_scan, dict) else {}
        )
        ads002_v2 = (
            ads002_v2_by_symbol.get(signal.symbol.upper())
            or self._ads002_v2_missing_state(
                symbol=signal.symbol,
                metadata=metadata,
                reason="DECISION_CYCLE_CONTEXT_UNAVAILABLE",
            )
        )
        payload = {
            "signal": {
                "signal_id": signal_id,
                "symbol": signal.symbol.upper(),
                "side": "buy",
                "signal_at": intended_at.isoformat(),
                "reference_price": str(signal.reference_price),
                "stop_price": str(signal.stop_price),
                "target_price": str(signal.take_profit_price),
                "payload": {
                    "reason": signal.reason,
                    "metadata": signal.metadata or {},
                    "cycle_key": cycle_key,
                    "candidate_key": candidate_key,
                },
            },
            "intent": {
                "intent_id": intent_id,
                "idempotency_key": client_order_id,
                "signal_id": signal_id,
                "symbol": signal.symbol.upper(),
                "side": "buy",
                "order_type": order_type,
                "time_in_force": time_in_force,
                "requested_qty": qty,
                "requested_notional": str(signal.notional),
                "risk_decision": "approved",
                "risk_reason": "execution risk checks passed",
                "intended_at": intended_at.isoformat(),
                "payload": {
                    "client_order_id": client_order_id,
                    "position_id": position_id,
                    "decision_at": intended_at.isoformat(),
                    "decision_reference_price": str(signal.reference_price),
                    "decision_quote": decision_quote,
                    "cycle_key": cycle_key,
                    "candidate_key": candidate_key,
                    "candidate_snapshot": {
                        "candidate_key": candidate_key,
                        "symbol": signal.symbol.upper(),
                        "observed_at": intended_at.isoformat(),
                        "action": "buy",
                        "qualified": True,
                        "candidate_state": "qualified",
                        "qualification_status": "qualified",
                        "final_decision": "selected_for_entry",
                        "reason": signal.reason,
                        "decision_reference_price": str(signal.reference_price),
                        "quote": decision_quote,
                        "features": signal.metadata or {},
                        "checks": {
                            "strategy": metadata.get("checks") or {},
                            "confirmations": metadata.get("confirmations") or {},
                            "regime_confirmations": metadata.get("regime_confirmations") or {},
                            "market_quality": market_quality,
                            "correlation": metadata.get("correlation") or {},
                        },
                        "stop_price": str(signal.stop_price),
                        "target_price": str(signal.take_profit_price),
                        "market_context": {
                            "confirmations": metadata.get("confirmations") or {},
                            "regime_confirmations": metadata.get("regime_confirmations") or {},
                        },
                        "constraints": {
                            "correlation": metadata.get("correlation") or {},
                            "sizing": metadata.get("sizing") or {},
                        },
                        "methodology_version": "live-decision-v1",
                        "strategy_version_id": strategy_version_id,
                        "market_lane": (
                            metadata.get("market_lane")
                            or market
                            or "us_equity"
                        ),
                        "strategy_family": (
                            "extended_rolling_momentum"
                            if is_extended_equity
                            else getattr(self.settings, "strategy_name", None)
                        ),
                        "model_version": strategy_version_id,
                        "calibration_version": None,
                        "regime_version": None,
                        "execution_adapter_version": (
                            "alpaca-equity-24x5-limit-v1"
                            if is_extended_equity
                            else "alpaca-equity-execution-v1"
                        ),
                        "data_source": "alpaca",
                        "data_feed": (
                            metadata.get("data_feed")
                            if is_extended_equity
                            else getattr(self.settings, "data_feed", None)
                        ),
                        "bar_interval": getattr(self.settings, "bar_timeframe", None),
                        "forward_outcomes_status": "pending",
                        "confirmation_state": {
                            "passes": metadata.get("confirmation_passes"),
                            "confirmations": metadata.get("confirmations") or {},
                        },
                        "regime_state": {
                            "passes": metadata.get("regime_passes"),
                            "confirmations": metadata.get("regime_confirmations") or {},
                        },
                        "research_attribution": {
                            "live_strategy_version": strategy_version_id,
                        },
                        "ads002": None if is_extended_equity else ads002_shadow,
                        "shadow_economics": (
                            None if is_extended_equity else shadow_economics
                        ),
                        "shadow_allocation": shadow_allocation,
                        "ads002_v2": None if is_extended_equity else ads002_v2,
                    },
                },
            },
        }
        ok = await self.emit_critical(
            event_type="order_intent",
            event_key=f"{self.settings.trading_run_id}:intent:{client_order_id}",
            symbol=signal.symbol.upper(),
            correlation_id=correlation_id,
            occurred_at=intended_at.isoformat(),
            payload=payload,
        )
        if not ok:
            return None
        return {
            "signal_id": signal_id,
            "intent_id": intent_id,
            "position_id": position_id,
            "client_order_id": client_order_id,
        }

    def persist_exit_intent(
        self,
        *,
        symbol: str,
        qty: str,
        client_order_id: str,
        exit_reason: str,
        correlation_id: str | None,
        intended_at: datetime,
        exit_metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        intent_id = str(uuid4())
        exit_id = str(uuid4())
        metadata = dict(exit_metadata or {})
        market = str(metadata.get("market") or "").lower()
        time_in_force = str(metadata.get("time_in_force") or "day")
        order_type = str(metadata.get("execution_order_type") or "market")
        payload = {
            "intent": {
                "intent_id": intent_id,
                "idempotency_key": client_order_id,
                "signal_id": None,
                "symbol": symbol.upper(),
                "side": "sell",
                "order_type": order_type,
                "time_in_force": time_in_force,
                "requested_qty": qty,
                "requested_notional": None,
                "risk_decision": "approved",
                "risk_reason": exit_reason,
                "intended_at": intended_at.isoformat(),
                "payload": {
                    "client_order_id": client_order_id,
                    "exit_id": exit_id,
                    "exit_reason": exit_reason,
                    "exit_metadata": metadata,
                },
            }
        }
        self.emit(
            event_type="order_intent",
            event_key=f"{self.settings.trading_run_id}:intent:{client_order_id}",
            symbol=symbol.upper(),
            correlation_id=correlation_id,
            occurred_at=intended_at.isoformat(),
            payload=payload,
        )
        return {
            "intent_id": intent_id,
            "exit_id": exit_id,
            "client_order_id": client_order_id,
        }

    def record_broker_order(
        self,
        order: dict[str, Any],
        *,
        intent_id: str | None = None,
        position_id: str | None = None,
        exit_id: str | None = None,
        exit_reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        broker_order_id = str(order.get("id") or "")
        if not broker_order_id:
            return
        status = str(order.get("status") or "unknown")
        filled_qty = str(order.get("filled_qty") or "0")
        updated = str(
            order.get("updated_at")
            or order.get("filled_at")
            or order.get("submitted_at")
            or ""
        )
        self.emit(
            event_type="broker_order",
            event_key=(
                f"{self.settings.trading_run_id}:order:{broker_order_id}:"
                f"{status}:{filled_qty}:{updated}"
            ),
            symbol=str(order.get("symbol") or "").upper(),
            correlation_id=correlation_id,
            occurred_at=(
                str(order.get("submitted_at")) if order.get("submitted_at") else None
            ),
            payload={
                "order": order,
                "intent_id": intent_id,
                "position_id": position_id,
                "exit_id": exit_id,
                "exit_reason": exit_reason,
            },
        )

    def should_reconcile(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.last_reconcile_at is None:
            return True
        return (
            now.astimezone(timezone.utc) - self.last_reconcile_at
        ).total_seconds() >= self.settings.ledger_reconcile_seconds

    def record_reconciliation(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        correlation_id: str | None,
        observed_at: datetime,
    ) -> None:
        if not self.enabled:
            return
        observed_utc = observed_at.astimezone(timezone.utc)
        self.last_reconcile_at = observed_utc

        run_started_at = self.settings.trading_run_started_at

        def at_or_after_run_start(raw: Any) -> bool:
            if run_started_at is None:
                return True
            if not raw:
                return False
            try:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return False
            if stamp.tzinfo is None:
                return False
            return stamp.astimezone(timezone.utc) >= run_started_at

        bot_orders = [
            order
            for order in orders
            if self._owns_broker_order(order)
            and self._projectable_order(order)
            and at_or_after_run_start(order.get("submitted_at"))
        ]
        bot_orders.sort(key=lambda order: str(order.get("submitted_at") or ""))
        for order in bot_orders:
            self.record_broker_order(
                order,
                exit_reason=self._inferred_exit_reason(order),
                correlation_id=correlation_id,
            )

        bot_order_ids = {
            str(order.get("id") or "")
            for order in bot_orders
            if order.get("id")
        }
        for activity in fills:
            activity_id = str(activity.get("id") or "")
            order_id = str(activity.get("order_id") or "")
            if not activity_id or not order_id or order_id not in bot_order_ids:
                continue
            if not at_or_after_run_start(
                activity.get("transaction_time") or activity.get("date")
            ):
                continue
            self.emit(
                event_type="broker_fill",
                event_key=f"{self.settings.trading_run_id}:fill:{activity_id}",
                symbol=str(activity.get("symbol") or "").upper(),
                correlation_id=correlation_id,
                occurred_at=str(
                    activity.get("transaction_time")
                    or activity.get("date")
                    or observed_utc.isoformat()
                ),
                payload={"activity": activity},
            )

        gross_exposure = sum(
            abs(float(position.get("market_value") or 0))
            for position in positions
        )
        unrealized_pnl = sum(
            float(position.get("unrealized_pl") or 0)
            for position in positions
        )
        last_equity = float(risk_reference_equity(account))
        equity = float(account.get("equity") or 0)
        drawdown_pct = (
            max((last_equity - equity) / last_equity, 0.0)
            if last_equity > 0
            else 0.0
        )
        bucket = observed_utc.replace(second=0, microsecond=0).isoformat()
        self.emit(
            event_type="account_snapshot",
            event_key=f"{self.settings.trading_run_id}:account:{bucket}",
            correlation_id=correlation_id,
            occurred_at=observed_utc.isoformat(),
            payload={
                "equity": account.get("equity"),
                "last_equity": account.get("last_equity"),
                "risk_reference_equity": str(risk_reference_equity(account)),
                "day_pnl": str(day_pnl(account)),
                "cash_flow_accounting": account.get("cash_flow_accounting"),
                "cash_flow_error": account.get("cash_flow_error"),
                "cash": account.get("cash"),
                "buying_power": account.get("buying_power"),
                "realized_pnl": None,
                "unrealized_pnl": str(unrealized_pnl),
                "gross_exposure": str(gross_exposure),
                "net_exposure": str(gross_exposure),
                "drawdown_pct": str(drawdown_pct),
                "open_positions": len(positions),
                "positions": positions,
            },
        )

    def _at_or_after_run_start(self, raw: Any) -> bool:
        run_started_at = self.settings.trading_run_started_at
        if run_started_at is None:
            return True
        if not raw:
            return False
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            return False
        return stamp.astimezone(timezone.utc) >= run_started_at

    def _build_reconciliation_events(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        correlation_id: str | None,
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        observed_utc = observed_at.astimezone(timezone.utc)
        owned_order_ids = self._owned_order_ids_from_snapshot(orders)
        protective_order_ids = self._protective_stop_lineage_ids(orders)
        bot_orders = [
            order
            for order in orders
            if str(order.get("id") or "") in owned_order_ids
            and self._projectable_order(order, protective_order_ids)
            and self._at_or_after_run_start(order.get("submitted_at"))
        ]
        bot_orders.sort(key=lambda order: str(order.get("submitted_at") or ""))

        events: list[dict[str, Any]] = []
        for order in bot_orders:
            broker_order_id = str(order.get("id") or "")
            if not broker_order_id:
                continue
            status = str(order.get("status") or "unknown")
            filled_qty = str(order.get("filled_qty") or "0")
            updated = str(
                order.get("updated_at")
                or order.get("filled_at")
                or order.get("submitted_at")
                or ""
            )
            events.append(
                self._event(
                    event_type="broker_order",
                    event_key=(
                        f"{self.settings.trading_run_id}:order:{broker_order_id}:"
                        f"{status}:{filled_qty}:{updated}"
                    ),
                    symbol=str(order.get("symbol") or "").upper(),
                    correlation_id=correlation_id,
                    occurred_at=(
                        str(order.get("submitted_at"))
                        if order.get("submitted_at")
                        else None
                    ),
                    payload={
                        "order": order,
                        "exit_reason": self._inferred_exit_reason(
                            order,
                            protective_order_ids,
                        ),
                    },
                )
            )

        bot_order_ids = {
            str(order.get("id") or "")
            for order in bot_orders
            if order.get("id")
        }
        for activity in fills:
            activity_id = str(activity.get("id") or "")
            order_id = str(activity.get("order_id") or "")
            if not activity_id or not order_id or order_id not in bot_order_ids:
                continue
            if not self._at_or_after_run_start(
                activity.get("transaction_time") or activity.get("date")
            ):
                continue
            events.append(
                self._event(
                    event_type="broker_fill",
                    event_key=f"{self.settings.trading_run_id}:fill:{activity_id}",
                    symbol=str(activity.get("symbol") or "").upper(),
                    correlation_id=correlation_id,
                    occurred_at=str(
                        activity.get("transaction_time")
                        or activity.get("date")
                        or observed_utc.isoformat()
                    ),
                    payload={"activity": activity},
                )
            )

        gross_exposure = sum(
            abs(float(position.get("market_value") or 0))
            for position in positions
        )
        unrealized_pnl = sum(
            float(position.get("unrealized_pl") or 0)
            for position in positions
        )
        last_equity = float(risk_reference_equity(account))
        equity = float(account.get("equity") or 0)
        drawdown_pct = (
            max((last_equity - equity) / last_equity, 0.0)
            if last_equity > 0
            else 0.0
        )
        bucket = observed_utc.replace(second=0, microsecond=0).isoformat()
        events.append(
            self._event(
                event_type="account_snapshot",
                event_key=f"{self.settings.trading_run_id}:account:{bucket}",
                correlation_id=correlation_id,
                occurred_at=observed_utc.isoformat(),
                payload={
                    "equity": account.get("equity"),
                    "last_equity": account.get("last_equity"),
                    "risk_reference_equity": str(risk_reference_equity(account)),
                    "day_pnl": str(day_pnl(account)),
                    "cash_flow_accounting": account.get("cash_flow_accounting"),
                    "cash_flow_error": account.get("cash_flow_error"),
                    "cash": account.get("cash"),
                    "buying_power": account.get("buying_power"),
                    "realized_pnl": None,
                    "unrealized_pnl": str(unrealized_pnl),
                    "gross_exposure": str(gross_exposure),
                    "net_exposure": str(gross_exposure),
                    "drawdown_pct": str(drawdown_pct),
                    "open_positions": len(positions),
                    "positions": positions,
                },
            )
        )
        return events

    async def sync_reconciliation(
        self,
        *,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        managed_symbols: list[str],
        correlation_id: str | None,
        observed_at: datetime,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("canonical trading persistence is not configured")

        events = self._build_reconciliation_events(
            account=account,
            positions=positions,
            orders=orders,
            fills=fills,
            correlation_id=correlation_id,
            observed_at=observed_at,
        )
        async with httpx.AsyncClient(timeout=8.0) as http:
            if not await self._send_batch(http, events):
                raise RuntimeError(self.last_error or "broker snapshot persistence failed")

            base = self.settings.trading_ingest_url.rsplit("/", 1)[0]
            response = await http.post(
                f"{base}/trading-reconcile",
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={
                    "action": "reconcile",
                    "reconcile": {
                        "run_id": self.settings.trading_run_id,
                        "strategy_version_id": self.settings.strategy_version_id,
                        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                        "managed_symbols": managed_symbols,
                        "broker_positions": positions,
                        "open_orders": open_orders,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("reconciliation endpoint returned no result")
            self.last_reconcile_at = observed_at.astimezone(timezone.utc)
            self.last_error = None
            return result

    async def resolve_intent_not_found(
        self,
        *,
        client_order_id: str,
        correlation_id: str | None,
        checked_at: datetime,
    ) -> bool:
        if not self.enabled:
            return False
        base = self.settings.trading_ingest_url.rsplit("/", 1)[0]
        async with httpx.AsyncClient(timeout=8.0) as http:
            response = await http.post(
                f"{base}/trading-reconcile",
                headers={
                    "content-type": "application/json",
                    "x-anevum-ingest-token": self.settings.trading_ingest_token,
                },
                json={
                    "action": "resolve_intent",
                    "intent": {
                        "run_id": self.settings.trading_run_id,
                        "client_order_id": client_order_id,
                        "state": "broker_not_found",
                        "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
                        "correlation_id": correlation_id,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            return bool(payload.get("ok"))

    async def persist_recovered_order(
        self,
        order: dict[str, Any],
        *,
        correlation_id: str | None,
        intent_id: str | None = None,
        position_id: str | None = None,
        exit_id: str | None = None,
        exit_reason: str | None = None,
    ) -> bool:
        broker_order_id = str(order.get("id") or "")
        if not broker_order_id:
            return False
        status = str(order.get("status") or "unknown")
        filled_qty = str(order.get("filled_qty") or "0")
        updated = str(
            order.get("updated_at")
            or order.get("filled_at")
            or order.get("submitted_at")
            or ""
        )
        event = self._event(
            event_type="broker_order",
            event_key=(
                f"{self.settings.trading_run_id}:order:{broker_order_id}:"
                f"{status}:{filled_qty}:{updated}"
            ),
            symbol=str(order.get("symbol") or "").upper(),
            correlation_id=correlation_id,
            occurred_at=(
                str(order.get("submitted_at")) if order.get("submitted_at") else None
            ),
            payload={
                "order": order,
                "intent_id": intent_id,
                "position_id": position_id,
                "exit_id": exit_id,
                "exit_reason": exit_reason,
                "recovered_by_client_order_id": True,
            },
        )
        async with httpx.AsyncClient(timeout=8.0) as http:
            return await self._send_batch(http, [event])

    @staticmethod
    def _transport_chunks(
        events: list[dict[str, Any]],
        *,
        max_events: int = 100,
        max_body_bytes: int = 200_000,
    ) -> list[list[dict[str, Any]]]:
        """Split telemetry below both ingest count and body-size ceilings."""
        if not events:
            return []

        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []

        for event in events:
            candidate = [*current, event]
            encoded_size = len(
                json.dumps(
                    {"events": candidate},
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
            if current and (
                len(candidate) > max_events
                or encoded_size > max_body_bytes
            ):
                chunks.append(current)
                current = [event]
            else:
                current = candidate

        if current:
            chunks.append(current)
        return chunks

    async def _send_batch(
        self,
        http: httpx.AsyncClient,
        events: list[dict[str, Any]],
        *,
        require_all: bool = False,
    ) -> bool:
        if not events:
            return True

        try:
            # The trading-ingest Edge Function accepts at most 100 events and
            # rejects request bodies above 256 KB. Some decision-cycle payloads
            # are tens of KB each, so count-only batching is insufficient.
            # Keep a conservative byte margin to account for HTTP serialization
            # details while preserving idempotent event keys.
            for chunk in self._transport_chunks(events):
                response = await http.post(
                    self.settings.trading_ingest_url,
                    headers={
                        "content-type": "application/json",
                        "x-anevum-ingest-token": self.settings.trading_ingest_token,
                    },
                    json={"events": chunk},
                )
                response.raise_for_status()
                try:
                    receipt = response.json()
                except ValueError:
                    receipt = {}
                if isinstance(receipt, dict):
                    self.shed_count += max(0, int(receipt.get("shed") or 0))
                    storage = receipt.get("storage") or {}
                    if isinstance(storage, dict) and "analytics_shedding" in storage:
                        self.storage_analytics_shedding = storage["analytics_shedding"] is True
                if require_all:
                    if (not isinstance(receipt, dict)
                            or receipt.get("ok") is not True
                            or receipt.get("inserted") != len(chunk)
                            or receipt.get("shed", 0) != 0):
                        raise RuntimeError("canonical_report_write_unconfirmed")
                self.sent_count += len(chunk)

            self.last_sent_at = datetime.now(timezone.utc)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    async def _run_foundation(self) -> None:
        assert self.foundation_sink is not None
        flush_seconds = float(
            getattr(self.settings, "foundation_flush_seconds", 1.0)
        )
        batch_size = int(getattr(self.settings, "foundation_batch_size", 50))
        while not self.stop_event.is_set():
            result = await self.foundation_sink.flush_once(limit=batch_size)
            if result.get("ok"):
                self.foundation_last_error = None
                self.foundation_delivered_count += int(result.get("delivered") or 0)
                sleep_for = flush_seconds if result.get("empty") else 0.05
            else:
                self.foundation_last_error = str(
                    result.get("error") or "foundation delivery failed"
                )
                sleep_for = flush_seconds
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def _run(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as http:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    first = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                batch = [first]
                while len(batch) < 50:
                    try:
                        batch.append(self.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                delivered = False
                for attempt in range(3):
                    if await self._send_batch(http, batch):
                        delivered = True
                        break
                    await asyncio.sleep(0.25 * (2 ** attempt))

                for _ in batch:
                    self.queue.task_done()

                if not delivered:
                    self.dropped_count += len(batch)
