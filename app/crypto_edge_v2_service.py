from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from typing import Any

import httpx
from fastapi import FastAPI

from .config import Settings, get_settings
from .market_data import MarketDataClient
from .research_agent.crypto_edge_discovery_v2 import (
    METHODOLOGY_VERSION,
    run_crypto_edge_discovery_v2,
)


DEFAULT_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")
V1_CORPUS_START = datetime(2026, 8, 31, 1, 53, tzinfo=timezone.utc)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _research_settings(settings: Settings, symbols: tuple[str, ...]) -> Settings:
    confirmations = tuple(symbol for symbol in ("BTC/USD", "ETH/USD") if symbol in symbols)
    return settings.model_copy(
        update={
            "strategy_name": "rolling_momentum_vwap",
            "allowed_symbols_raw": ",".join(symbols),
            "scan_symbols_raw": ",".join(symbols),
            "confirmation_symbols_raw": ",".join(confirmations),
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


class CryptoEdgeDiscoveryV2Runtime:
    """Fixed-corpus, broker-isolated cost-aware crypto research worker."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.market_data = MarketDataClient(settings)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(timezone.utc)
        self.last_attempt_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.last_status: str | None = None
        self.last_result_summary: dict[str, Any] | None = None

        self.enabled = os.getenv(
            "CRYPTO_EDGE_V2_ENABLED",
            "true",
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.interval_seconds = _env_int(
            "CRYPTO_EDGE_V2_INTERVAL_SECONDS",
            86400,
            minimum=3600,
        )
        self.corpus_days = _env_int(
            "CRYPTO_EDGE_V2_CORPUS_DAYS",
            30,
            minimum=25,
        )
        self.min_train_days = _env_int(
            "CRYPTO_EDGE_V2_MIN_TRAIN_DAYS",
            10,
            minimum=5,
        )
        self.validation_days = _env_int(
            "CRYPTO_EDGE_V2_VALIDATION_DAYS",
            5,
            minimum=2,
        )
        self.holdout_days = _env_int(
            "CRYPTO_EDGE_V2_HOLDOUT_DAYS",
            5,
            minimum=2,
        )
        self.minimum_validation_trades = _env_int(
            "CRYPTO_EDGE_V2_MIN_TRADES",
            20,
            minimum=5,
        )
        self.symbols = DEFAULT_SYMBOLS

    def status(self) -> dict[str, Any]:
        return {
            "ok": self.last_error is None,
            "system": "GRAEN",
            "program": "Crypto Edge Discovery v2",
            "methodology_version": METHODOLOGY_VERSION,
            "enabled": self.enabled,
            "running": self.task is not None and not self.task.done(),
            "broker_orders_possible": False,
            "execution_authority": False,
            "evaluation_end": V1_CORPUS_START.isoformat(),
            "corpus_days": self.corpus_days,
            "symbols": list(self.symbols),
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_status": self.last_status,
            "last_result_summary": self.last_result_summary,
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(
                self._run(),
                name="graen-crypto-edge-v2",
            )

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.run_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print("CRYPTO_EDGE_V2_ERROR", {"error": self.last_error}, flush=True)
                await self._emit(
                    "crypto_edge_discovery_v2_error",
                    {
                        "methodology_version": METHODOLOGY_VERSION,
                        "error": self.last_error,
                        "broker_orders_possible": False,
                        "execution_authority": False,
                    },
                    key_suffix=hashlib.sha256(self.last_error.encode()).hexdigest()[:16],
                )
                await self._slack("*GRAEN // CRYPTO EDGE V2 ERROR*\n" + self.last_error)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, Any]:
        current = datetime.now(timezone.utc)
        self.last_attempt_at = current.isoformat()
        end = V1_CORPUS_START
        start = end - timedelta(days=self.corpus_days)
        research_settings = _research_settings(self.settings, self.symbols)

        bars = await self.market_data.historical_crypto_bars_many(
            list(self.symbols),
            start=start,
            end=end,
        )
        result = run_crypto_edge_discovery_v2(
            settings=research_settings,
            bars_by_symbol=bars,
            initial_equity=self.settings.order_notional,
            min_train_days=self.min_train_days,
            validation_days=self.validation_days,
            holdout_days=self.holdout_days,
            minimum_validation_trades=self.minimum_validation_trades,
        )
        payload = {
            **result,
            "system": "GRAEN",
            "program": "Crypto Edge Discovery v2",
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "range_requested": {
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "configured_symbols": list(self.symbols),
            "broker_orders_possible": False,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
        }
        emitted = await self._emit(
            "crypto_edge_discovery_v2_result",
            payload,
            key_suffix=end.date().isoformat(),
        )
        self.last_status = str(result.get("status") or "UNKNOWN")
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        selected = result.get("selected_candidate") or {}
        holdout = result.get("holdout") or {}
        self.last_result_summary = {
            "status": self.last_status,
            "selected_candidate": selected.get("candidate_id"),
            "holdout_passed": bool(result.get("holdout_passed")),
            "walk_forward_passed": bool(result.get("walk_forward_passed")),
            "candidate_count": len(result.get("candidates") or []),
            "persisted": emitted,
            "holdout_high_cost_expectancy": (
                ((holdout.get("cost_scenarios") or {}).get("high") or {}).get(
                    "expectancy_return"
                )
            ),
        }
        print("CRYPTO_EDGE_V2_COMPLETE", self.last_result_summary, flush=True)
        await self._slack(
            "*GRAEN // CRYPTO EDGE DISCOVERY V2*\n"
            + "status: " + self.last_status
            + " | selected: " + str(self.last_result_summary["selected_candidate"] or "none")
            + " | holdout: " + ("PASS" if self.last_result_summary["holdout_passed"] else "not passed")
            + " | mode: research-only"
        )
        return payload

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
        event = {
            "event_key": (
                "graen:crypto-edge-v2:"
                + event_type
                + ":"
                + METHODOLOGY_VERSION
                + ":"
                + key_suffix
            )[:200],
            "run_id": None,
            "strategy_version_id": self.settings.crypto_strategy_version_id,
            "event_type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "source": "graen-crypto-edge-discovery-v2",
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=30.0) as http:
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
                response = await http.post(url, json={"text": message})
                response.raise_for_status()
        except Exception as exc:
            print("CRYPTO_EDGE_V2_SLACK_ERROR", {"error": type(exc).__name__}, flush=True)


settings = get_settings()
runtime = CryptoEdgeDiscoveryV2Runtime(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="ANEVUM GRAEN Crypto Edge Discovery v2",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    state = runtime.status()
    return {
        "ok": state["ok"],
        "system": state["system"],
        "program": state["program"],
        "running": state["running"],
        "broker_orders_possible": False,
        "last_error": state["last_error"],
    }


@app.get("/status")
async def status():
    return runtime.status()
