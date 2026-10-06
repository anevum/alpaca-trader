"""Research-only counterfactual capital allocation for RHEN V4.3.

The live sizing/risk engine remains authoritative. This module receives an
already-approved live notional and can only recommend an equal-or-smaller
counterfactual amount, or zero.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from .capital_allocator import AllocationCandidate, opportunity_multiplier
from .execution_costs import ZERO, d


CENT = Decimal("0.01")
BPS = Decimal("10000")
FULL_SIZE_EDGE_BPS = Decimal("15")
FLOOR_MULTIPLIER = Decimal("0.25")
METHODOLOGY_VERSION = "rhen-shadow-allocation-v1"


def selected_entry_shadow_allocation(
    *,
    symbol: str,
    live_safe_notional: Any,
    min_order_notional: Any,
    expected_holding_minutes: Any,
    shadow_economics: dict[str, Any] | None,
) -> dict[str, Any]:
    live_notional = max(d(live_safe_notional), ZERO).quantize(
        CENT, rounding=ROUND_DOWN
    )
    minimum = max(d(min_order_notional), ZERO)
    hold_minutes = max(d(expected_holding_minutes), Decimal("1"))
    shadow = shadow_economics if isinstance(shadow_economics, dict) else {}
    estimate = shadow.get("estimate")
    estimate = estimate if isinstance(estimate, dict) else {}
    admission = shadow.get("shadow_admission")
    admission = admission if isinstance(admission, dict) else {}

    expected_net_bps = d(estimate.get("expected_net_bps"))
    confidence = d(estimate.get("confidence"))
    economics_admitted = admission.get("would_admit") is True

    multiplier = ZERO
    proposed = ZERO
    reason = str(admission.get("reason") or "shadow_economics_unavailable")

    if live_notional > ZERO and economics_admitted and expected_net_bps > ZERO:
        multiplier = opportunity_multiplier(
            expected_net_bps=expected_net_bps,
            confidence=confidence,
            full_size_edge_bps=FULL_SIZE_EDGE_BPS,
            floor_multiplier=FLOOR_MULTIPLIER,
        )
        proposed = (live_notional * multiplier).quantize(
            CENT, rounding=ROUND_DOWN
        )
        # Counterfactual allocation must never expand beyond the amount already
        # approved by RHEN's authoritative live sizing/risk path.
        proposed = min(proposed, live_notional)
        if proposed < minimum:
            proposed = ZERO
            reason = "shadow_notional_below_live_minimum"
        else:
            reason = "shadow_allocation_candidate"

    expected_net_dollars = proposed * expected_net_bps / BPS
    row = AllocationCandidate(
        symbol=str(symbol or "").upper(),
        expected_net_bps=expected_net_bps,
        expected_net_dollars=expected_net_dollars,
        fill_probability=confidence,
        proposed_notional=proposed,
        expected_holding_minutes=hold_minutes,
        confidence=confidence,
    )

    return {
        "schema_version": "shadow_capital_allocation.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "research_only": True,
        "execution_authority": False,
        "changes_live_decision": False,
        "bounded_by_live_safe_notional": True,
        "symbol": row.symbol,
        "live_safe_notional": str(live_notional),
        "shadow_notional": str(proposed),
        "shadow_multiplier": str(multiplier),
        "expected_net_bps": str(expected_net_bps),
        "confidence": str(confidence),
        "expected_holding_minutes": str(hold_minutes),
        "expected_net_dollars": str(expected_net_dollars),
        "capital_velocity_per_minute": str(row.capital_velocity),
        "would_allocate": proposed > ZERO,
        "reason": reason,
    }
