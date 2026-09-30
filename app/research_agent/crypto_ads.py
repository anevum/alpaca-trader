from __future__ import annotations

from math import exp
from typing import Any, Mapping

from ..math_kernel import EvidenceProbability


METHODOLOGY_VERSION = "ads-crypto-v1"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_crypto_ads(
    feature_state: Mapping[str, Any],
    *,
    calibration_version: str,
    calibrated: bool = False,
) -> dict[str, Any]:
    """Research-only crypto ADS state.

    The score uses shared mathematical evidence primitives, but probability is
    withheld until a crypto-specific calibration has passed its validation gate.
    """
    normalized = dict(feature_state.get("normalized") or {})
    raw = dict(feature_state.get("raw") or {})
    momentum = _f(normalized.get("volatility_normalized_momentum"))
    spread = max(_f(raw.get("spread_bps")), 0.0)
    activity = max(_f(raw.get("trade_count") or raw.get("trade_volume")), 0.0)
    quote_age_ms = raw.get("quote_age_ms")
    age_penalty = min(max(_f(quote_age_ms) / 5000.0, 0.0), 2.0) if quote_age_ms is not None else 1.0
    spread_penalty = min(spread / 50.0, 2.0)
    activity_bonus = min(activity / 100.0, 1.0)
    score = momentum + 0.25 * activity_bonus - 0.5 * spread_penalty - 0.25 * age_penalty
    probability = 1.0 / (1.0 + exp(-score)) if calibrated else None
    evidence = EvidenceProbability(
        score=score,
        probability=probability,
        calibration_version=calibration_version,
        calibrated=calibrated,
        reason=(
            "crypto-specific calibration active"
            if calibrated
            else "probability withheld until crypto calibration promotion"
        ),
    )
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "market_lane": "crypto",
        "raw_features": raw,
        "normalized_features": normalized,
        **evidence.as_dict(),
        "research_only": True,
        "execution_authority": False,
        "equity_calibration_reused": False,
    }
