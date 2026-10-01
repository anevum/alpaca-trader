from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .alpaca_client import AlpacaClient
from .config import get_settings
from .cash_flow import day_pnl, risk_reference_equity
from .command_access import CommandAuthError, authenticate_command_admin
from .execution import ExecutionEngine
from .crypto_execution import CryptoExecutionEngine
from .crypto_layer import (
    CryptoMarketDataClient,
    CryptoRollingMomentumStrategy,
    CryptoScanner,
    CryptoUniverse,
)
from .market_data import MarketDataClient
from .persistence import TradingEventSink
from .provenance import RHEN_VERSION, capture_runtime_provenance
from .research_scheduler import ResearchReportScheduler
from .sizing import sizing_snapshot
from .slack_notifier import SlackNotifier
from .state import runtime_state
from .scanner import ReadOnlyScanner
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy
from .universe import DynamicUniverse

settings = get_settings()
client = AlpacaClient(settings)
market_data = MarketDataClient(settings)
if settings.strategy_name == "rolling_momentum_vwap":
    strategy = RollingMomentumVwapStrategy(
        fast_window=settings.fast_window,
        slow_window=settings.slow_window,
        min_momentum_pct=settings.min_momentum_pct,
        min_vwap_edge_pct=settings.min_vwap_edge_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
        min_confirmations=settings.min_confirmations,
        regime_window=settings.regime_window,
        regime_min_confirmations=settings.regime_min_confirmations,
        regime_min_return_pct=settings.regime_min_return_pct,
        max_vwap_extension_pct=settings.max_vwap_extension_pct,
        volatility_stop_enabled=settings.volatility_stop_enabled,
        volatility_stop_multiplier=settings.volatility_stop_multiplier,
        volatility_stop_lookback_bars=settings.volatility_stop_lookback_bars,
        max_dynamic_stop_pct=settings.max_dynamic_stop_pct,
    )
else:
    strategy = OpeningRangeVwapStrategy(
        opening_range_minutes=settings.opening_range_minutes,
        max_opening_range_pct=settings.max_opening_range_pct,
        max_breakout_extension_pct=settings.max_breakout_extension_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
    )
event_sink = TradingEventSink(settings)
universe = DynamicUniverse(settings, client, market_data, runtime_state)
engine = ExecutionEngine(
    settings,
    client,
    market_data,
    strategy,
    runtime_state,
    ledger=event_sink,
    universe=universe,
)
scanner = ReadOnlyScanner(
    settings,
    client,
    market_data,
    strategy,
    runtime_state,
    universe=universe,
)
crypto_market_data = CryptoMarketDataClient(settings)
crypto_strategy = CryptoRollingMomentumStrategy(
    fast_window=settings.crypto_fast_window,
    slow_window=settings.crypto_slow_window,
    min_momentum_pct=settings.crypto_min_momentum_pct,
    min_vwap_edge_pct=settings.crypto_min_vwap_edge_pct,
    stop_pct=settings.crypto_stop_pct,
    target_pct=settings.crypto_target_pct,
    entry_start=settings.entry_start,
    entry_cutoff=settings.entry_cutoff,
    confirmation_symbols=settings.crypto_confirmation_symbols,
    min_confirmations=1,
    regime_window=settings.crypto_regime_window,
    regime_min_confirmations=1,
    regime_min_return_pct=settings.crypto_regime_min_return_pct,
    max_vwap_extension_pct=settings.crypto_max_vwap_extension_pct,
    volatility_stop_enabled=settings.crypto_volatility_stop_enabled,
    volatility_stop_multiplier=settings.crypto_volatility_stop_multiplier,
    volatility_stop_lookback_bars=settings.crypto_volatility_lookback_bars,
    max_dynamic_stop_pct=settings.crypto_max_dynamic_stop_pct,
    strategy_version_id=settings.crypto_strategy_version_id,
    model_version=settings.crypto_model_version,
    calibration_version=settings.crypto_calibration_version,
    calibration_promoted=settings.crypto_calibration_promoted,
    regime_version=settings.crypto_regime_version,
    execution_adapter_version=settings.crypto_execution_adapter_version,
    feature_volatility_lookback=settings.crypto_volatility_lookback_bars,
)
crypto_universe = CryptoUniverse(
    settings, client, crypto_market_data, runtime_state
)
crypto_scanner = CryptoScanner(
    settings,
    crypto_market_data,
    crypto_strategy,
    runtime_state,
    crypto_universe,
)
crypto_engine = CryptoExecutionEngine(
    settings,
    client,
    crypto_market_data,
    crypto_strategy,
    runtime_state,
    crypto_universe,
    ledger=event_sink,
)
research_reports = ResearchReportScheduler(
    settings,
    client,
    market_data,
    runtime_state,
    event_sink,
)
slack_notifier = SlackNotifier(settings)
_stop = asyncio.Event()
NY = ZoneInfo("America/New_York")
runtime_provenance = None


def emit_runtime_event(event: dict) -> None:
    event_sink.emit(
        event_type=str(event.get("kind") or "runtime_event"),
        occurred_at=str(event.get("at") or ""),
        symbol=str(event.get("symbol") or ""),
        correlation_id=event.get("correlation_id"),
        payload={
            "action": event.get("action"),
            "message": event.get("message"),
            "reason": event.get("reason"),
            **(event.get("payload") or {}),
        },
    )
    slack_notifier.record_event(event)


runtime_state.set_event_emitter(emit_runtime_event)


def require_admin(authorization: str | None):
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    expected = f"Bearer {settings.admin_token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_scheduler_token(x_anevum_scheduler_token: str | None) -> None:
    expected = str(getattr(settings, "trading_ingest_token", "") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="scheduler token is not configured")
    if x_anevum_scheduler_token is None or not hmac.compare_digest(
        x_anevum_scheduler_token,
        expected,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


def scheduler_configuration_snapshot() -> dict:
    comparison = event_sink._comparison_configuration()
    protected = {
        "trading_mode": settings.trading_mode,
        "execution_enabled": settings.execution_enabled,
        "execution_authorized": settings.execution_authorized,
        "paper_execution_authorized": settings.paper_execution_authorized,
        "live_execution_authorized": settings.live_execution_authorized,
        "bot_armed": settings.bot_armed,
        "strategy_version_id": settings.strategy_version_id,
        "max_order_notional": str(settings.max_order_notional),
        "max_position_notional": str(settings.max_position_notional),
        "max_concurrent_positions": settings.max_concurrent_positions,
        "max_total_position_notional": str(settings.max_total_position_notional),
        "max_daily_orders": settings.max_daily_orders,
        "max_daily_loss": str(settings.max_daily_loss),
        "risk_per_trade_pct": str(settings.risk_per_trade_pct),
        "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
        "crypto_execution_enabled": settings.crypto_execution_enabled,
    }
    material = {"comparison": comparison, "protected": protected}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return {
        "fingerprint": "sha256:" + digest,
        "strategy_name": settings.strategy_name,
        "strategy_version_id": settings.strategy_version_id,
        "trading_mode": settings.trading_mode,
        "execution_authorized": settings.execution_authorized,
        "crypto_execution_enabled": settings.crypto_execution_enabled,
    }


async def scheduler_session_detail(session: date) -> dict:
    rows = await market_data.market_calendar_details(start=session, end=session)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="requested date is not a U.S. equity trading session",
        )
    row = dict(rows[0])
    row["date"] = row["date"].isoformat()
    return row


