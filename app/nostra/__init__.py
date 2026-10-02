"""NOSTRA forecasting evidence foundation.

This package owns immutable forecasting contracts, baseline forecasts, scoring,
and durable ledger transport. It has no execution authority.
"""

from .baselines import (
    BASELINE_VERSION,
    empirical_direction_baseline,
    uniform_direction_baseline,
    volatility_persistence_baseline,
    zero_return_baseline,
)
from .contracts import (
    EVALUATION_SCHEMA_VERSION,
    FORECAST_METHODOLOGY_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    build_evaluation,
    build_forecast,
    build_outcome,
    build_score_record,
    build_snapshot,
    canonical_json,
    stable_id,
)
from .ledger import NostraLedger
from .scoring import (
    SCORING_VERSION,
    multiclass_brier,
    multiclass_log_loss,
    score_direction_forecast,
    score_return_forecast,
    skill_score,
)

__all__ = [
    "BASELINE_VERSION",
    "EVALUATION_SCHEMA_VERSION",
    "FORECAST_METHODOLOGY_VERSION",
    "NostraLedger",
    "PROVENANCE_SCHEMA_VERSION",
    "SCORING_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_evaluation",
    "build_forecast",
    "build_outcome",
    "build_score_record",
    "build_snapshot",
    "canonical_json",
    "empirical_direction_baseline",
    "multiclass_brier",
    "multiclass_log_loss",
    "score_direction_forecast",
    "score_return_forecast",
    "skill_score",
    "stable_id",
    "uniform_direction_baseline",
    "volatility_persistence_baseline",
    "zero_return_baseline",
]
