"""Research-only opportunity economics for RHEN V4.3.

This layer never changes live qualification, ranking, sizing, or broker authority.
It converts already-observed signal/quote evidence into a conservative economic
proxy that can be compared with realized outcomes later.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .execution_costs import (
    ZERO,
    clamp,
    d,
    economic_admission,
    estimate_costs,
)


BPS = Decimal("10000")
HALF = Decimal("0.5")

METHODOLOGY_VERSION = "rhen-shadow-economics-v1"
MINIMUM_NET_BPS = Decimal("2")
MINIMUM_GROSS_TO_COST_RATIO = Decimal("1.5")
REGULATORY_RESERVE_BPS = Decimal("0.10")
MIN_SLIPPAGE_BPS = Decimal("0.50")
BASE_UNCERTAINTY_BPS = Decimal("2")
MAX_EXTRA_UNCERTAINTY_BPS = Decimal("5")


def _bps_from_pct(value: Any) -> Decimal:
    return max(d(value), ZERO) * BPS


def _quality_confidence(metadata: dict[str, Any]) -> Decimal:
    score = d(metadata.get("quality_score"))
    if score <= ZERO:
        return ZERO
    return clamp(score / Decimal("100"))


def candidate_shadow_economics(
    *,
    target_pct: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, explicitly non-authoritative economic proxy.

    Gross edge is intentionally conservative:
    - infer signal edge only from observed momentum/VWAP displacement;
    - cap it at the live strategy's configured target;
    - discount it by quality confidence;
    - reserve explicit spread, slippage, regulatory, and uncertainty costs.

    This is a research measurement, not an execution gate.
    """
    quality = dict(metadata.get("market_quality") or {})
    confidence = _quality_confidence(metadata)

    momentum_bps = _bps_from_pct(metadata.get("momentum_pct"))
    vwap_edge_bps = _bps_from_pct(metadata.get("vwap_edge_pct"))
    target_bps = _bps_from_pct(target_pct)

    observed_signal_bps = max(momentum_bps, vwap_edge_bps)
    bounded_signal_bps = min(observed_signal_bps, target_bps) if target_bps > ZERO else observed_signal_bps

    # Keep half of the observed bounded signal even at low confidence so the
    # proxy remains comparable across all candidates while still discounting
    # weak observations.
    confidence_discount = HALF + HALF * confidence
    expected_gross_bps = bounded_signal_bps * confidence_discount

    spread_bps = _bps_from_pct(quality.get("spread_pct"))
    expected_slippage_bps = max(MIN_SLIPPAGE_BPS, spread_bps * HALF)
    uncertainty_reserve_bps = (
        BASE_UNCERTAINTY_BPS
        + (Decimal("1") - confidence) * MAX_EXTRA_UNCERTAINTY_BPS
    )

    estimate = estimate_costs(
        expected_gross_bps=expected_gross_bps,
        spread_bps=spread_bps,
        expected_slippage_bps=expected_slippage_bps,
        regulatory_bps=REGULATORY_RESERVE_BPS,
        uncertainty_reserve_bps=uncertainty_reserve_bps,
        sample_size=0,
        confidence=confidence,
    )
    would_admit, reason = economic_admission(
        estimate,
        minimum_net_bps=MINIMUM_NET_BPS,
        minimum_gross_to_cost_ratio=MINIMUM_GROSS_TO_COST_RATIO,
    )

    return {
        "schema_version": "shadow_opportunity_economics.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "research_only": True,
        "execution_authority": False,
        "changes_live_decision": False,
        "inputs": {
            "momentum_bps": str(momentum_bps),
            "vwap_edge_bps": str(vwap_edge_bps),
            "target_bps": str(target_bps),
            "observed_signal_bps": str(observed_signal_bps),
            "bounded_signal_bps": str(bounded_signal_bps),
            "quality_score": metadata.get("quality_score"),
            "spread_bps": str(spread_bps),
        },
        "estimate": estimate.as_dict(),
        "shadow_admission": {
            "would_admit": would_admit,
            "reason": reason,
            "minimum_net_bps": str(MINIMUM_NET_BPS),
            "minimum_gross_to_cost_ratio": str(MINIMUM_GROSS_TO_COST_RATIO),
        },
    }
