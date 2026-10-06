"""Post-event validation for RHEN V4.3 shadow capital allocation.

Compares the normalized selected-entry shadow allocation with mature forward
outcomes. The result is descriptive research evidence only. It does not alter
live sizing, capital, risk, orders, strategy configuration, or promotion state.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
BPS = Decimal("10000")
METHODOLOGY_VERSION = "rhen-shadow-allocation-validation-v1"
DEFAULT_HORIZONS = (10, 15)


def _d(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def _fraction(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_serialize(item) for item in value]
    return value


def _outcome(
    candidate: Mapping[str, Any],
    horizon_minutes: int,
) -> Mapping[str, Any] | None:
    raw = candidate.get("outcomes") or candidate.get("forward_outcomes") or []
    if isinstance(raw, Mapping):
        item = raw.get(str(horizon_minutes), raw.get(horizon_minutes))
        return item if isinstance(item, Mapping) else None
    if isinstance(raw, Sequence) and not isinstance(
        raw, (str, bytes, bytearray)
    ):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            try:
                horizon = int(item.get("horizon_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if horizon == horizon_minutes:
                return item
    return None


def _estimated_cost_bps(candidate: Mapping[str, Any]) -> Decimal | None:
    features = candidate.get("features")
    features = features if isinstance(features, Mapping) else {}
    economics = features.get("shadow_economics")
    if not isinstance(economics, Mapping):
        return None
    if economics.get("research_only") is not True:
        return None
    if economics.get("execution_authority") is True:
        return None
    gross = _d(economics.get("expected_gross_bps"))
    net = _d(economics.get("expected_net_bps"))
    if gross is None or net is None:
        return None
    return max(gross - net, ZERO)


def evaluate_shadow_allocation(
    candidates: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    scoped: list[dict[str, Any]] = []
    sessions: set[str] = set()
    methodologies: set[str] = set()

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        allocation = candidate.get("shadow_allocation")
        if not isinstance(allocation, Mapping):
            continue
        if allocation.get("research_only") is not True:
            continue
        if allocation.get("execution_authority") is True:
            continue
        if allocation.get("bounded_by_live_safe_notional") is not True:
            continue

        ratio_pct = _d(allocation.get("shadow_to_live_pct"))
        if ratio_pct is None or ratio_pct < ZERO or ratio_pct > HUNDRED:
            continue
        cost_bps = _estimated_cost_bps(candidate)
        if cost_bps is None:
            continue

        observed_at = str(candidate.get("observed_at") or "")
        if len(observed_at) >= 10:
            sessions.add(observed_at[:10])
        methodology = str(allocation.get("methodology_version") or "")
        if methodology:
            methodologies.add(methodology)

        scoped.append(
            {
                "candidate": candidate,
                "ratio": ratio_pct / HUNDRED,
                "ratio_pct": ratio_pct,
                "cost_bps": cost_bps,
                "would_allocate": allocation.get("would_allocate") is True,
            }
        )

    by_horizon: list[dict[str, Any]] = []
    for horizon in horizons:
        complete: list[dict[str, Any]] = []
        excluded: dict[str, int] = defaultdict(int)

        for row in scoped:
            outcome = _outcome(row["candidate"], int(horizon))
            if not isinstance(outcome, Mapping):
                excluded["missing_outcome"] += 1
                continue
            if str(outcome.get("status") or "").lower() != "complete":
                excluded["incomplete_outcome"] += 1
                continue
            forward = _d(outcome.get("forward_return"))
            if forward is None:
                excluded["missing_forward_return"] += 1
                continue

            realized_gross_bps = forward * BPS
            realized_net_bps = realized_gross_bps - row["cost_bps"]
            live_proxy = realized_net_bps
            shadow_proxy = realized_net_bps * row["ratio"]
            delta = shadow_proxy - live_proxy
            complete.append(
                {
                    **row,
                    "realized_net_bps": realized_net_bps,
                    "live_proxy_contribution_bps": live_proxy,
                    "shadow_proxy_contribution_bps": shadow_proxy,
                    "allocation_delta_bps": delta,
                }
            )

        ratios = [row["ratio_pct"] for row in complete]
        live = [row["live_proxy_contribution_bps"] for row in complete]
        shadow = [row["shadow_proxy_contribution_bps"] for row in complete]
        deltas = [row["allocation_delta_bps"] for row in complete]

        improved = [row for row in complete if row["allocation_delta_bps"] > ZERO]
        worsened = [row for row in complete if row["allocation_delta_bps"] < ZERO]
        unchanged = [row for row in complete if row["allocation_delta_bps"] == ZERO]
        avoided_loss = [
            row["allocation_delta_bps"]
            for row in complete
            if row["realized_net_bps"] < ZERO
            and row["allocation_delta_bps"] > ZERO
        ]
        sacrificed_gain = [
            -row["allocation_delta_bps"]
            for row in complete
            if row["realized_net_bps"] > ZERO
            and row["allocation_delta_bps"] < ZERO
        ]
        zero_allocations = sum(1 for row in complete if row["ratio"] == ZERO)

        if not scoped:
            state = "NO_SHADOW_EVIDENCE"
        elif len(complete) < 10:
            state = "COLLECTING"
        elif len(complete) < 30 or len(sessions) < 2:
            state = "OBSERVING"
        else:
            state = "DESCRIPTIVE_VALIDATION"

        by_horizon.append(
            {
                "horizon_minutes": int(horizon),
                "state": state,
                "selected_entry_count": len(scoped),
                "complete_outcome_count": len(complete),
                "outcome_coverage": _fraction(len(complete), len(scoped)),
                "independent_sessions": len(sessions),
                "mean_shadow_to_live_pct": _mean(ratios),
                "mean_live_proxy_net_bps": _mean(live),
                "mean_shadow_proxy_net_bps": _mean(shadow),
                "mean_allocation_delta_bps": _mean(deltas),
                "improved_entry_count": len(improved),
                "worsened_entry_count": len(worsened),
                "unchanged_entry_count": len(unchanged),
                "improved_entry_fraction": _fraction(
                    len(improved), len(complete)
                ),
                "zero_allocation_count": zero_allocations,
                "mean_avoided_loss_bps": _mean(avoided_loss),
                "mean_sacrificed_gain_bps": _mean(sacrificed_gain),
                "excluded": dict(excluded),
            }
        )

    return _serialize(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "source_allocation_methodology_versions": sorted(methodologies),
            "research_only": True,
            "execution_authority": False,
            "changes_live_decision": False,
            "capital_scaling_authorized": False,
            "automatic_application_authorized": False,
            "promotion_authorized": False,
            "selected_entry_count": len(scoped),
            "independent_sessions": len(sessions),
            "horizons": by_horizon,
            "interpretation": (
                "Counterfactual normalized contribution analysis. Positive "
                "allocation_delta_bps means the smaller/zero shadow allocation "
                "would have improved the outcome by reducing exposure to a "
                "losing entry; negative values measure gain that would have "
                "been sacrificed on a winning entry. This is not realized P&L "
                "and cannot change live capital allocation."
            ),
        }
    )
