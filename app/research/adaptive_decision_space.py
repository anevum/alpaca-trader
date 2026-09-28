from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import exp
from typing import Any, Mapping


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def clamp01(value: Decimal) -> Decimal:
    return min(max(value, Decimal("0")), Decimal("1"))


def logistic(value: Decimal) -> Decimal:
    x = float(value)
    return Decimal(str(1.0 / (1.0 + exp(-x))))


@dataclass(frozen=True)
class DecisionVector:
    attention: Decimal
    qualification: Decimal
    timing: Decimal
    exit_health: Decimal
    confidence: Decimal

    def as_dict(self) -> dict[str, float]:
        return {
            "attention": round(float(self.attention), 6),
            "qualification": round(float(self.qualification), 6),
            "timing": round(float(self.timing), 6),
            "exit_health": round(float(self.exit_health), 6),
            "confidence": round(float(self.confidence), 6),
        }


DEFAULT_WEIGHTS: dict[str, dict[str, Decimal]] = {
    "attention": {
        "intraday_return": Decimal("0.20"),
        "session_range": Decimal("0.15"),
        "relative_volume": Decimal("0.25"),
        "liquidity": Decimal("0.25"),
        "trend_persistence": Decimal("0.15"),
    },
    "qualification": {
        "momentum": Decimal("0.20"),
        "vwap_edge": Decimal("0.15"),
        "confirmations": Decimal("0.15"),
        "regime": Decimal("0.20"),
        "spread": Decimal("0.10"),
        "freshness": Decimal("0.10"),
        "relative_volume": Decimal("0.05"),
        "trend_persistence": Decimal("0.05"),
    },
    "timing": {
        "momentum_acceleration": Decimal("0.25"),
        "distance_from_vwap_optimum": Decimal("0.20"),
        "bar_impulse": Decimal("0.20"),
        "spread": Decimal("0.10"),
        "freshness": Decimal("0.10"),
        "confirmation_alignment": Decimal("0.15"),
    },
    "exit_health": {
        "current_return": Decimal("0.20"),
        "mfe_retention": Decimal("0.20"),
        "momentum_health": Decimal("0.20"),
        "regime_health": Decimal("0.20"),
        "time_remaining": Decimal("0.10"),
        "adverse_excursion": Decimal("0.10"),
    },
}


def _weighted_score(
    features: Mapping[str, Any],
    weights: Mapping[str, Decimal],
) -> Decimal:
    total = Decimal("0")
    weight_sum = Decimal("0")
    for name, weight in weights.items():
        value = clamp01(d(features.get(name)))
        total += value * weight
        weight_sum += weight
    if weight_sum <= 0:
        return Decimal("0")
    return clamp01(total / weight_sum)


def build_decision_vector(
    *,
    attention_features: Mapping[str, Any],
    qualification_features: Mapping[str, Any],
    timing_features: Mapping[str, Any],
    exit_features: Mapping[str, Any],
    confidence_features: Mapping[str, Any],
    weights: Mapping[str, Mapping[str, Decimal]] | None = None,
) -> DecisionVector:
    """Return independent decision-space scores without authorizing execution.

    This module is research-only. It does not import broker clients, risk gates,
    live runtime state, or execution functions. The scores are intended for
    replay, attribution, and future experiment proposals only.
    """
    selected = weights or DEFAULT_WEIGHTS
    attention = _weighted_score(
        attention_features, selected.get("attention", {})
    )
    qualification = _weighted_score(
        qualification_features, selected.get("qualification", {})
    )
    timing = _weighted_score(
        timing_features, selected.get("timing", {})
    )
    exit_health = _weighted_score(
        exit_features, selected.get("exit_health", {})
    )

    evidence_coverage = clamp01(d(confidence_features.get("coverage")))
    sample_strength = clamp01(d(confidence_features.get("sample_strength")))
    stability = clamp01(d(confidence_features.get("stability")))
    confidence = (
        evidence_coverage * Decimal("0.40")
        + sample_strength * Decimal("0.35")
        + stability * Decimal("0.25")
    )

    return DecisionVector(
        attention=attention,
        qualification=qualification,
        timing=timing,
        exit_health=exit_health,
        confidence=clamp01(confidence),
    )


def adaptive_priority(
    vector: DecisionVector,
    *,
    coefficients: Mapping[str, Any] | None = None,
) -> Decimal:
    """Combine decision dimensions into a bounded research priority score.

    The default deliberately keeps confidence separate from raw opportunity
    quality so sparse evidence cannot produce false certainty.
    """
    c = {
        "attention": Decimal("0.20"),
        "qualification": Decimal("0.35"),
        "timing": Decimal("0.30"),
        "exit_health": Decimal("0.15"),
        **{k: d(v) for k, v in (coefficients or {}).items()},
    }
    raw = (
        vector.attention * c["attention"]
        + vector.qualification * c["qualification"]
        + vector.timing * c["timing"]
        + vector.exit_health * c["exit_health"]
    )
    normalized = clamp01(raw)
    return clamp01(
        normalized
        * (
            Decimal("0.5")
            + Decimal("0.5") * vector.confidence
        )
    )
