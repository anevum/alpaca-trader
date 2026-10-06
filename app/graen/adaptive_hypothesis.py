"""Deterministic hypothesis generator for the bounded adaptive GRAEN lane.

This module does not read market data, call a model, touch broker state, or
change live strategy configuration. It only produces frozen research specs
accepted by graen.engineering's trusted compiler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from graen.engineering import SCHEMA, validate_spec


UTC = timezone.utc
PROGRAM_ID = "GRAEN-ADAPTIVE-FLOW-V1"
MAX_GENERATIONS = 16

# A finite, reviewable lattice. Each generation changes the research program
# while keeping the trusted mechanism, universe, gates, and execution boundary
# fixed. Confirmatory windows are never reused after they have opened.
PARAMETER_LATTICE = (
    {"lookback_bars": 36, "volume_z": 1.5, "trade_count_z": 1.5,
     "range_ratio": 1.25, "body_strength": 0.55, "close_location": 0.65,
     "hold_minutes": 15, "cooldown_minutes": 30},
    {"lookback_bars": 48, "volume_z": 2.0, "trade_count_z": 1.5,
     "range_ratio": 1.50, "body_strength": 0.60, "close_location": 0.70,
     "hold_minutes": 20, "cooldown_minutes": 40},
    {"lookback_bars": 72, "volume_z": 1.5, "trade_count_z": 2.0,
     "range_ratio": 1.50, "body_strength": 0.65, "close_location": 0.75,
     "hold_minutes": 30, "cooldown_minutes": 60},
    {"lookback_bars": 96, "volume_z": 2.0, "trade_count_z": 2.0,
     "range_ratio": 1.75, "body_strength": 0.60, "close_location": 0.80,
     "hold_minutes": 30, "cooldown_minutes": 60},
    {"lookback_bars": 144, "volume_z": 2.5, "trade_count_z": 2.0,
     "range_ratio": 1.50, "body_strength": 0.70, "close_location": 0.75,
     "hold_minutes": 45, "cooldown_minutes": 90},
    {"lookback_bars": 192, "volume_z": 2.0, "trade_count_z": 2.5,
     "range_ratio": 2.00, "body_strength": 0.65, "close_location": 0.80,
     "hold_minutes": 60, "cooldown_minutes": 120},
    {"lookback_bars": 288, "volume_z": 2.5, "trade_count_z": 2.5,
     "range_ratio": 2.00, "body_strength": 0.70, "close_location": 0.85,
     "hold_minutes": 60, "cooldown_minutes": 120},
    {"lookback_bars": 48, "volume_z": 1.0, "trade_count_z": 1.5,
     "range_ratio": 1.25, "body_strength": 0.60, "close_location": 0.75,
     "hold_minutes": 10, "cooldown_minutes": 30},
    {"lookback_bars": 72, "volume_z": 2.5, "trade_count_z": 1.0,
     "range_ratio": 1.75, "body_strength": 0.75, "close_location": 0.85,
     "hold_minutes": 20, "cooldown_minutes": 60},
    {"lookback_bars": 96, "volume_z": 1.5, "trade_count_z": 2.5,
     "range_ratio": 1.25, "body_strength": 0.55, "close_location": 0.70,
     "hold_minutes": 45, "cooldown_minutes": 90},
    {"lookback_bars": 144, "volume_z": 3.0, "trade_count_z": 1.5,
     "range_ratio": 2.25, "body_strength": 0.75, "close_location": 0.90,
     "hold_minutes": 30, "cooldown_minutes": 90},
    {"lookback_bars": 192, "volume_z": 1.5, "trade_count_z": 3.0,
     "range_ratio": 1.75, "body_strength": 0.65, "close_location": 0.75,
     "hold_minutes": 90, "cooldown_minutes": 180},
    {"lookback_bars": 36, "volume_z": 3.5, "trade_count_z": 2.0,
     "range_ratio": 2.50, "body_strength": 0.80, "close_location": 0.90,
     "hold_minutes": 15, "cooldown_minutes": 60},
    {"lookback_bars": 72, "volume_z": 2.0, "trade_count_z": 3.5,
     "range_ratio": 2.00, "body_strength": 0.80, "close_location": 0.85,
     "hold_minutes": 45, "cooldown_minutes": 120},
    {"lookback_bars": 144, "volume_z": 1.0, "trade_count_z": 1.0,
     "range_ratio": 1.10, "body_strength": 0.55, "close_location": 0.60,
     "hold_minutes": 120, "cooldown_minutes": 240},
    {"lookback_bars": 288, "volume_z": 4.0, "trade_count_z": 4.0,
     "range_ratio": 3.00, "body_strength": 0.85, "close_location": 0.95,
     "hold_minutes": 30, "cooldown_minutes": 120},
)


def conservative_exposure_ledger(now: datetime) -> dict[str, Any]:
    freeze = now.astimezone(UTC)
    return {
        "schema_version": "graen.corpus-exposure-ledger.v1",
        "complete": True,
        "policy": "all_history_before_freeze_is_treated_as_inspected",
        "inspection_cutoff": freeze.isoformat(),
        "inspected_intervals": [
            ["1970-01-01T00:00:00+00:00", freeze.isoformat()]
        ],
        "research_only": True,
        "execution_authority": False,
    }


def build_spec(
    generation: int,
    *,
    exposure_artifact_id: str,
    search_history: list[str],
    now: datetime,
) -> dict[str, Any]:
    if generation < 1 or generation > MAX_GENERATIONS:
        raise ValueError("adaptive_generation_exhausted")

    freeze = now.astimezone(UTC)
    today = freeze.replace(hour=0, minute=0, second=0, microsecond=0)
    development_end = today
    development_start = development_end - timedelta(days=120)
    validation_start = today + timedelta(days=1)
    validation_end = validation_start + timedelta(days=31)
    holdout_start = validation_end
    holdout_end = holdout_start + timedelta(days=31)

    spec = {
        "schema_version": SCHEMA,
        "hypothesis_id": f"CRYPTO-FLOW-ADAPTIVE-{generation:03d}",
        "epoch": (
            f"{freeze.date().isoformat()}-G{generation:03d}"
        ),
        "mechanism": "bar_flow_pressure_v1",
        "universe": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "parameters": dict(PARAMETER_LATTICE[generation - 1]),
        "corpus": {
            "development": [
                development_start.isoformat(),
                development_end.isoformat(),
            ],
            "validation": [
                validation_start.isoformat(),
                validation_end.isoformat(),
            ],
            "holdout": [
                holdout_start.isoformat(),
                holdout_end.isoformat(),
            ],
        },
        "gates": {
            "policy": "activity_shock_v9_frozen_gates",
            "selection_cost": "high",
            "delay_minutes": 5,
            "confirmatory_candidates": 1,
        },
        "falsification": (
            "Reject immediately on any frozen stage gate failure. "
            "Do not inspect a later stage until its predecessor passes."
        ),
        "search_history": list(search_history)[-50:] or [
            "fresh adaptive program; no confirmatory corpus inspected"
        ],
        "exposure_artifact_id": exposure_artifact_id,
        "research_only": True,
        "execution_authority": False,
    }
    validate_spec(spec)
    return spec
