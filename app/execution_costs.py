"""Deterministic shadow economics for RHEN V4.3 opportunity admission."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")
ONE = Decimal("1")


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = ONE) -> Decimal:
    return min(max(value, low), high)


@dataclass(frozen=True)
class CostEstimate:
    expected_gross_bps: Decimal
    round_trip_spread_bps: Decimal
    expected_slippage_bps: Decimal
    regulatory_bps: Decimal
    uncertainty_reserve_bps: Decimal
    expected_net_bps: Decimal
    gross_to_cost_ratio: Decimal | None
    sample_size: int
    confidence: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected_gross_bps": str(self.expected_gross_bps),
            "round_trip_spread_bps": str(self.round_trip_spread_bps),
            "expected_slippage_bps": str(self.expected_slippage_bps),
            "regulatory_bps": str(self.regulatory_bps),
            "uncertainty_reserve_bps": str(self.uncertainty_reserve_bps),
            "expected_net_bps": str(self.expected_net_bps),
            "gross_to_cost_ratio": (
                None if self.gross_to_cost_ratio is None else str(self.gross_to_cost_ratio)
            ),
            "sample_size": self.sample_size,
            "confidence": str(self.confidence),
        }


def shrunk_expectancy_bps(
    *,
    sample_mean_bps: Decimal,
    sample_size: int,
    prior_mean_bps: Decimal,
    shrinkage_k: int,
) -> Decimal:
    n = max(int(sample_size), 0)
    k = max(int(shrinkage_k), 1)
    weight = Decimal(n) / Decimal(n + k)
    return weight * d(sample_mean_bps) + (ONE - weight) * d(prior_mean_bps)


def estimate_costs(
    *,
    expected_gross_bps: Decimal,
    spread_bps: Decimal,
    expected_slippage_bps: Decimal,
    regulatory_bps: Decimal = ZERO,
    uncertainty_reserve_bps: Decimal = ZERO,
    sample_size: int = 0,
    confidence: Decimal = ZERO,
) -> CostEstimate:
    gross = max(d(expected_gross_bps), ZERO)
    spread = max(d(spread_bps), ZERO)
    # The current quote is a one-way spread observation. Use it twice for a
    # conservative round-trip burden until entry/exit empirical models mature.
    round_trip_spread = spread * Decimal("2")
    slippage = max(d(expected_slippage_bps), ZERO)
    regulatory = max(d(regulatory_bps), ZERO)
    uncertainty = max(d(uncertainty_reserve_bps), ZERO)
    total_cost = round_trip_spread + slippage + regulatory + uncertainty
    net = gross - total_cost
    ratio = gross / total_cost if total_cost > ZERO else None
    return CostEstimate(
        expected_gross_bps=gross,
        round_trip_spread_bps=round_trip_spread,
        expected_slippage_bps=slippage,
        regulatory_bps=regulatory,
        uncertainty_reserve_bps=uncertainty,
        expected_net_bps=net,
        gross_to_cost_ratio=ratio,
        sample_size=max(int(sample_size), 0),
        confidence=clamp(d(confidence)),
    )


def economic_admission(
    estimate: CostEstimate,
    *,
    minimum_net_bps: Decimal,
    minimum_gross_to_cost_ratio: Decimal,
) -> tuple[bool, str]:
    if estimate.expected_net_bps <= ZERO:
        return False, "expected_net_edge_nonpositive"
    if estimate.expected_net_bps < d(minimum_net_bps):
        return False, "expected_net_edge_below_hurdle"
    ratio = estimate.gross_to_cost_ratio
    if ratio is not None and ratio < d(minimum_gross_to_cost_ratio):
        return False, "gross_to_cost_ratio_below_hurdle"
    return True, "economic_gate_passed"


def implementation_shortfall_bps(
    *,
    side: str,
    fill_price: Decimal,
    decision_mid: Decimal,
) -> Decimal | None:
    fill = d(fill_price)
    mid = d(decision_mid)
    if fill <= ZERO or mid <= ZERO:
        return None
    direction = Decimal("1") if str(side).lower() == "buy" else Decimal("-1")
    return direction * (fill - mid) / mid * Decimal("10000")
