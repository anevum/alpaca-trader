from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
from fastapi import FastAPI, Header, HTTPException

from .alpaca_client import AlpacaClient
from .config import get_settings
from .execution import ExecutionEngine
from .market_data import MarketDataClient
from .state import runtime_state
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy

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
engine = ExecutionEngine(settings, client, market_data, strategy, runtime_state)
_stop = asyncio.Event()


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
            "runtime_paused": runtime_state.paused,
            "entries_enabled": runtime_state.entries_enabled,
            "funding_ready": runtime_state.funding_ready,
            "last_strategy_at": runtime_state.last_strategy_at,
            "last_decision": runtime_state.last_decision,
            "last_error": runtime_state.last_error,
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
        },
        "risk": {
            "max_daily_orders": settings.max_daily_orders,
            "entry_orders_today": entry_count,
            "entries_remaining": max(settings.max_daily_orders - entry_count, 0),
            "max_daily_loss": str(settings.max_daily_loss),
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
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


async def refresh_account_state() -> None:
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


async def monitor_loop():
    while not _stop.is_set():
        try:
            if settings.credentials_configured:
                await refresh_account_state()
                if settings.execution_enabled and settings.bot_armed and not runtime_state.paused:
                    await engine.run_once()
            else:
                runtime_state.funding_ready = False
                runtime_state.last_error = "credentials not configured"
        except Exception as exc:
            runtime_state.last_error = f"{type(exc).__name__}: {exc}"

        try:
            await asyncio.wait_for(_stop.wait(), timeout=settings.poll_seconds)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(monitor_loop())
    yield
    _stop.set()
    await task


app = FastAPI(title="Alpaca Trading Bot", version="0.6.0", lifespan=lifespan)


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
        "runtime_paused": runtime_state.paused,
        "credentials_configured": settings.credentials_configured,
        "funding_ready": runtime_state.funding_ready,
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
        },
        "risk": {
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
            "max_daily_orders": settings.max_daily_orders,
            "max_daily_loss": str(settings.max_daily_loss),
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
    if runtime_state.paused:
        raise HTTPException(status_code=409, detail="runtime is paused")
    if not settings.execution_authorized:
        raise HTTPException(status_code=409, detail="live/paper execution is not authorized")
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
