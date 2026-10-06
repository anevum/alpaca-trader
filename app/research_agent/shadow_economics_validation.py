"""Post-event validation for RHEN V4.3 shadow opportunity economics.

This module compares the research-only economic proxy captured at decision time
with mature forward outcomes. It is descriptive evidence only and has no
execution, sizing, promotion, or configuration authority.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any


ZERO = Decimal("0")
BPS = Decimal("10000")
METHODOLOGY_VERSION = "rhen-shadow-economics-validation-v1"
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


def _shadow(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    features = candidate.get("features")
    features = features if isinstance(features, Mapping) else {}
    shadow = features.get("shadow_economics")
    if not isinstance(shadow, Mapping):
        return None
    if shadow.get("research_only") is not True:
        return None
    if shadow.get("execution_authority") is True:
        return None
    return shadow


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


def _fraction(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def evaluate_shadow_economics(
    candidates: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    scoped = []
    methodology_versions: set[str] = set()
    sessions: set[str] = set()

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        shadow = _shadow(candidate)
        if shadow is None:
            continue
        expected_gross = _d(shadow.get("expected_gross_bps"))
        expected_net = _d(shadow.get("expected_net_bps"))
        if expected_gross is None or expected_net is None:
            continue
        methodology = str(shadow.get("methodology_version") or "")
        if methodology:
            methodology_versions.add(methodology)
        observed_at = str(candidate.get("observed_at") or "")
        if len(observed_at) >= 10:
            sessions.add(observed_at[:10])
        scoped.append(
            {
                "candidate": candidate,
                "shadow": shadow,
                "expected_gross_bps": expected_gross,
                "expected_net_bps": expected_net,
                "estimated_cost_bps": max(expected_gross - expected_net, ZERO),
                "would_admit": shadow.get("would_admit") is True,
            }
        )

    by_horizon: list[dict[str, Any]] = []
    for horizon in horizons:
        complete = []
        excluded: dict[str, int] = defaultdict(int)
        for row in scoped:
            outcome = _outcome(row["candidate"], int(horizon))
            if not isinstance(outcome, Mapping):
                excluded["missing_outcome"] += 1
                continue
            if str(outcome.get("status") or "").lower() != "complete":
                excluded["incomplete_outcome"] += 1
                continue
            forward_return = _d(outcome.get("forward_return"))
            if forward_return is None:
                excluded["missing_forward_return"] += 1
                continue

            realized_gross_bps = forward_return * BPS
            realized_net_bps = (
                realized_gross_bps - row["estimated_cost_bps"]
            )
            calibration_error = (
                realized_net_bps - row["expected_net_bps"]
            )
            complete.append(
                {
                    **row,
                    "realized_gross_bps": realized_gross_bps,
                    "realized_net_bps": realized_net_bps,
                    "calibration_error_bps": calibration_error,
                }
            )

        expected = [row["expected_net_bps"] for row in complete]
        realized = [row["realized_net_bps"] for row in complete]
        errors = [row["calibration_error_bps"] for row in complete]
        absolute_errors = [abs(value) for value in errors]
        admitted = [row for row in complete if row["would_admit"]]
        rejected = [row for row in complete if not row["would_admit"]]

        admitted_realized = [row["realized_net_bps"] for row in admitted]
        rejected_realized = [row["realized_net_bps"] for row in rejected]
        admitted_positive = sum(
            1 for value in admitted_realized if value > ZERO
        )
        rejected_positive = sum(
            1 for value in rejected_realized if value > ZERO
        )

        admitted_mean = _mean(admitted_realized)
        rejected_mean = _mean(rejected_realized)
        selection_lift = (
            admitted_mean - rejected_mean
            if admitted_mean is not None and rejected_mean is not None
            else None
        )
        coverage = _fraction(len(complete), len(scoped))

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
                "shadow_candidate_count": len(scoped),
                "complete_outcome_count": len(complete),
                "outcome_coverage": coverage,
                "independent_sessions": len(sessions),
                "mean_expected_net_bps": _mean(expected),
                "mean_realized_net_bps": _mean(realized),
                "mean_calibration_error_bps": _mean(errors),
                "mean_absolute_calibration_error_bps": _mean(absolute_errors),
                "admitted_count": len(admitted),
                "rejected_count": len(rejected),
                "admitted_mean_realized_net_bps": admitted_mean,
                "rejected_mean_realized_net_bps": rejected_mean,
                "admission_selection_lift_bps": selection_lift,
                "admitted_positive_realized_fraction": _fraction(
                    admitted_positive, len(admitted)
                ),
                "rejected_positive_realized_fraction": _fraction(
                    rejected_positive, len(rejected)
                ),
                "excluded": dict(excluded),
            }
        )

    return _serialize(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "source_shadow_methodology_versions": sorted(
                methodology_versions
            ),
            "research_only": True,
            "execution_authority": False,
            "changes_live_decision": False,
            "automatic_application_authorized": False,
            "promotion_authorized": False,
            "candidate_count": len(scoped),
            "independent_sessions": len(sessions),
            "horizons": by_horizon,
            "interpretation": (
                "Post-event calibration of the decision-time shadow economics "
                "proxy. Forward returns are converted to bps and reduced by the "
                "proxy's own estimated cost burden. Results are descriptive and "
                "cannot change production behavior."
            ),
        }
    )
