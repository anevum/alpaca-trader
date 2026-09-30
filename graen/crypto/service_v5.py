from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from typing import Any

import httpx
from fastapi import FastAPI

from app.config import Settings, get_settings
from app.market_data import MarketDataClient
from .research_v5 import (
    DEVELOPMENT_START,
    HOLDOUT_END,
    METHODOLOGY_VERSION,
    UNIVERSE,
    run_crypto_research_v5,
)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _research_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "bar_timeframe": "5Min",
            "market_data_batch_size": 6,
            "dynamic_universe_enabled": False,
            "crypto_execution_enabled": False,
        }
    )


class GraenCryptoV5Runtime:
    def __init__(self, settings: Settings):
        self.settings = _research_settings(settings)
        self.market_data = MarketDataClient(self.settings)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.interval_seconds = _env_int("GRAEN_CRYPTO_V5_INTERVAL_SECONDS", 86400, minimum=3600)
        self.last_error: str | None = None
        self.last_result_summary: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "ok": self.last_error is None,
            "system": "GRAEN",
            "program": "Crypto Native Research v5",
            "methodology_version": METHODOLOGY_VERSION,
            "running": self.task is not None and not self.task.done(),
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "last_error": self.last_error,
            "last_result_summary": self.last_result_summary,
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="graen-crypto-native-v5")

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
                print("GRAEN_CRYPTO_V5_ERROR", {"error": self.last_error}, flush=True)
                await self._emit(
                    "crypto_native_research_v5_error",
                    {
                        "methodology_version": METHODOLOGY_VERSION,
                        "error": self.last_error,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                    key_suffix=hashlib.sha256(self.last_error.encode()).hexdigest()[:16],
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, Any]:
        fetch_start = DEVELOPMENT_START - timedelta(hours=8)
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in UNIVERSE}
        chunk_start = fetch_start
        while chunk_start < HOLDOUT_END:
            chunk_end = min(chunk_start + timedelta(days=20), HOLDOUT_END)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(UNIVERSE),
                start=chunk_start,
                end=chunk_end,
            )
            for symbol in UNIVERSE:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol in UNIVERSE:
            clean: list[dict[str, Any]] = []
            seen: set[str] = set()
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(str(bar.get("t") or "").replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.astimezone(timezone.utc) < HOLDOUT_END:
                    clean.append(bar)
            bars[symbol] = clean

        result = run_crypto_research_v5(bars_by_symbol=bars)
        payload = {
            **result,
            "system": "GRAEN",
            "program": "Crypto Native Research v5",
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "requested_range": {
                "start": fetch_start.isoformat(),
                "end": HOLDOUT_END.isoformat(),
            },
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
        }
        persisted = await self._emit(
            "crypto_native_research_v5_result",
            payload,
            key_suffix=HOLDOUT_END.date().isoformat(),
        )
        self.last_result_summary = {
            "status": result.get("status"),
            "research_state": result.get("research_state"),
            "feature_survivors": len(
                ((result.get("feature_research") or {}).get("validation_survivors") or [])
            ),
            "candidate_survivors": len(
                ((result.get("candidate_experiments") or {}).get("validation_survivors") or [])
            ),
            "holdout_opened": bool((result.get("holdout_evaluation") or {}).get("opened")),
            "holdout_passed": bool((result.get("holdout_evaluation") or {}).get("passed")),
            "persisted": persisted,
        }
        print("GRAEN_CRYPTO_V5_COMPLETE", self.last_result_summary, flush=True)
        await self._slack(
            "*GRAEN // CRYPTO NATIVE RESEARCH V5*\n"
            + "state: " + str(self.last_result_summary["research_state"])
            + " | features: " + str(self.last_result_summary["feature_survivors"])
            + " | validation candidates: " + str(self.last_result_summary["candidate_survivors"])
            + " | holdout opened: " + ("yes" if self.last_result_summary["holdout_opened"] else "no")
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
                "graen:crypto-native-v5:"
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
            "source": "graen-crypto-native-v5",
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=60.0) as http:
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
            async with httpx.AsyncClient(timeout=8.0) as http:
                response = await http.post(url, json={"text": message})
                response.raise_for_status()
        except Exception as exc:
            print("GRAEN_CRYPTO_V5_SLACK_ERROR", {"error": type(exc).__name__}, flush=True)


settings = get_settings()
runtime = GraenCryptoV5Runtime(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN Crypto Native Research v5", lifespan=lifespan)


@app.get("/health")
async def health():
    state = runtime.status()
    return {
        "ok": state["ok"],
        "system": state["system"],
        "program": state["program"],
        "running": state["running"],
        "execution_authority": False,
        "broker_orders_possible": False,
        "last_error": state["last_error"],
    }


@app.get("/status")
async def status():
    return runtime.status()
