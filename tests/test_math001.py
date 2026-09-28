import pytest

from app.research_agent.math001 import (
    DEFAULT_LAMBDAS,
    Math001Error,
    anytime_p_values,
    fixed_lambda_e_process,
    hoeffding_lower_confidence_sequence,
    mixture_e_process,
    simulate_null_search,
    summarize_evidence,
)


def test_fixed_lambda_process_matches_exact_product():
    path = fixed_lambda_e_process((1.0, -1.0, 0.5), 0.2)
    assert path == pytest.approx((1.2, 0.96, 1.056))


def test_mixture_is_convex_combination_of_component_processes():
    outcomes = (1.0, 1.0, -1.0, 0.25)
    lambdas = (0.1, 0.4)
    weights = (0.25, 0.75)
    mixture = mixture_e_process(outcomes, lambdas=lambdas, weights=weights)
    first = fixed_lambda_e_process(outcomes, lambdas[0])
    second = fixed_lambda_e_process(outcomes, lambdas[1])
    expected = tuple(
        weights[0] * a + weights[1] * b
        for a, b in zip(first, second)
    )
    assert mixture == pytest.approx(expected)


def test_anytime_p_value_uses_running_max_e_value():
    p = anytime_p_values((1.0, 5.0, 4.0, 10.0))
    assert p == pytest.approx((1.0, 0.2, 0.2, 0.1))


def test_confidence_sequence_stays_within_normalized_support():
    lower = hoeffding_lower_confidence_sequence((1.0,) * 100, alpha=0.05)
    assert all(-1.0 <= value <= 1.0 for value in lower)
    assert lower[-1] > lower[0]


def test_summary_is_explicitly_research_only():
    summary = summarize_evidence((1.0,) * 100, alpha=0.05)
    assert summary.project_id == "MATH-001"
    assert summary.policy_status == "STUDY_ONLY_UNFROZEN"
    assert summary.observation_count == 100
    assert summary.max_e_value >= summary.current_e_value
    assert 0.0 <= summary.anytime_p_value <= 1.0


def test_outcome_normalization_fails_closed():
    with pytest.raises(Math001Error):
        mixture_e_process((0.0, 1.01), lambdas=DEFAULT_LAMBDAS)


def test_null_search_simulation_is_reproducible():
    kwargs = dict(
        replicates=20,
        candidates_per_replicate=8,
        observations_per_candidate=30,
        min_observations=5,
        seed=42,
    )
    first = simulate_null_search(**kwargs)
    second = simulate_null_search(**kwargs)
    assert first == second
    assert 0.0 <= first.naive_false_discovery_rate <= 1.0
    assert 0.0 <= first.anytime_familywise_false_discovery_rate <= 1.0
    assert first.anytime_familywise_threshold == pytest.approx(160.0)
