from dataclasses import replace

import pytest

from app.research_agent.proposal import (
    ProposalFormatError,
    proposal_bytes,
    proposal_hash,
    revise_proposal,
)


def test_proposal_serialization_and_hash_are_deterministic(proposal):
    assert proposal_bytes(proposal) == proposal_bytes(proposal)
    assert proposal_hash(proposal) == proposal_hash(proposal)
    assert proposal_bytes(proposal).endswith(b"\n")


def test_revision_is_explicit_stable_identity_and_changes_hash(proposal):
    revised = revise_proposal(
        proposal,
        reason="Synthetic feed decision before freeze",
        data_feed="sip",
    )
    assert revised.proposal_id == proposal.proposal_id
    assert revised.revision == proposal.revision + 1
    assert revised.revision_reason == "Synthetic feed decision before freeze"
    assert proposal_hash(revised) != proposal_hash(proposal)


def test_even_reason_only_revision_changes_hash(proposal):
    revised = revise_proposal(proposal, reason="Record a material review decision")
    assert proposal_hash(revised) != proposal_hash(proposal)


def test_missing_required_fields_and_hidden_reasoning_fail(proposal_factory, proposal_data):
    proposal_data.pop("primary_endpoint")
    with pytest.raises(ProposalFormatError, match="primary_endpoint"):
        proposal_factory(**proposal_data)
    with pytest.raises(ProposalFormatError, match="hidden reasoning"):
        proposal_factory(rationale={"chain_of_thought": "forbidden"})


def test_revision_identity_cannot_be_mutated(proposal):
    with pytest.raises(ProposalFormatError, match="identity fields"):
        revise_proposal(proposal, reason="invalid", proposal_id="new-id")
    assert replace(proposal, title="changed") != proposal
