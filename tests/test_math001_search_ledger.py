from uuid import UUID

import pytest

from app.research_agent.search_ledger import (
    SearchLedgerError,
    family_identity,
    hypothesis_definitions,
    normalize_family_name,
    proposal_search_ledger,
    search_event_artifact,
)


def test_family_identity_normalizes_spelling_without_losing_display_name():
    first = family_identity("Relative-Strength_Impulse")
    second = family_identity(" relative strength impulse ")
    assert first["family_id"] == second["family_id"]
    assert first["family_hash"] == second["family_hash"]
    assert first["normalized_name"] == "relative strength impulse"
    assert first["display_name"] == "Relative-Strength_Impulse"


def test_proposal_creates_scientific_parent_and_candidate_variants(proposal):
    parent, variants = hypothesis_definitions(proposal)
    assert parent["hypothesis_kind"] == "SCIENTIFIC_HYPOTHESIS"
    assert variants
    assert all(row["hypothesis_kind"] == "CANDIDATE_VARIANT" for row in variants)
    assert all(row["parent_hypothesis_id"] == parent["hypothesis_id"] for row in variants)
    assert len({row["definition_hash"] for row in variants}) == len(variants)


def test_candidate_identity_is_definition_based_not_proposal_revision(proposal):
    first_parent, first_variants = hypothesis_definitions(proposal)
    revised = proposal.__class__(
        **{
            **{field: getattr(proposal, field) for field in proposal.__dataclass_fields__},
            "revision": proposal.revision + 1,
            "revision_reason": "same definition, new review generation",
        }
    )
    second_parent, second_variants = hypothesis_definitions(revised)
    assert first_parent["hypothesis_id"] == second_parent["hypothesis_id"]
    assert [row["hypothesis_id"] for row in first_variants] == [
        row["hypothesis_id"] for row in second_variants
    ]


def test_proposal_ledger_counts_each_unique_candidate_variant(proposal):
    ledger = proposal_search_ledger(proposal, proposal_accepted=True)
    assert ledger["ledger_version"] == "math001-search-ledger-v1"
    assert ledger["production_authority"] is False
    assert ledger["protected_stage_authority"] is False
    assert ledger["candidate_variant_count"] == len(ledger["hypotheses"]) - 1
    assert len(ledger["events"]) == len(ledger["hypotheses"])
    assert all(event["event_type"] == "PROPOSED" for event in ledger["events"])
    assert all(event["data_contaminating"] is False for event in ledger["events"])
    assert all(event["payload"]["stage_opened"] is False for event in ledger["events"])


def test_search_event_artifact_is_deterministic_and_bounded():
    family_id = str(UUID("10000000-0000-0000-0000-000000000001"))
    hypothesis_id = str(UUID("20000000-0000-0000-0000-000000000001"))
    kwargs = dict(
        hypothesis_id=hypothesis_id,
        family_id=family_id,
        event_type="RESULT_INSPECTED",
        research_stage="DEVELOPMENT",
        event_at="2026-09-27T12:00:00Z",
        event_key="historical:test:result-inspected",
        experiment_id=str(UUID("30000000-0000-0000-0000-000000000001")),
        corpus_id="edge-corpus-v1",
        data_scope_hash="a" * 64,
        data_contaminating=True,
        payload={"terminal": False},
    )
    first = search_event_artifact(**kwargs)
    second = search_event_artifact(**kwargs)
    assert first == second
    assert first["data_contaminating"] is True
    assert UUID(first["search_event_id"])


@pytest.mark.parametrize(
    "event_type,stage",
    [("UNKNOWN", "DEVELOPMENT"), ("PROPOSED", "QUARANTINE")],
)
def test_search_event_rejects_unbounded_types_and_quarantine(event_type, stage):
    with pytest.raises(SearchLedgerError):
        search_event_artifact(
            hypothesis_id=str(UUID("20000000-0000-0000-0000-000000000001")),
            family_id=str(UUID("10000000-0000-0000-0000-000000000001")),
            event_type=event_type,
            research_stage=stage,
            event_at="2026-09-27T12:00:00Z",
            event_key="bad-event",
        )


def test_empty_family_fails_closed():
    with pytest.raises(SearchLedgerError):
        normalize_family_name("  ")
