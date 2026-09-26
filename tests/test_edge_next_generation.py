from app.edge_next_generation import next_generation_plan


def test_next_generation_is_triggered_only_after_rejection():
    plan = next_generation_plan(
        {
            "outcome": "all_families_rejected_in_validation",
            "design_new_family": True,
        }
    )

    assert plan["triggered"] is True
    assert len(plan["families"]) >= 5
    assert plan["capital_scaling_allowed"] is False


def test_next_generation_stays_closed_when_survivor_exists():
    plan = next_generation_plan(
        {
            "outcome": "historical_survivor_requires_forward_shadow",
            "design_new_family": False,
        }
    )

    assert plan["triggered"] is False
    assert plan["families"] == []
