import pytest

from app.research_agent.atp001 import (
    TwoStateSwitchingConfig,
    expected_reward,
    myopic_switching_thresholds,
    observation_update,
    solve_finite_horizon,
)


def test_myopic_thresholds_have_exact_hysteresis_width():
    config = TwoStateSwitchingConfig(
        correct_reward=1.0,
        wrong_reward=-1.0,
        switch_cost=0.4,
    )
    thresholds = myopic_switching_thresholds(config)
    assert thresholds["switch_1_to_0_below"] == pytest.approx(0.4)
    assert thresholds["switch_0_to_1_above"] == pytest.approx(0.6)
    assert thresholds["hysteresis_width"] == pytest.approx(0.2)


def test_zero_switching_cost_collapses_myopic_band():
    thresholds = myopic_switching_thresholds(
        TwoStateSwitchingConfig(switch_cost=0.0)
    )
    assert thresholds["switch_1_to_0_below"] == pytest.approx(0.5)
    assert thresholds["switch_0_to_1_above"] == pytest.approx(0.5)


def test_expected_reward_is_symmetric():
    config = TwoStateSwitchingConfig()
    assert expected_reward(0.8, 1, config) == pytest.approx(
        expected_reward(0.2, 0, config)
    )


def test_bayesian_observation_moves_belief_in_expected_direction():
    config = TwoStateSwitchingConfig()
    p_pos, posterior_pos = observation_update(0.5, True, config)
    p_neg, posterior_neg = observation_update(0.5, False, config)
    assert p_pos == pytest.approx(0.5)
    assert p_neg == pytest.approx(0.5)
    assert posterior_pos > 0.5
    assert posterior_neg < 0.5


def test_terminal_finite_horizon_policy_matches_analytic_thresholds():
    config = TwoStateSwitchingConfig(
        correct_reward=1.0,
        wrong_reward=-1.0,
        switch_cost=0.4,
    )
    result = solve_finite_horizon(config, horizon=1, grid_size=1001)[0]
    assert result["switch_1_to_0_below"] == pytest.approx(0.401, abs=0.002)
    assert result["switch_0_to_1_at_or_above"] == pytest.approx(0.601, abs=0.002)


def test_finite_horizon_solver_returns_bounded_policy_boundaries():
    result = solve_finite_horizon(
        TwoStateSwitchingConfig(switch_cost=0.25),
        horizon=5,
        grid_size=501,
    )
    assert len(result) == 5
    for row in result:
        lower = row["switch_1_to_0_below"]
        upper = row["switch_0_to_1_at_or_above"]
        assert lower is None or 0.0 <= lower <= 1.0
        assert upper is None or 0.0 <= upper <= 1.0
