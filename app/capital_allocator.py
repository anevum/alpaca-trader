"""Bounded capital ranking primitives for RHEN V4.3 shadow allocation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from .execution_costs import ZERO, clamp, d


CENT = Decimal("0.01")


@dataclass(frozen=True)
class AllocationCandidate:
    symbol: str
    expected_net_bps: Decimal
    expected_net_dollars: Decimal
    fill_probability: Decimal
    proposed_notional: Decimal
    expected_holding_minutes: Decimal
    confidence: Decimal

    @property
    def capital_velocity(self) -> Decimal:
        notional = max(d(self.proposed_notional), ZERO)
        minutes = max(d(self.expected_holding_minutes), ZERO)
        if notional <= ZERO or minutes <= ZERO:
            return ZERO
        return (
            clamp(d(self.fill_probability))
            * d(self.expected_net_dollars)
            / (notional * minutes)
        )


def opportunity_multiplier(
    *,
    expected_net_bps: Decimal,
    confidence: Decimal,
    full_size_edge_bps: Decimal,
    floor_multiplier: Decimal,
) -> Decimal:
    floor = clamp(d(floor_multiplier))
    full_edge = max(d(full_size_edge_bps), Decimal("0.000001"))
    edge_strength = clamp(max(d(expected_net_bps), ZERO) / full_edge)
    return floor + (ONE - floor) * edge_strength * clamp(d(confidence))


ONE = Decimal("1")


def bounded_notional(
    *,
    safe_risk_notional: Decimal,
    multiplier: Decimal,
    buying_power_cap: Decimal,
    position_cap: Decimal,
    remaining_gross_cap: Decimal,
    remaining_stop_risk_notional_cap: Decimal,
    liquidity_cap: Decimal,
    correlation_cluster_cap: Decimal,
) -> tuple[Decimal, str, dict[str, str]]:
    caps = {
        "risk_cap": max(d(safe_risk_notional) * clamp(d(multiplier)), ZERO),
        "buying_power_cap": max(d(buying_power_cap), ZERO),
        "position_cap": max(d(position_cap), ZERO),
        "remaining_gross_cap": max(d(remaining_gross_cap), ZERO),
        "portfolio_stop_risk_cap": max(d(remaining_stop_risk_notional_cap), ZERO),
        "liquidity_cap": max(d(liquidity_cap), ZERO),
        "correlation_cluster_cap": max(d(correlation_cluster_cap), ZERO),
    }
    binding = min(caps, key=lambda key: caps[key])
    final = caps[binding].quantize(CENT, rounding=ROUND_DOWN)
    return final, binding, {key: str(value) for key, value in caps.items()}


def rank_candidates(candidates: list[AllocationCandidate]) -> list[AllocationCandidate]:
    return sorted(
        candidates,
        key=lambda row: (
            row.capital_velocity,
            d(row.expected_net_bps),
            d(row.confidence),
            row.symbol,
        ),
        reverse=True,
    )
