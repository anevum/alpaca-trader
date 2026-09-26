from decimal import Decimal

from app.edge_elimination import (
    development_elimination,
    holdout_elimination,
    research_outcome,
    validation_elimination,
)


def aggregate(
    *,
    events=150,
    expectancy="0.001",
    pf="1.4",
    positive_periods=6,
    worst="0.0002",
):
    return {
        "events": events,
        "expectancy_pct": expectancy,
        "profit_factor": pf,
        "positive_periods": positive_periods,
        "worst_period_expectancy_pct": worst,
        "session_expectancy_lower_95_pct": "0.0001",
    }


def scenario(name, controlled, **others):
    families = {"controlled_continuation": controlled}
    families.update(others)
    return {
        "scenario": name,
        "aggregate_by_family": families,
    }


def test_development_rejects_family_that_fails_stress_costs():
    good = aggregate()
    bad = aggregate(expectancy="-0.0001", pf="0.9", positive_periods=3)
    result = development_elimination(
        [
            scenario("base", good),
            scenario("moderate", good),
            scenario("stress", bad),
        ]
    )

    assert "controlled_continuation" in result["rejected"]
    assert "controlled_continuation" not in result["survivors"]


def test_validation_unlocks_holdout_only_for_frozen_survivor():
    good = aggregate(
        events=60,
        pf="1.2",
        positive_periods=2,
        worst="0.0001",
    )
    result = validation_elimination(
        [
            scenario("base", good),
            scenario("moderate", good),
            scenario("stress", good),
        ],
        frozen_families=["controlled_continuation"],
    )

    assert result["holdout_unlocked"] is True
    assert result["survivors"] == ["controlled_continuation"]


def test_holdout_never_allows_capital_scaling():
    good = aggregate(
        events=30,
        pf="1.2",
        positive_periods=1,
        worst="0.0001",
    )
    result = holdout_elimination(
        [
            scenario("base", good),
            scenario("moderate", good),
            scenario("stress", good),
        ],
        frozen_families=["controlled_continuation"],
    )

    assert result["historical_survivors"] == ["controlled_continuation"]
    assert result["forward_shadow_required"] is True
    assert result["capital_scaling_allowed"] is False


def test_no_development_survivor_forces_new_family_design():
    development = {"survivors": []}
    outcome = research_outcome(development)

    assert outcome["design_new_family"] is True
    assert outcome["forward_shadow_allowed"] is False
    assert outcome["capital_scaling_allowed"] is False
