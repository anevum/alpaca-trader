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
from app.slack_brand import decorate_slack_message
from .research_v6 import (
    CONTEXT_UNIVERSE,
    METHODOLOGY_VERSION,
    STRATEGY_VERSION_ID,
    run_crypto_research_v6,
)
from .shadow_v6 import CryptoResidualReclaimShadow


DEVELOPMENT_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
VALIDATION_START = datetime(2025, 10, 15, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2025, 11, 15, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2025, 12, 1, tzinfo=timezone.utc)

PREVIOUSLY_INSPECTED_RANGES = (
    {
        "id": "crypto-v5-fetch",
        "start": "2026-01-31T16:00:00+00:00",
        "end": "2026-06-01T00:00:00+00:00",
    },
    {
        "id": "crypto-v4",
        "start": "2026-06-02T01:53:00+00:00",
        "end": "2026-07-02T01:53:00+00:00",
    },
    {
        "id": "crypto-v3",
        "start": "2026-07-02T01:53:00+00:00",
        "end": "2026-08-01T01:53:00+00:00",
    },
    {
        "id": "crypto-v2.1",
        "start": "2026-08-01T01:53:00+00:00",
        "end": "2026-08-31T01:53:00+00:00",
    },
    {
        "id": "crypto-v1",
        "start": "2026-08-31T01:53:00+00:00",
        "end": "2026-09-30T01:53:00+00:00",
    },
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
            "market_data_batch_size": len(CONTEXT_UNIVERSE),
            "dynamic_universe_enabled": False,
            "crypto_execution_enabled": False,
        }
    )


