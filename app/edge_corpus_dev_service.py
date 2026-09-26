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
    _bars_for_panel,
    _load_role,
    _panel_integrity,
    _scenario_stage,
    _shared_panel,
)
from .edge_development_decisive import irreversible_development_rejections
from .edge_discovery import EdgeDiscoveryStudy, aggregate_family_periods
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


def _research_settings(settings, manifest):
    return settings.model_copy(
        update={
            "stop_pct": Decimal(manifest.research_stop_pct),
            "target_pct": Decimal(manifest.research_target_pct),
            "entry_start_raw": manifest.research_entry_start,
            "entry_cutoff_raw": manifest.research_entry_cutoff,
        }
    )


def _scenario(manifest, name: str) -> tuple[str, Decimal, Decimal]:
    for scenario_name, spread, slippage in manifest.research_cost_scenarios:
        if scenario_name.lower() == name.lower():
            return (
                scenario_name,
                Decimal(spread),
                Decimal(slippage),
            )
    raise RuntimeError(f"corpus manifest has no {name!r} cost scenario")


def _period_summary(period: dict[str, Any]) -> dict[str, Any]:
    return {
        family: {
            "events": summary.get("events"),
            "expectancy_pct": summary.get("expectancy_pct"),
            "profit_factor": summary.get("profit_factor"),
        }
        for family, summary in (period.get("summary_by_family") or {}).items()
    }


async def run_development_only() -> None:
    try:
        runtime_settings = get_settings()
        if (
            runtime_settings.execution_enabled
            or runtime_settings.bot_armed
            or runtime_settings.live_trading
        ):
            raise RuntimeError("research service execution gates are not hard-disabled")

        manifest = load_manifest("research/edge-corpus-v1.json")
        settings = _research_settings(runtime_settings, manifest)

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
            "research_stop_pct": manifest.research_stop_pct,
            "research_target_pct": manifest.research_target_pct,
            "research_entry_start": manifest.research_entry_start,
            "research_entry_cutoff": manifest.research_entry_cutoff,
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
            integrity = {
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
            }
            integrity_windows.append(integrity)
            print(json.dumps({
                "event": "edge_corpus_integrity_window",
                **integrity,
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

        stress_name, stress_spread, stress_slippage = _scenario(
            manifest,
            "stress",
        )
        total_periods = len(payloads)
        stress_periods: list[dict[str, Any]] = []
        proof: dict[str, Any] | None = None
        STATE["status"] = "evaluating_development_stress"

        for index, payload in enumerate(payloads, start=1):
            bars = _bars_for_panel(
                payload,
                panel,
                manifest.confirmation_symbols,
            )
            study = EdgeDiscoveryStudy(
                settings,
                panel,
                confirmation_symbols=manifest.confirmation_symbols,
                event_cooldown_minutes=manifest.research_event_cooldown_minutes,
            )
            result = await asyncio.to_thread(
                study.run,
                bars,
                spread_bps=stress_spread,
                slippage_bps=stress_slippage,
                horizon=manifest.research_horizon_minutes,
            )
            window = payload["window"]
            period = {
                "period": window["id"],
                "range": {
                    "start": window["start"],
                    "end": window["end"],
                },
                "role": window["role"],
                **result,
            }
            stress_periods.append(period)
            proof = irreversible_development_rejections(
                stress_periods,
                total_periods=total_periods,
            )
            STATE.update({
                "status": "evaluating_development_stress",
                "periods_completed": index,
                "periods_total": total_periods,
                "irreversible_proof": proof,
            })
            print(json.dumps({
                "event": "edge_corpus_stress_period_complete",
                "period": window["id"],
                "position": index,
                "total": total_periods,
                "summary_by_family": _period_summary(period),
                "irreversible_proof": proof,
            }), flush=True)

            if proof["all_families_irreversibly_rejected"]:
                break

        if proof is None:
            raise RuntimeError("development produced no stress periods")

        if proof["all_families_irreversibly_rejected"]:
            gate = {
                "stage": "development",
                "survivors": [],
                "rejected": list(proof["irreversibly_rejected"]),
                "decisive_scenario": stress_name,
                "early_stop_valid": True,
                "proof": proof,
                "criteria": {
                    "rule": (
                        "A family must pass every cost scenario. Failure of the "
                        "stress scenario is sufficient for development rejection."
                    ),
                },
            }
            scenarios_run = [stress_name]
        else:
            stress_scenario = {
                "scenario": stress_name,
                "spread_bps": str(stress_spread),
                "slippage_bps_per_side": str(stress_slippage),
                "periods": stress_periods,
                "aggregate_by_family": aggregate_family_periods(
                    stress_periods,
                    horizon=manifest.research_horizon_minutes,
                ),
            }
            other_scenarios = [
                (
                    name,
                    Decimal(spread),
                    Decimal(slippage),
                )
                for name, spread, slippage in manifest.research_cost_scenarios
                if name != stress_name
            ]
            extra = await asyncio.to_thread(
                _scenario_stage,
                payloads,
                settings=settings,
                panel=panel,
                confirmation_symbols=manifest.confirmation_symbols,
                scenarios=other_scenarios,
                horizon=manifest.research_horizon_minutes,
                event_cooldown_minutes=manifest.research_event_cooldown_minutes,
            )
            scenarios = [*extra, stress_scenario]
            gate = development_elimination(scenarios)
            scenarios_run = [item["scenario"] for item in scenarios]

        report = {
            "status": "research_only",
            "stage": "development_only",
            "corpus": {
                "version": manifest.version,
                "manifest_sha256": manifest_sha256(manifest),
                "data_feed": manifest.data_feed,
                "timeframe": manifest.timeframe,
            },
            "research_settings": {
                "horizon_minutes": manifest.research_horizon_minutes,
                "event_cooldown_minutes": (
                    manifest.research_event_cooldown_minutes
                ),
                "stop_pct": manifest.research_stop_pct,
                "target_pct": manifest.research_target_pct,
                "entry_start": manifest.research_entry_start,
                "entry_cutoff": manifest.research_entry_cutoff,
                "scenarios_run": scenarios_run,
            },
            "integrity_windows": integrity_windows,
            "panel_integrity": panel_integrity,
            "stress_periods": [
                {
                    "period": period["period"],
                    "range": period["range"],
                    "role": period["role"],
                    "summary_by_family": period["summary_by_family"],
                }
                for period in stress_periods
            ],
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
            "development_elimination": gate,
        })
        print(json.dumps({
            "event": "edge_corpus_development_complete",
            "panel_integrity": panel_integrity,
            "development_elimination": gate,
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
        **STATE,
        "validation_opened": False,
        "holdout_opened": False,
    }
