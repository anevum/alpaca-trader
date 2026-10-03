from __future__ import annotations

from datetime import datetime, timezone

from graen.crypto.btc_xgb_replication_v14 import (
    ALPACA_SCREEN_END,
    ALPACA_SCREEN_START,
    CAMPAIGN_ID,
    COST_SCENARIOS,
    LAMBDA_COST,
    METHODOLOGY_VERSION,
    _rolling_mean,
    campaign_manifest,
    cost_aware_positions,
    walk_forward_folds,
)


UTC = timezone.utc


def test_v14_manifest_is_research_only_and_fails_closed():
    manifest = campaign_manifest()
    assert manifest["campaign_id"] == CAMPAIGN_ID
    assert manifest["methodology_version"] == METHODOLOGY_VERSION
    assert manifest["replication_status"] == "CLOSEST_METHOD_PREFLIGHT_NOT_EXACT_REPLICATION"
    assert manifest["authority"] == {
        "research_only": True,
        "promotion_eligible": False,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
    assert manifest["known_source_limitations"]


def test_v14_walk_forward_contract_is_temporal_and_frozen():
    folds = walk_forward_folds(ALPACA_SCREEN_START, ALPACA_SCREEN_END)
    assert len(folds) == 18
    assert folds[0].train_start == datetime(2021, 1, 1, tzinfo=UTC)
    assert folds[0].train_end == datetime(2022, 1, 1, tzinfo=UTC)
    assert folds[0].validation_end == datetime(2022, 4, 1, tzinfo=UTC)
    assert folds[0].test_end == datetime(2022, 7, 1, tzinfo=UTC)
    assert folds[-1].test_end == datetime(2026, 10, 1, tzinfo=UTC)
    for left, right in zip(folds[:-1], folds[1:], strict=True):
        assert left.train_start < left.train_end < left.validation_end < left.test_end
        assert right.train_start > left.train_start
        assert right.test_end > left.test_end


def test_cost_aware_rule_requires_forecast_to_clear_switching_cost():
    cost = COST_SCENARIOS["alpaca_base_30bp"]
    assert LAMBDA_COST * cost == 0.006
    positions = cost_aware_positions(
        [0.0070, 0.0010, -0.0050, -0.0070],
        cost_per_turnover=cost,
    )
    assert positions == [1, 1, 1, 0]


def test_paper_and_alpaca_cost_scenarios_are_not_conflated():
    assert COST_SCENARIOS["paper_10bp"] < COST_SCENARIOS["alpaca_fee_only_25bp"]
    assert COST_SCENARIOS["alpaca_fee_only_25bp"] < COST_SCENARIOS["alpaca_base_30bp"]
    assert COST_SCENARIOS["alpaca_base_30bp"] < COST_SCENARIOS["alpaca_stress_40bp"]


def test_v14_rolling_mean_does_not_poison_all_future_windows_after_warmup_nan():
    import numpy as np

    values = np.asarray([np.nan, 1.0, 2.0, 3.0, 4.0], dtype=float)
    result = _rolling_mean(values, 3)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert np.isnan(result[2])
    assert result[3] == 2.0
    assert result[4] == 3.0
