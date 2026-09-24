from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from decimal import Decimal
from fastapi import FastAPI, Header, HTTPException

from .alpaca_client import AlpacaClient
from .config import get_settings
from .state import runtime_state
from .strategy import DisabledStrategy

settings = get_settings()
client = AlpacaClient(settings)
strategy = DisabledStrategy()
_stop = asyncio.Event()


def require_admin(authorization: str | None):
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    expected = f"Bearer {settings.admin_token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def monitor_loop():
    while not _stop.is_set():
        try:
            runtime_state.mark_poll()
            if settings.credentials_configured:
                account = await client.account()
                cash = Decimal(str(account.get("cash", "0")))
                runtime_state.last_cash = str(cash)
                runtime_state.last_buying_power = str(account.get("buying_power", "0"))
                runtime_state.funding_ready = cash >= settings.min_ready_cash
                runtime_state.last_error = None
            else:
                runtime_state.funding_ready = False
                runtime_state.last_error = "credentials not configured"

            await strategy.evaluate()
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


app = FastAPI(title="Alpaca Trading Bot", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "trading_mode": settings.trading_mode,
        "order_execution_present": False,
        "bot_armed": settings.bot_armed,
        "credentials_configured": settings.credentials_configured,
        "funding_ready": runtime_state.funding_ready,
        "last_error": runtime_state.last_error,
    }


@app.get("/v1/status")
async def status(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return {
        "trading_mode": settings.trading_mode,
        "order_execution_present": False,
        "live_execution_authorized": settings.live_execution_authorized,
        "bot_armed": settings.bot_armed,
        "allowed_symbols": sorted(settings.allowed_symbols),
        "min_ready_cash": str(settings.min_ready_cash),
        "risk": {
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
            "max_daily_orders": settings.max_daily_orders,
            "max_daily_loss": str(settings.max_daily_loss),
        },
        "runtime": {
            "started_at": runtime_state.started_at,
            "last_poll_at": runtime_state.last_poll_at,
            "funding_ready": runtime_state.funding_ready,
            "cash": runtime_state.last_cash,
            "buying_power": runtime_state.last_buying_power,
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
