from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid5

from .models import ExperimentProposal, canonical_json, deterministic_dict
from .proposal import proposal_hash


LEDGER_VERSION = "math001-search-ledger-v1"
FAMILY_ID_NAMESPACE = UUID("6f90b1f4-f34a-5d87-9dc3-7b9ca3e2fb3d")
HYPOTHESIS_ID_NAMESPACE = UUID("ace4ce41-d4cc-56e0-9f8c-192028f6d41f")
EVENT_ID_NAMESPACE = UUID("4a21b6b1-9ebf-5ce4-91ef-05577739a59e")


class SearchLedgerError(ValueError):
    pass


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_uuid(namespace: UUID, key: str) -> str:
    return str(uuid5(namespace, key))


def normalize_family_name(value: str) -> str:
    normalized = " ".join(
        str(value or "")
        .strip()
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )
    if not normalized:
        raise SearchLedgerError("research family is required")
    return normalized


def family_identity(display_name: str) -> dict[str, str]:
    normalized = normalize_family_name(display_name)
    family_hash = _sha256_text(normalized)
    return {
        "family_id": _stable_uuid(FAMILY_ID_NAMESPACE, family_hash),
        "family_key": f"rhen-family:{family_hash}",
        "family_hash": family_hash,
        "normalized_name": normalized,
        "display_name": str(display_name).strip(),
    }


def _sorted_unique_text(values: Sequence[Any]) -> list[str]:
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _definition_hash(payload: Mapping[str, Any]) -> str:
    return _sha256_text(canonical_json(payload))


def _hypothesis_record(
    *,
    family: Mapping[str, str],
    kind: str,
    definition: Mapping[str, Any],
    proposal: ExperimentProposal,
    parent_hypothesis_id: str | None,
) -> dict[str, Any]:
    digest = _definition_hash(definition)
    return {
        "hypothesis_id": _stable_uuid(HYPOTHESIS_ID_NAMESPACE, digest),
        "hypothesis_key": f"rhen-hypothesis:{digest}",
        "definition_hash": digest,
        "family_id": family["family_id"],
        "parent_hypothesis_id": parent_hypothesis_id,
        "hypothesis_kind": kind,
        "origin_kind": "AGENT",
        "search_generation": proposal.revision - 1,
        "research_question_id": proposal.research_question_id,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.revision,
        "proposal_hash": proposal_hash(proposal),
        "statement": proposal.hypothesis,
        "null_or_falsification_statement": proposal.null_or_falsification_statement,
        "economic_mechanism": proposal.economic_mechanism,
        "primary_endpoint": proposal.primary_endpoint,
        "definition": dict(definition),
        "source_agent_run_id": str(proposal.created_from_agent_run),
        "source_commit": proposal.source_commit,
        "created_at": deterministic_dict({"created_at": proposal.created_at})["created_at"],
    }


