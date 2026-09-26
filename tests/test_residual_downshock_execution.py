from copy import deepcopy

import pytest

from app.residual_downshock_execution import (
    RawBar,
    ResearchExecutionError,
    _cooldown,
    aggregate_session,
    assert_persistence_preconditions,
    evaluate_configuration,
    quantile,
    select_survivor,
    sign_flip_p_value,
    synchronized_session_returns,
    time_bucket,
)
from app.residual_downshock_v2_1 import expand_parameter_grid, load_manifest


def test_time_buckets_match_frozen_boundaries():
    assert time_bucket(580) == "early"
    assert time_bucket(655) == "early"
    assert time_bucket(660) == "midday"
    assert time_bucket(840) == "late"
    assert time_bucket(900) == "late"
    assert time_bucket(905) is None


def test_quantile_uses_deterministic_linear_percentile():
    assert quantile([0.0, 1.0], 0.05) == 0.05
    assert quantile([], 0.05) is None


def test_cooldown_resets_by_absolute_time_and_preserves_cross_symbol():
    events = [
        {"symbol": "AAPL", "absolute_minute": 100, "minute": 100},
        {"symbol": "MSFT", "absolute_minute": 105, "minute": 105},
        {"symbol": "AAPL", "absolute_minute": 129, "minute": 129},
        {"symbol": "AAPL", "absolute_minute": 130, "minute": 130},
    ]
    kept = _cooldown(events, 30)
    assert [(event["symbol"], event["minute"]) for event in kept] == [("AAPL", 100), ("MSFT", 105), ("AAPL", 130)]


def test_sign_flip_is_deterministic_and_day_clustered():
    events = [
        {"session": "2026-01-05", "stress_return": 0.01},
        {"session": "2026-01-05", "stress_return": 0.02},
        {"session": "2026-01-06", "stress_return": -0.01},
    ]
    assert sign_flip_p_value(events, seed=21020262, resamples=100) == sign_flip_p_value(events, seed=21020262, resamples=100)


def test_selection_uses_frozen_order_only():
    manifest = load_manifest()
    base = {
        "verdict": "PASS",
        "metrics": {"bootstrap_lower_95": 0.001, "worst_window_stress_expectancy": 0.001, "stress_expectancy": 0.002, "event_count": 300},
    }
    a = deepcopy(base); a["configuration_id"] = "RDR21-02"
    b = deepcopy(base); b["configuration_id"] = "RDR21-01"
    assert select_survivor(manifest, [a, b]) == "RDR21-01"


def test_evaluator_grid_is_deterministic_with_empty_observations():
    manifest = load_manifest()
    first = [evaluate_configuration(manifest, cfg, []) for cfg in expand_parameter_grid(manifest)]
    second = [evaluate_configuration(manifest, cfg, []) for cfg in expand_parameter_grid(manifest)]
    assert first == second
    assert all(result["verdict"] == "REJECT" for result in first)
    assert [result["configuration_id"] for result in first] == [f"RDR21-{index:02d}" for index in range(1, 7)]


def test_no_lookahead_sync_requires_current_and_prior_exact_bins():
    bar = lambda close: {"close": close}
    stock = {575: bar(100), 580: bar(99), 585: bar(98)}
    market = {575: bar(100), 580: bar(100)}
    sector = {575: bar(100), 580: bar(99), 585: bar(99)}
    assert list(synchronized_session_returns(stock, market, sector)) == [580]


def test_persistence_is_single_use_and_checksum_bound():
    manifest = load_manifest()
    snapshot = {"status": "planned", "stage_reached": "methodology_frozen", "survivor_state": "not_run", "sample_count": None, "manifest_hash": manifest["freeze"]["manifest_checksum_sha256"]}
    assert_persistence_preconditions(snapshot, 0, manifest["freeze"]["manifest_checksum_sha256"])
    with pytest.raises(ResearchExecutionError):
        assert_persistence_preconditions(snapshot, 1, manifest["freeze"]["manifest_checksum_sha256"])
    changed = deepcopy(snapshot); changed["status"] = "completed"
    with pytest.raises(ResearchExecutionError):
        assert_persistence_preconditions(changed, 0, manifest["freeze"]["manifest_checksum_sha256"])