class SchedulerSessionRequest(BaseModel):
    session: date
    scheduled_at: datetime | None = None


async def require_command_admin(authorization: str | None) -> dict:
    try:
        return await authenticate_command_admin(
            authorization,
            team_domain=settings.command_access_team_domain,
            audience=settings.command_access_aud,
            allowed_emails=settings.command_access_emails_raw,
        )
    except CommandAuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


def public_position(position: dict) -> dict:
    keys = (
        "symbol", "qty", "side", "avg_entry_price", "current_price",
        "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc",
        "change_today",
    )
    return {key: position.get(key) for key in keys}


def public_order(order: dict) -> dict:
    keys = (
        "id", "client_order_id", "symbol", "side", "type", "status",
        "order_class", "qty", "filled_qty", "filled_avg_price",
        "limit_price", "stop_price", "submitted_at", "filled_at",
        "canceled_at",
    )
    return {key: order.get(key) for key in keys}


async def command_snapshot() -> dict:
    account = await client.account()
    clock = await client.clock()
    positions = await client.positions()
    open_orders = await client.open_orders()
    recent_orders = await client.recent_orders(limit=100)
    bot_orders = [
        order
        for order in recent_orders
        if str(order.get("client_order_id", "")).startswith("anevum-")
    ]
    entry_count = engine._entry_orders_today(recent_orders)
    equity = Decimal(str(account.get("equity", "0")))
    last_equity = Decimal(str(account.get("last_equity", "0")))
    allocator = sizing_snapshot(settings, account, positions)
    return {
        "system": "RHEN",
        "observed_at": runtime_state.last_poll_at,
        "mode": settings.trading_mode,
        "market": {
            "is_open": bool(clock.get("is_open")),
            "timestamp": clock.get("timestamp"),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        },
        "bot": {
            "execution_enabled": settings.execution_enabled,
            "execution_authorized": settings.execution_authorized,
            "bot_armed": settings.bot_armed,
            "scan_only": settings.scan_only,
            "runtime_paused": runtime_state.paused,
            "entries_enabled": runtime_state.entries_enabled,
            "startup_reconciled": runtime_state.startup_reconciled,
            "reconciliation_safe": runtime_state.reconciliation_safe,
            "last_reconciliation": runtime_state.last_reconciliation,
            "funding_ready": runtime_state.funding_ready,
            "last_strategy_at": runtime_state.last_strategy_at,
            "last_decision": runtime_state.last_decision,
            "last_error": runtime_state.last_error,
            "exit_states": runtime_state.exit_states,
            "crypto_lane_enabled": settings.crypto_lane_enabled,
            "crypto_execution_enabled": settings.crypto_execution_enabled,
            "crypto_universe_size": settings.crypto_universe_size,
            "crypto_poll_seconds": settings.crypto_poll_seconds,
            "crypto_confirmation_symbols": list(settings.crypto_confirmation_symbols),
        },
        "account": {
            "equity": str(account.get("equity", "0")),
            "last_equity": str(account.get("last_equity", "0")),
            "day_pnl": str(day_pnl(account)),
            "raw_equity_change": str(equity - last_equity),
            "risk_reference_equity": str(risk_reference_equity(account)),
            "cash_flow_accounting": account.get("cash_flow_accounting"),
            "cash_flow_error": account.get("cash_flow_error"),
            "cash": str(account.get("cash", "0")),
            "buying_power": str(account.get("buying_power", "0")),
            "trading_blocked": bool(account.get("trading_blocked")),
            "account_blocked": bool(account.get("account_blocked")),
        },
        "strategy": {
            "scan_symbols": list(settings.scan_symbols),
            "confirmation_symbols": list(settings.confirmation_symbols),
            "opening_range_minutes": settings.opening_range_minutes,
            "fast_window": settings.fast_window,
            "slow_window": settings.slow_window,
            "min_momentum_pct": str(settings.min_momentum_pct),
            "min_vwap_edge_pct": str(settings.min_vwap_edge_pct),
            "min_confirmations": settings.min_confirmations,
            "regime_window": settings.regime_window,
            "regime_min_confirmations": settings.regime_min_confirmations,
            "regime_min_return_pct": str(settings.regime_min_return_pct),
            "max_vwap_extension_pct": str(settings.max_vwap_extension_pct),
            "loss_streak_limit": settings.loss_streak_limit,
            "loss_streak_cooldown_minutes": settings.loss_streak_cooldown_minutes,
            "broker_protective_stop_enabled": settings.broker_protective_stop_enabled,
            "volatility_stop_enabled": settings.volatility_stop_enabled,
            "volatility_stop_multiplier": str(settings.volatility_stop_multiplier),
            "volatility_stop_lookback_bars": settings.volatility_stop_lookback_bars,
            "max_dynamic_stop_pct": str(settings.max_dynamic_stop_pct),
            "profit_protect_enabled": settings.profit_protect_enabled,
            "profit_protect_activation_pct": str(settings.profit_protect_activation_pct),
            "profit_protect_retain_fraction": str(settings.profit_protect_retain_fraction),
            "profit_protect_min_pct": str(settings.profit_protect_min_pct),
            "profit_stop_step_pct": str(settings.profit_stop_step_pct),
            "thesis_exit_enabled": settings.thesis_exit_enabled,
            "thesis_failure_cycles": settings.thesis_failure_cycles,
            "thesis_exit_max_return_pct": str(settings.thesis_exit_max_return_pct),
            "max_hold_minutes": settings.max_hold_minutes,
            "reentry_cooldown_minutes": settings.reentry_cooldown_minutes,
            "max_opening_range_pct": str(settings.max_opening_range_pct),
            "max_breakout_extension_pct": str(settings.max_breakout_extension_pct),
            "entry_start": settings.entry_start_raw,
            "entry_cutoff": settings.entry_cutoff_raw,
            "force_flat_time": settings.force_flat_time_raw,
            "stop_pct": str(settings.stop_pct),
            "target_pct": str(settings.target_pct),
            "order_notional": str(settings.order_notional),
            "data_feed": settings.data_feed,
            "max_bar_age_seconds": settings.max_bar_age_seconds,
            "max_spread_pct": str(settings.max_spread_pct),
            "min_quality_score": str(settings.min_quality_score),
            "regime_window": settings.regime_window,
            "regime_min_confirmations": settings.regime_min_confirmations,
            "regime_min_return_pct": str(settings.regime_min_return_pct),
            "max_vwap_extension_pct": str(settings.max_vwap_extension_pct),
            "loss_streak_limit": settings.loss_streak_limit,
            "loss_streak_cooldown_minutes": settings.loss_streak_cooldown_minutes,
            "order_owner_tag": settings.order_owner_tag,
            "max_pairwise_correlation": str(settings.max_pairwise_correlation),
            "correlation_lookback_bars": settings.correlation_lookback_bars,
            "correlation_min_observations": settings.correlation_min_observations,
        },
        "risk": {
            "max_daily_orders": settings.max_daily_orders,
            "entry_orders_today": entry_count,
            "entries_remaining": (
                None
                if settings.portfolio_limit_mode == "risk"
                and settings.max_daily_orders == 0
                else max(settings.max_daily_orders - entry_count, 0)
            ),
            "max_daily_loss": str(settings.max_daily_loss),
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
            "max_concurrent_positions": settings.max_concurrent_positions,
            "max_new_entries_per_cycle": settings.max_new_entries_per_cycle,
            "max_total_position_notional": str(settings.max_total_position_notional),
            "sizing_mode": settings.sizing_mode,
            "risk_per_trade_pct": str(settings.risk_per_trade_pct),
            "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
            "min_order_notional": str(settings.min_order_notional),
            "portfolio_limit_mode": settings.portfolio_limit_mode,
            "max_position_gross_pct": str(settings.max_position_gross_pct),
            "max_portfolio_stop_risk_pct": str(settings.max_portfolio_stop_risk_pct),
        },
        "allocator": allocator,
        "universe": {
            "enabled": settings.dynamic_universe_enabled,
            "source": runtime_state.universe_source,
            "active_count": len(runtime_state.universe_active_symbols),
            "candidate_count": runtime_state.universe_candidate_count,
            "eligible_count": runtime_state.universe_eligible_count,
            "updated_at": runtime_state.universe_updated_at,
            "error": runtime_state.universe_error,
            "active_symbols": runtime_state.universe_active_symbols,
        },
        "scanner": runtime_state.last_completed_scan,
        "history": runtime_state.decision_history,
        "positions": [public_position(position) for position in positions],
        "open_orders": [public_order(order) for order in open_orders],
        "recent_orders": [public_order(order) for order in bot_orders[:30]],
        "last_order": runtime_state.last_order,
        "research": {
            "status": research_reports.status(),
            "current_focus": (
                research_reports.last_daily_report.get("next_offline_research_action")
                if research_reports.last_daily_report
                else (
                    research_reports.last_weekly_report.get("next_offline_research_action")
                    if research_reports.last_weekly_report
                    else None
                )
            ),
            "latest_daily": research_reports.last_daily_report,
            "latest_weekly": research_reports.last_weekly_report,
        },
    }


