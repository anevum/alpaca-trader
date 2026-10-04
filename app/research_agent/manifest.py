from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .authorization import FreezeAuthorization, authorize_freeze_with_charter
from .design_checks import DesignReview, assert_freeze_eligible
from .experiment import PreparedExperiment, experiment_identity, prepare_experiment
from .feasibility import FeasibilityResult, feasibility_artifact
from .models import DesignCheckStatus, ExperimentProposal, canonical_json
from .proposal import proposal_hash


MANIFEST_VERSION = "rhen-research-manifest-v1"
MANIFEST_HASH_METHOD = "sha256_canonical_json_blank_self_hash"


class FreezeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FreezePreview:
    manifest: Mapping[str, Any]
    methodology: str
    experiment: PreparedExperiment
    design_review: DesignReview
    authorization: FreezeAuthorization


def _json_line(value: Any) -> str:
    return canonical_json(value)


def methodology_document(proposal: ExperimentProposal) -> str:
    sections = (
        ("Identity", {
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.revision,
            "research_question_id": proposal.research_question_id,
            "research_family": proposal.research_family,
            "source_commit": proposal.source_commit,
        }),
        ("Hypothesis and falsification", {
            "hypothesis": proposal.hypothesis,
            "null_or_falsification_statement": proposal.null_or_falsification_statement,
            "economic_mechanism": proposal.economic_mechanism,
        }),
        ("Endpoints", {
            "primary_endpoint": proposal.primary_endpoint,
            "secondary_diagnostics": proposal.secondary_diagnostics,
        }),
        ("Corpus", {
            "data_provider": proposal.data_provider,
            "data_feed": proposal.data_feed,
            "raw_interval": proposal.raw_interval,
            "derived_interval": proposal.derived_interval,
            "tradable_universe": proposal.tradable_universe,
            "market_benchmark": proposal.market_benchmark,
            "sector_or_context_mapping": proposal.sector_or_context_mapping,
            "corpus_quality_floors": proposal.corpus_quality_floors,
        }),
        ("Protected windows", {
            "development": proposal.development_windows,
            "validation": proposal.validation_windows,
            "holdout": proposal.holdout_windows,
            "quarantine_rule": proposal.quarantine_rule,
        }),
        ("Controls and costs", {
            "cost_scenarios": proposal.cost_scenarios,
            "controls": proposal.controls,
            "sample_floors": proposal.sample_floors,
            "concentration_limits": proposal.concentration_limits,
        }),
        ("Inference and selection", {
            "configurations": proposal.configurations,
            "robustness_tests": proposal.robustness_tests,
            "uncertainty_method": proposal.uncertainty_method,
            "multiple_testing_method": proposal.multiple_testing_method,
            "survivor_selection_rule": proposal.survivor_selection_rule,
        }),
        ("Gates and terminal criteria", {
            "stage_gates": proposal.stage_gates,
            "terminal_rejection_criteria": proposal.terminal_rejection_criteria,
        }),
    )
    lines = [f"# {proposal.title}", ""]
    for title, payload in sections:
        lines.extend((f"## {title}", "", f"`{_json_line(payload)}`", ""))
    return "\n".join(lines).rstrip() + "\n"


def calculate_manifest_hash(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload["manifest_hash"] = ""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    expected = calculate_manifest_hash(manifest)
    if manifest.get("manifest_hash") != expected:
        raise FreezeError("manifest checksum does not verify")
    return (canonical_json(manifest) + "\n").encode("utf-8")


def _design_artifact(review: DesignReview) -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "status": item.status.value,
            "message": item.message,
            "path": item.path,
        }
        for item in review.results
    ]


