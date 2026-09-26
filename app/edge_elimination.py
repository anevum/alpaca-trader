from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .edge_discovery import FAMILY_NAMES
from .replay import d


@dataclass(frozen=True)
class GateProfile:
    min_events: int
    min_profit_factor: Decimal
    min_positive_periods: int
    max_negative_worst_period: Decimal
    require_positive_expectancy: bool = True


DEVELOPMENT_PROFILE = GateProfile(
    min_events=120,
    min_profit_factor=Decimal("1.20"),
    min_positive_periods=4,
    max_negative_worst_period=Decimal("-0.0015"),
)

VALIDATION_PROFILE = GateProfile(
    min_events=40,
    min_profit_factor=Decimal("1.15"),
    min_positive_periods=2,
    max_negative_worst_period=Decimal("-0.0005"),
)

HOLDOUT_PROFILE = GateProfile(
    min_events=20,
    min_profit_factor=Decimal("1.10"),
    min_positive_periods=1,
    max_negative_worst_period=Decimal("0"),
)


def _profile_payload(profile: GateProfile) -> dict[str, Any]:
    return {
        "minimum_events": profile.min_events,
        "minimum_profit_factor": str(profile.min_profit_factor),
        "minimum_positive_periods": profile.min_positive_periods,
        "minimum_worst_period_expectancy_pct": str(
            profile.max_negative_worst_period
        ),
        "positive_expectancy_required": profile.require_positive_expectancy,
    }


def _scenario_check(
    aggregate: dict[str, Any],
    profile: GateProfile,
) -> dict[str, Any]:
    events = int(aggregate.get("events") or 0)
    expectancy = d(aggregate.get("expectancy_pct"))
    pf_raw = aggregate.get("profit_factor")
    profit_factor = (
        d(pf_raw) if pf_raw is not None else Decimal("0")
    )
    positive_periods = int(aggregate.get("positive_periods") or 0)
    worst_period = d(aggregate.get("worst_period_expectancy_pct"))

    checks = {
        "sample": events >= profile.min_events,
        "expectancy": (
            expectancy > 0
            if profile.require_positive_expectancy
            else True
        ),
        "profit_factor": profit_factor >= profile.min_profit_factor,
        "period_consistency": (
            positive_periods >= profile.min_positive_periods
        ),
        "worst_period": (
            worst_period >= profile.max_negative_worst_period
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "actual": {
            "events": events,
            "expectancy_pct": str(expectancy),
            "profit_factor": str(profit_factor),
            "positive_periods": positive_periods,
            "worst_period_expectancy_pct": str(worst_period),
            "session_expectancy_lower_95_pct": aggregate.get(
                "session_expectancy_lower_95_pct"
            ),
        },
    }


def evaluate_stage(
    scenario_results: list[dict[str, Any]],
    *,
    profile: GateProfile,
    eligible_families: list[str] | None = None,
) -> dict[str, Any]:
    candidates = list(eligible_families or FAMILY_NAMES)
    family_results: dict[str, Any] = {}

    for family in candidates:
        scenario_checks = []
        for scenario in scenario_results:
            aggregate = (
                scenario.get("aggregate_by_family") or {}
            ).get(family, {})
            scenario_checks.append(
                {
                    "scenario": str(scenario.get("scenario") or ""),
                    **_scenario_check(aggregate, profile),
                }
            )

        passed = bool(scenario_checks) and all(
            item["passed"] for item in scenario_checks
        )
        failed_scenarios = [
            item["scenario"]
            for item in scenario_checks
            if not item["passed"]
        ]
        family_results[family] = {
            "passed": passed,
            "failed_scenarios": failed_scenarios,
            "scenario_checks": scenario_checks,
        }

    survivors = [
        family
        for family, result in family_results.items()
        if result["passed"]
    ]
    rejected = [
        family
        for family, result in family_results.items()
        if not result["passed"]
    ]
    return {
        "criteria": _profile_payload(profile),
        "families": family_results,
        "survivors": survivors,
        "rejected": rejected,
    }


def development_elimination(
    scenario_results: list[dict[str, Any]],
) -> dict[str, Any]:
    result = evaluate_stage(
        scenario_results,
        profile=DEVELOPMENT_PROFILE,
    )
    result.update(
        {
            "stage": "development",
            "next_step": (
                "freeze survivors and evaluate validation windows"
                if result["survivors"]
                else "all five families rejected; design new structural families"
            ),
        }
    )
    return result


def validation_elimination(
    scenario_results: list[dict[str, Any]],
    *,
    frozen_families: list[str],
) -> dict[str, Any]:
    result = evaluate_stage(
        scenario_results,
        profile=VALIDATION_PROFILE,
        eligible_families=frozen_families,
    )
    result.update(
        {
            "stage": "validation",
            "frozen_families": list(frozen_families),
            "holdout_unlocked": bool(result["survivors"]),
            "next_step": (
                "open holdout only for validation survivors"
                if result["survivors"]
                else "validation rejected every frozen family; return to new structural hypotheses"
            ),
        }
    )
    return result


def holdout_elimination(
    scenario_results: list[dict[str, Any]],
    *,
    frozen_families: list[str],
) -> dict[str, Any]:
    result = evaluate_stage(
        scenario_results,
        profile=HOLDOUT_PROFILE,
        eligible_families=frozen_families,
    )
    result.update(
        {
            "stage": "holdout",
            "frozen_families": list(frozen_families),
            "historical_survivors": list(result["survivors"]),
            "forward_shadow_required": bool(result["survivors"]),
            "capital_scaling_allowed": False,
            "next_step": (
                "forward shadow validation"
                if result["survivors"]
                else "holdout rejected every family; design a new strategy family"
            ),
        }
    )
    return result


def research_outcome(
    development: dict[str, Any],
    validation: dict[str, Any] | None = None,
    holdout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not development.get("survivors"):
        return {
            "outcome": "all_families_rejected_in_development",
            "survivor": None,
            "design_new_family": True,
            "forward_shadow_allowed": False,
            "capital_scaling_allowed": False,
        }

    if validation is None:
        return {
            "outcome": "development_survivor_requires_validation",
            "survivor": None,
            "design_new_family": False,
            "forward_shadow_allowed": False,
            "capital_scaling_allowed": False,
        }

    if not validation.get("survivors"):
        return {
            "outcome": "all_families_rejected_in_validation",
            "survivor": None,
            "design_new_family": True,
            "forward_shadow_allowed": False,
            "capital_scaling_allowed": False,
        }

    if holdout is None:
        return {
            "outcome": "validation_survivor_requires_holdout",
            "survivor": None,
            "design_new_family": False,
            "forward_shadow_allowed": False,
            "capital_scaling_allowed": False,
        }

    survivors = list(holdout.get("historical_survivors") or [])
    if not survivors:
        return {
            "outcome": "all_families_rejected_in_holdout",
            "survivor": None,
            "design_new_family": True,
            "forward_shadow_allowed": False,
            "capital_scaling_allowed": False,
        }

    return {
        "outcome": "historical_survivor_requires_forward_shadow",
        "survivor": survivors[0] if len(survivors) == 1 else survivors,
        "design_new_family": False,
        "forward_shadow_allowed": True,
        "capital_scaling_allowed": False,
    }
