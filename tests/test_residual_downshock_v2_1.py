import json
from copy import deepcopy

import pytest

from app.residual_downshock_v2_1 import (
    ResidualModel,
    aggregate_five_minute,
    apply_round_trip_cost,
    clustered_bootstrap_mean,
    data_quality_pass,
    deterministic_control_choice,
    expand_parameter_grid,
    five_minute_bin_end,
    is_downshock,
    load_manifest,
    manifest_checksum,
    residual,
    robust_z,
    stage_allowed,
    suppress_overlaps,
    synchronized_returns,
)


MANIFEST = "research/residual-downshock-rebound-v2.1.json"


def test_manifest_parses_and_checksum_is_stable():
    m = load_manifest(MANIFEST)
    assert manifest_checksum(MANIFEST) == m["freeze"]["manifest_checksum_sha256"]


def test_parameter_grid_is_exactly_six_in_frozen_order():
    m = load_manifest(MANIFEST)
    grid = expand_parameter_grid(m)
    assert len(grid) == 6
    assert grid[0] == {
        "configuration_id": "RDR21-01",
        "residual_z_threshold": 2.0,
        "absolute_residual_floor": 0.003,
    }
    assert grid[-1] == {
        "configuration_id": "RDR21-06",
        "residual_z_threshold": 3.0,
        "absolute_residual_floor": 0.005,
    }


def test_five_minute_alignment_uses_fixed_regular_session_bins():
    assert five_minute_bin_end(9 * 60 + 30) == 9 * 60 + 35
    assert five_minute_bin_end(9 * 60 + 34) == 9 * 60 + 35
    assert five_minute_bin_end(9 * 60 + 35) == 9 * 60 + 40
    assert five_minute_bin_end(16 * 60) is None


def test_aggregate_five_minute_does_not_forward_fill_missing_minutes():
    rows = [
        {"minute_of_day": 570, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
        {"minute_of_day": 574, "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 20},
    ]
    panel = aggregate_five_minute(rows)
    assert panel[575]["raw_minute_count"] == 2
    assert panel[575]["open"] == 100
    assert panel[575]["close"] == 101.5


def test_synchronization_requires_current_and_prior_timestamp_for_all_assets():
    def row(close):
        return {"close": close}
    stock = {575: row(100), 580: row(99), 585: row(98)}
    market = {575: row(100), 580: row(100)}
    sector = {575: row(100), 580: row(99), 585: row(99)}
    out = synchronized_returns(stock, market, sector)
    assert [r["t"] for r in out] == [580]


def test_residual_formula_matches_frozen_market_plus_sector_excess_form():
    model = ResidualModel(alpha=0.001, beta_market=1.2, beta_sector_excess=0.5)
    row = {"stock_return": -0.02, "market_return": -0.01, "sector_return": -0.015}
    expected = -0.02 - (0.001 + 1.2 * -0.01 + 0.5 * (-0.015 - -0.01))
    assert residual(row, model) == pytest.approx(expected)


def test_robust_z_uses_median_mad_and_floor():
    history = [-0.001, 0.0, 0.001, 0.0, 0.0]
    z = robust_z(-0.004, history, min_scale=1e-6)
    assert z is not None
    assert z < 0


def test_downshock_requires_both_absolute_and_standardized_extreme():
    assert is_downshock(-0.006, -2.6, residual_floor=0.005, z_threshold=2.5)
    assert not is_downshock(-0.004, -3.0, residual_floor=0.005, z_threshold=2.5)
    assert not is_downshock(-0.006, -2.0, residual_floor=0.005, z_threshold=2.5)


def test_cost_model_preserves_22bp_stress_round_trip_at_flat_price():
    value = apply_round_trip_cost(100, 100, full_spread_bps=12, slippage_bps_per_side=5)
    assert value == pytest.approx(-0.00219758, rel=1e-5)


def test_cooldown_suppresses_same_symbol_overlap_but_keeps_cross_symbol():
    events = [
        {"symbol": "AAPL", "decision_minute": 600},
        {"symbol": "MSFT", "decision_minute": 605},
        {"symbol": "AAPL", "decision_minute": 620},
        {"symbol": "AAPL", "decision_minute": 630},
    ]
    kept = suppress_overlaps(events, cooldown_minutes=30)
    assert [(e["symbol"], e["decision_minute"]) for e in kept] == [
        ("AAPL", 600), ("MSFT", 605), ("AAPL", 630)
    ]


def test_stage_gates_keep_validation_and_holdout_locked():
    m = load_manifest(MANIFEST)
    checksum = m["freeze"]["manifest_checksum_sha256"]
    assert stage_allowed(m, "development")
    assert not stage_allowed(m, "validation")
    dev = {
        "stage": "development",
        "verdict": "PASS",
        "manifest_checksum": checksum,
        "selected_configuration_id": "RDR21-03",
    }
    assert stage_allowed(m, "validation", dev)
    assert not stage_allowed(m, "holdout", dev)
    val = {
        "stage": "validation",
        "verdict": "PASS",
        "manifest_checksum": checksum,
        "selected_configuration_id": "RDR21-03",
    }
    assert stage_allowed(m, "holdout", val)


def test_data_quality_gate_is_session_and_sync_based_not_density_based():
    m = load_manifest(MANIFEST)
    good = {
        "pagination_complete": True,
        "expected_session_representation": 1.0,
        "synchronization_completeness": 0.91,
        "market_benchmark_completeness": 0.99,
        "sector_benchmark_completeness": 0.98,
        "model_availability_ratio": 0.95,
        "iex_bar_density_ratio": 0.46,
    }
    assert data_quality_pass(good, m)
    bad = deepcopy(good)
    bad["expected_session_representation"] = 0.93
    assert not data_quality_pass(bad, m)
    bad = deepcopy(good)
    bad["model_availability_ratio"] = 0.89
    assert not data_quality_pass(bad, m)


def test_negative_control_selection_is_deterministic():
    first = deterministic_control_choice(["x3", "x1", "x2"], seed=21020263, key="AAPL|2026-01-05|early")
    second = deterministic_control_choice(["x2", "x3", "x1"], seed=21020263, key="AAPL|2026-01-05|early")
    assert first == second


def test_cluster_bootstrap_seed_is_deterministic_and_day_clustered():
    events = [
        {"session": "2026-01-05", "value": 0.01},
        {"session": "2026-01-05", "value": 0.02},
        {"session": "2026-01-06", "value": -0.01},
        {"session": "2026-01-07", "value": 0.03},
    ]
    first = clustered_bootstrap_mean(events, seed=21020261, resamples=20)
    second = clustered_bootstrap_mean(events, seed=21020261, resamples=20)
    assert first == second
    assert len(first) == 20
