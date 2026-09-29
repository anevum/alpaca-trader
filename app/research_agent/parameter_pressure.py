from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from .adaptation_proposal import DEFAULT_PARAMETER_POLICY

METHODOLOGY_VERSION = "asc-parameter-pressure-v1"


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _selected_from_report(
    report: Mapping[str, Any],
    parameter: str,
) -> Mapping[str, Any] | None:
    lab = report.get("counterfactual_lab")
    if not isinstance(lab, Mapping):
        return None
    rolling = lab.get("rolling_searches")
    if not isinstance(rolling, Mapping):
        return None
    result = rolling.get(parameter)
    if not isinstance(result, Mapping):
        return None
    selected = result.get("selected")
    return selected if isinstance(selected, Mapping) else None


def compute_parameter_pressure(
    reports: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Mapping[str, Any]] | None = None,
    window: int = 10,
) -> dict[str, Any]:
    """Measure whether validated adaptive preferences repeatedly hit bounds."""

    rules = dict(policy or DEFAULT_PARAMETER_POLICY)
    scoped = [row for row in reports if isinstance(row, Mapping)][-max(window, 1):]
    output = []

    for parameter, rule in sorted(rules.items()):
        minimum = _d(rule.get("minimum"))
        maximum = _d(rule.get("maximum"))
        step = _d(rule.get("daily_step"))
        if minimum is None or maximum is None or step is None:
            continue

        observations = 0
        lower_hits = 0
        upper_hits = 0
        values: list[str] = []

        for report in scoped:
            selected = _selected_from_report(report, parameter)
            if not selected or selected.get("validity_passed") is not True:
                continue
            value = _d(selected.get("requested_value"))
            if value is None:
                continue
            observations += 1
            values.append(str(value))
            if value <= minimum + step:
                lower_hits += 1
            if value >= maximum - step:
                upper_hits += 1

        boundary_hits = lower_hits + upper_hits
        fraction = (
            Decimal(boundary_hits) / Decimal(observations)
            if observations else Decimal("0")
        )

        if observations < 5:
            status = "COLLECTING"
        elif fraction >= Decimal("0.60"):
            status = "DEGRADED"
        elif fraction >= Decimal("0.30"):
            status = "WATCH"
        else:
            status = "HEALTHY"

        direction = None
        if lower_hits > upper_hits:
            direction = "LOWER"
        elif upper_hits > lower_hits:
            direction = "UPPER"
        elif boundary_hits:
            direction = "MIXED"

        output.append(
            {
                "parameter": parameter,
                "status": status,
                "observations": observations,
                "lower_boundary_hits": lower_hits,
                "upper_boundary_hits": upper_hits,
                "boundary_fraction": str(fraction),
                "dominant_boundary": direction,
                "validated_requested_values": values,
            }
        )

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "window_reports": len(scoped),
        "parameters": output,
        "read_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
    }
