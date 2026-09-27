from __future__ import annotations

from math import exp, log
from typing import Any

from .model import artifact_checksum


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    e = exp(value)
    return e / (1.0 + e)


def fit_logistic(
    rows: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    target_name: str,
    model_key: str,
    trained_through: str,
    epochs: int = 800,
    learning_rate: float = 0.05,
    l2: float = 0.01,
) -> dict[str, Any]:
    """Deterministic research-only L2 logistic regression.

    Rows must already be point-in-time and out-of-sample-safe. This trainer does
    not manufacture a random split; callers must preserve time order.
    """
    if len(rows) < 20:
        raise ValueError("at least 20 research rows are required")
    x: list[list[float]] = []
    y: list[float] = []
    for row in rows:
        if target_name not in row:
            continue
        try:
            vector = [float(row[name]) for name in feature_names]
            target = float(row[target_name])
        except (KeyError, TypeError, ValueError):
            continue
        if target not in {0.0, 1.0}:
            continue
        x.append(vector)
        y.append(target)
    if len(x) < 20:
        raise ValueError("fewer than 20 complete rows remain after filtering")

    n = len(x)
    p = len(feature_names)
    means = [sum(row[j] for row in x) / n for j in range(p)]
    scales = []
    for j in range(p):
        variance = sum((row[j] - means[j]) ** 2 for row in x) / n
        scales.append(max(variance ** 0.5, 1e-9))
    standardized = [
        [(row[j] - means[j]) / scales[j] for j in range(p)]
        for row in x
    ]

    weights = [0.0] * p
    intercept = 0.0
    for _ in range(epochs):
        grad_w = [0.0] * p
        grad_b = 0.0
        for row, target in zip(standardized, y):
            score = intercept + sum(w * value for w, value in zip(weights, row))
            error = _sigmoid(score) - target
            grad_b += error
            for j in range(p):
                grad_w[j] += error * row[j]
        intercept -= learning_rate * grad_b / n
        for j in range(p):
            gradient = grad_w[j] / n + l2 * weights[j]
            weights[j] -= learning_rate * gradient

    payload: dict[str, Any] = {
        "model_key": model_key,
        "target": target_name,
        "feature_names": list(feature_names),
        "means": means,
        "scales": scales,
        "weights": weights,
        "intercept": intercept,
        "trained_through": trained_through,
        "status": "RESEARCH_ONLY",
        "training_rows": n,
        "methodology": {
            "algorithm": "deterministic_l2_logistic",
            "epochs": epochs,
            "learning_rate": learning_rate,
            "l2": l2,
            "split_policy": "chronological_with_embargo",
        },
    }
    payload["checksum"] = artifact_checksum(payload)
    return payload


def score_rows(
    rows: list[dict[str, Any]],
    artifact: dict[str, Any],
) -> dict[str, float | int]:
    names = tuple(str(item) for item in artifact["feature_names"])
    means = [float(value) for value in artifact["means"]]
    scales = [float(value) for value in artifact["scales"]]
    weights = [float(value) for value in artifact["weights"]]
    intercept = float(artifact["intercept"])
    target_name = str(artifact["target"])

    probabilities: list[float] = []
    targets: list[float] = []
    for row in rows:
        try:
            values = [float(row[name]) for name in names]
            target = float(row[target_name])
        except (KeyError, TypeError, ValueError):
            continue
        if target not in {0.0, 1.0}:
            continue
        z = intercept + sum(
            ((value - mean) / scale) * weight
            for value, mean, scale, weight in zip(values, means, scales, weights)
        )
        probabilities.append(_sigmoid(z))
        targets.append(target)

    if not probabilities:
        raise ValueError("no complete evaluation rows")

    base_rate = sum(targets) / len(targets)
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, targets)) / len(targets)
    base_brier = sum((base_rate - y) ** 2 for y in targets) / len(targets)

    epsilon = 1e-12
    clipped = [min(max(p, epsilon), 1.0 - epsilon) for p in probabilities]
    log_loss = -sum(
        y * log(p) + (1.0 - y) * log(1.0 - p)
        for p, y in zip(clipped, targets)
    ) / len(targets)
    base_p = min(max(base_rate, epsilon), 1.0 - epsilon)
    base_log_loss = -sum(
        y * log(base_p) + (1.0 - y) * log(1.0 - base_p)
        for y in targets
    ) / len(targets)

    accuracy = sum(
        (p >= 0.5) == bool(y) for p, y in zip(probabilities, targets)
    ) / len(targets)
    majority_accuracy = max(base_rate, 1.0 - base_rate)

    return {
        "rows": len(targets),
        "base_rate": base_rate,
        "brier_score": brier,
        "base_rate_brier_score": base_brier,
        "brier_skill_score": (
            1.0 - brier / base_brier if base_brier > 0 else 0.0
        ),
        "log_loss": log_loss,
        "base_rate_log_loss": base_log_loss,
        "log_loss_skill_score": (
            1.0 - log_loss / base_log_loss if base_log_loss > 0 else 0.0
        ),
        "direction_accuracy": accuracy,
        "majority_class_accuracy": majority_accuracy,
    }
