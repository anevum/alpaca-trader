from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from math import log
from typing import Any

from .nostra_regime import REGIMES

METHODOLOGY_VERSION = "nostra-transition-v1"
MIN_MATURE_SESSIONS = 10
MIN_MATURE_TRANSITIONS_PER_STATE = 50


def _valid_regime(value: Any) -> str | None:
    regime = str(value or "").strip().upper()
    return regime if regime in REGIMES else None


def _entropy(probabilities: Mapping[str, float]) -> float:
    values = [max(float(value), 0.0) for value in probabilities.values()]
    total = sum(values)
    if total <= 0:
        return 1.0
    normalized = [value / total for value in values if value > 0]
    if len(normalized) <= 1:
        return 0.0
    raw = -sum(value * log(value) for value in normalized)
    return raw / log(len(REGIMES))


def build_transition_model(
    observations: Sequence[Mapping[str, Any]],
    *,
    alpha: float = 0.5,
) -> dict[str, Any]:
    """Build a smoothed empirical next-regime model from ordered observations.

    Transitions are only formed within the same trading session. The model is a
    transparent research baseline, not an execution model.
    """

    if alpha <= 0:
        raise ValueError("alpha must be positive")

    by_session: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in observations:
        session = str(row.get("session") or "")
        observed_at = str(row.get("observed_at") or row.get("feature_time") or "")
        regime = _valid_regime(row.get("regime"))
        if not session or not observed_at or regime is None:
            continue
        by_session[session].append((observed_at, regime))

    counts: dict[str, dict[str, int]] = {
        source: {target: 0 for target in REGIMES}
        for source in REGIMES
    }
    session_counts: dict[str, set[str]] = {
        source: set() for source in REGIMES
    }
    total_transitions = 0

    for session, rows in by_session.items():
        rows.sort(key=lambda item: item[0])
        previous: str | None = None
        for _, regime in rows:
            if previous is None:
                previous = regime
                continue
            counts[previous][regime] += 1
            session_counts[previous].add(session)
            total_transitions += 1
            previous = regime

    states: dict[str, Any] = {}
    k = len(REGIMES)
    for source in REGIMES:
        row_total = sum(counts[source].values())
        denominator = row_total + alpha * k
        probabilities = {
            target: (counts[source][target] + alpha) / denominator
            for target in REGIMES
        }
        entropy = _entropy(probabilities)
        session_count = len(session_counts[source])
        sample_component = min(row_total / 100.0, 1.0)
        session_component = min(session_count / MIN_MATURE_SESSIONS, 1.0)
        confidence = sample_component * session_component * (1.0 - 0.5 * entropy)
        minimums_met = (
            session_count >= MIN_MATURE_SESSIONS
            and row_total >= MIN_MATURE_TRANSITIONS_PER_STATE
        )
        if not minimums_met:
            confidence = min(confidence, 0.49)

        states[source] = {
            "transition_count": row_total,
            "independent_sessions": session_count,
            "probabilities": {
                key: round(value, 6)
                for key, value in sorted(probabilities.items())
            },
            "normalized_entropy": round(entropy, 6),
            "confidence": round(max(min(confidence, 1.0), 0.0), 6),
            "minimums_met": minimums_met,
        }

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "alpha": alpha,
        "sessions": len(by_session),
        "total_transitions": total_transitions,
        "states": states,
        "research_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }


def forecast_next_regime(
    model: Mapping[str, Any],
    *,
    current_regime: str,
) -> dict[str, Any]:
    regime = _valid_regime(current_regime)
    if regime is None:
        raise ValueError("current_regime is not in the NOSTRA vocabulary")
    states = model.get("states")
    states = states if isinstance(states, Mapping) else {}
    state = states.get(regime)
    if not isinstance(state, Mapping):
        raise ValueError("transition model does not contain current regime")

    probabilities = state.get("probabilities")
    probabilities = probabilities if isinstance(probabilities, Mapping) else {}
    ordered = sorted(
        ((str(key), float(value)) for key, value in probabilities.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    next_regime = ordered[0][0] if ordered else "UNKNOWN"
    next_probability = ordered[0][1] if ordered else 0.0

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "current_regime": regime,
        "next_regime": next_regime,
        "next_probability": round(next_probability, 6),
        "probabilities": dict(probabilities),
        "confidence": state.get("confidence", 0.0),
        "minimums_met": bool(state.get("minimums_met")),
        "transition_count": int(state.get("transition_count") or 0),
        "independent_sessions": int(state.get("independent_sessions") or 0),
        "research_only": True,
        "execution_authority": False,
    }


def evaluate_transition_calibration(
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate frozen next-regime probability forecasts once outcomes mature."""

    usable = []
    for row in predictions:
        realized = _valid_regime(row.get("realized_regime"))
        probabilities = row.get("probabilities")
        if realized is None or not isinstance(probabilities, Mapping):
            continue
        vector = {
            regime: max(float(probabilities.get(regime, 0.0) or 0.0), 0.0)
            for regime in REGIMES
        }
        total = sum(vector.values())
        if total <= 0:
            continue
        vector = {key: value / total for key, value in vector.items()}
        usable.append((vector, realized))

    if not usable:
        return {
            "observations": 0,
            "multiclass_brier": None,
            "log_loss": None,
            "top1_accuracy": None,
            "mature": False,
            "research_only": True,
        }

    n = len(usable)
    realized_counts = {
        regime: sum(1 for _, realized in usable if realized == regime)
        for regime in REGIMES
    }
    base_probabilities = {
        regime: realized_counts[regime] / n
        for regime in REGIMES
    }

    brier = 0.0
    base_brier = 0.0
    loss = 0.0
    base_loss = 0.0
    correct = 0
    eps = 1e-12
    majority_regime = max(base_probabilities, key=base_probabilities.get)
    majority_correct = 0

    for probabilities, realized in usable:
        brier += sum(
            (probabilities[regime] - (1.0 if regime == realized else 0.0)) ** 2
            for regime in REGIMES
        ) / len(REGIMES)
        base_brier += sum(
            (
                base_probabilities[regime]
                - (1.0 if regime == realized else 0.0)
            ) ** 2
            for regime in REGIMES
        ) / len(REGIMES)

        p_realized = max(probabilities[realized], eps)
        base_p_realized = max(base_probabilities[realized], eps)
        loss -= log(p_realized)
        base_loss -= log(base_p_realized)

        predicted = max(probabilities, key=probabilities.get)
        correct += int(predicted == realized)
        majority_correct += int(majority_regime == realized)

    mean_brier = brier / n
    mean_base_brier = base_brier / n
    mean_loss = loss / n
    mean_base_loss = base_loss / n

    return {
        "observations": n,
        "multiclass_brier": round(mean_brier, 8),
        "base_rate_brier": round(mean_base_brier, 8),
        "brier_skill_score": round(
            1.0 - mean_brier / mean_base_brier
            if mean_base_brier > 0 else 0.0,
            8,
        ),
        "log_loss": round(mean_loss, 8),
        "base_rate_log_loss": round(mean_base_loss, 8),
        "log_loss_skill_score": round(
            1.0 - mean_loss / mean_base_loss
            if mean_base_loss > 0 else 0.0,
            8,
        ),
        "top1_accuracy": round(correct / n, 6),
        "majority_class_accuracy": round(majority_correct / n, 6),
        "mature": n >= 100,
        "research_only": True,
        "execution_authority": False,
    }