def freeze_preview(
    proposal: ExperimentProposal,
    *,
    decisions: Iterable[Mapping[str, Any]],
    feasibility: FeasibilityResult | None,
    allow_standing_charter: bool = False,
) -> FreezePreview:
    design_review = assert_freeze_eligible(proposal)
    digest = proposal_hash(proposal)
    if proposal.feasibility_required:
        if feasibility is None:
            raise FreezeError("passing corpus feasibility is required")
        if (
            feasibility.status != "PASS"
            or feasibility.proposal_id != proposal.proposal_id
            or feasibility.proposal_revision != proposal.revision
            or feasibility.proposal_hash != digest
            or feasibility.provider != proposal.data_provider
            or feasibility.feed != proposal.data_feed
        ):
            raise FreezeError("corpus feasibility does not exactly match and pass")
        feasibility_payload: Mapping[str, Any] | None = feasibility_artifact(feasibility)
    else:
        feasibility_payload = None
    authorization = authorize_freeze_with_charter(
        decisions,
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.revision,
        proposal_hash=digest,
        allow_standing_charter=allow_standing_charter,
    )
    methodology = methodology_document(proposal)
    methodology_hash = hashlib.sha256(methodology.encode("utf-8")).hexdigest()
    experiment_id, experiment_key = experiment_identity(proposal)
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_hash": "",
        "manifest_hash_method": MANIFEST_HASH_METHOD,
        "experiment_id": experiment_id,
        "experiment_key": experiment_key,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.revision,
        "proposal_hash": digest,
        "source_commit": proposal.source_commit,
        "evidence_cutoff": proposal.evidence_cutoff,
        "created_at": proposal.created_at,
        "methodology_sha256": methodology_hash,
        "methodology": {
            "hypothesis": proposal.hypothesis,
            "null_or_falsification_statement": proposal.null_or_falsification_statement,
            "economic_mechanism": proposal.economic_mechanism,
            "primary_endpoint": proposal.primary_endpoint,
            "secondary_diagnostics": proposal.secondary_diagnostics,
            "cost_scenarios": proposal.cost_scenarios,
            "controls": proposal.controls,
            "configurations": proposal.configurations,
            "sample_floors": proposal.sample_floors,
            "corpus_quality_floors": proposal.corpus_quality_floors,
            "concentration_limits": proposal.concentration_limits,
            "robustness_tests": proposal.robustness_tests,
            "uncertainty_method": proposal.uncertainty_method,
            "multiple_testing_method": proposal.multiple_testing_method,
            "survivor_selection_rule": proposal.survivor_selection_rule,
            "stage_gates": proposal.stage_gates,
            "terminal_rejection_criteria": proposal.terminal_rejection_criteria,
        },
        "corpus": {
            "provider": proposal.data_provider,
            "feed": proposal.data_feed,
            "raw_interval": proposal.raw_interval,
            "derived_interval": proposal.derived_interval,
            "tradable_universe": proposal.tradable_universe,
            "market_benchmark": proposal.market_benchmark,
            "sector_or_context_mapping": proposal.sector_or_context_mapping,
            "feasibility": feasibility_payload,
        },
        "windows": {
            "development": proposal.development_windows,
            "validation": proposal.validation_windows,
            "holdout": proposal.holdout_windows,
            "quarantine_rule": proposal.quarantine_rule,
        },
        "protected_stage_rules": {
            "development": {"opened": False, "requires_exact_authorization": True},
            "validation": {"opened": False, "requires_exact_authorization": True},
            "holdout": {"opened": False, "requires_exact_authorization": True},
            "quarantine": {"opened": False, "access": "locked"},
            "production_promotion": "forbidden",
        },
        "design_checks": _design_artifact(design_review),
        "design_warnings": [
            item.message
            for item in design_review.results
            if item.status is DesignCheckStatus.WARNING
        ],
        "freeze_authorization": {
            "decision_reference": authorization.decision_reference,
            "authorized_by": authorization.authorized_by,
            "authorized_at": authorization.authorized_at,
            "proposal_id": authorization.proposal_id,
            "proposal_revision": authorization.proposal_revision,
            "proposal_hash": authorization.proposal_hash,
        },
    }
    manifest["manifest_hash"] = calculate_manifest_hash(manifest)
    experiment = prepare_experiment(
        proposal,
        manifest_hash=manifest["manifest_hash"],
        methodology=methodology,
    )
    return FreezePreview(
        manifest=manifest,
        methodology=methodology,
        experiment=experiment,
        design_review=design_review,
        authorization=authorization,
    )
