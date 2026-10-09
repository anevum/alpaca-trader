from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

METHODOLOGY_VERSION = "asc-strategy-router-v1"
NO_TRADE = "NO_TRADE"


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def rank_strategy_families(
    *,
    regime_state: Mapping[str, Any],
    families: Sequence[Mapping[str, Any]],
    minimum_confidence: Any = "0.70",
) -> dict[str, Any]:
    """Research-only regime-conditioned strategy-family ranking."""

    regime = str(regime_state.get("regime") or "UNKNOWN")
    confidence_floor = _d(minimum_confidence) or Decimal("0.70")
    ranked = []

    for family in families:
        if not isinstance(family, Mapping):
            continue
        family_key = str(family.get("family_key") or "")
        if not family_key:
            continue
        evidence = family.get("regime_evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        row = evidence.get(regime)
        row = row if isinstance(row, Mapping) else {}

        expected_utility = _d(row.get("expected_utility"))
        confidence = _d(row.get("confidence"))
        minimums_met = row.get("minimums_met") is True
        # Registry normalizes `status`, while older research evidence uses
        # `validation_status`. Either can describe research eligibility.
        validation_status = family.get("validation_status") or family.get("status")
        eligible = bool(
            expected_utility is not None
            and confidence is not None
            and confidence >= confidence_floor
            and minimums_met
            and validation_status in {
                "FROZEN_VALIDATION",
                "HOLDOUT_COMPLETE",
                "CHALLENGER_CANDIDATE",
            }
        )
        ranked.append(
            {
                "family_key": family_key,
                "validation_status": validation_status,
                "expected_utility": (
                    str(expected_utility) if expected_utility is not None else None
                ),
                "confidence": str(confidence) if confidence is not None else None,
                "minimums_met": minimums_met,
                "eligible": eligible,
            }
        )

    ranked.sort(
        key=lambda row: (
            row["eligible"],
            _d(row.get("expected_utility")) or Decimal("-999"),
            _d(row.get("confidence")) or Decimal("0"),
        ),
        reverse=True,
    )

    eligible = [row for row in ranked if row["eligible"]]
    selected = eligible[0] if eligible else None
    if selected is not None:
        utility = _d(selected.get("expected_utility")) or Decimal("0")
        if utility <= 0:
            selected = None

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "regime": regime,
        "ranked_families": ranked,
        "selected_research_family": (
            selected.get("family_key") if selected else NO_TRADE
        ),
        "no_trade_selected": selected is None,
        "selection_reason": (
            "VALIDATED_POSITIVE_UTILITY_FAMILY"
            if selected else "NO_VALIDATED_POSITIVE_UTILITY_FAMILY"
        ),
        "research_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
