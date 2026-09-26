from __future__ import annotations

import asyncio
import json
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .config import get_settings
from .edge_corpus import load_manifest, manifest_sha256
from .edge_corpus_runner import (
    MIN_SHARED_PANEL_RATIO,
    _load_role,
    _panel_integrity,
    _scenario_stage,
    _shared_panel,
)
from .edge_discovery_runner import DEFAULT_COST_SCENARIOS
from .edge_elimination import development_elimination
from .market_data import MarketDataClient


app = FastAPI()
STATE: dict[str, Any] = {
    "status": "starting",
    "stage": "development_only",
    "validation_opened": False,
    "holdout_opened": False,
    "report_path": "edge-corpus-development-report.json",
}


def _compact_elimination(gate: dict[str, Any]) -> dict[str, Any]:
    families = {}
    for name, result in (gate.get("families") or {}).items():
        families[name] = {
            "passed": result.get("passed"),
            "failed_scenarios": result.get("failed_scenarios"),
            "scenario_checks": [
                {
                    "scenario": item.get("scenario"),
                    "passed": item.get("passed"),
                    "actual": item.get("actual"),
                    "checks": item.get("checks"),
                }
                for item in result.get("scenario_checks") or []
            ],
        }
    return {
        "survivors": gate.get("survivors") or [],
        "rejected": gate.get("rejected") or [],
        "families": families,
        "criteria": gate.get("criteria"),
    }


async def run_development_only() -> None:
    try:
        settings = get_settings()
        if settings.execution_enabled or settings.bot_armed or settings.live_trading:
            raise RuntimeError("research service execution gates are not hard-disabled")

        manifest = load_manifest("research/edge-corpus-v1.json")
        if settings.data_feed != manifest.data_feed:
            raise RuntimeError(
                f"DATA_FEED={settings.data_feed} does not match {manifest.data_feed}"
            )
        if settings.bar_timeframe != manifest.timeframe:
            raise RuntimeError(
                f"BAR_TIMEFRAME={settings.bar_timeframe} does not match {manifest.timeframe}"
            )

        STATE["status"] = "fetching_development"
        print(json.dumps({
            "event": "edge_corpus_development_started",
            "manifest_sha256": manifest_sha256(manifest),
            "development_windows": 6,
            "validation_opened": False,
            "holdout_opened": False,
        }), flush=True)

        market_data = MarketDataClient(settings)
        payloads = await _load_role(
            market_data,
            manifest,
            "development",
            cache_dir=".edge_corpus",
            refresh=True,
        )

        integrity_windows = []
        for payload in payloads:
            cov = payload.get("coverage") or {}
            integrity_windows.append({
                "window": payload.get("window"),
                "fetch_pagination_complete": (
                    (payload.get("fetch_integrity") or {}).get("pagination_complete")
                ),
                "eligible_candidate_count": cov.get("eligible_candidate_count"),
                "candidate_count": cov.get("candidate_count"),
                "missing_or_incomplete_confirmations": cov.get(
                    "missing_or_incomplete_confirmations"
                ),
                "symbols_with_missing_sessions": {
                    symbol: sessions
                    for symbol, sessions in (cov.get("missing_sessions") or {}).items()
                    if sessions
                },
                "minimum_iex_density_ratio": min(
                    (cov.get("iex_bar_density_ratio") or {"": 0.0}).values()
                ),
            })
            print(json.dumps({
                "event": "edge_corpus_integrity_window",
                **integrity_windows[-1],
            }), flush=True)

        panel = _shared_panel(payloads, manifest)
        panel_integrity = _panel_integrity(panel, manifest.candidate_symbols)
        print(json.dumps({
            "event": "edge_corpus_panel_integrity",
            **panel_integrity,
        }), flush=True)
        if not panel_integrity["passed"]:
            raise RuntimeError(
                "development corpus shared-symbol coverage is below the "
                f"{MIN_SHARED_PANEL_RATIO} minimum after session-integrity checks"
            )

        STATE["status"] = "evaluating_development"
        scenarios = list(DEFAULT_COST_SCENARIOS)
        scenario_results = _scenario_stage(
            payloads,
            settings=settings,
            panel=panel,
            confirmation_symbols=manifest.confirmation_symbols,
            scenarios=scenarios,
            horizon=manifest.research_horizon_minutes,
            event_cooldown_minutes=manifest.research_event_cooldown_minutes,
        )
        gate = development_elimination(scenario_results)

        report = {
            "status": "research_only",
            "stage": "development_only",
            "corpus": {
                "version": manifest.version,
                "manifest_sha256": manifest_sha256(manifest),
                "data_feed": manifest.data_feed,
                "timeframe": manifest.timeframe,
            },
            "integrity_windows": integrity_windows,
            "panel_integrity": panel_integrity,
            "development_elimination": gate,
            "validation_opened": False,
            "holdout_opened": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
            "capital_scaling_allowed": False,
        }
        Path(STATE["report_path"]).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        STATE.update({
            "status": "complete",
            "panel_integrity": panel_integrity,
            "development_elimination": _compact_elimination(gate),
        })
        print(json.dumps({
            "event": "edge_corpus_development_complete",
            "panel_integrity": panel_integrity,
            "development_elimination": _compact_elimination(gate),
            "validation_opened": False,
            "holdout_opened": False,
        }), flush=True)
    except Exception as exc:
        STATE.update({
            "status": "failed",
            "error": str(exc),
        })
        print(json.dumps({
            "event": "edge_corpus_development_failed",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "validation_opened": False,
            "holdout_opened": False,
        }), flush=True)


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(run_development_only())


@app.get("/")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "status": STATE.get("status"),
        "stage": STATE.get("stage"),
        "validation_opened": False,
        "holdout_opened": False,
    }
