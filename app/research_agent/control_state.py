from __future__ import annotations

from collections.abc import Mapping
from typing import Any

NORMAL = "NORMAL"
ADAPT = "ADAPT"
RESEARCH = "RESEARCH"
DEFENSIVE = "DEFENSIVE"
VALID_STATES = {NORMAL, ADAPT, RESEARCH, DEFENSIVE}

METHODOLOGY_VERSION = "asc-control-state-v1"


def _state(value: Any) -> str:
    normalized = str(value or NORMAL).strip().upper()
    if normalized not in VALID_STATES:
        raise ValueError(f"invalid ASC state: {value}")
    return normalized


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def transition_control_state(
    *,
    previous_state: str,
    health_snapshot: Mapping[str, Any],
    consecutive_clear_observations: int = 0,
    consecutive_adapt_observations: int = 0,
    consecutive_research_observations: int = 0,
) -> dict[str, Any]:
    """Apply deterministic ASC state transition rules.

    v1 is supervisory only. It never changes strategy, risk, sizing, broker
    state, or production configuration.
    """

    previous = _state(previous_state)
    requested = _state(health_snapshot.get("control_state"))
    clear = _int(consecutive_clear_observations)
    adapt_count = _int(consecutive_adapt_observations)
    research_count = _int(consecutive_research_observations)

    reason_codes = list(health_snapshot.get("control_reason_codes") or [])
    transition_reasons: list[str] = []

    # Safety escalation is immediate.
    if requested == DEFENSIVE:
        target = DEFENSIVE
        transition_reasons.append("IMMEDIATE_DEFENSIVE_ESCALATION")

    # DEFENSIVE recovery requires repeated clean observations. It may recover
    # only to RESEARCH first so evidence can be reviewed before NORMAL.
    elif previous == DEFENSIVE:
        if requested in {NORMAL, ADAPT} and clear >= 2:
            target = RESEARCH
            transition_reasons.append("DEFENSIVE_RECOVERY_TO_RESEARCH")
        else:
            target = DEFENSIVE
            transition_reasons.append("DEFENSIVE_HOLD_UNTIL_RECOVERY_CONFIRMED")

    # Research escalation is immediate when health has a degraded research
    # trigger. Returning from RESEARCH is intentionally slower.
    elif requested == RESEARCH:
        target = RESEARCH
        transition_reasons.append("RESEARCH_ESCALATION")
    elif previous == RESEARCH:
        if requested == NORMAL and clear >= 3:
            target = NORMAL
            transition_reasons.append("RESEARCH_RECOVERY_CONFIRMED")
        elif requested == ADAPT and adapt_count >= 2:
            target = ADAPT
            transition_reasons.append("RESEARCH_TO_ADAPT_AFTER_STABLE_WATCH")
        else:
            target = RESEARCH
            transition_reasons.append("RESEARCH_HYSTERESIS_HOLD")

    # ADAPT requires two consecutive watch observations before entering from
    # NORMAL. This prevents one noisy health snapshot from changing posture.
    elif requested == ADAPT:
        if previous == ADAPT or adapt_count >= 2:
            target = ADAPT
            transition_reasons.append("ADAPT_WATCH_CONFIRMED")
        else:
            target = NORMAL
            transition_reasons.append("ADAPT_PENDING_CONFIRMATION")

    # NORMAL recovery from ADAPT requires two clear observations.
    elif previous == ADAPT and requested == NORMAL:
        if clear >= 2:
            target = NORMAL
            transition_reasons.append("ADAPT_RECOVERY_CONFIRMED")
        else:
            target = ADAPT
            transition_reasons.append("ADAPT_HYSTERESIS_HOLD")

    else:
        target = NORMAL
        transition_reasons.append("NORMAL_NO_ESCALATION")

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "previous_state": previous,
        "requested_state": requested,
        "state": target,
        "changed": target != previous,
        "health_reason_codes": reason_codes,
        "transition_reason_codes": transition_reasons,
        "counters": {
            "consecutive_clear_observations": clear,
            "consecutive_adapt_observations": adapt_count,
            "consecutive_research_observations": research_count,
        },
        "read_only": True,
        "execution_authority": False,
        "risk_or_sizing_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
