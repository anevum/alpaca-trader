from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

BASELINE_VERSION = "nostra-baselines-v1"


def zero_return_baseline() -> dict[str, Any]:
    return {
        "baseline_id": "zero_return",
        "baseline_version": BASELINE_VERSION,
        "target_kind": "return",
        "expected_return": 0.0,
        "research_only": True,
        "execution_authority": False,
    }


def uniform_direction_baseline(
    labels: Sequence[str] = ("up", "neutral", "down"),
) -> dict[str, Any]:
    clean = tuple(str(label).strip().lower() for label in labels if str(label).strip())
    if not clean or len(set(clean)) != len(clean):
        raise ValueError("labels must be non-empty and unique")
    p = 1.0 / len(clean)
    return {
        "baseline_id": "uniform_direction",
        "baseline_version": BASELINE_VERSION,
        "target_kind": "direction",
        "probabilities": {label: p for label in clean},
        "research_only": True,
        "execution_authority": False,
    }


def empirical_direction_baseline(
    realized_labels: Iterable[str],
    *,
    labels: Sequence[str] = ("up", "neutral", "down"),
    alpha: float = 1.0,
) -> dict[str, Any]:
    """Laplace-smoothed base rates from prior realized observations only."""

    clean_labels = tuple(
        str(label).strip().lower() for label in labels if str(label).strip()
    )
    if not clean_labels or len(set(clean_labels)) != len(clean_labels):
        raise ValueError("labels must be non-empty and unique")
    if alpha <= 0:
        raise ValueError("alpha must be positive")

    counts = Counter(str(item).strip().lower() for item in realized_labels)
    unknown = set(counts) - set(clean_labels)
    if unknown:
        raise ValueError(f"unknown realized labels: {sorted(unknown)}")

    total = sum(counts.values())
    denominator = total + alpha * len(clean_labels)
    probabilities = {
        label: (counts.get(label, 0) + alpha) / denominator
        for label in clean_labels
    }
    return {
        "baseline_id": "empirical_direction",
        "baseline_version": BASELINE_VERSION,
        "target_kind": "direction",
        "samples": total,
        "alpha": alpha,
        "probabilities": probabilities,
        "research_only": True,
        "execution_authority": False,
    }


def volatility_persistence_baseline(last_realized_volatility: float) -> dict[str, Any]:
    value = float(last_realized_volatility)
    if value < 0:
        raise ValueError("last_realized_volatility cannot be negative")
    return {
        "baseline_id": "volatility_persistence",
        "baseline_version": BASELINE_VERSION,
        "target_kind": "volatility",
        "expected_volatility": value,
        "research_only": True,
        "execution_authority": False,
    }