def _ready_symbols() -> list[str]:
    return [
        symbol
        for symbol, payload in runtime_state.last_scan.items()
        if payload.get("action") == "buy"
    ]


async def refresh_account_state() -> dict:
    runtime_state.mark_poll()
    account = await client.account()
    cash = Decimal(str(account.get("cash", "0")))
    equity = Decimal(str(account.get("equity", "0")))
    last_equity = Decimal(str(account.get("last_equity", "0")))
    runtime_state.last_cash = str(cash)
    runtime_state.last_buying_power = str(account.get("buying_power", "0"))
    runtime_state.last_equity = str(equity)
    runtime_state.last_equity_reference = str(last_equity)
    runtime_state.last_day_pnl = str(day_pnl(account))
    runtime_state.last_risk_reference_equity = str(risk_reference_equity(account))
    runtime_state.last_cash_flow_accounting = account.get("cash_flow_accounting")
    runtime_state.last_cash_flow_error = account.get("cash_flow_error")
    runtime_state.funding_ready = cash >= settings.min_ready_cash
    runtime_state.last_error = None
    return account


async def reconcile_broker_state(
    account: dict | None = None,
    *,
    startup: bool = False,
    force: bool = False,
) -> dict | None:
    now = datetime.now(NY)
    if settings.scan_only:
        if startup:
            runtime_state.set_reconciliation(
                {"safe_to_enter": False, "reason": "scan-only service"},
                startup=True,
            )
        return runtime_state.last_reconciliation
    if not force and not event_sink.should_reconcile(now):
        return runtime_state.last_reconciliation

    try:
        account = account or await client.account()
        positions, open_orders, recent_orders, fills = await asyncio.gather(
            client.positions(),
            client.open_orders(),
            client.recent_orders(limit=100),
            client.fill_activities(date=now.date().isoformat(), limit=100),
        )
        result = await event_sink.sync_reconciliation(
            account=account,
            positions=positions,
            orders=recent_orders,
            fills=fills,
            open_orders=open_orders,
            managed_symbols=event_sink.managed_symbols_from_snapshot(
                orders=recent_orders,
                fills=fills,
                open_orders=open_orders,
            ),
            correlation_id=runtime_state.current_correlation_id,
            observed_at=now,
        )

        unresolved = list(result.get("unresolved_intents") or [])
        if unresolved:
            changed = False
            for intent in unresolved:
                client_order_id = str(intent.get("client_order_id") or "")
                if not client_order_id:
                    continue
                recovered = None
                for attempt in range(3):
                    recovered = await client.order_by_client_order_id(client_order_id)
                    if recovered is not None:
                        break
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))

                if recovered is not None:
                    ok = await event_sink.persist_recovered_order(
                        recovered,
                        correlation_id=runtime_state.current_correlation_id,
                    )
                    if not ok:
                        raise RuntimeError(
                            f"recovered broker order {client_order_id} could not be persisted"
                        )
                    changed = True
                    continue

                ok = await event_sink.resolve_intent_not_found(
                    client_order_id=client_order_id,
                    correlation_id=runtime_state.current_correlation_id,
                    checked_at=datetime.now(NY),
                )
                if not ok:
                    raise RuntimeError(
                        f"unresolved intent {client_order_id} could not be cleared"
                    )
                changed = True

            if changed:
                now = datetime.now(NY)
                account = await client.account()
                positions, open_orders, recent_orders, fills = await asyncio.gather(
                    client.positions(),
                    client.open_orders(),
                    client.recent_orders(limit=100),
                    client.fill_activities(date=now.date().isoformat(), limit=100),
                )
                result = await event_sink.sync_reconciliation(
                    account=account,
                    positions=positions,
                    orders=recent_orders,
                    fills=fills,
                    open_orders=open_orders,
                    managed_symbols=event_sink.managed_symbols_from_snapshot(
                orders=recent_orders,
                fills=fills,
                open_orders=open_orders,
            ),
                    correlation_id=runtime_state.current_correlation_id,
                    observed_at=now,
                )

        runtime_state.set_reconciliation(result, startup=startup)
        runtime_state.record_event(
            kind="reconciliation",
            action="safe" if runtime_state.reconciliation_safe else "blocked",
            message=(
                "broker and canonical ledger reconciled"
                if runtime_state.reconciliation_safe
                else "broker/canonical mismatch; new entries blocked"
            ),
            payload=result,
        )
        return result
    except Exception as exc:
        runtime_state.reconciliation_safe = False
        if startup:
            runtime_state.startup_reconciled = True
        event_sink.last_error = f"reconciliation {type(exc).__name__}: {exc}"
        runtime_state.last_error = event_sink.last_error
        runtime_state.last_reconciliation = {
            "safe_to_enter": False,
            "error": event_sink.last_error,
        }
        runtime_state.record_event(
            kind="reconciliation",
            action="error",
            message="reconciliation failed; new entries blocked",
            reason=event_sink.last_error,
            payload=runtime_state.last_reconciliation,
        )
        print(
            "LEDGER_RECONCILE_ERROR",
            {"error": event_sink.last_error},
            flush=True,
        )
        return runtime_state.last_reconciliation


