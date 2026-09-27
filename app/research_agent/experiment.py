from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID, uuid5

from .models import ExperimentProposal, ExperimentWorkflowState, canonical_json


EXPERIMENT_NAMESPACE = UUID("3f414326-c331-50c2-a731-ad3b40f41c9e")


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    experiment_id: str
    experiment_key: str
    row: Mapping[str, Any]


def experiment_identity(proposal: ExperimentProposal) -> tuple[str, str]:
    key = f"research-agent:{proposal.proposal_id}:r{proposal.revision}"
    return str(uuid5(EXPERIMENT_NAMESPACE, key)), key


def _window_row(window: Any) -> dict[str, Any]:
    return {
        "id": window.window_id,
        "stage": window.stage,
        "start": str(window.starts_on),
        "end": str(window.ends_on),
        "expected_sessions": [str(item) for item in window.expected_sessions],
        "access": "locked",
        "opened": False,
    }


def prepare_experiment(
    proposal: ExperimentProposal,
    *,
    manifest_hash: str,
    methodology: str,
) -> PreparedExperiment:
    experiment_id, experiment_key = experiment_identity(proposal)
    dataset_window = {
        "development": [_window_row(item) for item in proposal.development_windows],
        "validation": [_window_row(item) for item in proposal.validation_windows],
        "holdout": [_window_row(item) for item in proposal.holdout_windows],
        "quarantine": {
            "rule": proposal.quarantine_rule,
            "access": "locked",
            "opened": False,
        },
    }
    universe_payload = {
        "tradable_universe": proposal.tradable_universe,
        "market_benchmark": proposal.market_benchmark,
        "sector_or_context_mapping": proposal.sector_or_context_mapping,
    }
    universe_version = hashlib.sha256(
        canonical_json(universe_payload).encode("utf-8")
    ).hexdigest()
    row = {
        "experiment_id": experiment_id,
        "experiment_key": experiment_key,
        "name": proposal.title,
        "research_family": proposal.research_family,
        "hypothesis": proposal.hypothesis,
        "environment": "simulation",
        "status": "planned",
        "workflow_state": ExperimentWorkflowState.FROZEN.value,
        "dataset_window": dataset_window,
        "methodology": {
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.revision,
            "document": methodology,
            "primary_endpoint": proposal.primary_endpoint,
            "secondary_diagnostics": proposal.secondary_diagnostics,
            "controls": proposal.controls,
            "sample_floors": proposal.sample_floors,
            "corpus_quality_floors": proposal.corpus_quality_floors,
            "robustness_tests": proposal.robustness_tests,
            "uncertainty_method": proposal.uncertainty_method,
            "multiple_testing_method": proposal.multiple_testing_method,
            "survivor_selection_rule": proposal.survivor_selection_rule,
            "stage_gates": proposal.stage_gates,
        },
        "metrics": {},
        "decision": None,
        "conclusion": None,
        "manifest_version": "rhen-research-manifest-v1",
        "manifest_hash": manifest_hash,
        "manifest_hash_method": "sha256_canonical_json_blank_self_hash",
        "code_commit": proposal.source_commit,
        "data_source": proposal.data_provider,
        "data_feed": proposal.data_feed,
        "timeframe": proposal.raw_interval,
        "rejection_criteria": {
            "terminal": proposal.terminal_rejection_criteria,
            "null_or_falsification_statement": proposal.null_or_falsification_statement,
        },
        "stage_reached": "frozen",
        "survivor_state": None,
        "capital_scaling_authorized": False,
        "methodology_version": proposal.proposal_version,
        "universe_version": universe_version,
        "cost_assumptions": {"scenarios": proposal.cost_scenarios},
        "robustness_metrics": {},
    }
    return PreparedExperiment(
        experiment_id=experiment_id,
        experiment_key=experiment_key,
        row=row,
    )
