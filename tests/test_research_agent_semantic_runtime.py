import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.research_agent.policy import SafetyPolicyViolation
from app.research_agent.semantic import (
    SemanticReviewError,
    apply_semantic_output,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000099")
SOURCE_COMMIT = "a" * 40
NOW = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)


def wrapper(action="NOOP", proposal_json="{}", selected_question_id=None):
    return {
        "action": action,
        "selected_question_id": selected_question_id,
        "rationale": {
            "conclusion": "bounded result",
            "supporting_evidence": ["canonical evidence"],
            "contradicting_evidence": [],
            "uncertainties": [],
        },
        "proposal_json": proposal_json,
    }


def test_noop_has_no_proposal_and_no_authority():
    result = apply_semantic_output(
        wrapper(),
        run_id=RUN_ID,
        source_commit=SOURCE_COMMIT,
        evidence_cutoff="2026-09-25T20:00:00Z",
        created_at=NOW,
    )
    assert result["action"] == "NOOP"
    assert result["proposal"] is None
    assert result["proposal_accepted"] is False


def test_non_proposal_action_cannot_smuggle_proposal():
    with pytest.raises(SemanticReviewError, match="cannot carry"):
        apply_semantic_output(
            wrapper(proposal_json='{"proposal_id":"smuggled"}'),
            run_id=RUN_ID,
            source_commit=SOURCE_COMMIT,
            evidence_cutoff=None,
            created_at=NOW,
        )


def test_proposal_uses_runtime_provenance_and_design_checks(proposal_data):
    proposal_data["created_from_agent_run"] = "model-supplied-run"
    proposal_data["source_commit"] = "model-supplied-commit"
    proposal_data["created_at"] = "2000-01-01T00:00:00Z"
    payload = wrapper(
        action="PROPOSE_EXPERIMENT",
        selected_question_id=proposal_data["research_question_id"],
        proposal_json=json.dumps(proposal_data),
    )
    result = apply_semantic_output(
        payload,
        run_id=RUN_ID,
        source_commit=SOURCE_COMMIT,
        evidence_cutoff="2026-09-25T20:00:00Z",
        created_at=NOW,
    )
    assert result["proposal"]["created_from_agent_run"] == str(RUN_ID)
    assert result["proposal"]["source_commit"] == SOURCE_COMMIT
    assert result["proposal"]["created_at"] == "2026-09-27T14:00:00Z"
    assert result["proposal"]["evidence_cutoff"] == "2026-09-25T20:00:00Z"
    assert result["design_review"]["freeze_eligible"] is True
    assert result["proposal_accepted"] is True


@pytest.mark.parametrize(
    "family",
    [
        "controlled_continuation",
        "controlled-continuation",
        "controlled continuation",
        "opening_breakout_retest",
    ],
)
def test_terminal_edge_discovery_families_cannot_be_revived(proposal_data, family):
    proposal_data["research_family"] = family
    payload = wrapper(
        action="PROPOSE_EXPERIMENT",
        selected_question_id=proposal_data["research_question_id"],
        proposal_json=json.dumps(proposal_data),
    )
    with pytest.raises(SafetyPolicyViolation):
        apply_semantic_output(
            payload,
            run_id=RUN_ID,
            source_commit=SOURCE_COMMIT,
            evidence_cutoff=None,
            created_at=NOW,
        )


def test_rdr_v2_1_cannot_be_reopened_under_variant_identity(proposal_data):
    proposal_data["research_family"] = "new-label"
    proposal_data["title"] = "Residual Downshock Rebound v2.1 continuation"
    payload = wrapper(
        action="PROPOSE_EXPERIMENT",
        selected_question_id=proposal_data["research_question_id"],
        proposal_json=json.dumps(proposal_data),
    )
    with pytest.raises(SafetyPolicyViolation):
        apply_semantic_output(
            payload,
            run_id=RUN_ID,
            source_commit=SOURCE_COMMIT,
            evidence_cutoff=None,
            created_at=NOW,
        )
