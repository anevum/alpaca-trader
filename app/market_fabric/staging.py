"""Isolated observation process: no ExecutionEngine, main or research runtime."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import json
import os

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect

from app.command_access import authenticate_command_admin, CommandAuthError
from app.config import Settings
from app.market_data import MarketDataClient
from app.rhen44_release import release_status
from app.sizing import calculate_entry_notional, effective_gross_limit, effective_position_limit, long_exposure
from app.strategy import RollingMomentumVwapStrategy, OpeningRangeVwapStrategy
from .runtime import ShadowFabric


def assert_observer_only(settings):
    crypto_enabled = os.getenv("CRYPTO_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if settings.execution_enabled or settings.bot_armed or settings.live_trading or not settings.scan_only or settings.extended_equity_execution_enabled or crypto_enabled:
        raise ValueError("isolated observer requires all execution/arming flags disabled")


class ReadOnlyBroker:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def read(self, path):
        if path not in {"/v2/account", "/v2/positions", "/v2/orders"}:
            raise ValueError("read-only broker path not allowed")
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            response = await client.get(self.settings.base_url+path, headers={"APCA-API-KEY-ID":self.settings.alpaca_api_key,
                "APCA-API-SECRET-KEY":self.settings.alpaca_api_secret}, params={"status":"open","nested":"true","limit":500} if path=="/v2/orders" else None)
            response.raise_for_status()
            return response.json()

    async def snapshot(self):
        account, positions, orders = await asyncio.gather(*(self.read(p) for p in ("/v2/account","/v2/positions","/v2/orders")))
        if len(orders) >= 500:
            raise ValueError("open order reconciliation may be truncated")
        s = self.settings
        return {"account":account,"positions":positions,"open_orders":orders,
            "sizing":{"base_safe_notional":calculate_entry_notional(s,account,positions),"cash":max(Decimal("0"),Decimal(str(account["cash"]))),
                "hard_gross_envelope":effective_gross_limit(s,account),"existing_gross_exposure":long_exposure(positions),
                "hard_caps":{"max_order_notional":s.max_order_notional,"max_position_notional":effective_position_limit(s,account)}}}


def champion_signal(settings):
    s = settings
    common = dict(stop_pct=s.stop_pct,target_pct=s.target_pct,entry_start=s.entry_start,entry_cutoff=s.entry_cutoff,confirmation_symbols=s.confirmation_symbols)
    if s.strategy_name == "opening_range_vwap":
        return OpeningRangeVwapStrategy(**common,opening_range_minutes=s.opening_range_minutes,max_opening_range_pct=s.max_opening_range_pct,max_breakout_extension_pct=s.max_breakout_extension_pct)
    return RollingMomentumVwapStrategy(**common,fast_window=s.fast_window,slow_window=s.slow_window,min_momentum_pct=s.min_momentum_pct,min_vwap_edge_pct=s.min_vwap_edge_pct,
        min_confirmations=s.min_confirmations,regime_window=s.regime_window,regime_min_confirmations=s.regime_min_confirmations,regime_min_return_pct=s.regime_min_return_pct,
        max_vwap_extension_pct=s.max_vwap_extension_pct,volatility_stop_enabled=s.volatility_stop_enabled,volatility_stop_multiplier=s.volatility_stop_multiplier,
        volatility_stop_lookback_bars=s.volatility_stop_lookback_bars,max_dynamic_stop_pct=s.max_dynamic_stop_pct)


def create_app(settings=None):
    settings = settings or Settings()
    assert_observer_only(settings)
    fabric = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal fabric
        telemetry_task = None
        strategy = champion_signal(settings)
        def evaluate(symbol, now, store):
            def bars(s):
                return [{"t":b["timestamp"],"o":b["open"],"h":b["high"],"l":b["low"],"c":b["close"],"v":b["volume"],
                    **({"vw":b["vwap"]} if b.get("vwap") is not None else {})} for b in store.rows.get(s,{}).get("bars",())]
            return strategy.evaluate(bars(symbol),{s:bars(s) for s in settings.confirmation_symbols},symbol,False,settings.order_notional,now=now)
        if settings.rhen_market_stream_enabled:
            fabric = ShadowFabric(settings, MarketDataClient(settings), evaluator=evaluate, account_reader=ReadOnlyBroker(settings).snapshot)
            fabric.start()
            async def telemetry():
                while True:
                    # Private Railway operational logs; no credentials, token,
                    # account dollars, positions, symbols, orders or fill payloads.
                    body = await health()
                    body.update(provenance="OPERATIONAL",observed_at=datetime.now(timezone.utc).isoformat(),
                        broker_stream_state=fabric.broker.state if fabric.broker else "DISABLED",
                        account_quality=fabric.visual.system.get("account_observation",{}).get("quality_state","UNAVAILABLE"),
                        reconstruction_source=fabric.visual.system.get("reconstruction_source"),
                        bootstrap_error=fabric.visual.system.get("bootstrap_error"),
                        bar_count=sum(len(row["bars"]) for row in fabric.store.rows.values()),
                        quote_count=sum(row.get("quote") is not None for row in fabric.store.rows.values()),
                        subscribed_channels=sorted(fabric.manager.subscribed_channels),
                        unavailable_channels=sorted(fabric.manager.unavailable_channels),
                        scanner_summary=fabric.rejections.summary(datetime.now(timezone.utc)))
                    print("RHEN44_SHADOW_TELEMETRY "+json.dumps(body,allow_nan=False),flush=True)
                    await asyncio.sleep(30)
            telemetry_task = asyncio.create_task(telemetry())
        try:
            yield
        finally:
            if telemetry_task is not None:
                telemetry_task.cancel()
                await asyncio.gather(telemetry_task,return_exceptions=True)
            if fabric is not None:
                await fabric.stop()
                fabric = None

    app = FastAPI(title="RHEN 4.4 isolated shadow",lifespan=lifespan)
    async def authorize(authorization):
        try:
            return await authenticate_command_admin(authorization,team_domain=settings.command_access_team_domain,
                audience=settings.command_access_aud,allowed_emails=settings.command_access_emails_raw)
        except CommandAuthError as exc:
            raise HTTPException(exc.status_code,exc.detail) from exc

    @app.get("/health")
    async def health():
        return {"ok":True,"system":"RHEN44_SHADOW","source_commit":os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "execution_authority":False,"broker_orders_possible":False,"isolated_runtime":True,
            "rhen44":release_status(settings,fabric),"market_connection":fabric.store.connection if fabric else "DISABLED",
            "stream_errors":fabric.manager.errors if fabric else 0,"stream_error":fabric.manager.last_error if fabric else None,
            "stream_error_code":fabric.manager.last_error_code if fabric else None,
            "subscribed_symbols":len(fabric.store.subscribed) if fabric else 0,"intended_symbols":len(settings.extended_equity_symbols),
            "context":fabric.store.context if fabric else None}

    @app.get("/v1/command/shadow/status")
    async def status(authorization: str | None = Header(default=None)):
        await authorize(authorization)
        return fabric.visual.snapshot() if fabric else {"state":"DISABLED"}

    @app.websocket("/v1/command/stream")
    async def stream(websocket: WebSocket):
        try:
            await authorize(websocket.headers.get("authorization"))
        except HTTPException:
            await websocket.close(code=1008)
            return
        if not settings.command_live_stream_enabled or fabric is None:
            await websocket.close(code=1013)
            return
        expiry=float(jwt.decode(websocket.headers["authorization"][7:],options={"verify_signature":False})["exp"])
        await websocket.accept()
        queue=fabric.visual.publisher.subscribe()
        try:
            while True:
                remaining=expiry-datetime.now(timezone.utc).timestamp()
                if remaining<=0:
                    await websocket.close(code=1008)
                    break
                message=await asyncio.wait_for(queue.get(),timeout=min(remaining,10))
                if message is None:
                    await websocket.close(code=1013)
                    break
                await websocket.send_json(message)
        except asyncio.TimeoutError:
            await websocket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            fabric.visual.publisher.unsubscribe(queue)
    return app
