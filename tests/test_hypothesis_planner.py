from datetime import datetime, timezone

from app.research_agent.hypothesis_planner import (
    _catalog,
    online_alpha,
    plan_next,
    select_uninspected_corpus,
)

UTC = timezone.utc


def test_online_alpha_spending_has_bounded_prefix():
    values = [online_alpha(index) for index in range(1, 10000)]
    assert values[0] == 0.025
    assert sum(values) < 0.05
    assert values[-1] < values[0]


def test_planner_selects_complete_untouched_corpus():
    result = select_uninspected_corpus(
        {
            "complete": True,
            "inspected_intervals": [],
        },
        now=datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )
    assert result["available"] is True
    assert result["development_reusable_search_sandbox"] is True
    assert result["confirmatory_untouched_at_freeze"] is True
    assert result["development"] == [
        "2024-01-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ]
    assert result["validation"][1] == result["holdout"][0]
    assert result["development"][1] <= result["validation"][0]
    assert result["corpus_end"] == "2026-10-03T00:00:00+00:00"


def test_planner_never_overlaps_exposed_corpus():
    result = select_uninspected_corpus(
        {
            "complete": True,
            "inspected_intervals": [
                {
                    "start": "2026-06-05T00:00:00+00:00",
                    "end": "2026-10-03T00:00:00+00:00",
                }
            ],
        },
        now=datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )
    assert result["available"] is True
    assert result["corpus_end"] <= "2026-06-05T00:00:00+00:00"


def test_planner_uses_complete_manifest_history_not_only_recent_snapshot():
    first = plan_next(
        {"artifacts": []},
        {
            "complete": True,
            "inspected_intervals": [],
            "strategy_manifest_hashes": [],
        },
        now=datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )
    assert first["state"] == "READY"
    first_hash = first["manifest_hash"]

    second = plan_next(
        {"artifacts": []},
        {
            "complete": True,
            "inspected_intervals": [],
            "strategy_manifest_hashes": [first_hash],
        },
        now=datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
    )
    assert second["state"] == "READY"
    assert second["manifest_hash"] != first_hash
    assert second["search_generation"] == 2
    assert second["validation_alpha"] < first["validation_alpha"]


def test_catalog_exhaustion_is_engineering_boundary_not_parameter_recycling():
    prior = []
    snapshot = {"artifacts": []}
    exposure = {
        "complete": True,
        "inspected_intervals": [],
        "strategy_manifest_hashes": prior,
    }
    for _ in range(80):
        result = plan_next(
            snapshot,
            exposure,
            now=datetime(2026, 10, 4, 18, 0, tzinfo=UTC),
        )
        if result["state"] == "ENGINEERING_REQUIRED":
            assert result["reason"] == "trusted_hypothesis_catalog_exhausted"
            assert result["trusted_hypothesis_space_size"] == 40
            assert len(prior) == 40
            return
        assert result["selection_policy"] == (
            "maximin_structural_novelty_without_backtest_performance"
        )
        assert result["information_value"]["uses_backtest_performance"] is False
        prior.append(result["manifest_hash"])
    raise AssertionError("finite trusted hypothesis space must exhaust rather than recycle")


def test_trusted_hypothesis_space_is_unique_and_structurally_broad():
    catalog = _catalog()
    hashes = {row.hypothesis_id for row in catalog}
    assert len(catalog) == 40
    assert len(hashes) == 40
    assert {row.family for row in catalog} == {
        "cross_asset_diffusion",
        "breadth_laggard_response",
    }
    assert len({row.parameters["lookback_minutes"] for row in catalog}) >= 5
    assert len({row.parameters["hold_minutes"] for row in catalog}) >= 3
