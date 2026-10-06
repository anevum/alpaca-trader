"""Deterministic hypothesis generator for the bounded adaptive GRAEN lane.

V3 starts a genuinely new mechanism after V2 exhausted its finite absolute
momentum/VWAP threshold search. It does not read market data, call a model,
touch broker state, or change live strategy configuration. It only produces
frozen research specs accepted by graen.engineering's trusted compiler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from graen.engineering import SCHEMA, validate_spec


UTC = timezone.utc
PROGRAM_ID = "GRAEN-ADAPTIVE-ACCELERATION-V3"
SUPERSEDES_PROGRAM_ID = "GRAEN-ADAPTIVE-CROSS-SECTIONAL-V2"
MAX_GENERATIONS = 12

# Finite lattice for a different mechanism family: peer-relative acceleration
# confirmed by participation and range expansion. Mechanism, universe, costs,
# stage order, and execution boundary stay frozen.
PARAMETER_LATTICE = (
    {"relative_15m_min": 0.0005, "acceleration_5m_min": 0.0002,
     "volume_ratio_min": 1.10, "range_ratio_min": 0.90,
     "max_vwap_extension_pct": 0.0150, "min_expected_move_pct": 0.0030,
     "hold_minutes": 15, "cooldown_minutes": 30, "rank_top_n": 2},
    {"relative_15m_min": 0.0010, "acceleration_5m_min": 0.0005,
     "volume_ratio_min": 1.15, "range_ratio_min": 1.00,
     "max_vwap_extension_pct": 0.0120, "min_expected_move_pct": 0.0040,
     "hold_minutes": 20, "cooldown_minutes": 40, "rank_top_n": 2},
    {"relative_15m_min": 0.0015, "acceleration_5m_min": 0.0008,
     "volume_ratio_min": 1.20, "range_ratio_min": 1.05,
     "max_vwap_extension_pct": 0.0100, "min_expected_move_pct": 0.0050,
     "hold_minutes": 30, "cooldown_minutes": 60, "rank_top_n": 2},
    {"relative_15m_min": 0.0020, "acceleration_5m_min": 0.0010,
     "volume_ratio_min": 1.25, "range_ratio_min": 1.10,
     "max_vwap_extension_pct": 0.0090, "min_expected_move_pct": 0.0050,
     "hold_minutes": 30, "cooldown_minutes": 60, "rank_top_n": 1},
    {"relative_15m_min": 0.0025, "acceleration_5m_min": 0.0015,
     "volume_ratio_min": 1.35, "range_ratio_min": 1.15,
     "max_vwap_extension_pct": 0.0080, "min_expected_move_pct": 0.0060,
     "hold_minutes": 45, "cooldown_minutes": 90, "rank_top_n": 1},
    {"relative_15m_min": 0.0030, "acceleration_5m_min": 0.0020,
     "volume_ratio_min": 1.50, "range_ratio_min": 1.25,
     "max_vwap_extension_pct": 0.0070, "min_expected_move_pct": 0.0075,
     "hold_minutes": 60, "cooldown_minutes": 120, "rank_top_n": 1},
    {"relative_15m_min": 0.0005, "acceleration_5m_min": 0.0005,
     "volume_ratio_min": 1.30, "range_ratio_min": 1.20,
     "max_vwap_extension_pct": 0.0180, "min_expected_move_pct": 0.0040,
     "hold_minutes": 15, "cooldown_minutes": 45, "rank_top_n": 2},
    {"relative_15m_min": 0.0010, "acceleration_5m_min": 0.0010,
     "volume_ratio_min": 1.40, "range_ratio_min": 1.30,
     "max_vwap_extension_pct": 0.0150, "min_expected_move_pct": 0.0050,
     "hold_minutes": 20, "cooldown_minutes": 60, "rank_top_n": 2},
    {"relative_15m_min": 0.0015, "acceleration_5m_min": 0.0015,
     "volume_ratio_min": 1.50, "range_ratio_min": 1.40,
     "max_vwap_extension_pct": 0.0120, "min_expected_move_pct": 0.0060,
     "hold_minutes": 30, "cooldown_minutes": 90, "rank_top_n": 2},
    {"relative_15m_min": 0.0020, "acceleration_5m_min": 0.0020,
     "volume_ratio_min": 1.75, "range_ratio_min": 1.50,
     "max_vwap_extension_pct": 0.0100, "min_expected_move_pct": 0.0075,
     "hold_minutes": 45, "cooldown_minutes": 120, "rank_top_n": 1},
    {"relative_15m_min": 0.0030, "acceleration_5m_min": 0.0025,
     "volume_ratio_min": 2.00, "range_ratio_min": 1.75,
     "max_vwap_extension_pct": 0.0080, "min_expected_move_pct": 0.0100,
     "hold_minutes": 60, "cooldown_minutes": 120, "rank_top_n": 1},
    {"relative_15m_min": 0.0010, "acceleration_5m_min": 0.0002,
     "volume_ratio_min": 1.05, "range_ratio_min": 0.85,
     "max_vwap_extension_pct": 0.0200, "min_expected_move_pct": 0.0030,
     "hold_minutes": 10, "cooldown_minutes": 20, "rank_top_n": 2},
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
        "hypothesis_id": f"CRYPTO-ACCEL-ADAPTIVE-{generation:03d}",
        "epoch": f"{freeze.date().isoformat()}-ACCEL-G{generation:03d}",
        "mechanism": "cross_sectional_acceleration_v1",
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
            "Rank only from information available at the completed bar. "
            "Do not inspect a later stage until its predecessor passes."
        ),
        "search_history": list(search_history)[-50:] or [
            f"{SUPERSEDES_PROGRAM_ID}:EXHAUSTED",
            "fresh acceleration V3 program; confirmatory corpus sealed",
        ],
        "exposure_artifact_id": exposure_artifact_id,
        "research_only": True,
        "execution_authority": False,
    }
    validate_spec(spec)
    return spec
