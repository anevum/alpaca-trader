from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException

from .alpaca_client import AlpacaClient
from .config import get_settings
from .execution import ExecutionEngine
from .market_data import MarketDataClient
from .persistence import TradingEventSink
from .sizing import sizing_snapshot
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
_stop = asyncio.Event()
NY = ZoneInfo("America/New_York")


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


runtime_state.set_event_emitter(emit_runtime_event)


def require_admin(authorization: str | None):
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    expected = f"Bearer {settings.admin_token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


SUPABASE_URL = "https://mfntzxheldzdvlokyntk.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_XfkgeXau2-6XOPzoXF-Nnw_FSnx0Sae"
COMMAND_FOUNDER_EMAIL = "devon@anevum.com"


async def require_command_admin(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="RHENLINK authorization required")

    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="RHENLINK authorization required")

    async with httpx.AsyncClient(timeout=8.0) as http:
        response = await http.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                "Authorization": f"Bearer {token}",
                "Cache-Control": "no-store",
            },
        )
    payload = response.json() if response.content else {}
    if not response.is_success:
        raise HTTPException(status_code=401, detail="RHENLINK session is not valid")

    metadata = payload.get("app_metadata") or {}
    role = str(metadata.get("role") or "").strip().lower()
    email = str(payload.get("email") or "").strip().lower()
    confirmed = bool(payload.get("email_confirmed_at") or payload.get("confirmed_at"))
    authorized = (
        (email == COMMAND_FOUNDER_EMAIL and confirmed)
        or metadata.get("command_admin") is True
        or metadata.get("wiki_admin") is True
        or role in {"owner", "founder", "admin", "command_admin", "wiki_admin"}
    )
    if not authorized:
        raise HTTPException(status_code=403, detail="COMMAND administrator authorization required")
    return payload


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
        },
        "account": {
            "equity": str(account.get("equity", "0")),
            "last_equity": str(account.get("last_equity", "0")),
            "day_pnl": str(equity - last_equity),
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
            "max_daily_loss_pct": str(settings.max_daily_loss_pct),
            "effective_daily_loss_limit": allocator["daily_loss_limit"],
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
        "scanner": runtime_state.last_scan,
        "history": runtime_state.decision_history,
        "positions": [public_position(position) for position in positions],
        "open_orders": [public_order(order) for order in open_orders],
        "recent_orders": [public_order(order) for order in bot_orders[:30]],
        "last_order": runtime_state.last_order,
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
    runtime_state.last_day_pnl = str(equity - last_equity)
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

        try:
            await asyncio.wait_for(_stop.wait(), timeout=settings.poll_seconds)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
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
            "max_daily_loss_pct": str(settings.max_daily_loss_pct),
            "exit_states": runtime_state.exit_states,
        },
        flush=True,
    )
    await event_sink.start()
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

    event_sink.emit(
        event_type="runtime_start",
        correlation_id=uuid4().hex,
        payload={
            "trading_mode": settings.trading_mode,
            "scan_only": settings.scan_only,
            "execution_enabled": settings.execution_enabled,
            "bot_armed": settings.bot_armed,
            "strategy_name": settings.strategy_name,
            "persistence_configured": settings.persistence_configured,
        },
    )
    task = asyncio.create_task(monitor_loop())
    yield
    _stop.set()
    await task
    event_sink.emit(event_type="runtime_stop", correlation_id=uuid4().hex)
    await event_sink.stop()


app = FastAPI(title="Alpaca Trading Bot", version="0.8.0", lifespan=lifespan)


@app.get("/health")
async def health():
    signal = runtime_state.last_signal or {}
    order = runtime_state.last_order or {}
    return {
        "ok": True,
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
    }


@app.get("/v1/status")
async def status(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return {
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
            "max_daily_loss_pct": str(settings.max_daily_loss_pct),
            "sizing_mode": settings.sizing_mode,
            "risk_per_trade_pct": str(settings.risk_per_trade_pct),
            "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
            "min_order_notional": str(settings.min_order_notional),
        },
        "persistence": event_sink.status(),
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
            "last_scan": runtime_state.last_scan,
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


@app.get("/v1/command/status")
async def command_status(authorization: str | None = Header(default=None)):
    await require_command_admin(authorization)
    try:
        return await command_snapshot()
    except HTTPException:
        raise
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
