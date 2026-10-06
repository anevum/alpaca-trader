"""Deterministic hypothesis generator for the bounded adaptive GRAEN lane.

V2 deliberately starts a new research program after the finite V1
bar-flow-pressure lattice is exhausted. It does not read market data, call a
model, touch broker state, or change live strategy configuration. It only
produces frozen research specs accepted by graen.engineering's trusted
compiler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from graen.engineering import SCHEMA, validate_spec


UTC = timezone.utc
PROGRAM_ID = "GRAEN-ADAPTIVE-CROSS-SECTIONAL-V2"
SUPERSEDES_PROGRAM_ID = "GRAEN-ADAPTIVE-FLOW-V1"
MAX_GENERATIONS = 16

# Finite, reviewable lattice grounded in the currently deployed paper-only
# cross-sectional crypto candidate. The compiler may vary only these thresholds;
# the mechanism, universe, costs, stage order, and execution boundary stay
# frozen. No generation is created from validation/holdout outcomes.
PARAMETER_LATTICE = (
    {"momentum_5m_min": 0.0005, "momentum_15m_min": 0.0010,
     "momentum_60m_floor": -0.0050, "max_vwap_extension_pct": 0.0120,
     "min_expected_move_pct": 0.0030, "hold_minutes": 15,
     "cooldown_minutes": 30, "rank_top_n": 2},
    {"momentum_5m_min": 0.0010, "momentum_15m_min": 0.0020,
     "momentum_60m_floor": -0.0050, "max_vwap_extension_pct": 0.0100,
     "min_expected_move_pct": 0.0040, "hold_minutes": 20,
     "cooldown_minutes": 40, "rank_top_n": 2},
    {"momentum_5m_min": 0.0015, "momentum_15m_min": 0.0030,
     "momentum_60m_floor": -0.0025, "max_vwap_extension_pct": 0.0100,
     "min_expected_move_pct": 0.0050, "hold_minutes": 30,
     "cooldown_minutes": 60, "rank_top_n": 2},
    {"momentum_5m_min": 0.0020, "momentum_15m_min": 0.0040,
     "momentum_60m_floor": 0.0000, "max_vwap_extension_pct": 0.0080,
     "min_expected_move_pct": 0.0050, "hold_minutes": 30,
     "cooldown_minutes": 60, "rank_top_n": 1},
    {"momentum_5m_min": 0.0025, "momentum_15m_min": 0.0050,
     "momentum_60m_floor": 0.0000, "max_vwap_extension_pct": 0.0080,
     "min_expected_move_pct": 0.0060, "hold_minutes": 45,
     "cooldown_minutes": 90, "rank_top_n": 1},
    {"momentum_5m_min": 0.0030, "momentum_15m_min": 0.0060,
     "momentum_60m_floor": 0.0010, "max_vwap_extension_pct": 0.0070,
     "min_expected_move_pct": 0.0075, "hold_minutes": 60,
     "cooldown_minutes": 120, "rank_top_n": 1},
    {"momentum_5m_min": 0.0005, "momentum_15m_min": 0.0025,
     "momentum_60m_floor": -0.0100, "max_vwap_extension_pct": 0.0150,
     "min_expected_move_pct": 0.0040, "hold_minutes": 15,
     "cooldown_minutes": 45, "rank_top_n": 2},
    {"momentum_5m_min": 0.0010, "momentum_15m_min": 0.0030,
     "momentum_60m_floor": -0.0075, "max_vwap_extension_pct": 0.0150,
     "min_expected_move_pct": 0.0050, "hold_minutes": 20,
     "cooldown_minutes": 60, "rank_top_n": 2},
    {"momentum_5m_min": 0.0015, "momentum_15m_min": 0.0040,
     "momentum_60m_floor": -0.0050, "max_vwap_extension_pct": 0.0120,
     "min_expected_move_pct": 0.0060, "hold_minutes": 30,
     "cooldown_minutes": 90, "rank_top_n": 2},
    {"momentum_5m_min": 0.0020, "momentum_15m_min": 0.0050,
     "momentum_60m_floor": -0.0025, "max_vwap_extension_pct": 0.0100,
     "min_expected_move_pct": 0.0075, "hold_minutes": 45,
     "cooldown_minutes": 120, "rank_top_n": 1},
    {"momentum_5m_min": 0.0035, "momentum_15m_min": 0.0075,
     "momentum_60m_floor": 0.0025, "max_vwap_extension_pct": 0.0060,
     "min_expected_move_pct": 0.0100, "hold_minutes": 60,
     "cooldown_minutes": 120, "rank_top_n": 1},
    {"momentum_5m_min": 0.0005, "momentum_15m_min": 0.0010,
     "momentum_60m_floor": -0.0100, "max_vwap_extension_pct": 0.0200,
     "min_expected_move_pct": 0.0030, "hold_minutes": 10,
     "cooldown_minutes": 20, "rank_top_n": 2},
    {"momentum_5m_min": 0.0010, "momentum_15m_min": 0.0015,
     "momentum_60m_floor": -0.0050, "max_vwap_extension_pct": 0.0200,
     "min_expected_move_pct": 0.0040, "hold_minutes": 15,
     "cooldown_minutes": 30, "rank_top_n": 1},
    {"momentum_5m_min": 0.0025, "momentum_15m_min": 0.0035,
     "momentum_60m_floor": 0.0000, "max_vwap_extension_pct": 0.0120,
     "min_expected_move_pct": 0.0060, "hold_minutes": 20,
     "cooldown_minutes": 40, "rank_top_n": 1},
    {"momentum_5m_min": 0.0015, "momentum_15m_min": 0.0025,
     "momentum_60m_floor": -0.0025, "max_vwap_extension_pct": 0.0075,
     "min_expected_move_pct": 0.0050, "hold_minutes": 45,
     "cooldown_minutes": 90, "rank_top_n": 1},
    {"momentum_5m_min": 0.0040, "momentum_15m_min": 0.0080,
     "momentum_60m_floor": 0.0050, "max_vwap_extension_pct": 0.0050,
     "min_expected_move_pct": 0.0120, "hold_minutes": 30,
     "cooldown_minutes": 120, "rank_top_n": 1},
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
        "hypothesis_id": f"CRYPTO-CROSS-ADAPTIVE-{generation:03d}",
        "epoch": f"{freeze.date().isoformat()}-CROSS-G{generation:03d}",
        "mechanism": "cross_sectional_intraday_v1",
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
            "fresh cross-sectional V2 program; confirmatory corpus sealed",
        ],
        "exposure_artifact_id": exposure_artifact_id,
        "research_only": True,
        "execution_authority": False,
    }
    validate_spec(spec)
    return spec
