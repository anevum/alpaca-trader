from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


MODEL_ID = "shrunken_drift"
MODEL_VERSION = "nostra-shrunken-drift-v1"
METHODOLOGY_VERSION = "nostra-shrunken-drift-method-v1"
MIN_INDEPENDENT_CYCLES = 30
SHRINKAGE_CYCLES = 30.0


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def shrunken_drift_forecast(training_state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a conservative rolling drift forecast from prior matured cycles.

    The caller must provide a point-in-time training state whose window ends
    strictly before the candidate being forecast. Each scan cycle counts once
    in the drift estimate so a wide cross-section cannot masquerade as
    independent evidence.
    """

    if training_state.get("research_only") is not True:
        raise ValueError("training state must be research-only")
    if training_state.get("execution_authority") is not False:
        raise ValueError("training state cannot have execution authority")

    cycles = int(training_state.get("independent_cycles") or 0)
    raw_outcomes = int(training_state.get("raw_outcome_count") or 0)
    cycle_mean = _finite(training_state.get("mean_cycle_return"))
    eligible = (
        bool(training_state.get("eligible"))
        and cycles >= MIN_INDEPENDENT_CYCLES
        and cycle_mean is not None
    )
    if not eligible:
        return {
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "methodology_version": METHODOLOGY_VERSION,
            "eligible": False,
            "expected_return": None,
            "reason": "insufficient_independent_cycles",
            "independent_cycles": cycles,
            "raw_outcome_count": raw_outcomes,
            "minimum_independent_cycles": MIN_INDEPENDENT_CYCLES,
            "research_only": True,
            "execution_authority": False,
        }

    shrinkage_weight = cycles / (cycles + SHRINKAGE_CYCLES)
    expected = cycle_mean * shrinkage_weight
    return {
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "eligible": True,
        "expected_return": expected,
        "raw_mean_cycle_return": cycle_mean,
        "shrinkage_weight": shrinkage_weight,
        "shrinkage_cycles": SHRINKAGE_CYCLES,
        "independent_cycles": cycles,
        "raw_outcome_count": raw_outcomes,
        "training_window_start": training_state.get("window_start"),
        "training_window_end": training_state.get("window_end"),
        "training_cutoff": training_state.get("training_cutoff"),
        "through_score_id": training_state.get("through_score_id"),
        "research_only": True,
        "execution_authority": False,
    }