class GraenCryptoV6Runtime:
    def __init__(self, settings: Settings):
        self.settings = _research_settings(settings)
        self.market_data = MarketDataClient(self.settings)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.shadow_task: asyncio.Task | None = None
        self.interval_seconds = _env_int("GRAEN_CRYPTO_V6_INTERVAL_SECONDS", 86400, minimum=3600)
        self.shadow_interval_seconds = _env_int(
            "GRAEN_CRYPTO_V6_SHADOW_INTERVAL_SECONDS",
            30,
            minimum=15,
        )
        self.shadow = CryptoResidualReclaimShadow(self.settings)
        self.last_error: str | None = None
        self.last_result_summary: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "ok": self.last_error is None,
            "system": "GRAEN",
            "program": "Crypto Native Research v6",
            "methodology_version": METHODOLOGY_VERSION,
            "strategy_version_id": STRATEGY_VERSION_ID,
            "running": self.task is not None and not self.task.done(),
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "fresh_corpus": {
                "development_start": DEVELOPMENT_START.isoformat(),
                "validation_start": VALIDATION_START.isoformat(),
                "holdout_start": HOLDOUT_START.isoformat(),
                "holdout_end": HOLDOUT_END.isoformat(),
            },
            "last_error": self.last_error,
            "last_result_summary": self.last_result_summary,
            "shadow": self.shadow.status(),
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="graen-crypto-native-v6")
        if self.shadow_task is None:
            self.shadow_task = asyncio.create_task(
                self._run_shadow(),
                name="graen-crypto-native-v6-shadow",
            )

    async def stop(self) -> None:
        self.stop_event.set()
        tasks = [task for task in (self.task, self.shadow_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self.shadow_task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.run_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print("GRAEN_CRYPTO_V6_ERROR", {"error": self.last_error}, flush=True)
                await self._emit(
                    "crypto_native_research_v6_error",
                    {
                        "methodology_version": METHODOLOGY_VERSION,
                        "strategy_version_id": STRATEGY_VERSION_ID,
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

    async def _run_shadow(self) -> None:
        while not self.stop_event.is_set():
            try:
                events = await self.shadow.cycle()
                for event in events:
                    event_type = str(event.get("event_type") or "crypto_shadow_v6_event")
                    symbol = str(event.get("symbol") or "")
                    occurred_at = str(event.get("occurred_at") or datetime.now(timezone.utc).isoformat())
                    payload = dict(event.get("payload") or {})
                    payload.update({
                        "system": "GRAEN",
                        "program": "Crypto Native Research v6 Shadow",
                        "strategy_version_id": STRATEGY_VERSION_ID,
                        "mode": "shadow",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "crypto_execution_enabled": False,
                    })
                    key_material = f"{event_type}:{symbol}:{occurred_at}"
                    await self._emit(
                        event_type,
                        payload,
                        key_suffix=hashlib.sha256(key_material.encode()).hexdigest()[:24],
                        symbol=symbol,
                        occurred_at=occurred_at,
                    )
                    if event_type == "crypto_shadow_v6_entry":
                        await self._slack(
                            "*GRAEN // CRYPTO SHADOW ENTRY*\n"
                            + str(symbol)
                            + " | CRR-001 simulated entry"
                            + " | broker orders: disabled"
                        )
                    elif event_type == "crypto_shadow_v6_exit":
                        net = payload.get("stressed_cost_net_return")
                        pct = (
                            f"{float(net) * 100:.3f}%"
                            if net is not None
                            else "n/a"
                        )
                        await self._slack(
                            "*GRAEN // CRYPTO SHADOW EXIT*\n"
                            + str(symbol)
                            + " | CRR-001 | stressed-cost return: "
                            + pct
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    "GRAEN_CRYPTO_V6_SHADOW_ERROR",
                    {"error": f"{type(exc).__name__}: {exc}"},
                    flush=True,
                )
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.shadow_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, Any]:
        fetch_start = DEVELOPMENT_START - timedelta(hours=8)
        fetch_end = HOLDOUT_END + timedelta(minutes=135)
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in CONTEXT_UNIVERSE}
        chunk_start = fetch_start

        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(CONTEXT_UNIVERSE),
                start=chunk_start,
                end=chunk_end,
            )
            for symbol in CONTEXT_UNIVERSE:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol in CONTEXT_UNIVERSE:
            clean: list[dict[str, Any]] = []
            seen: set[str] = set()
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(identity.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                stamp = stamp.astimezone(timezone.utc)
                if fetch_start <= stamp < fetch_end:
                    clean.append(bar)
            bars[symbol] = clean

        result = run_crypto_research_v6(
            bars_by_symbol=bars,
            development_start=DEVELOPMENT_START,
            validation_start=VALIDATION_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            corpus_provenance_verified=True,
            previously_inspected_ranges=PREVIOUSLY_INSPECTED_RANGES,
        )
        payload = {
            **result,
            "system": "GRAEN",
            "program": "Crypto Native Research v6",
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "requested_range": {
                "start": fetch_start.isoformat(),
                "end": fetch_end.isoformat(),
            },
            "corpus_provenance": {
                "verified": True,
                "prior_ranges": list(PREVIOUSLY_INSPECTED_RANGES),
                "selected_fresh_range": {
                    "development_start": DEVELOPMENT_START.isoformat(),
                    "validation_start": VALIDATION_START.isoformat(),
                    "holdout_start": HOLDOUT_START.isoformat(),
                    "holdout_end": HOLDOUT_END.isoformat(),
                },
            },
            "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
        }
        persisted = await self._emit(
            "crypto_native_research_v6_result",
            payload,
            key_suffix=HOLDOUT_END.date().isoformat(),
        )
        self.last_result_summary = {
            "status": result.get("status"),
            "validation_passed": bool(result.get("validation_passed")),
            "holdout_opened": bool((result.get("holdout") or {}).get("opened")),
            "holdout_passed": bool((result.get("holdout") or {}).get("passed")),
            "development_trade_count": int(
                (((result.get("development") or {}).get("primary_reclaim") or {}).get("trade_count") or 0)
            ),
            "development_expectancy": (
                ((result.get("development") or {}).get("primary_reclaim") or {}).get("expectancy_per_trade")
            ),
            "validation_trade_count": int(
                (((result.get("validation") or {}).get("primary_reclaim") or {}).get("trade_count") or 0)
            ),
            "validation_expectancy": (
                ((result.get("validation") or {}).get("primary_reclaim") or {}).get("expectancy_per_trade")
            ),
            "persisted": persisted,
        }
        print("GRAEN_CRYPTO_V6_COMPLETE", self.last_result_summary, flush=True)
        await self._slack(
            "*GRAEN // CRYPTO NATIVE RESEARCH V6*\n"
            + "state: " + str(self.last_result_summary["status"])
            + " | development trades: " + str(self.last_result_summary["development_trade_count"])
            + " | validation trades: " + str(self.last_result_summary["validation_trade_count"])
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
        symbol: str = "",
        occurred_at: str | None = None,
    ) -> bool:
        url = str(self.settings.trading_ingest_url or "").strip()
        token = str(self.settings.trading_ingest_token or "").strip()
        if not url or not token:
            return False
        event = {
            "event_key": (
                "graen:crypto-native-v6:"
                + event_type
                + ":"
                + METHODOLOGY_VERSION
                + ":"
                + key_suffix
            )[:200],
            "run_id": None,
            "strategy_version_id": STRATEGY_VERSION_ID,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "symbol": symbol or None,
            "source": "graen-crypto-native-v6",
            "payload": payload,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                response = await http.post(
                    url,
                    headers={"x-anevum-ingest-token": token},
                    json={"events": [event]},
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            print(
                "GRAEN_CRYPTO_V6_INGEST_ERROR",
                {
                    "event_type": event_type,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                flush=True,
            )
            return False

    async def _slack(self, message: str) -> None:
        url = str(self.settings.slack_webhook_url or "").strip()
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as http:
                response = await http.post(url, json={"text": decorate_slack_message(message, system="GRAEN")})
                response.raise_for_status()
        except Exception as exc:
            print("GRAEN_CRYPTO_V6_SLACK_ERROR", {"error": type(exc).__name__}, flush=True)


settings = get_settings()
runtime = GraenCryptoV6Runtime(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN Crypto Native Research v6", lifespan=lifespan)


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
