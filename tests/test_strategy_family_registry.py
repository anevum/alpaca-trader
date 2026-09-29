import pytest

from app.research_agent.strategy_family_registry import (
    add_research_family,
    build_strategy_family_registry,
)


def test_registry_declares_current_live_family_without_router_authority():
    registry = build_strategy_family_registry()
    assert registry["current_production_family"] == "rhen-long-momentum-v1"
    current = registry["families"][0]
    assert current["strategy_name"] == "rolling_momentum_vwap"
    assert current["status"] == "PRODUCTION_CHAMPION"
    assert current["research_execution_authority"] is False
    assert registry["routing_execution_authority"] is False


def test_research_family_can_be_added_without_execution_authority():
    registry = add_research_family(
        build_strategy_family_registry(),
        {
            "family_key": "mean-reversion-research-v1",
            "strategy_name": "mean_reversion_research",
            "direction": "LONG",
            "asset_class": "US_EQUITY",
            "status": "SPEC_ONLY",
            "hypothesis": "independent family for non-trending regimes",
        },
    )
    keys = [row["family_key"] for row in registry["families"]]
    assert "mean-reversion-research-v1" in keys
    added = next(
        row for row in registry["families"]
        if row["family_key"] == "mean-reversion-research-v1"
    )
    assert added["research_execution_authority"] is False
    assert added["automatic_promotion_authorized"] is False


def test_registry_rejects_family_that_attempts_execution_authority():
    with pytest.raises(ValueError):
        build_strategy_family_registry(
            [
                {
                    "family_key": "unsafe",
                    "strategy_name": "unsafe",
                    "direction": "LONG",
                    "status": "ACTIVE_SHADOW",
                    "research_execution_authority": True,
                }
            ]
        )


def test_duplicate_family_keys_are_rejected():
    with pytest.raises(ValueError):
        build_strategy_family_registry(
            [
                {
                    "family_key": "same",
                    "strategy_name": "one",
                    "direction": "LONG",
                    "status": "SPEC_ONLY",
                },
                {
                    "family_key": "same",
                    "strategy_name": "two",
                    "direction": "SHORT",
                    "status": "SPEC_ONLY",
                },
            ]
        )
