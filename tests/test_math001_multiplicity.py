import math

import pytest

from app.research_agent.multiplicity import (
    MultiplicityError,
    async_e_lond,
    benjamini_hochberg,
    benjamini_yekutieli,
    bonferroni,
    e_bh,
    e_lond,
    harmonic_number,
    holm,
    multiplicity_plan_from_proposal,
    normalize_method_name,
    online_study_policy,
    simulate_multiplicity_benchmark,
    telescoping_gamma,
)


def test_bonferroni_and_holm_known_example():
    p = (0.001, 0.01, 0.03, 0.2)
    bon = bonferroni(p, alpha=0.05)
    step = holm(p, alpha=0.05)
    assert bon.rejected_indices == (0, 1)
    assert step.rejected_indices == (0, 1)
    assert bon.error_metric == "FWER"
    assert step.dependence_scope == "ARBITRARY"


def test_bh_and_by_known_example():
    p = (0.001, 0.01, 0.03, 0.2)
    bh = benjamini_hochberg(p, alpha=0.05)
    by = benjamini_yekutieli(p, alpha=0.05)
    assert bh.rejected_indices == (0, 1, 2)
    assert by.rejected_indices == (0, 1)
    assert harmonic_number(4) == pytest.approx(25 / 12)


def test_e_bh_uses_descending_k_over_alpha_rank_threshold():
    e = (2.0, 10.0, 50.0, 100.0)
    result = e_bh(e, alpha=0.05)
    assert result.ordered_indices == (3, 2, 1, 0)
    assert result.decision_thresholds == pytest.approx((80.0, 40.0, 80 / 3, 20.0))
    assert result.rejected_indices == (2, 3)
    assert result.dependence_scope == "ARBITRARY"


def test_telescoping_gamma_is_positive_and_subunit_mass():
    gamma = telescoping_gamma(1000)
    assert all(value > 0 for value in gamma)
    assert math.fsum(gamma) < 1.0
    assert math.fsum(gamma) == pytest.approx(1000 / 1001)


def test_e_lond_updates_level_only_after_prior_discoveries():
    gamma = (0.5, 0.25, 0.125)
    result = e_lond((50.0, 100.0, 100.0), alpha=0.05, gamma=gamma)
    assert result.test_levels[0] == pytest.approx(0.025)
    assert result.rejected[0] is True
    assert result.test_levels[1] == pytest.approx(0.025)
    assert result.rejected[1] is True
    assert result.test_levels[2] == pytest.approx(0.01875)


def test_async_e_lond_counts_only_completed_prior_discoveries():
    gamma = (0.5, 0.25, 0.125)
    result = async_e_lond(
        (50.0, 100.0, 100.0),
        ((), (), (0,)),
        alpha=0.05,
        gamma=gamma,
    )
    assert result.rejected[0] is True
    assert result.test_levels[1] == pytest.approx(0.0125)
    assert result.test_levels[2] == pytest.approx(0.0125)


@pytest.mark.parametrize(
    "raw,canonical",
    [
        ("Bonferroni", "bonferroni"),
        ("benjamini-hochberg", "bh"),
        ("Benjamini Yekutieli", "by"),
        ("e-BH", "e_bh"),
        ("e-LOND", "e_lond"),
        ("async e LOND", "async_e_lond"),
    ],
)
def test_method_normalization(raw, canonical):
    assert normalize_method_name(raw) == canonical


def test_fixed_family_plan_requires_alpha(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "holm"},
    )
    with pytest.raises(MultiplicityError, match="alpha"):
        multiplicity_plan_from_proposal(proposal)


def test_bh_plan_requires_dependence_assumption(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "bh", "alpha": 0.05},
    )
    with pytest.raises(MultiplicityError, match="PRDS"):
        multiplicity_plan_from_proposal(proposal)


def test_e_bh_plan_requires_frozen_e_value_source(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "e_bh", "alpha": 0.05},
    )
    with pytest.raises(MultiplicityError, match="e_value_source"):
        multiplicity_plan_from_proposal(proposal)


def test_holm_plan_is_deterministic_and_non_authoritative(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "holm", "alpha": 0.05},
    )
    first = multiplicity_plan_from_proposal(proposal)
    second = multiplicity_plan_from_proposal(proposal)
    assert first == second
    assert first["method"] == "holm"
    assert first["family_size"] == 2
    assert first["production_authority"] is False
    assert first["protected_stage_authority"] is False
    assert first["policy_status"] == "STUDY_ONLY_UNFROZEN"


def test_online_policy_is_study_only():
    result = online_study_policy(alpha=0.05, asynchronous=True)
    assert result["method"] == "async_e_lond"
    assert result["scope"] == "CROSS_EXPERIMENT_CONFIRMATORY_STREAM"
    assert result["production_authority"] is False
    assert result["policy_status"] == "STUDY_ONLY_UNFROZEN"


def test_benchmark_is_reproducible_and_reports_all_methods():
    kwargs = dict(
        replicates=30,
        hypothesis_count=12,
        nonnull_count=3,
        effect=2.0,
        rho=0.5,
        seed=123,
    )
    first = simulate_multiplicity_benchmark(**kwargs)
    second = simulate_multiplicity_benchmark(**kwargs)
    assert first == second
    expected = {
        "bonferroni",
        "holm",
        "bh",
        "by",
        "e_bh",
        "e_lond",
        "async_e_lond",
    }
    assert set(first.results["independent_null"]) == expected
    for scenario in first.results.values():
        for metrics in scenario.values():
            assert 0.0 <= metrics["empirical_fdr"] <= 1.0
            assert 0.0 <= metrics["empirical_power"] <= 1.0
            assert 0.0 <= metrics["familywise_false_discovery_rate"] <= 1.0


def test_invalid_inputs_fail_closed():
    with pytest.raises(MultiplicityError):
        bonferroni((0.1, 1.1))
    with pytest.raises(MultiplicityError):
        e_bh((1.0, -0.01))
    with pytest.raises(MultiplicityError):
        e_lond((1.0, 2.0), gamma=(0.8, 0.8))