def hypothesis_definitions(
    proposal: ExperimentProposal,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    family = family_identity(proposal.research_family)

    scientific_definition = {
        "definition_version": "math001-scientific-hypothesis-v1",
        "family_hash": family["family_hash"],
        "hypothesis": proposal.hypothesis.strip(),
        "null_or_falsification_statement": proposal.null_or_falsification_statement.strip(),
        "economic_mechanism": proposal.economic_mechanism.strip(),
        "primary_endpoint": proposal.primary_endpoint.strip(),
        "secondary_diagnostics": _sorted_unique_text(proposal.secondary_diagnostics),
    }
    parent = _hypothesis_record(
        family=family,
        kind="SCIENTIFIC_HYPOTHESIS",
        definition=scientific_definition,
        proposal=proposal,
        parent_hypothesis_id=None,
    )

    configurations = list(proposal.configurations) or [{}]
    variants_by_hash: dict[str, dict[str, Any]] = {}
    for configuration in configurations:
        if not isinstance(configuration, Mapping):
            raise SearchLedgerError("proposal configuration must be an object")
        variant_definition = {
            "definition_version": "math001-candidate-variant-v1",
            "scientific_definition_hash": parent["definition_hash"],
            "configuration": dict(configuration),
            "tradable_universe": _sorted_unique_text(proposal.tradable_universe),
            "market_benchmark": proposal.market_benchmark,
            "sector_or_context_mapping": dict(proposal.sector_or_context_mapping),
            "data_provider": proposal.data_provider,
            "data_feed": proposal.data_feed,
            "raw_interval": proposal.raw_interval,
            "derived_interval": proposal.derived_interval,
            "cost_scenarios": [dict(item) for item in proposal.cost_scenarios],
            "controls": [dict(item) for item in proposal.controls],
            "primary_endpoint": proposal.primary_endpoint,
            "sample_floors": dict(proposal.sample_floors),
            "uncertainty_method": dict(proposal.uncertainty_method),
            "multiple_testing_method": (
                dict(proposal.multiple_testing_method)
                if proposal.multiple_testing_method is not None
                else None
            ),
        }
        variant = _hypothesis_record(
            family=family,
            kind="CANDIDATE_VARIANT",
            definition=variant_definition,
            proposal=proposal,
            parent_hypothesis_id=parent["hypothesis_id"],
        )
        variants_by_hash.setdefault(variant["definition_hash"], variant)

    return parent, tuple(
        variants_by_hash[key] for key in sorted(variants_by_hash)
    )


def _proposal_event(
    *,
    hypothesis: Mapping[str, Any],
    proposal: ExperimentProposal,
    ordinal: int,
    proposal_accepted: bool,
) -> dict[str, Any]:
    event_key = (
        f"{proposal.proposal_id}:r{proposal.revision}:"
        f"{hypothesis['hypothesis_id']}:PROPOSED:{ordinal}"
    )
    return {
        "search_event_id": _stable_uuid(EVENT_ID_NAMESPACE, event_key),
        "event_key": event_key,
        "family_id": hypothesis["family_id"],
        "hypothesis_id": hypothesis["hypothesis_id"],
        "event_type": "PROPOSED",
        "research_stage": "PROPOSAL",
        "source_kind": "AGENT",
        "event_at": deterministic_dict({"created_at": proposal.created_at})["created_at"],
        "evidence_cutoff": deterministic_dict(
            {"evidence_cutoff": proposal.evidence_cutoff}
        )["evidence_cutoff"],
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.revision,
        "proposal_hash": proposal_hash(proposal),
        "source_agent_run_id": str(proposal.created_from_agent_run),
        "experiment_id": None,
        "corpus_id": None,
        "data_scope_hash": None,
        "data_contaminating": False,
        "payload": {
            "ledger_version": LEDGER_VERSION,
            "hypothesis_kind": hypothesis["hypothesis_kind"],
            "proposal_accepted": bool(proposal_accepted),
            "approval_required": bool(proposal_accepted),
            "stage_opened": False,
        },
    }


def proposal_search_ledger(
    proposal: ExperimentProposal,
    *,
    proposal_accepted: bool,
) -> dict[str, Any]:
    family = family_identity(proposal.research_family)
    parent, variants = hypothesis_definitions(proposal)
    hypotheses = (parent, *variants)
    events = tuple(
        _proposal_event(
            hypothesis=hypothesis,
            proposal=proposal,
            ordinal=index,
            proposal_accepted=proposal_accepted,
        )
        for index, hypothesis in enumerate(hypotheses)
    )
    return {
        "ledger_version": LEDGER_VERSION,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.revision,
        "proposal_hash": proposal_hash(proposal),
        "source_agent_run_id": str(proposal.created_from_agent_run),
        "source_commit": proposal.source_commit,
        "family": family,
        "hypotheses": [dict(item) for item in hypotheses],
        "events": [dict(item) for item in events],
        "candidate_variant_count": len(variants),
        "search_generation": proposal.revision - 1,
        "production_authority": False,
        "protected_stage_authority": False,
    }


def search_event_artifact(
    *,
    hypothesis_id: str,
    family_id: str,
    event_type: str,
    research_stage: str,
    event_at: str,
    event_key: str,
    source_agent_run_id: str | None = None,
    proposal_id: str | None = None,
    proposal_revision: int | None = None,
    proposal_hash_value: str | None = None,
    experiment_id: str | None = None,
    corpus_id: str | None = None,
    data_scope_hash: str | None = None,
    global_filtration_id: str | None = None,
    evidence_cutoff: str | None = None,
    source_kind: str = "SYSTEM",
    data_contaminating: bool = False,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not event_key.strip():
        raise SearchLedgerError("event_key is required")
    if event_type not in {
        "PROPOSED",
        "SCREENED",
        "DATA_ACCESSED",
        "RESULT_INSPECTED",
        "REJECTED",
        "ABANDONED",
        "FROZEN",
        "DEVELOPMENT_OPENED",
        "DEVELOPMENT_COMPLETED",
        "VALIDATION_OPENED",
        "VALIDATION_COMPLETED",
        "HOLDOUT_OPENED",
        "HOLDOUT_COMPLETED",
        "CONFIRMED",
        "REPLICATED",
        "ARCHIVED",
    }:
        raise SearchLedgerError("unsupported search event type")
    if research_stage not in {
        "NONE",
        "PROPOSAL",
        "DEVELOPMENT",
        "VALIDATION",
        "HOLDOUT",
    }:
        raise SearchLedgerError("unsupported research stage")
    if proposal_revision is not None and proposal_revision < 1:
        raise SearchLedgerError("proposal_revision must be positive")
    if source_kind not in {"AGENT", "MANUAL", "BACKFILL", "SYSTEM"}:
        raise SearchLedgerError("unsupported source kind")
    for name, value in (
        ("proposal_hash", proposal_hash_value),
        ("data_scope_hash", data_scope_hash),
    ):
        if value is not None and (
            len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise SearchLedgerError(f"{name} must be a lowercase SHA-256 digest")
    return {
        "search_event_id": _stable_uuid(EVENT_ID_NAMESPACE, event_key),
        "event_key": event_key,
        "family_id": str(UUID(family_id)),
        "hypothesis_id": str(UUID(hypothesis_id)),
        "event_type": event_type,
        "research_stage": research_stage,
        "source_kind": source_kind,
        "event_at": str(event_at),
        "evidence_cutoff": evidence_cutoff,
        "proposal_id": proposal_id,
        "proposal_revision": proposal_revision,
        "proposal_hash": proposal_hash_value,
        "source_agent_run_id": (
            str(UUID(source_agent_run_id))
            if source_agent_run_id is not None
            else None
        ),
        "experiment_id": (
            str(UUID(experiment_id)) if experiment_id is not None else None
        ),
        "corpus_id": corpus_id,
        "data_scope_hash": data_scope_hash,
        "global_filtration_id": global_filtration_id,
        "data_contaminating": bool(data_contaminating),
        "payload": dict(payload or {}),
    }