async def monitor_loop():
    while not _stop.is_set():
        runtime_state.begin_cycle(uuid4().hex)
        cycle_started_at = datetime.now(NY)
        runtime_state.last_scan = {}
        market_is_open: bool | None = None
        cycle_execution_result: dict | None = None
        try:
            if settings.credentials_configured:
                if settings.scan_only:
                    runtime_state.mark_poll()
                    runtime_state.funding_ready = False
                    await scanner.scan_once()
                else:
                    account = await refresh_account_state()
                    await reconcile_broker_state(account)
                    if (
                        settings.execution_enabled
                        and settings.bot_armed
                        and not runtime_state.paused
                    ):
                        result = await engine.run_once()
                        cycle_execution_result = result
                        print(
                            "LIVE_EXECUTION_CYCLE",
                            {
                                "action": result.get("action"),
                                "symbol": result.get("symbol"),
                                "reason": result.get("reason") or runtime_state.last_decision,
                                "last_error": runtime_state.last_error,
                            },
                            flush=True,
                        )
            else:
                runtime_state.funding_ready = False
                runtime_state.last_error = "credentials not configured"
        except Exception as exc:
            runtime_state.last_error = f"{type(exc).__name__}: {exc}"
            event_sink.emit(
                event_type="runtime_error",
                correlation_id=runtime_state.current_correlation_id,
                payload={"error": runtime_state.last_error},
            )
            print(
                "LIVE_LOOP_ERROR",
                {"error": runtime_state.last_error},
                flush=True,
            )

        if event_sink.enabled and runtime_state.current_correlation_id:
            cycle_ended_at = datetime.now(NY)
            scan = dict(runtime_state.last_scan)
            event_sink.record_decision_cycle(
                correlation_id=runtime_state.current_correlation_id,
                cycle_started_at=cycle_started_at,
                cycle_ended_at=cycle_ended_at,
                market_is_open=(True if scan else market_is_open),
                active_universe=(
                    list(runtime_state.universe_active_symbols)
                    or list(scan)
                ),
                scan=scan,
                cycle_outcome=(runtime_state.last_decision or "cycle_complete"),
                data_status="degraded" if runtime_state.last_error else "ok",
                degraded=bool(runtime_state.last_error),
                error=runtime_state.last_error,
                runtime=(runtime_provenance.as_dict() if runtime_provenance else {}),
                execution_result=cycle_execution_result,
            )

        try:
            await asyncio.wait_for(_stop.wait(), timeout=settings.poll_seconds)
        except asyncio.TimeoutError:
            pass


