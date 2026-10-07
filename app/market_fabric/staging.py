"""Isolated observation process: no ExecutionEngine, main or research runtime."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import json
import math
import os

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, Query

from app.command_access import authenticate_command_admin, CommandAuthError
from app.config import Settings
from app.market_data import MarketDataClient
from app.rhen44_release import release_status
from app.sizing import calculate_entry_notional, effective_gross_limit, effective_position_limit, long_exposure
from app.strategy import RollingMomentumVwapStrategy, OpeningRangeVwapStrategy
from .runtime import ShadowFabric


def telemetry_safe(value):
    """Keep observer telemetry serializable without inventing numeric evidence."""
    if isinstance(value, dict):
        return {str(key): telemetry_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [telemetry_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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

    async def assets(self, symbols=None):
        # Exactly the bounded current hotset; no writes or broad asset dump.
        requested = tuple(dict.fromkeys(symbols or self.settings.extended_equity_symbols))
        if not 1 <= len(requested) <= self.settings.rhen_market_stream_capacity:
            raise ValueError("asset read exceeds bounded stream capacity")
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            async def read_asset(symbol):
                response = await client.get(self.settings.base_url+"/v2/assets/"+symbol,
                    headers={"APCA-API-KEY-ID":self.settings.alpaca_api_key,
                             "APCA-API-SECRET-KEY":self.settings.alpaca_api_secret})
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("symbol") != symbol:
                    raise ValueError("asset identity mismatch")
                return body
            rows = await asyncio.gather(*(read_asset(s) for s in requested))
        return [row for row in rows if row is not None]


def champion_signal(settings):
    s = settings
    common = dict(stop_pct=s.stop_pct,target_pct=s.target_pct,entry_start=s.entry_start,entry_cutoff=s.entry_cutoff,confirmation_symbols=s.confirmation_symbols)
    if s.strategy_name == "opening_range_vwap":
        return OpeningRangeVwapStrategy(**common,opening_range_minutes=s.opening_range_minutes,max_opening_range_pct=s.max_opening_range_pct,max_breakout_extension_pct=s.max_breakout_extension_pct)
    return RollingMomentumVwapStrategy(**common,fast_window=s.fast_window,slow_window=s.slow_window,min_momentum_pct=s.min_momentum_pct,min_vwap_edge_pct=s.min_vwap_edge_pct,
        min_confirmations=s.min_confirmations,regime_window=s.regime_window,regime_min_confirmations=s.regime_min_confirmations,regime_min_return_pct=s.regime_min_return_pct,
        max_vwap_extension_pct=s.max_vwap_extension_pct,volatility_stop_enabled=s.volatility_stop_enabled,volatility_stop_multiplier=s.volatility_stop_multiplier,
        volatility_stop_lookback_bars=s.volatility_stop_lookback_bars,max_dynamic_stop_pct=s.max_dynamic_stop_pct)


class ReadOnlyChampion:
    """One explicit runtime reconciliation read, without credentials or writes."""
    def __init__(self, *, transport=None):
        self.transport = transport

    async def snapshot(self):
        # Existing private Railway service; no new public proxy/domain is created.
        async with httpx.AsyncClient(timeout=10,transport=self.transport) as http:
            response = await http.get("http://alpaca-trader.railway.internal:8080/health")
            if response.status_code not in (200, 503):
                response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("execution"), dict):
            raise ValueError("Champion health envelope unavailable")
        execution = body.get("execution",{}).get("body",{})
        if not isinstance(execution, dict) or execution.get("ok") is not True:
            raise ValueError("Champion execution observation unavailable")
        identity = execution.get("protected_configuration_identity",{})
        persistence = execution.get("persistence",{})
        return {"observed_at":datetime.now(timezone.utc).isoformat(),
            "source":"RHEN/private_champion_health","provenance":"OPERATIONAL",
            "runtime_ok":response.status_code == 200 and body.get("ok") is True and execution.get("ok") is True,
            "aggregate_http_status":response.status_code,
            "module_failures":[name for name in body.get("module_failures",())
                               if name in ("graen", "nostra", "velum", "iren", "research_agent")],
            "reconciliation_safe":execution.get("reconciliation_safe") is True,
            "strategy_version":persistence.get("strategy_version_id"),
            "run_id":persistence.get("run_id"),
            "protected_configuration_fingerprint":identity.get("fingerprint"),
            "source_commit":execution.get("runtime_provenance",{}).get("git_commit"),
            "universe":{
                "enabled":bool((execution.get("universe") or {}).get("enabled")),
                "source":(execution.get("universe") or {}).get("source"),
                "active_count":len((execution.get("universe") or {}).get("active_symbols") or ()),
                "candidate_count":(execution.get("universe") or {}).get("candidate_count"),
                "eligible_count":(execution.get("universe") or {}).get("eligible_count"),
                "updated_at":(execution.get("universe") or {}).get("updated_at"),
                "active_symbols":[str(s).upper() for s in ((execution.get("universe") or {}).get("active_symbols") or ())
                                  if isinstance(s,str) and s and len(s) <= 16][:500],
            },
            "broker_write_authority":False}


class ReadOnlyCanonicalLedger:
    """Token-authenticated GET-only projection of the canonical RHEN order/fill ledger."""
    ENDPOINT = "http://alpaca-trader.railway.internal:8080/v1/trading-report-read"
    VERSION = "rhen-canonical-ledger-read-v1"

    def __init__(self, settings, *, transport=None):
        self.token = str(getattr(settings, "trading_ingest_token", "") or "").strip()
        self.transport = transport

    async def snapshot(self, run_id, *, limit=1000):
        run_id = str(run_id or "").strip()
        if not self.token or not run_id or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("canonical ledger read unavailable")
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as http:
            response = await http.get(self.ENDPOINT,
                headers={"x-anevum-ingest-token":self.token},
                params={"latest":"ledger","run_id":run_id,"limit":limit})
            response.raise_for_status()
            body = response.json()
        if (not isinstance(body,dict) or body.get("ok") is not True
            or body.get("ledger_version") != self.VERSION or body.get("run_id") != run_id
            or body.get("execution_authority") is not False
            or body.get("broker_orders_possible") is not False
            or not isinstance(body.get("events"),list)):
            raise ValueError("canonical ledger identity mismatch")
        return body


def create_app(settings=None):
    settings = settings or Settings()
    assert_observer_only(settings)
    fabric = None
    command_observation = {"bootstrap_reads":0,"websocket_accepts":0,"authentication_rejections":0,"last_rejection_status":None}

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
            broker = ReadOnlyBroker(settings)
            async def current_assets():
                if fabric is None:
                    return []
                return await broker.assets(fabric.store.symbols)
            fabric = ShadowFabric(settings, MarketDataClient(settings), evaluator=evaluate, account_reader=broker.snapshot,
                                  champion_reader=ReadOnlyChampion().snapshot, asset_reader=current_assets,
                                  ledger_reader=ReadOnlyCanonicalLedger(settings).snapshot)
            fabric.start()
            async def telemetry():
                failures = 0
                while True:
                    try:
                        # Private Railway operational logs; no credentials, token,
                        # account dollars, positions, symbols, orders or fill payloads.
                        account = fabric.visual.system.get("account_observation") or {}
                        performance = fabric.visual.system.get("account_performance") or {}
                        body = await health()
                        body.update(provenance="OPERATIONAL",observed_at=datetime.now(timezone.utc).isoformat(),
                            broker_stream_state=fabric.broker.state if fabric.broker else "DISABLED",
                            account_quality=account.get("quality_state","UNAVAILABLE"),
                            reconstruction_source=fabric.visual.system.get("reconstruction_source"),
                            restored_bar_count=fabric.visual.system.get("restored_bar_count"),
                            bootstrap_error=fabric.visual.system.get("bootstrap_error"),
                            policy_recovery=fabric.policy_recovery,
                            champion_health_lineage=("VERIFIED_READ" if fabric.champion_ready(datetime.now(timezone.utc))
                                else "DEGRADED_READ" if fabric.champion_observation else "UNAVAILABLE"),
                            protected_configuration_fingerprint=(fabric.champion_observation or {}).get("protected_configuration_fingerprint"),
                            account_performance_quality=performance.get("quality_state","UNAVAILABLE"),
                            archived_observation_count=fabric.checkpoint.db.execute("SELECT count(*) FROM shadow_visual").fetchone()[0],
                            bar_count=sum(len(row["bars"]) for row in fabric.store.rows.values()),
                            quote_count=sum(row.get("quote") is not None for row in fabric.store.rows.values()),
                            subscribed_channels=sorted(fabric.manager.subscribed_channels),
                            unavailable_channels=sorted(fabric.manager.unavailable_channels),
                            scanner_prerequisites={"required_bars":fabric.store.warm_bars,
                                "symbols_with_required_bars":sum(len(row.get("bars",())) >= fabric.store.warm_bars for row in fabric.store.rows.values()),
                                "evaluable_symbols":sum(fabric.store.snapshot(s,datetime.now(timezone.utc))["evaluable"] for s in fabric.store.symbols)},
                            market_processing={"processed_events":fabric.manager.processed_events,
                                "cooperative_yields":fabric.manager.cooperative_yields,
                                "max_callback_ms":fabric.manager.max_callback_ms,
                                "max_publisher_tick_lag_ms":fabric.visual.publisher.max_tick_lag_ms,
                                "command_clients":len(fabric.visual.publisher.clients)},
                            scanner_coverage={k:v for k,v in fabric.coverage.summary(fabric.store,datetime.now(timezone.utc)).items() if k != "symbols"},
                            scanner_summary=fabric.rejections.summary(datetime.now(timezone.utc)))
                        body["asset_eligibility"] = fabric.asset_summary(datetime.now(timezone.utc))
                        body["command_transport"] = dict(command_observation)
                        body["canonical_ledger_parity"] = fabric.visual.system.get("canonical_ledger_parity") or {
                            "quality_state":"UNAVAILABLE","parity_complete":False,
                            "entry_authority":False,"broker_write_authority":False}
                        body["execution_recovery"] = fabric.visual.system.get("execution_recovery") or {
                            "restored_events":0,"coverage_state":"UNAVAILABLE","entry_authority":False}
                        print("RHEN44_SHADOW_TELEMETRY "+json.dumps(telemetry_safe(body),allow_nan=False),flush=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures += 1
                        print("RHEN44_SHADOW_TELEMETRY_ERROR "+json.dumps({
                            "error":type(exc).__name__,"failure_count":failures,
                            "execution_authority":False,"broker_orders_possible":False}),flush=True)
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
            command_observation["authentication_rejections"] += 1
            command_observation["last_rejection_status"] = exc.status_code
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
        command_observation["websocket_accepts"] += 1
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

    @app.get("/v1/command/shadow/bootstrap")
    async def bootstrap(authorization: str | None = Header(default=None)):
        await authorize(authorization)
        if not settings.command_live_stream_enabled or fabric is None:
            raise HTTPException(503,"shadow observer unavailable")
        command_observation["bootstrap_reads"] += 1
        return fabric.visual.publisher.bootstrap()

    @app.get("/v1/command/shadow/history")
    async def history(series: str, start: str, end: str, clock: str | None = None,
                      limit: int = Query(default=2400,ge=1,le=5000), authorization: str | None = Header(default=None)):
        await authorize(authorization)
        if fabric is None:
            raise HTTPException(503,"shadow observer unavailable")
        from .contracts import utc
        now = datetime.now(timezone.utc)
        try:
            replay_clock = utc(clock) if clock else now
            if replay_clock > now:
                raise ValueError("future replay clock")
            return fabric.archive.history(series,start,end,clock=replay_clock,limit=limit)
        except ValueError as exc:
            raise HTTPException(422,"invalid source history range") from exc
    return app
