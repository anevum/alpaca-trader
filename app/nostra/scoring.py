from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

SCORING_VERSION = "nostra-scoring-v1"


def _probabilities(values: Mapping[str, Any]) -> dict[str, float]:
    if not values:
        raise ValueError("probabilities cannot be empty")
    result = {str(key): float(value) for key, value in values.items()}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in result.values()):
        raise ValueError("all probabilities must be finite and between 0 and 1")
    if not math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("probabilities must sum to 1")
    return result


def multiclass_brier(probabilities: Mapping[str, Any], realized: str) -> float:
    probs = _probabilities(probabilities)
    realized = str(realized)
    if realized not in probs:
        raise ValueError("realized class must exist in probabilities")
    return sum(
        (probability - (1.0 if label == realized else 0.0)) ** 2
        for label, probability in probs.items()
    ) / len(probs)


def multiclass_log_loss(
    probabilities: Mapping[str, Any],
    realized: str,
    *,
    epsilon: float = 1e-12,
) -> float:
    probs = _probabilities(probabilities)
    realized = str(realized)
    if realized not in probs:
        raise ValueError("realized class must exist in probabilities")
    return -math.log(max(probs[realized], epsilon))


def skill_score(model_loss: float, baseline_loss: float) -> float | None:
    model = float(model_loss)
    baseline = float(baseline_loss)
    if not math.isfinite(model) or not math.isfinite(baseline):
        raise ValueError("losses must be finite")
    if baseline <= 0:
        return None
    return 1.0 - model / baseline


def score_direction_forecast(
    probabilities: Mapping[str, Any],
    realized: str,
    *,
    baseline_probabilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    brier = multiclass_brier(probabilities, realized)
    log_loss = multiclass_log_loss(probabilities, realized)
    result: dict[str, Any] = {
        "scoring_version": SCORING_VERSION,
        "target_kind": "direction",
        "realized": str(realized),
        "brier": brier,
        "log_loss": log_loss,
        "research_only": True,
        "execution_authority": False,
    }
    if baseline_probabilities is not None:
        baseline_brier = multiclass_brier(baseline_probabilities, realized)
        baseline_log_loss = multiclass_log_loss(baseline_probabilities, realized)
        result["baseline"] = {
            "brier": baseline_brier,
            "log_loss": baseline_log_loss,
        }
        result["skill"] = {
            "brier": skill_score(brier, baseline_brier),
            "log_loss": skill_score(log_loss, baseline_log_loss),
        }
    return result


def score_return_forecast(
    expected_return: float,
    realized_return: float,
    *,
    baseline_expected_return: float = 0.0,
) -> dict[str, Any]:
    prediction = float(expected_return)
    realized = float(realized_return)
    baseline_prediction = float(baseline_expected_return)
    values = (prediction, realized, baseline_prediction)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("returns must be finite")

    error = prediction - realized
    baseline_error = baseline_prediction - realized
    absolute_error = abs(error)
    squared_error = error * error
    baseline_absolute_error = abs(baseline_error)
    baseline_squared_error = baseline_error * baseline_error
    return {
        "scoring_version": SCORING_VERSION,
        "target_kind": "return",
        "expected_return": prediction,
        "realized_return": realized,
        "absolute_error": absolute_error,
        "squared_error": squared_error,
        "baseline": {
            "expected_return": baseline_prediction,
            "absolute_error": baseline_absolute_error,
            "squared_error": baseline_squared_error,
        },
        "skill": {
            "absolute_error": skill_score(absolute_error, baseline_absolute_error),
            "squared_error": skill_score(squared_error, baseline_squared_error),
        },
        "research_only": True,
        "execution_authority": False,
    }
