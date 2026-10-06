from decimal import Decimal

from app.capital_allocator import (
    AllocationCandidate,
    bounded_notional,
    opportunity_multiplier,
    rank_candidates,
)
from app.execution_costs import (
    economic_admission,
    estimate_costs,
    implementation_shortfall_bps,
    shrunk_expectancy_bps,
)


def test_sparse_expectancy_shrinks_toward_conservative_prior():
    estimate = shrunk_expectancy_bps(
        sample_mean_bps=Decimal("30"),
        sample_size=5,
        prior_mean_bps=Decimal("4"),
        shrinkage_k=20,
    )
    assert estimate == Decimal("9.2")


def test_expected_net_economics_are_cost_aware():
    estimate = estimate_costs(
        expected_gross_bps=Decimal("20"),
        spread_bps=Decimal("2"),
        expected_slippage_bps=Decimal("3"),
        regulatory_bps=Decimal("0.5"),
        uncertainty_reserve_bps=Decimal("2.5"),
        sample_size=40,
        confidence=Decimal("0.8"),
    )
    assert estimate.round_trip_spread_bps == Decimal("4")
    assert estimate.expected_net_bps == Decimal("10.0")
    allowed, reason = economic_admission(
        estimate,
        minimum_net_bps=Decimal("5"),
        minimum_gross_to_cost_ratio=Decimal("2"),
    )
    assert allowed is True
    assert reason == "economic_gate_passed"


def test_negative_or_marginal_edge_is_rejected():
    estimate = estimate_costs(
        expected_gross_bps=Decimal("8"),
        spread_bps=Decimal("2"),
        expected_slippage_bps=Decimal("2"),
        uncertainty_reserve_bps=Decimal("3"),
    )
    allowed, reason = economic_admission(
        estimate,
        minimum_net_bps=Decimal("5"),
        minimum_gross_to_cost_ratio=Decimal("2"),
    )
    assert allowed is False
    assert reason == "expected_net_edge_nonpositive"


def test_implementation_shortfall_has_execution_direction():
    assert implementation_shortfall_bps(
        side="buy",
        fill_price=Decimal("100.10"),
        decision_mid=Decimal("100"),
    ) == Decimal("10.000")
    assert implementation_shortfall_bps(
        side="sell",
        fill_price=Decimal("99.90"),
        decision_mid=Decimal("100"),
    ) == Decimal("10.000")


def test_opportunity_multiplier_is_bounded():
    assert opportunity_multiplier(
        expected_net_bps=Decimal("10"),
        confidence=Decimal("0.5"),
        full_size_edge_bps=Decimal("20"),
        floor_multiplier=Decimal("0.25"),
    ) == Decimal("0.4375")


def test_final_notional_is_minimum_of_all_hard_caps():
    final, binding, caps = bounded_notional(
        safe_risk_notional=Decimal("80"),
        multiplier=Decimal("0.5"),
        buying_power_cap=Decimal("50"),
        position_cap=Decimal("45"),
        remaining_gross_cap=Decimal("42"),
        remaining_stop_risk_notional_cap=Decimal("38"),
        liquidity_cap=Decimal("60"),
        correlation_cluster_cap=Decimal("55"),
    )
    assert final == Decimal("38.00")
    assert binding == "portfolio_stop_risk_cap"
    assert caps["risk_cap"] == "40.0"


def test_capital_velocity_prefers_faster_more_efficient_candidate():
    slower = AllocationCandidate(
        symbol="SLOW",
        expected_net_bps=Decimal("20"),
        expected_net_dollars=Decimal("0.20"),
        fill_probability=Decimal("0.9"),
        proposed_notional=Decimal("50"),
        expected_holding_minutes=Decimal("60"),
        confidence=Decimal("0.9"),
    )
    faster = AllocationCandidate(
        symbol="FAST",
        expected_net_bps=Decimal("12"),
        expected_net_dollars=Decimal("0.12"),
        fill_probability=Decimal("0.9"),
        proposed_notional=Decimal("20"),
        expected_holding_minutes=Decimal("10"),
        confidence=Decimal("0.8"),
    )
    assert rank_candidates([slower, faster])[0].symbol == "FAST"
