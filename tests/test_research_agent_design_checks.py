from app.research_agent.models import DesignCheckStatus
from app.research_agent.design_checks import validate_design


def failures(proposal):
    return {
        item.code
        for item in validate_design(proposal).results
        if item.status is DesignCheckStatus.FAIL
    }


def test_valid_design_passes_and_explicit_warnings_are_preserved(proposal):
    review = validate_design(proposal)
    assert review.freeze_eligible is True
    assert [item.message for item in review.warnings] == [
        "Synthetic warning remains visible."
    ]


def test_overlap_and_development_validation_reuse_are_detected(proposal_factory, proposal_data):
    proposal_data["validation_windows"][0]["starts_on"] = "2026-01-06"
    proposal_data["validation_windows"][0]["ends_on"] = "2026-01-10"
    proposal = proposal_factory(**proposal_data)
    assert {"no_stage_window_overlap", "no_development_validation_reuse"} <= failures(proposal)


def test_chronology_context_and_protected_ambiguity_fail(proposal_factory, proposal_data):
    proposal_data["validation_windows"][0]["starts_on"] = "2025-12-01"
    proposal_data["validation_windows"][0]["ends_on"] = "2025-12-02"
    proposal_data["validation_windows"][0]["access"] = "open"
    proposal_data["sector_or_context_mapping"] = {"AAPL": "XLK"}
    proposal = proposal_factory(**proposal_data)
    assert {
        "valid_stage_chronology",
        "context_mapping_complete",
        "protected_windows_locked",
    } <= failures(proposal)


def test_required_methodology_failures_block_freeze(proposal_factory):
    proposal = proposal_factory(
        holdout_windows=[],
        cost_scenarios=[],
        sample_floors={},
        corpus_quality_floors={},
        terminal_rejection_criteria=[],
        multiple_testing_method=None,
        survivor_selection_rule=None,
    )
    assert {
        "holdout_present",
        "costs_present",
        "sample_floor_present",
        "corpus_floor_present",
        "terminal_rejection_present",
        "multiple_testing_fixed",
        "survivor_rule_fixed",
    } <= failures(proposal)
    assert validate_design(proposal).freeze_eligible is False


def test_outcome_contingent_placeholder_and_unsupported_feed_fail(proposal_factory):
    proposal = proposal_factory(
        primary_endpoint="Choose after results",
        data_feed="silently-substituted-feed",
    )
    assert {"no_outcome_contingent_placeholders", "supported_feed"} <= failures(proposal)


def test_incomplete_multiplicity_method_blocks_freeze(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "holm"},
    )
    assert "multiplicity_plan_valid" in failures(proposal)
    assert validate_design(proposal).freeze_eligible is False


def test_bh_without_dependence_assumption_blocks_freeze(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "bh", "alpha": 0.05},
    )
    assert "multiplicity_plan_valid" in failures(proposal)


def test_frozen_holm_plan_passes(proposal_factory):
    proposal = proposal_factory(
        multiple_testing_method={"name": "holm", "alpha": 0.05},
    )
    assert "multiplicity_plan_valid" not in failures(proposal)