async def crypto_monitor_loop():
    """Independent 24/7 crypto market lane; shadow execution until validated."""
    while not _stop.is_set():
        if settings.crypto_lane_enabled and settings.credentials_configured:
            try:
                runtime_state.begin_crypto_cycle(uuid4().hex)
                result = await crypto_engine.run_once()
                print(
                    "CRYPTO_EXECUTION_CYCLE",
                    {
                        "execution_enabled": settings.crypto_execution_enabled,
                        "action": result.get("action"),
                        "symbol": result.get("symbol"),
                        "reason": result.get("reason"),
                    },
                    flush=True,
                )
            except Exception as exc:
                runtime_state.crypto_scanner_healthy = False
                runtime_state.crypto_execution_healthy = False
                runtime_state.crypto_last_error = f"{type(exc).__name__}: {exc}"
                runtime_state.crypto_last_decision = (
                    f"crypto lane error: {type(exc).__name__}: {exc}"
                )
                runtime_state.record_event(
                    kind="crypto_runtime_error",
                    action="error",
                    message=runtime_state.crypto_last_decision,
                    payload={"market": "crypto"},
                    correlation_id=runtime_state.crypto_current_correlation_id,
                )
                print(
                    "CRYPTO_LOOP_ERROR",
                    {"error": runtime_state.crypto_last_decision},
                    flush=True,
                )
        try:
            await asyncio.wait_for(
                _stop.wait(),
                timeout=settings.crypto_poll_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def slack_market_observer_loop():
    """Read-only market-state observer used only for Slack transition notices."""
    while not _stop.is_set():
        if slack_notifier.enabled and settings.credentials_configured:
            try:
                clock = await client.clock()
                slack_notifier.observe_market_state(
                    bool(clock.get("is_open")),
                    observed_at=datetime.now(NY),
                )
            except Exception as exc:
                print(
                    "SLACK_MARKET_OBSERVER_ERROR",
                    {"error": f"{type(exc).__name__}: {exc}"},
                    flush=True,
                )
        try:
            await asyncio.wait_for(_stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


def _runtime_configuration_snapshot() -> dict:
    """Non-secret runtime configuration needed to reproduce deployment state."""
    return {
        "trading_mode": settings.trading_mode,
        "scan_only": settings.scan_only,
        "execution_enabled": settings.execution_enabled,
        "bot_armed": settings.bot_armed,
        "live_trading": settings.live_trading,
        "strategy_name": settings.strategy_name,
        "strategy_version_id": settings.strategy_version_id,
        "data_feed": settings.data_feed,
        "bar_timeframe": settings.bar_timeframe,
        "scan_symbols": list(settings.scan_symbols),
        "confirmation_symbols": list(settings.confirmation_symbols),
        "dynamic_universe_enabled": settings.dynamic_universe_enabled,
        "universe_size": settings.universe_size,
        "entry_start": settings.entry_start_raw,
        "entry_cutoff": settings.entry_cutoff_raw,
        "force_flat_time": settings.force_flat_time_raw,
        "sizing_mode": settings.sizing_mode,
        "portfolio_limit_mode": settings.portfolio_limit_mode,
        "order_notional": str(settings.order_notional),
        "max_concurrent_positions": settings.max_concurrent_positions,
        "max_new_entries_per_cycle": settings.max_new_entries_per_cycle,
        "max_total_position_notional": str(settings.max_total_position_notional),
        "max_position_gross_pct": str(settings.max_position_gross_pct),
        "max_portfolio_stop_risk_pct": str(settings.max_portfolio_stop_risk_pct),
        "max_daily_orders": settings.max_daily_orders,
        "max_daily_loss": str(settings.max_daily_loss),
        "stop_pct": str(settings.stop_pct),
        "target_pct": str(settings.target_pct),
        "broker_protective_stop_enabled": settings.broker_protective_stop_enabled,
        "volatility_stop_enabled": settings.volatility_stop_enabled,
        "profit_protect_enabled": settings.profit_protect_enabled,
        "thesis_exit_enabled": settings.thesis_exit_enabled,
        "crypto_lane_enabled": settings.crypto_lane_enabled,
        "crypto_execution_enabled": settings.crypto_execution_enabled,
        "crypto_universe_size": settings.crypto_universe_size,
        "crypto_poll_seconds": settings.crypto_poll_seconds,
        "crypto_quote_currencies": sorted(settings.crypto_quote_currencies),
        "crypto_confirmation_symbols": list(settings.crypto_confirmation_symbols),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runtime_provenance
    print(
        "SAFE_RUNTIME_CONFIG",
        {
            "scan_only": settings.scan_only,
            "execution_enabled": settings.execution_enabled,
            "bot_armed": settings.bot_armed,
            "live_trading": settings.live_trading,
            "strategy_name": settings.strategy_name,
            "scan_symbols": list(settings.scan_symbols),
            "confirmation_symbols": list(settings.confirmation_symbols),
            "fast_window": settings.fast_window,
            "slow_window": settings.slow_window,
            "entry_start": settings.entry_start_raw,
            "entry_cutoff": settings.entry_cutoff_raw,
            "max_hold_minutes": settings.max_hold_minutes,
            "reentry_cooldown_minutes": settings.reentry_cooldown_minutes,
            "stop_pct": str(settings.stop_pct),
            "target_pct": str(settings.target_pct),
            "order_notional": str(settings.order_notional),
            "max_concurrent_positions": settings.max_concurrent_positions,
            "max_new_entries_per_cycle": settings.max_new_entries_per_cycle,
            "max_total_position_notional": str(settings.max_total_position_notional),
            "portfolio_limit_mode": settings.portfolio_limit_mode,
            "max_position_gross_pct": str(settings.max_position_gross_pct),
            "max_portfolio_stop_risk_pct": str(settings.max_portfolio_stop_risk_pct),
            "market_data_batch_size": settings.market_data_batch_size,
            "dynamic_universe_enabled": settings.dynamic_universe_enabled,
            "universe_size": settings.universe_size,
            "universe_candidate_pool_size": settings.universe_candidate_pool_size,
            "universe_refresh_seconds": settings.universe_refresh_seconds,
            "universe_daily_lookback": settings.universe_daily_lookback,
            "max_bar_age_seconds": settings.max_bar_age_seconds,
            "max_spread_pct": str(settings.max_spread_pct),
            "min_quality_score": str(settings.min_quality_score),
            "broker_protective_stop_enabled": settings.broker_protective_stop_enabled,
            "volatility_stop_enabled": settings.volatility_stop_enabled,
            "volatility_stop_multiplier": str(settings.volatility_stop_multiplier),
            "volatility_stop_lookback_bars": settings.volatility_stop_lookback_bars,
            "max_dynamic_stop_pct": str(settings.max_dynamic_stop_pct),
            "profit_protect_enabled": settings.profit_protect_enabled,
            "profit_protect_activation_pct": str(settings.profit_protect_activation_pct),
            "profit_protect_retain_fraction": str(settings.profit_protect_retain_fraction),
            "profit_protect_min_pct": str(settings.profit_protect_min_pct),
            "profit_stop_step_pct": str(settings.profit_stop_step_pct),
            "thesis_exit_enabled": settings.thesis_exit_enabled,
            "thesis_failure_cycles": settings.thesis_failure_cycles,
            "thesis_exit_max_return_pct": str(settings.thesis_exit_max_return_pct),
            "max_pairwise_correlation": str(settings.max_pairwise_correlation),
            "correlation_lookback_bars": settings.correlation_lookback_bars,
            "correlation_min_observations": settings.correlation_min_observations,
            "max_daily_orders": settings.max_daily_orders,
            "max_daily_loss": str(settings.max_daily_loss),
            "exit_states": runtime_state.exit_states,
        },
        flush=True,
    )
    await event_sink.start()
    await slack_notifier.start()

    runtime_provenance = capture_runtime_provenance()
    runtime_start_payload = {
        "trading_mode": settings.trading_mode,
        "scan_only": settings.scan_only,
        "execution_enabled": settings.execution_enabled,
        "bot_armed": settings.bot_armed,
        "strategy_name": settings.strategy_name,
        "persistence_configured": settings.persistence_configured,
        "run_id": settings.trading_run_id or None,
        "strategy_version_id": settings.strategy_version_id or None,
        "runtime": runtime_provenance.as_dict(),
        "configuration": _runtime_configuration_snapshot(),
    }
    runtime_event_key = (
        f"{settings.trading_run_id}:runtime_start:"
        f"{runtime_provenance.runtime_instance_id}"
    )
    runtime_start_persisted = await event_sink.emit_critical(
        event_type="runtime_start",
        event_key=runtime_event_key,
        occurred_at=runtime_provenance.runtime_started_at,
        correlation_id=uuid4().hex,
        payload=runtime_start_payload,
    )
    if not runtime_start_persisted:
        # Preserve eventual delivery without changing execution behavior.
        event_sink.emit(
            event_type="runtime_start",
            event_key=runtime_event_key,
            occurred_at=runtime_provenance.runtime_started_at,
            correlation_id=uuid4().hex,
            payload=runtime_start_payload,
        )
        print(
            "RUNTIME_PROVENANCE_PERSIST_WARNING",
            {
                "runtime_instance_id": runtime_provenance.runtime_instance_id,
                "error": event_sink.last_error,
            },
            flush=True,
        )
    print(
        "RUNTIME_PROVENANCE",
        runtime_provenance.as_dict(),
        flush=True,
    )
    slack_notifier.notify_runtime_start(
        trading_mode=settings.trading_mode,
        strategy_name=settings.strategy_name,
        strategy_version_id=settings.strategy_version_id,
        execution_authorized=settings.execution_authorized,
    )
    if settings.crypto_lane_enabled:
        runtime_state.record_event(
            kind="crypto_runtime",
            action="startup",
            message=(
                "24/7 crypto lane online; "
                + ("live entry flag enabled" if settings.crypto_execution_enabled else "live entries gated")
            ),
            reason=(
                f"strategy={settings.crypto_strategy_version_id}; "
                f"model={settings.crypto_model_version}; "
                f"calibration={settings.crypto_calibration_version}; "
                f"regime={settings.crypto_regime_version}"
            ),
            payload={
                "market_lane": "crypto",
                "strategy_family": settings.crypto_strategy_family,
                "strategy_version_id": settings.crypto_strategy_version_id,
                "model_version": settings.crypto_model_version,
                "calibration_version": settings.crypto_calibration_version,
                "regime_version": settings.crypto_regime_version,
                "execution_adapter_version": settings.crypto_execution_adapter_version,
                "execution_enabled": settings.crypto_execution_enabled,
                "calibration_promoted": settings.crypto_calibration_promoted,
            },
        )

    if settings.credentials_configured and not settings.scan_only:
        runtime_state.begin_cycle(uuid4().hex)
        await reconcile_broker_state(startup=True, force=True)
    elif settings.scan_only:
        runtime_state.set_reconciliation(
            {"safe_to_enter": False, "reason": "scan-only service"},
            startup=True,
        )
    else:
        runtime_state.startup_reconciled = True
        runtime_state.reconciliation_safe = False

    await research_reports.start()
    task = asyncio.create_task(monitor_loop())
    crypto_task = asyncio.create_task(crypto_monitor_loop())
    slack_market_task = asyncio.create_task(slack_market_observer_loop())
    yield
    _stop.set()
    await task
    await crypto_task
    await slack_market_task
    await research_reports.stop()
    event_sink.emit(
        event_type="runtime_stop",
        correlation_id=uuid4().hex,
        payload={
            "runtime_instance_id": runtime_provenance.runtime_instance_id,
            "deployment_id": runtime_provenance.deployment_id,
            "git_commit": runtime_provenance.git_commit,
        },
    )
    await event_sink.stop()
    await slack_notifier.stop()


app = FastAPI(title="RHEN", version=RHEN_VERSION, lifespan=lifespan)


@app.get("/health")
async def health():
    signal = runtime_state.last_signal or {}
    order = runtime_state.last_order or {}
    return {
        "ok": True,
        "system": "RHEN",
        "trading_mode": settings.trading_mode,
        "order_execution_present": True,
        "execution_enabled": settings.execution_enabled,
        "execution_authorized": settings.execution_authorized,
        "bot_armed": settings.bot_armed,
        "scan_only": settings.scan_only,
        "runtime_paused": runtime_state.paused,
        "credentials_configured": settings.credentials_configured,
        "funding_ready": runtime_state.funding_ready,
        "startup_reconciled": runtime_state.startup_reconciled,
        "reconciliation_safe": runtime_state.reconciliation_safe,
        "last_reconciliation": runtime_state.last_reconciliation,
        "scan_symbol_count": len(settings.scan_symbols),
        "scan_ready_symbols": _ready_symbols(),
        "last_strategy_at": runtime_state.last_strategy_at,
        "last_decision": runtime_state.last_decision,
        "last_signal": {
            "action": signal.get("action"),
            "symbol": signal.get("symbol"),
            "reason": signal.get("reason"),
        },
        "last_order": {
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "status": order.get("status"),
            "reason": order.get("reason"),
        },
        "last_error": runtime_state.last_error,
        "persistence": event_sink.status(),
        "research_reporting": research_reports.status(),
        "slack_notifications": slack_notifier.status(),
        "runtime_provenance": runtime_provenance.as_dict() if runtime_provenance else None,
        "crypto": {
            "enabled": settings.crypto_lane_enabled,
            "execution_enabled": settings.crypto_execution_enabled,
            "session_model": "24x7",
            "market_lane": "crypto",
            "strategy_family": settings.crypto_strategy_family,
            "strategy_version_id": settings.crypto_strategy_version_id,
            "model_version": settings.crypto_model_version,
            "calibration_version": settings.crypto_calibration_version,
            "regime_version": settings.crypto_regime_version,
            "execution_adapter_version": settings.crypto_execution_adapter_version,
            "calibration_promoted": settings.crypto_calibration_promoted,
            "graen_promotion": runtime_state.crypto_graen_promotion,
            "scanner_healthy": runtime_state.crypto_scanner_healthy,
            "execution_healthy": runtime_state.crypto_execution_healthy,
            "last_market_data_at": runtime_state.crypto_last_market_data_at,
            "last_scan_at": runtime_state.crypto_last_scan_at,
            "symbols_scanned": runtime_state.crypto_candidates_generated,
            "candidates_generated": runtime_state.crypto_candidates_generated,
            "qualified_candidates": runtime_state.crypto_qualified_candidates,
            "rejection_counts": runtime_state.crypto_rejection_counts,
            "active_positions": runtime_state.crypto_active_positions,
            "aggregate_exposure_utilization_pct": (
                round(
                    float(Decimal(runtime_state.crypto_aggregate_exposure)
                          / settings.crypto_max_total_position_notional) * 100.0,
                    3,
                )
                if settings.crypto_max_total_position_notional > 0 else None
            ),
            "forward_evidence": runtime_state.crypto_forward_evidence_state,
            "latest_replay": runtime_state.crypto_replay_state,
            "breaker_state": runtime_state.crypto_breaker_state,
            "protective_order_status": runtime_state.crypto_protective_status,
            "universe_source": runtime_state.crypto_universe_source,
            "active_count": len(runtime_state.crypto_universe_active_symbols),
            "candidate_count": runtime_state.crypto_universe_candidate_count,
            "eligible_count": runtime_state.crypto_universe_eligible_count,
            "active_symbols": runtime_state.crypto_universe_active_symbols,
            "updated_at": runtime_state.crypto_universe_updated_at,
            "error": runtime_state.crypto_universe_error,
            "last_decision": runtime_state.crypto_last_decision,
            "last_signal": runtime_state.crypto_last_signal,
            "last_scan": runtime_state.crypto_last_completed_scan,
            "last_order": runtime_state.crypto_last_order,
            "last_error": runtime_state.crypto_last_error,
            "last_execution_at": runtime_state.crypto_last_execution_at,
            "execution_context": runtime_state.crypto_last_execution_context,
            "risk": {
                "order_notional": str(settings.crypto_order_notional),
                "max_order_notional": str(settings.crypto_max_order_notional),
                "max_total_position_notional": str(
                    settings.crypto_max_total_position_notional
                ),
                "max_concurrent_positions": settings.crypto_max_concurrent_positions,
                "max_entries_24h": settings.crypto_max_entries_24h,
                "max_spread_pct": str(settings.crypto_max_spread_pct),
                "stop_pct": str(settings.crypto_stop_pct),
                "target_pct": str(settings.crypto_target_pct),
                "max_hold_minutes": settings.crypto_max_hold_minutes,
            },
        },
    }


@app.get("/v1/scheduler/configuration")
async def scheduler_configuration(x_anevum_scheduler_token: str | None = Header(default=None)):
    require_scheduler_token(x_anevum_scheduler_token)
    return scheduler_configuration_snapshot()


@app.get("/v1/scheduler/calendar")
async def scheduler_calendar(
    start: date,
    end: date,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    if end < start or (end - start).days > 31:
        raise HTTPException(status_code=422, detail="invalid scheduler calendar range")
    rows = await market_data.market_calendar_details(start=start, end=end)
    return {
        "ok": True,
        "calendar": "US_EQUITIES",
        "timezone": "America/New_York",
        "sessions": [
            {
                **row,
                "date": row["date"].isoformat(),
            }
            for row in rows
        ],
    }


@app.post("/v1/scheduler/preflight")
async def scheduler_preflight(
    request: SchedulerSessionRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    session = await scheduler_session_detail(request.session)
    try:
        account, clock = await asyncio.gather(client.account(), client.clock())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"broker reachability failed: {type(exc).__name__}",
        ) from exc

    persistence = event_sink.status()
    config = scheduler_configuration_snapshot()
    checks = {
        "broker_api_reachable": bool(account),
        "runtime_alive": runtime_state.last_error is None,
        "persistence_enabled": bool(persistence.get("enabled")),
        "strategy_identified": bool(settings.strategy_version_id),
        "startup_reconciled": bool(runtime_state.startup_reconciled),
        "reconciliation_safe": bool(runtime_state.reconciliation_safe),
        "market_session_valid": True,
        "crypto_execution_disabled": not bool(settings.crypto_execution_enabled),
    }
    payload = {
        "ok": all(checks.values()),
        "workflow": "rhen.preflight",
        "session": session,
        "scheduled_at": request.scheduled_at.isoformat() if request.scheduled_at else None,
        "checks": checks,
        "configuration": config,
        "runtime": {
            "git_commit": runtime_provenance.git_commit if runtime_provenance else None,
            "deployment_id": runtime_provenance.deployment_id if runtime_provenance else None,
        },
        "broker_clock": {
            "is_open": bool(clock.get("is_open")),
            "timestamp": clock.get("timestamp"),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        },
        "persistence": {
            "enabled": persistence.get("enabled"),
            "last_error": persistence.get("last_error"),
            "queue_depth": persistence.get("queue_depth"),
        },
    }
    if not payload["ok"]:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.post("/v1/scheduler/market-open")
async def scheduler_market_open(
    request: SchedulerSessionRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    session = await scheduler_session_detail(request.session)
    try:
        clock = await client.clock()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"broker clock unavailable: {type(exc).__name__}",
        ) from exc

    checks = {
        "market_open": bool(clock.get("is_open")),
        "runtime_alive": runtime_state.last_error is None,
        "startup_reconciled": bool(runtime_state.startup_reconciled),
        "reconciliation_safe": bool(runtime_state.reconciliation_safe),
        "strategy_identified": bool(settings.strategy_version_id),
    }
    payload = {
        "ok": all(checks.values()),
        "workflow": "rhen.market_open",
        "session": session,
        "checks": checks,
        "runtime_state": {
            "paused": runtime_state.paused,
            "entries_enabled": runtime_state.entries_enabled,
            "last_poll_at": runtime_state.last_poll_at,
        },
        "configuration": scheduler_configuration_snapshot(),
    }
    if not payload["ok"]:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.post("/v1/scheduler/session-close")
async def scheduler_session_close(
    request: SchedulerSessionRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    session = await scheduler_session_detail(request.session)
    account = await refresh_account_state()
    reconciliation = await reconcile_broker_state(account, force=True)
    post_event = await research_reports.generate_post_event_evidence(request.session)
    report = await research_reports.generate_daily(request.session)
    return {
        "ok": True,
        "workflow": "rhen.session_close",
        "session": session,
        "reconciliation": reconciliation,
        "post_event": post_event,
        "daily_report": {
            "report_key": report.get("report_key"),
            "report_version": report.get("report_version"),
            "classification": report.get("classification"),
            "data_quality_warnings": report.get("data_quality_warnings"),
        },
        "configuration": scheduler_configuration_snapshot(),
    }


@app.post("/v1/scheduler/weekly-review")
async def scheduler_weekly_review(
    request: SchedulerSessionRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    session = await scheduler_session_detail(request.session)
    period_start = request.session - timedelta(days=request.session.weekday())
    report = await research_reports.generate_weekly(period_start, request.session)
    return {
        "ok": True,
        "workflow": "rhen.weekly_review",
        "session": session,
        "weekly_report": {
            "report_key": report.get("report_key"),
            "report_version": report.get("report_version"),
            "completeness_state": report.get("completeness_state"),
            "period_start": report.get("period_start"),
            "period_end": report.get("period_end"),
        },
        "configuration": scheduler_configuration_snapshot(),
    }


@app.get("/v1/session-calendar")
async def session_calendar():
    """Public exchange-session dates only; no account or order data."""
    today = datetime.now(NY).date()
    from datetime import timedelta
    sessions = await market_data.market_calendar(start=today, end=today + timedelta(days=14))
    future = [session for session in sessions if session > today]
    from .research_scheduler import is_last_session_of_week
    return {"session_date": today.isoformat(), "is_trading_session": today in sessions,
            "is_last_session_of_week": bool(today in sessions and future
                and is_last_session_of_week(today, future[0]))}


@app.get("/v1/status")
async def status(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return {
        "system": "RHEN",
        "trading_mode": settings.trading_mode,
        "execution_enabled": settings.execution_enabled,
        "execution_authorized": settings.execution_authorized,
        "paper_execution_authorized": settings.paper_execution_authorized,
        "live_execution_authorized": settings.live_execution_authorized,
        "bot_armed": settings.bot_armed,
        "runtime_paused": runtime_state.paused,
        "startup_reconciled": runtime_state.startup_reconciled,
        "reconciliation_safe": runtime_state.reconciliation_safe,
        "last_reconciliation": runtime_state.last_reconciliation,
        "allowed_symbols": sorted(settings.allowed_symbols),
        "strategy": {
            "name": settings.strategy_name,
            "scan_symbols": list(settings.scan_symbols),
            "confirmation_symbols": list(settings.confirmation_symbols),
            "opening_range_minutes": settings.opening_range_minutes,
            "fast_window": settings.fast_window,
            "slow_window": settings.slow_window,
            "min_momentum_pct": str(settings.min_momentum_pct),
            "min_vwap_edge_pct": str(settings.min_vwap_edge_pct),
            "min_confirmations": settings.min_confirmations,
            "regime_window": settings.regime_window,
            "regime_min_confirmations": settings.regime_min_confirmations,
            "regime_min_return_pct": str(settings.regime_min_return_pct),
            "max_vwap_extension_pct": str(settings.max_vwap_extension_pct),
            "loss_streak_limit": settings.loss_streak_limit,
            "loss_streak_cooldown_minutes": settings.loss_streak_cooldown_minutes,
            "max_hold_minutes": settings.max_hold_minutes,
            "reentry_cooldown_minutes": settings.reentry_cooldown_minutes,
            "max_opening_range_pct": str(settings.max_opening_range_pct),
            "max_breakout_extension_pct": str(settings.max_breakout_extension_pct),
            "entry_start": settings.entry_start_raw,
            "entry_cutoff": settings.entry_cutoff_raw,
            "force_flat_time": settings.force_flat_time_raw,
            "stop_pct": str(settings.stop_pct),
            "target_pct": str(settings.target_pct),
            "bar_timeframe": settings.bar_timeframe,
            "data_feed": settings.data_feed,
            "order_notional": str(settings.order_notional),
            "max_bar_age_seconds": settings.max_bar_age_seconds,
            "max_spread_pct": str(settings.max_spread_pct),
            "min_quality_score": str(settings.min_quality_score),
        },
        "risk": {
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
            "max_concurrent_positions": settings.max_concurrent_positions,
            "max_new_entries_per_cycle": settings.max_new_entries_per_cycle,
            "max_total_position_notional": str(settings.max_total_position_notional),
            "max_daily_orders": settings.max_daily_orders,
            "max_daily_loss": str(settings.max_daily_loss),
            "sizing_mode": settings.sizing_mode,
            "risk_per_trade_pct": str(settings.risk_per_trade_pct),
            "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
            "min_order_notional": str(settings.min_order_notional),
        },
        "persistence": event_sink.status(),
        "research_reporting": research_reports.status(),
        "crypto": {
            "enabled": settings.crypto_lane_enabled,
            "execution_enabled": settings.crypto_execution_enabled,
            "session_model": "24x7",
            "market_lane": "crypto",
            "strategy_family": settings.crypto_strategy_family,
            "strategy_version_id": settings.crypto_strategy_version_id,
            "model_version": settings.crypto_model_version,
            "calibration_version": settings.crypto_calibration_version,
            "regime_version": settings.crypto_regime_version,
            "execution_adapter_version": settings.crypto_execution_adapter_version,
            "calibration_promoted": settings.crypto_calibration_promoted,
            "graen_promotion": runtime_state.crypto_graen_promotion,
            "scanner_healthy": runtime_state.crypto_scanner_healthy,
            "execution_healthy": runtime_state.crypto_execution_healthy,
            "universe": {
                "source": runtime_state.crypto_universe_source,
                "active_symbols": runtime_state.crypto_universe_active_symbols,
                "candidate_count": runtime_state.crypto_universe_candidate_count,
                "eligible_count": runtime_state.crypto_universe_eligible_count,
                "updated_at": runtime_state.crypto_universe_updated_at,
                "error": runtime_state.crypto_universe_error,
            },
            "scan": {
                "last_scan_at": runtime_state.crypto_last_scan_at,
                "last_market_data_at": runtime_state.crypto_last_market_data_at,
                "symbols_scanned": runtime_state.crypto_candidates_generated,
                "candidates_generated": runtime_state.crypto_candidates_generated,
                "qualified_candidates": runtime_state.crypto_qualified_candidates,
                "rejection_counts": runtime_state.crypto_rejection_counts,
            },
            "positions": {
                "active_count": runtime_state.crypto_active_positions,
                "aggregate_exposure": runtime_state.crypto_aggregate_exposure,
                "max_aggregate_exposure": str(settings.crypto_max_total_position_notional),
            },
            "recent_orders": runtime_state.crypto_recent_orders,
            "last_order": runtime_state.crypto_last_order,
            "protective_order_status": runtime_state.crypto_protective_status,
            "forward_evidence": runtime_state.crypto_forward_evidence_state,
            "latest_replay": runtime_state.crypto_replay_state,
            "breaker_state": runtime_state.crypto_breaker_state,
            "last_error": runtime_state.crypto_last_error,
        },
        "runtime": {
            "started_at": runtime_state.started_at,
            "last_poll_at": runtime_state.last_poll_at,
            "last_strategy_at": runtime_state.last_strategy_at,
            "funding_ready": runtime_state.funding_ready,
            "cash": runtime_state.last_cash,
            "buying_power": runtime_state.last_buying_power,
            "equity": runtime_state.last_equity,
            "last_equity": runtime_state.last_equity_reference,
            "day_pnl": runtime_state.last_day_pnl,
            "risk_reference_equity": runtime_state.last_risk_reference_equity,
            "cash_flow_accounting": runtime_state.last_cash_flow_accounting,
            "cash_flow_error": runtime_state.last_cash_flow_error,
            "last_scan": runtime_state.last_scan,
            "last_completed_scan": runtime_state.last_completed_scan,
            "last_signal": runtime_state.last_signal,
            "last_decision": runtime_state.last_decision,
            "last_order": runtime_state.last_order,
            "last_error": runtime_state.last_error,
        },
    }


@app.get("/v1/account")
async def account(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return await client.account()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/positions")
async def positions(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return await client.positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/orders")
async def orders(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return await client.recent_orders(limit=100)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/run-once")
async def run_once(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if runtime_state.paused:
        raise HTTPException(status_code=409, detail="runtime is paused")
    try:
        if settings.scan_only:
            return await scanner.scan_once()
        account = await refresh_account_state()
        await reconcile_broker_state(account, force=True)
        return await engine.run_once()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/pause")
async def pause(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    runtime_state.paused = True
    runtime_state.last_decision = "runtime paused by administrator"
    return {"paused": True}


@app.post("/v1/resume-paper")
async def resume_paper(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if settings.trading_mode != "paper":
        raise HTTPException(
            status_code=409,
            detail="remote resume is paper-only; live mode must be armed through deployment configuration",
        )
    runtime_state.paused = False
    runtime_state.last_decision = "paper runtime resumed by administrator"
    return {"paused": False}


@app.get("/v1/command/session")
async def command_session(authorization: str | None = Header(default=None)):
    identity = await require_command_admin(authorization)
    return {
        "authenticated": True,
        "email": identity.get("email"),
        "auth_source": identity.get("auth_source") or "cloudflare_access",
        "command_admin": True,
    }


@app.get("/v1/command/status")
async def command_status(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    try:
        return await command_snapshot()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/command/reports/daily")
async def command_daily_report(
    session: date | None = None,
    authorization: str | None = Header(default=None),
):
    await require_command_admin(authorization)
    try:
        report = await research_reports.fetch_daily_report(session=session)
        if report is None:
            raise HTTPException(status_code=404, detail="canonical daily report not found")
        return report
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/command/evidence")
async def command_evidence(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    try:
        return await research_reports.fetch_command_evidence()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/command/reports/weekly")
async def command_weekly_report(
    week_end: date | None = None,
    authorization: str | None = Header(default=None),
):
    await require_command_admin(authorization)
    try:
        report = await research_reports.fetch_weekly_report(end_date=week_end)
        if report is None:
            raise HTTPException(status_code=404, detail="canonical weekly report not found")
        return report
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/command/reports/weekly/regenerate")
async def command_regenerate_weekly_report(
    week_end: date,
    authorization: str | None = Header(default=None),
):
    await require_command_admin(authorization)
    try:
        return await research_reports.regenerate_weekly(week_end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/command/entries/disable")
async def command_disable_entries(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    runtime_state.entries_enabled = False
    runtime_state.last_decision = "new entries disabled from COMMAND"
    runtime_state.record_event(
        kind="control",
        action="entries_disabled",
        message=runtime_state.last_decision,
    )
    return {"entries_enabled": False, "message": runtime_state.last_decision}


@app.post("/v1/command/entries/enable")
async def command_enable_entries(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    if settings.scan_only:
        raise HTTPException(status_code=409, detail="scan-only service cannot enable entries")
    if runtime_state.paused:
        raise HTTPException(status_code=409, detail="runtime is paused")
    if not settings.execution_authorized:
        raise HTTPException(status_code=409, detail="live/paper execution is not authorized")
    if not runtime_state.startup_reconciled or not runtime_state.reconciliation_safe:
        raise HTTPException(
            status_code=409,
            detail="broker/canonical reconciliation must be safe before enabling entries",
        )
    runtime_state.entries_enabled = True
    runtime_state.last_decision = "new entries enabled from COMMAND"
    runtime_state.record_event(
        kind="control",
        action="entries_enabled",
        message=runtime_state.last_decision,
    )
    return {"entries_enabled": True, "message": runtime_state.last_decision}


@app.post("/v1/command/orders/cancel")
async def command_cancel_orders(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    if settings.scan_only:
        raise HTTPException(status_code=409, detail="scan-only service cannot cancel orders")
    try:
        result = await engine.cancel_pending_bot_orders()
        if result.get("action") == "blocked":
            raise HTTPException(status_code=409, detail=str(result.get("reason")))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/command/position/close")
async def command_close_position(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    if settings.scan_only:
        raise HTTPException(status_code=409, detail="scan-only service cannot close positions")
    runtime_state.entries_enabled = False
    try:
        result = await engine.close_managed_position()
        if result.get("action") == "blocked":
            raise HTTPException(status_code=409, detail=str(result.get("reason")))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))