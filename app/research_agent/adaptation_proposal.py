from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any

METHODOLOGY_VERSION = "asc-adaptation-proposal-v1"

ADAPT = "ADAPT"
RESEARCH = "RESEARCH"

DEFAULT_PARAMETER_POLICY = {
    "min_momentum_pct": {
        "minimum": Decimal("0.0005"),
        "maximum": Decimal("0.0040"),
        "daily_step": Decimal("0.0002"),
        "weekly_step": Decimal("0.0005"),
    },
    "min_vwap_edge_pct": {
        "minimum": Decimal("0.0000"),
        "maximum": Decimal("0.0040"),
        "daily_step": Decimal("0.0002"),
        "weekly_step": Decimal("0.0005"),
    },
    "min_confirmations": {
        "minimum": Decimal("1"),
        "maximum": Decimal("3"),
        "daily_step": Decimal("1"),
        "weekly_step": Decimal("1"),
    },
    "max_vwap_extension_pct": {
        "minimum": Decimal("0.003"),
        "maximum": Decimal("0.015"),
        "daily_step": Decimal("0.001"),
        "weekly_step": Decimal("0.002"),
    },
}


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid numeric value: {value}") from None


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json(item) for item in value]
    return value


def _proposal_id(payload: Mapping[str, Any]) -> str:
    material = json.dumps(_json(payload), sort_keys=True, separators=(",", ":"))
    return "AP-" + sha256(material.encode()).hexdigest()[:16].upper()


def _clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return min(max(value, minimum), maximum)


def propose_parameter_change(
    *,
    parameter: str,
    current_value: Any,
    requested_value: Any,
    control_state: str,
    evidence: Mapping[str, Any],
    confidence: Any,
    cadence: str = "daily",
    policy: Mapping[str, Mapping[str, Any]] | None = None,
    source_strategy_version: str | None = None,
) -> dict[str, Any]:
    """Create a bounded, non-executing parameter-change proposal."""

    state = str(control_state or "").strip().upper()
    if state not in {ADAPT, RESEARCH}:
        raise ValueError("parameter proposals require ADAPT or RESEARCH control state")

    rules = dict(policy or DEFAULT_PARAMETER_POLICY)
    if parameter not in rules:
        raise ValueError(f"parameter is not in the adaptive policy: {parameter}")

    rule = rules[parameter]
    minimum = _d(rule["minimum"])
    maximum = _d(rule["maximum"])
    current = _d(current_value)
    requested = _d(requested_value)
    confidence_value = min(max(_d(confidence), Decimal("0")), Decimal("1"))

    if current < minimum or current > maximum:
        raise ValueError("current parameter value is outside declared adaptive bounds")

    cadence_name = str(cadence or "daily").strip().lower()
    if cadence_name not in {"daily", "weekly"}:
        raise ValueError("cadence must be daily or weekly")
    step_key = "daily_step" if cadence_name == "daily" else "weekly_step"
    step = abs(_d(rule[step_key]))

    bounded_requested = _clamp(requested, minimum, maximum)
    delta = bounded_requested - current
    applied_delta = _clamp(delta, -step, step)
    proposed = _clamp(current + applied_delta, minimum, maximum)

    clipped_by_range = bounded_requested != requested
    clipped_by_budget = applied_delta != delta

    payload = {
        "methodology_version": METHODOLOGY_VERSION,
        "source_strategy_version": source_strategy_version,
        "target_scope": "parameter",
        "parameter": parameter,
        "control_state": state,
        "cadence": cadence_name,
        "old_value": current,
        "requested_value": requested,
        "proposed_value": proposed,
        "delta": proposed - current,
        "adaptive_bounds": {
            "minimum": minimum,
            "maximum": maximum,
            "step_budget": step,
        },
        "clipped_by_range": clipped_by_range,
        "clipped_by_change_budget": clipped_by_budget,
        "evidence": dict(evidence),
        "confidence": confidence_value,
        "rollback_value": current,
        "authorization_required": True,
        "automatic_application_authorized": False,
        "execution_authority": False,
        "live_configuration_changed": False,
    }
    payload["proposal_id"] = _proposal_id(payload)
    return _json(payload)


def proposal_from_counterfactual(
    *,
    parameter: str,
    current_value: Any,
    alternatives: Sequence[Mapping[str, Any]],
    control_state: str,
    source_strategy_version: str | None = None,
    cadence: str = "daily",
) -> dict[str, Any] | None:
    """Select the strongest precomputed alternative, then apply proposal bounds.

    Counterfactual rows must already be generated and validated elsewhere.
    This function does not infer causal benefit from historical data.
    """

    eligible: list[Mapping[str, Any]] = []
    for row in alternatives:
        if not isinstance(row, Mapping):
            continue
        if row.get("validity_passed") is not True:
            continue
        if row.get("selection_bias_status") not in {"PASS", "CONTROLLED"}:
            continue
        if row.get("dependence_status") not in {"PASS", "CONTROLLED"}:
            continue
        if row.get("requested_value") in (None, ""):
            continue
        eligible.append(row)

    if not eligible:
        return None

    eligible.sort(
        key=lambda row: (
            _d(row.get("evidence_score", 0)),
            _d(row.get("expected_improvement", 0)),
        ),
        reverse=True,
    )
    best = eligible[0]
    confidence = best.get("confidence", 0)

    return propose_parameter_change(
        parameter=parameter,
        current_value=current_value,
        requested_value=best["requested_value"],
        control_state=control_state,
        cadence=cadence,
        confidence=confidence,
        source_strategy_version=source_strategy_version,
        evidence={
            "counterfactual_id": best.get("counterfactual_id"),
            "evidence_score": best.get("evidence_score"),
            "expected_improvement": best.get("expected_improvement"),
            "selection_bias_status": best.get("selection_bias_status"),
            "dependence_status": best.get("dependence_status"),
            "validity_passed": True,
        },
    )
