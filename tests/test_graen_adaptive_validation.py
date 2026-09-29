from app.research_agent.graen_adaptive_validation import (
    assess_adaptive_validation,
)


def lab():
    return {
        "rolling_searches": {
            "min_momentum_pct": {
                "search_ledger": {
                    "grid_frozen_before_evaluation": True,
                    "dependence_method": "session_level_effects",
                    "multiple_testing_method": (
                        "bonferroni_exact_session_sign_test"
                    ),
                },
                "results": [
                    {
                        "selection_bias_status": "CONTROLLED",
                        "dependence_status": "CONTROLLED",
                    }
                ],
            }
        }
    }


def shadow():
    return {
        "validation_passed": True,
        "no_lookahead_enforced": True,
        "independent_sessions": 12,
    }


def holdout():
    return {
        "status": "PASSED",
        "frozen_before_holdout": True,
        "quarantine_accessed": False,
        "independent_sessions": 6,
        "complete_candidates": 100,
        "forward_coverage": "0.98",
    }


def test_development_and_walk_forward_do_not_auto_pass_holdout():
    result = assess_adaptive_validation(
        counterfactual_lab=lab(),
        shadow_validation=shadow(),
    )
    assert result["selection_bias_status"] == "CONTROLLED"
    assert result["dependence_status"] == "CONTROLLED"
    assert result["multiple_testing_status"] == "CONTROLLED"
    assert result["walk_forward_passed"] is True
    assert result["frozen_validation_passed"] is False
    assert result["promotion_ready"] is False
    assert "FROZEN_HOLDOUT_NOT_PASSED" in result["reason_codes"]


def test_explicit_frozen_untouched_holdout_can_complete_graen_gate():
    result = assess_adaptive_validation(
        counterfactual_lab=lab(),
        shadow_validation=shadow(),
        holdout_result=holdout(),
    )
    assert result["frozen_validation_passed"] is True
    assert result["promotion_ready"] is True
    assert result["promotion_authorized"] is False


def test_quarantine_access_invalidates_holdout():
    bad = holdout()
    bad["quarantine_accessed"] = True
    result = assess_adaptive_validation(
        counterfactual_lab=lab(),
        shadow_validation=shadow(),
        holdout_result=bad,
    )
    assert result["frozen_validation_passed"] is False
    assert result["promotion_ready"] is False


def test_unfrozen_holdout_is_not_accepted():
    bad = holdout()
    bad["frozen_before_holdout"] = False
    result = assess_adaptive_validation(
        counterfactual_lab=lab(),
        shadow_validation=shadow(),
        holdout_result=bad,
    )
    assert result["frozen_validation_passed"] is False
