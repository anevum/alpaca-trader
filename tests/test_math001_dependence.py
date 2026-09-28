import pytest

from app.research_agent.dependence import (
    ASSUMPTION_VIOLATION_SCENARIOS,
    BASE_REQUIRED_CHECKS,
    CROSS_CANDIDATE_CHECK,
    DependenceError,
    VALID_NULL_SCENARIOS,
    canonical_robustness_tests,
    dependence_plan_from_proposal,
    diagnose_cross_candidate,
    diagnose_path,
    required_dependence_checks,
    simulate_dependence_benchmark,
)


def test_robustness_aliases_normalize_to_canonical_checks():
    values = canonical_robustness_tests(
        (
            "autocorrelation",
            "volatility clustering",
            "heavy tails",
            "overlapping outcomes",
            "martingale residual check",
            "common factor dependence",
        )
    )
    assert set(values) == {
        "conditional_mean_residual_check",
        "serial_autocorrelation_diagnostic",
        "volatility_clustering_stress",
        "rare_extreme_stress",
        "overlap_double_counting_check",
        "cross_candidate_dependence_stress",
    }


def test_multiple_configuration_proposal_requires_cross_candidate_check(proposal):
    required = required_dependence_checks(proposal)
    assert set(BASE_REQUIRED_CHECKS) <= set(required)
    assert CROSS_CANDIDATE_CHECK in required


def test_dependence_plan_is_proposal_bound_and_non_authoritative(proposal):
    plan = dependence_plan_from_proposal(proposal)
    assert plan["plan_version"] == "math001-dependence-plan-v1"
    assert plan["proposal_id"] == proposal.proposal_id
    assert plan["proposal_revision"] == proposal.revision
    assert plan["dependence_ready"] is True
    assert plan["missing_checks"] == ()
    assert plan["iid_required"] is False
    assert plan["production_authority"] is False
    assert plan["protected_stage_authority"] is False
    assert len(plan["plan_hash"]) == 64


def test_missing_check_is_visible_and_blocks_readiness(proposal_factory):
    proposal = proposal_factory(
        robustness_tests=[
            "conditional_mean_residual_check",
            "serial_autocorrelation_diagnostic",
            "volatility_clustering_stress",
            "rare_extreme_stress",
            "overlap_double_counting_check",
        ]
    )
    plan = dependence_plan_from_proposal(proposal)
    assert plan["dependence_ready"] is False
    assert plan["missing_checks"] == ("cross_candidate_dependence_stress",)


def test_path_diagnostics_detect_serial_and_volatility_structure():
    path = (0.1, 0.1, -0.1, -0.1, 1.0, 1.0, -1.0, -1.0)
    diagnostics = diagnose_path(path)
    assert diagnostics.observation_count == len(path)
    assert -1.0 <= diagnostics.lag1_autocorrelation <= 1.0
    assert -1.0 <= diagnostics.squared_lag1_autocorrelation <= 1.0
    assert diagnostics.max_absolute_outcome == 1.0
    assert diagnostics.longest_positive_run >= 2
    assert diagnostics.longest_negative_run >= 2


def test_path_diagnostics_reject_unbounded_b1_input():
    with pytest.raises(DependenceError, match="normalized"):
        diagnose_path((0.0, 1.01))


def test_cross_candidate_diagnostics_capture_common_path():
    diagnostics = diagnose_cross_candidate(
        (
            (1.0, -1.0, 1.0, -1.0),
            (1.0, -1.0, 1.0, -1.0),
            (-1.0, 1.0, -1.0, 1.0),
        )
    )
    assert diagnostics.candidate_count == 3
    assert diagnostics.pair_count == 3
    assert diagnostics.max_absolute_pairwise_correlation == pytest.approx(1.0)
    assert diagnostics.mean_absolute_pairwise_correlation == pytest.approx(1.0)


def test_benchmark_classifies_valid_nulls_vs_assumption_violations():
    benchmark = simulate_dependence_benchmark(
        replicates=20,
        candidates_per_replicate=6,
        observations_per_candidate=30,
        seed=42,
    )
    assert set(benchmark.results) == (
        VALID_NULL_SCENARIOS | ASSUMPTION_VIOLATION_SCENARIOS
    )
    for scenario in VALID_NULL_SCENARIOS:
        assert (
            benchmark.results[scenario]["classification"]
            == "VALID_CONDITIONAL_NULL"
        )
        assert benchmark.results[scenario]["b1_calibration_claim_applies"] is True
    for scenario in ASSUMPTION_VIOLATION_SCENARIOS:
        assert (
            benchmark.results[scenario]["classification"]
            == "KNOWN_CONDITIONAL_NULL_VIOLATION"
        )
        assert benchmark.results[scenario]["b1_calibration_claim_applies"] is False


def test_benchmark_is_reproducible():
    kwargs = dict(
        replicates=12,
        candidates_per_replicate=4,
        observations_per_candidate=25,
        seed=9,
    )
    first = simulate_dependence_benchmark(**kwargs)
    second = simulate_dependence_benchmark(**kwargs)
    assert first == second


def test_invalid_benchmark_configuration_fails_closed():
    with pytest.raises(DependenceError):
        simulate_dependence_benchmark(replicates=0)
    with pytest.raises(DependenceError):
        simulate_dependence_benchmark(common_factor_weight=1.5)
