from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import os
from typing import Any

import httpx

from .slack_brand import decorate_slack_message
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .config import Settings, get_settings
from .crypto_layer import CryptoRollingMomentumStrategy
from .market_data import MarketDataClient
from .replay import ReplayEngine
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy
from .velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from .crypto_velum import run_crypto_challengers
from .velum_manifest import (
    build_run_manifest,
    dataset_fingerprint,
    evidence_fingerprint,
)
from .velum_graen import GRAEN_CONTEXT_UNIVERSE, replay_candidate


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except Exception:
        return Decimal(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _run_blocking(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Keep CPU-heavy deterministic replay work off the FastAPI event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def _build_equity_strategy(settings: Settings):
    if settings.strategy_name == "rolling_momentum_vwap":
        return RollingMomentumVwapStrategy(
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
    return OpeningRangeVwapStrategy(
        opening_range_minutes=settings.opening_range_minutes,
        max_opening_range_pct=settings.max_opening_range_pct,
        max_breakout_extension_pct=settings.max_breakout_extension_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
    )


def _crypto_settings(settings: Settings) -> Settings:
    symbols = os.getenv(
        "VELUM_CRYPTO_SYMBOLS",
        ",".join(settings.crypto_always_include),
    )
    confirmations = os.getenv(
        "VELUM_CRYPTO_CONFIRMATION_SYMBOLS",
        ",".join(settings.crypto_confirmation_symbols),
    )
    return settings.model_copy(
        update={
            "strategy_name": "rolling_momentum_vwap",
            "allowed_symbols_raw": symbols,
            "scan_symbols_raw": symbols,
            "confirmation_symbols_raw": confirmations,
            "dynamic_universe_enabled": False,
            "fast_window": settings.crypto_fast_window,
            "slow_window": settings.crypto_slow_window,
            "min_momentum_pct": settings.crypto_min_momentum_pct,
            "min_vwap_edge_pct": settings.crypto_min_vwap_edge_pct,
            "stop_pct": settings.crypto_stop_pct,
            "target_pct": settings.crypto_target_pct,
            "regime_window": settings.crypto_regime_window,
            "regime_min_return_pct": settings.crypto_regime_min_return_pct,
            "max_vwap_extension_pct": settings.crypto_max_vwap_extension_pct,
            "volatility_stop_enabled": settings.crypto_volatility_stop_enabled,
            "volatility_stop_multiplier": settings.crypto_volatility_stop_multiplier,
            "volatility_stop_lookback_bars": settings.crypto_volatility_lookback_bars,
            "max_dynamic_stop_pct": settings.crypto_max_dynamic_stop_pct,
            "max_hold_minutes": settings.crypto_max_hold_minutes,
        }
    )


def _build_crypto_strategy(settings: Settings) -> CryptoRollingMomentumStrategy:
    return CryptoRollingMomentumStrategy(
        fast_window=settings.fast_window,
        slow_window=settings.slow_window,
        min_momentum_pct=settings.min_momentum_pct,
        min_vwap_edge_pct=settings.min_vwap_edge_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
        min_confirmations=min(settings.min_confirmations, len(settings.confirmation_symbols)),
        regime_window=settings.regime_window,
        regime_min_confirmations=min(
            settings.regime_min_confirmations,
            len(settings.confirmation_symbols),
        ),
        regime_min_return_pct=settings.regime_min_return_pct,
        max_vwap_extension_pct=settings.max_vwap_extension_pct,
        volatility_stop_enabled=settings.volatility_stop_enabled,
        volatility_stop_multiplier=settings.volatility_stop_multiplier,
        volatility_stop_lookback_bars=settings.volatility_stop_lookback_bars,
        max_dynamic_stop_pct=settings.max_dynamic_stop_pct,
        strategy_version_id=settings.crypto_strategy_version_id,
        model_version=settings.crypto_model_version,
        calibration_version=settings.crypto_calibration_version,
        calibration_promoted=settings.crypto_calibration_promoted,
        regime_version=settings.crypto_regime_version,
        execution_adapter_version=settings.crypto_execution_adapter_version,
        feature_volatility_lookback=settings.crypto_volatility_lookback_bars,
    )


class VelumRuntime:
    """Always-on research replay worker. It has no broker-order authority."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.market_data = MarketDataClient(settings)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(timezone.utc)
        self.last_tick_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.last_equity_session: str | None = None
        self.last_crypto_window_end: str | None = None
        self.last_equity_summary: dict[str, Any] | None = None
        self.last_crypto_summary: dict[str, Any] | None = None
        self.last_equity_run_id: str | None = None
        self.last_crypto_run_id: str | None = None

        self.poll_seconds = _env_int("VELUM_POLL_SECONDS", 60, minimum=30)
        self.crypto_interval_minutes = _env_int(
            "VELUM_CRYPTO_INTERVAL_MINUTES", 60, minimum=15
        )
        self.crypto_window_hours = _env_int(
            "VELUM_CRYPTO_WINDOW_HOURS", 24, minimum=2
        )
        self.bootstrap_paths = _env_int("VELUM_BOOTSTRAP_PATHS", 500, minimum=50)
        self.initial_equity = _env_decimal("VELUM_INITIAL_EQUITY", "100")
        self.equity_spread_bps = _env_decimal("VELUM_EQUITY_SPREAD_BPS", "5")
        self.equity_slippage_bps = _env_decimal("VELUM_EQUITY_SLIPPAGE_BPS", "2")
        self.crypto_spread_bps = _env_decimal("VELUM_CRYPTO_SPREAD_BPS", "10")
        self.crypto_slippage_bps = _env_decimal("VELUM_CRYPTO_SLIPPAGE_BPS", "5")
        self.enabled = _env_bool("VELUM_ENABLED", True)
        self.autorun = _env_bool("VELUM_AUTORUN", True)
        self.run_lock = asyncio.Lock()

    @staticmethod
    def _ny():
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")

    def status(self) -> dict[str, Any]:
        return {
            "ok": self.last_error is None,
            "system": "VELUM",
            "mode": "research_replay_only",
            "enabled": self.enabled,
            "autorun": self.autorun,
            "scheduler_managed": not self.autorun,
            "running": self.task is not None and not self.task.done(),
            "broker_orders_possible": False,
            "started_at": self.started_at.isoformat(),
            "runtime_provenance": {
                "system_version": "velum-runtime-v1",
                "git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
                "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID") or None,
                "runtime_started_at": self.started_at.isoformat(),
            },
            "last_tick_at": self.last_tick_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_equity_session": self.last_equity_session,
            "last_crypto_window_end": self.last_crypto_window_end,
            "last_equity_summary": self.last_equity_summary,
            "last_crypto_summary": self.last_crypto_summary,
            "last_equity_run_id": self.last_equity_run_id,
            "last_crypto_run_id": self.last_crypto_run_id,
            "crypto_interval_minutes": self.crypto_interval_minutes,
            "crypto_window_hours": self.crypto_window_hours,
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="velum-replay-worker")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=15)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.tick()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print("VELUM_ERROR", {"error": self.last_error}, flush=True)
                await self._emit(
                    "velum_error",
                    {
                        "error": self.last_error,
                        "broker_orders_possible": False,
                        "execution_authority": False,
                    },
                    key_suffix=hashlib.sha256(
                        self.last_error.encode()
                    ).hexdigest()[:16],
                )
                await self._slack("*VELUM // ERROR*\\n" + self.last_error)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def tick(self, now: datetime | None = None) -> None:
        if not self.enabled:
            return
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.last_tick_at = current.isoformat()

        session = await self._latest_completed_equity_session(current)
        if session is not None and session.isoformat() != self.last_equity_session:
            await self._run_equity(session)

        crypto_end = self._crypto_bucket_end(current)
        crypto_key = crypto_end.isoformat()
        if crypto_key != self.last_crypto_window_end:
            await self._run_crypto(crypto_end)

        self.last_success_at = datetime.now(timezone.utc).isoformat()

    async def _latest_completed_equity_session(
        self,
        now_utc: datetime,
    ) -> date | None:
        now_ny = now_utc.astimezone(self._ny())
        sessions = await self.market_data.market_calendar(
            start=now_ny.date() - timedelta(days=10),
            end=now_ny.date(),
        )
        completed = [
            session
            for session in sessions
            if session < now_ny.date()
            or (
                session == now_ny.date()
                and now_ny.timetz().replace(tzinfo=None) >= time(16, 5)
            )
        ]
        return completed[-1] if completed else None

    def _crypto_bucket_end(self, now_utc: datetime) -> datetime:
        minute = int(now_utc.timestamp() // 60)
        bucket = minute - (minute % self.crypto_interval_minutes)
        return datetime.fromtimestamp(bucket * 60, tz=timezone.utc)

    async def _run_equity(self, session: date) -> None:
        strategy = _build_equity_strategy(self.settings)
        engine = ReplayEngine(self.settings, strategy)
        start = datetime.combine(session, time(0, 0), tzinfo=self._ny()).astimezone(timezone.utc)
        end = datetime.combine(
            session + timedelta(days=1),
            time(0, 0),
            tzinfo=self._ny(),
        ).astimezone(timezone.utc)
        symbols = list(dict.fromkeys([
            *self.settings.scan_symbols,
            *self.settings.confirmation_symbols,
        ]))
        bars = await self.market_data.historical_bars_many(
            symbols,
            start=start,
            end=end,
        )
        baseline = await _run_blocking(
            engine.run,
            bars,
            initial_equity=self.initial_equity,
            spread_bps=self.equity_spread_bps,
            slippage_bps=self.equity_slippage_bps,
        )
        stress = await _run_blocking(
            engine.run,
            bars,
            initial_equity=self.initial_equity,
            spread_bps=self.equity_spread_bps * Decimal("2"),
            slippage_bps=self.equity_slippage_bps * Decimal("2"),
        )
        seed = int(session.strftime("%Y%m%d"))
        bootstrap = await _run_blocking(
            bootstrap_trade_distribution,
            baseline["trades"],
            paths=self.bootstrap_paths,
            seed=seed,
        )
        coverage = {symbol: len(bars.get(symbol, [])) for symbol in symbols}
        payload = self._payload(
            asset_class="equity",
            start=start,
            end=end,
            baseline=baseline,
            stress=stress,
            bootstrap=bootstrap,
            coverage=coverage,
            strategy_version_id=self.settings.strategy_version_id or None,
            notes={
                "market_session": session.isoformat(),
                "universe_replay": "configured scan snapshot",
                "dynamic_universe_reconstruction": False,
            },
        )
        manifest = build_run_manifest(
            asset_class="equity",
            mode="REPLAY",
            methodology_version=payload["methodology_version"],
            start=start,
            end=end,
            dataset_hash=await _run_blocking(dataset_fingerprint, bars),
            coverage=coverage,
            strategy_version_id=self.settings.strategy_version_id or None,
            strategy=baseline["strategy"],
            execution_assumptions=baseline["assumptions"],
            random_seed=seed,
            runtime_git_commit=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            evidence_hash=await _run_blocking(evidence_fingerprint, payload),
        )
        payload["run_manifest"] = manifest
        await self._emit(
            "velum_replay_result",
            payload,
            key_suffix="equity:" + session.isoformat() + ":" + manifest["velum_run_id"],
        )
        self.last_equity_session = session.isoformat()
        self.last_equity_run_id = manifest["velum_run_id"]
        self.last_equity_summary = payload["baseline"]["summary"]
        print(
            "VELUM_EQUITY_COMPLETE",
            {
                "session": self.last_equity_session,
                "trades": baseline["summary"]["trades"],
                "return_pct": baseline["summary"]["return_pct"],
            },
            flush=True,
        )
        await self._slack(
            "*VELUM // EQUITY REPLAY COMPLETE*\\n"
            + "session: " + session.isoformat()
            + " | trades: " + str(baseline["summary"]["trades"])
            + " | return: " + f'{baseline["summary"]["return_pct"] * 100:.3f}%'
            + " | mode: research-only"
        )

    async def _run_crypto(self, end: datetime) -> None:
        crypto_settings = _crypto_settings(self.settings)
        strategy = _build_crypto_strategy(crypto_settings)
        engine = ContinuousReplayEngine(crypto_settings, strategy)
        start = end - timedelta(hours=self.crypto_window_hours)
        symbols = list(dict.fromkeys([
            *crypto_settings.scan_symbols,
            *crypto_settings.confirmation_symbols,
        ]))
        bars = await self.market_data.historical_crypto_bars_many(
            symbols,
            start=start,
            end=end,
        )
        baseline = await _run_blocking(
            engine.run,
            bars,
            initial_equity=self.initial_equity,
            spread_bps=self.crypto_spread_bps,
            slippage_bps=self.crypto_slippage_bps,
        )
        stress = await _run_blocking(
            engine.run,
            bars,
            initial_equity=self.initial_equity,
            spread_bps=self.crypto_spread_bps * Decimal("2"),
            slippage_bps=self.crypto_slippage_bps * Decimal("2"),
        )
        seed = int(end.strftime("%Y%m%d%H"))
        bootstrap = await _run_blocking(
            bootstrap_trade_distribution,
            baseline["trades"],
            paths=self.bootstrap_paths,
            seed=seed,
        )
        challenger_experiment = await _run_blocking(
            run_crypto_challengers,
            settings=crypto_settings,
            bars_by_symbol=bars,
            initial_equity=self.initial_equity,
            spread_bps=self.crypto_spread_bps,
            slippage_bps=self.crypto_slippage_bps,
            equity_settings=self.settings,
        )
        coverage = {symbol: len(bars.get(symbol, [])) for symbol in symbols}
        payload = self._payload(
            asset_class="crypto",
            start=start,
            end=end,
            baseline=baseline,
            stress=stress,
            bootstrap=bootstrap,
            coverage=coverage,
            strategy_version_id=crypto_settings.crypto_strategy_version_id or None,
            notes={
                "market_session": "24x7",
                "crypto_location": crypto_settings.crypto_location,
                "symbols": list(crypto_settings.scan_symbols),
                "confirmation_symbols": list(crypto_settings.confirmation_symbols),
                "strategy_version_id": crypto_settings.crypto_strategy_version_id,
                "model_version": crypto_settings.crypto_model_version,
                "calibration_version": crypto_settings.crypto_calibration_version,
                "regime_version": crypto_settings.crypto_regime_version,
                "execution_adapter_version": crypto_settings.crypto_execution_adapter_version,
            },
        )
        payload["challenger_experiment"] = challenger_experiment
        manifest = build_run_manifest(
            asset_class="crypto",
            mode="REPLAY",
            methodology_version=payload["methodology_version"],
            start=start,
            end=end,
            dataset_hash=await _run_blocking(dataset_fingerprint, bars),
            coverage=coverage,
            strategy_version_id=crypto_settings.crypto_strategy_version_id or None,
            strategy=baseline["strategy"],
            execution_assumptions=baseline["assumptions"],
            random_seed=seed,
            runtime_git_commit=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            evidence_hash=await _run_blocking(evidence_fingerprint, payload),
        )
        payload["run_manifest"] = manifest
        await self._emit(
            "velum_replay_result",
            payload,
            key_suffix=(
                "crypto:"
                + end.isoformat()
                + ":"
                + str(self.crypto_window_hours)
                + "h:"
                + manifest["velum_run_id"]
            ),
        )
        self.last_crypto_window_end = end.isoformat()
        self.last_crypto_run_id = manifest["velum_run_id"]
        self.last_crypto_summary = payload["baseline"]["summary"]
        print(
            "VELUM_CRYPTO_COMPLETE",
            {
                "window_end": self.last_crypto_window_end,
                "trades": baseline["summary"]["trades"],
                "return_pct": baseline["summary"]["return_pct"],
            },
            flush=True,
        )
        if _env_bool("VELUM_SLACK_CRYPTO_EVERY_RUN", False):
            await self._slack(
                "*VELUM // CRYPTO REPLAY COMPLETE*\\n"
                + "window end: " + end.isoformat()
                + " | trades: " + str(baseline["summary"]["trades"])
                + " | return: " + f'{baseline["summary"]["return_pct"] * 100:.3f}%'
                + " | mode: 24/7 research-only"
            )

    def _payload(
        self,
        *,
        asset_class: str,
        start: datetime,
        end: datetime,
        baseline: dict[str, Any],
        stress: dict[str, Any],
        bootstrap: dict[str, Any],
        coverage: dict[str, int],
        strategy_version_id: str | None,
        notes: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "system": "VELUM",
            "methodology_version": "velum-replay-v2",
            "asset_class": asset_class,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "baseline": {
                "summary": baseline["summary"],
                "assumptions": baseline["assumptions"],
                "strategy": baseline["strategy"],
            },
            "stress": {
                "summary": stress["summary"],
                "assumptions": stress["assumptions"],
            },
            "bootstrap": bootstrap,
            "bar_coverage": coverage,
            "notes": notes,
            "research_only": True,
            "broker_orders_possible": False,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
            "strategy_version_id": strategy_version_id,
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        }

    @staticmethod
    def _event_key(
        strategy: str,
        event_type: str,
        methodology_version: str,
        key_suffix: str,
    ) -> str:
        return (
            "velum:"
            + strategy
            + ":"
            + event_type
            + ":"
            + methodology_version
            + ":"
            + key_suffix
        )[:200]

    async def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        key_suffix: str,
    ) -> bool:
        url = str(self.settings.trading_ingest_url or "").strip()
        token = str(self.settings.trading_ingest_token or "").strip()
        if not url or not token:
            return False

        event_strategy_version = (
            payload.get("strategy_version_id")
            or self.settings.strategy_version_id
            or None
        )
        strategy = str(event_strategy_version or "unversioned")
        methodology = str(payload.get("methodology_version") or "unversioned")
        event = {
            "event_key": self._event_key(
                strategy,
                event_type,
                methodology,
                key_suffix,
            ),
            "run_id": None,
            "strategy_version_id": event_strategy_version,
            "event_type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "source": "velum-replay",
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                url,
                headers={"x-anevum-ingest-token": token},
                json={"events": [event]},
            )
            response.raise_for_status()
        return True

    async def _slack(self, message: str) -> None:
        url = str(self.settings.slack_webhook_url or "").strip()
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                response = await http.post(url, json={"text": decorate_slack_message(message, system="VELUM")})
                response.raise_for_status()
        except Exception as exc:
            print("VELUM_SLACK_ERROR", {"error": type(exc).__name__}, flush=True)


class VelumEquityRequest(BaseModel):
    session: date
    scheduled_at: datetime | None = None


class VelumCryptoRequest(BaseModel):
    window_end: datetime
    scheduled_at: datetime | None = None


class VelumGraenCandidateRequest(BaseModel):
    problem_id: str
    graen_run_id: str
    campaign_id: str
    epoch_index: int
    generation: int
    candidate_methodology: str = "graen-crypto-autonomous-v8"
    candidate_spec: dict[str, Any]
    replay_start: datetime
    replay_end: datetime
    seed: int = 91000


def require_graen_token(x_graen_velum_token: str | None) -> None:
    expected = os.getenv("VELUM_GRAEN_TOKEN", "").strip()
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="GRAEN replay token is not configured")
    if x_graen_velum_token is None or not hmac.compare_digest(
        x_graen_velum_token,
        expected,
    ):
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


settings = get_settings()
velum = VelumRuntime(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if velum.autorun:
        await velum.start()
    try:
        yield
    finally:
        if velum.autorun:
            await velum.stop()


app = FastAPI(title="ANEVUM VELUM", lifespan=lifespan)


@app.get("/health")
async def health():
    current = velum.status()
    return {
        "ok": current["ok"],
        "system": "VELUM",
        "mode": current["mode"],
        "running": current["running"],
        "broker_orders_possible": False,
        "last_error": current["last_error"],
    }


@app.get("/status")
async def status():
    return velum.status()


@app.post("/v1/graen/candidate-replay")
async def graen_candidate_replay(
    request: VelumGraenCandidateRequest,
    x_graen_velum_token: str | None = Header(default=None),
):
    require_graen_token(x_graen_velum_token)
    if request.replay_start.tzinfo is None or request.replay_end.tzinfo is None:
        raise HTTPException(status_code=422, detail="replay range must be timezone-aware")
    start = request.replay_start.astimezone(timezone.utc)
    end = request.replay_end.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if not start < end:
        raise HTTPException(status_code=422, detail="invalid replay range")
    if end > now:
        raise HTTPException(status_code=409, detail="replay range is not complete")
    if end - start > timedelta(days=180):
        raise HTTPException(status_code=422, detail="replay range exceeds 180 days")

    fetch_start = start - timedelta(hours=8)
    fetch_end = end + timedelta(hours=3)
    async with velum.run_lock:
        bars = await velum.market_data.historical_crypto_bars_many(
            list(GRAEN_CONTEXT_UNIVERSE),
            start=fetch_start,
            end=fetch_end,
        )
        result = await _run_blocking(
            replay_candidate,
            bars,
            candidate_spec=request.candidate_spec,
            candidate_methodology=request.candidate_methodology,
            start=start,
            end=end,
            seed=request.seed,
        )
        result.update({
            "system": "VELUM",
            "problem_id": request.problem_id,
            "graen_run_id": request.graen_run_id,
            "campaign_id": request.campaign_id,
            "candidate_methodology": request.candidate_methodology,
            "epoch_index": request.epoch_index,
            "generation": request.generation,
            "bar_coverage": {
                symbol: len(bars.get(symbol, []))
                for symbol in GRAEN_CONTEXT_UNIVERSE
            },
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        })
        emitted = await velum._emit(
            "velum_graen_candidate_replay",
            result,
            key_suffix=(
                request.problem_id
                + ":"
                + str(result.get("candidate_id") or "unknown")
                + ":"
                + end.isoformat()
            ),
        )
        result["evidence_emitted"] = emitted
        if result["engineering_gate"]["passed"]:
            await velum._slack(
                "*VELUM // GRAEN CANDIDATE REPLAY PASS*\n"
                + str(result.get("candidate_id") or "unknown")
                + " | research-only | forward shadow required"
            )
        return {
            "ok": True,
            "result": result,
            "broker_orders_possible": False,
            "execution_authority": False,
            "promotion_authorized": False,
        }


@app.post("/v1/scheduler/equity")
async def scheduler_equity(
    request: VelumEquityRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    async with velum.run_lock:
        session_key = request.session.isoformat()
        if velum.last_equity_session == session_key:
            return {
                "ok": True,
                "duplicate": True,
                "session": session_key,
                "velum_run_id": velum.last_equity_run_id,
                "broker_orders_possible": False,
            }

        rows = await velum.market_data.market_calendar_details(
            start=request.session,
            end=request.session,
        )
        if not rows:
            raise HTTPException(
                status_code=422,
                detail="requested date is not a U.S. equity trading session",
            )
        close_raw = str(rows[0].get("close") or "16:00")
        close_at = datetime.combine(
            request.session,
            time.fromisoformat(close_raw),
            tzinfo=velum._ny(),
        ).astimezone(timezone.utc)
        if close_at > datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="equity session is not complete")

        await velum._run_equity(request.session)
        return {
            "ok": True,
            "duplicate": False,
            "session": velum.last_equity_session,
            "velum_run_id": velum.last_equity_run_id,
            "summary": velum.last_equity_summary,
            "broker_orders_possible": False,
        }


@app.post("/v1/scheduler/crypto")
async def scheduler_crypto(
    request: VelumCryptoRequest,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    require_scheduler_token(x_anevum_scheduler_token)
    if request.window_end.tzinfo is None:
        raise HTTPException(status_code=422, detail="window_end must be timezone-aware")
    window_end = request.window_end.astimezone(timezone.utc)
    if window_end > datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="crypto replay window is not complete")
    expected_bucket = velum._crypto_bucket_end(window_end)
    if expected_bucket != window_end.replace(second=0, microsecond=0):
        raise HTTPException(status_code=422, detail="window_end is not aligned to VELUM cadence")

    async with velum.run_lock:
        window_key = window_end.isoformat()
        if velum.last_crypto_window_end == window_key:
            return {
                "ok": True,
                "duplicate": True,
                "window_end": window_key,
                "velum_run_id": velum.last_crypto_run_id,
                "broker_orders_possible": False,
            }
        await velum._run_crypto(window_end)
        return {
            "ok": True,
            "duplicate": False,
            "window_end": velum.last_crypto_window_end,
            "velum_run_id": velum.last_crypto_run_id,
            "summary": velum.last_crypto_summary,
            "broker_orders_possible": False,
        }
