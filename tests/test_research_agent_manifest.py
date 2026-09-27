from copy import deepcopy

import pytest

from app.research_agent.authorization import AuthorizationError
from app.research_agent.design_checks import DesignValidationError
from app.research_agent.feasibility import (
    ResearchFeasibilityAdapter,
    availability_request,
    evaluate_feasibility,
)
from app.research_agent.manifest import (
    calculate_manifest_hash,
    freeze_preview,
    manifest_bytes,
)
from app.research_agent.proposal import revise_proposal


def passing_feasibility(proposal, fixture):
    adapter = ResearchFeasibilityAdapter(lambda **_request: fixture)
    return evaluate_feasibility(proposal, adapter.fetch(availability_request(proposal)))


def test_freeze_preview_is_deterministic_and_opens_nothing(
    proposal, availability_fixture, freeze_decision
):
    feasibility = passing_feasibility(proposal, availability_fixture)
    first = freeze_preview(
        proposal, decisions=[freeze_decision], feasibility=feasibility
    )
    second = freeze_preview(
        proposal, decisions=[freeze_decision], feasibility=feasibility
    )
    assert manifest_bytes(first.manifest) == manifest_bytes(second.manifest)
    assert first.methodology == second.methodology
    assert first.experiment.row["workflow_state"] == "FROZEN"
    assert first.experiment.row["metrics"] == {}
    windows = first.experiment.row["dataset_window"]
    for stage in ("development", "validation", "holdout"):
        assert all(item["opened"] is False for item in windows[stage])
        assert all(item["access"] == "locked" for item in windows[stage])
    assert windows["quarantine"]["opened"] is False
    assert first.manifest["protected_stage_rules"]["development"]["opened"] is False


def test_any_methodology_mutation_changes_manifest_checksum(
    proposal, availability_fixture, freeze_decision
):
    preview = freeze_preview(
        proposal,
        decisions=[freeze_decision],
        feasibility=passing_feasibility(proposal, availability_fixture),
    )
    changed = deepcopy(preview.manifest)
    changed["methodology"]["primary_endpoint"] = "mutated"
    assert calculate_manifest_hash(changed) != preview.manifest["manifest_hash"]


def test_proposal_revision_invalidates_prior_freeze_authorization(
    proposal, availability_fixture, freeze_decision
):
    revised = revise_proposal(
        proposal,
        reason="Pre-freeze synthetic feed revision",
        data_feed="sip",
    )
    fixture = deepcopy(availability_fixture)
    fixture["feed"] = "sip"
    with pytest.raises(AuthorizationError):
        freeze_preview(
            revised,
            decisions=[freeze_decision],
            feasibility=passing_feasibility(revised, fixture),
        )


def test_required_design_failure_blocks_freeze_before_stage_or_data_outcomes(
    proposal_factory, freeze_decision
):
    invalid = proposal_factory(cost_scenarios=[])
    with pytest.raises(DesignValidationError):
        freeze_preview(invalid, decisions=[freeze_decision], feasibility=None)
