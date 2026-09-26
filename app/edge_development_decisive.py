from __future__ import annotations

from decimal import Decimal
from typing import Any

from .edge_discovery import FAMILY_NAMES
from .edge_elimination import DEVELOPMENT_PROFILE
from .replay import d


def irreversible_development_rejections(
    periods: list[dict[str, Any]],
    *,
    total_periods: int,
) -> dict[str, Any]:
    """Return only failures that future development periods cannot repair."""
    periods_seen = len(periods)
    remaining = max(total_periods - periods_seen, 0)
    families: dict[str, Any] = {}

    for family in FAMILY_NAMES:
        expectations: list[Decimal] = []
        events = 0
        for period in periods:
            summary = (period.get("summary_by_family") or {}).get(family, {})
            expectations.append(d(summary.get("expectancy_pct")))
            events += int(summary.get("events") or 0)

        positive_periods = sum(value > 0 for value in expectations)
        max_possible_positive_periods = positive_periods + remaining
        worst = min(expectations) if expectations else Decimal("0")

        reason: str | None = None
        if expectations and worst < DEVELOPMENT_PROFILE.max_negative_worst_period:
            reason = "worst_period_below_floor"
        elif (
            max_possible_positive_periods
            < DEVELOPMENT_PROFILE.min_positive_periods
        ):
            reason = "insufficient_possible_positive_periods"

        families[family] = {
            "irreversibly_rejected": reason is not None,
            "reason": reason,
            "events_seen": events,
            "positive_periods_seen": positive_periods,
            "periods_seen": periods_seen,
            "periods_remaining": remaining,
            "max_possible_positive_periods": max_possible_positive_periods,
            "worst_period_expectancy_pct": str(worst),
            "worst_period_floor_pct": str(
                DEVELOPMENT_PROFILE.max_negative_worst_period
            ),
            "minimum_positive_periods": (
                DEVELOPMENT_PROFILE.min_positive_periods
            ),
        }

    rejected = [
        family
        for family, item in families.items()
        if item["irreversibly_rejected"]
    ]
    survivors_possible = [
        family
        for family in FAMILY_NAMES
        if family not in rejected
    ]
    return {
        "families": families,
        "irreversibly_rejected": rejected,
        "survival_still_possible": survivors_possible,
        "all_families_irreversibly_rejected": not survivors_possible,
        "periods_seen": periods_seen,
        "total_periods": total_periods,
    }
