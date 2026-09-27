from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Mapping

from .models import ExperimentProposal, ResearchWindow, canonical_json, deterministic_dict


PROPOSAL_VERSION = "rhen-research-proposal-v1"
FORBIDDEN_REASONING_KEYS = frozenset(
    {"chain_of_thought", "hidden_reasoning", "private_reasoning", "reasoning_trace"}
)


class ProposalFormatError(ValueError):
    pass


def _require(mapping: Mapping[str, Any], name: str) -> Any:
    if name not in mapping:
        raise ProposalFormatError(f"missing required proposal field: {name}")
    return mapping[name]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProposalFormatError(f"{name} must be an object")
    return dict(value)


def _mapping_tuple(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ProposalFormatError(f"{name} must be an array")
    return tuple(_mapping(item, name) for item in value)


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProposalFormatError(f"{name} must be an array")
    return tuple(str(item) for item in value)


def _window(value: Any, expected_stage: str) -> ResearchWindow:
    row = _mapping(value, f"{expected_stage}_window")
    sessions = row.get("expected_sessions")
    if not isinstance(sessions, (list, tuple)):
        raise ProposalFormatError("window expected_sessions must be an array")
    stage = str(row.get("stage") or expected_stage).strip().lower()
    return ResearchWindow(
        window_id=str(_require(row, "window_id")),
        stage=stage,
        starts_on=_require(row, "starts_on"),
        ends_on=_require(row, "ends_on"),
        expected_sessions=tuple(sessions),
        access=str(row.get("access") or "locked").strip().lower(),
    )


def _windows(value: Any, stage: str) -> tuple[ResearchWindow, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProposalFormatError(f"{stage}_windows must be an array")
    return tuple(_window(item, stage) for item in value)


def _assert_no_hidden_reasoning(value: Any, path: str = "proposal") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_REASONING_KEYS:
                raise ProposalFormatError(f"hidden reasoning field is forbidden: {path}.{key}")
            _assert_no_hidden_reasoning(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_hidden_reasoning(item, f"{path}[{index}]")


def proposal_from_dict(value: Mapping[str, Any]) -> ExperimentProposal:
    _assert_no_hidden_reasoning(value)
    revision = _require(value, "revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ProposalFormatError("revision must be a positive integer")
    proposal = ExperimentProposal(
        proposal_version=str(_require(value, "proposal_version")),
        proposal_id=str(_require(value, "proposal_id")),
        revision=revision,
        revision_reason=str(_require(value, "revision_reason")),
        research_question_id=str(_require(value, "research_question_id")),
        title=str(_require(value, "title")),
        research_family=str(_require(value, "research_family")),
        hypothesis=str(_require(value, "hypothesis")),
        null_or_falsification_statement=str(
            _require(value, "null_or_falsification_statement")
        ),
        rationale=_mapping(_require(value, "rationale"), "rationale"),
        source_evidence=_mapping_tuple(
            _require(value, "source_evidence"), "source_evidence"
        ),
        evidence_cutoff=_require(value, "evidence_cutoff"),
        economic_mechanism=str(_require(value, "economic_mechanism")),
        primary_endpoint=str(_require(value, "primary_endpoint")),
        secondary_diagnostics=_string_tuple(
            _require(value, "secondary_diagnostics"), "secondary_diagnostics"
        ),
        tradable_universe=_string_tuple(
            _require(value, "tradable_universe"), "tradable_universe"
        ),
        market_benchmark=(
            str(value["market_benchmark"])
            if value.get("market_benchmark") is not None
            else None
        ),
        sector_or_context_mapping={
            str(key): str(item)
            for key, item in _mapping(
                _require(value, "sector_or_context_mapping"),
                "sector_or_context_mapping",
            ).items()
        },
        data_provider=str(_require(value, "data_provider")),
        data_feed=str(_require(value, "data_feed")),
        raw_interval=str(_require(value, "raw_interval")),
        derived_interval=str(_require(value, "derived_interval")),
        development_windows=_windows(
            _require(value, "development_windows"), "development"
        ),
        validation_windows=_windows(
            _require(value, "validation_windows"), "validation"
        ),
        holdout_windows=_windows(_require(value, "holdout_windows"), "holdout"),
        quarantine_rule=str(_require(value, "quarantine_rule")),
        cost_scenarios=_mapping_tuple(
            _require(value, "cost_scenarios"), "cost_scenarios"
        ),
        controls=_mapping_tuple(_require(value, "controls"), "controls"),
        configurations=_mapping_tuple(
            _require(value, "configurations"), "configurations"
        ),
        sample_floors={
            str(key): int(item)
            for key, item in _mapping(
                _require(value, "sample_floors"), "sample_floors"
            ).items()
        },
        corpus_quality_floors=_mapping(
            _require(value, "corpus_quality_floors"), "corpus_quality_floors"
        ),
        concentration_limits=_mapping(
            _require(value, "concentration_limits"), "concentration_limits"
        ),
        robustness_tests=_string_tuple(
            _require(value, "robustness_tests"), "robustness_tests"
        ),
        uncertainty_method=_mapping(
            _require(value, "uncertainty_method"), "uncertainty_method"
        ),
        multiple_testing_method=(
            _mapping(value["multiple_testing_method"], "multiple_testing_method")
            if value.get("multiple_testing_method") is not None
            else None
        ),
        survivor_selection_rule=(
            _string_tuple(value["survivor_selection_rule"], "survivor_selection_rule")
            if value.get("survivor_selection_rule") is not None
            else None
        ),
        stage_gates=_mapping(_require(value, "stage_gates"), "stage_gates"),
        terminal_rejection_criteria=_string_tuple(
            _require(value, "terminal_rejection_criteria"),
            "terminal_rejection_criteria",
        ),
        created_from_agent_run=_require(value, "created_from_agent_run"),
        created_at=_require(value, "created_at"),
        source_commit=str(_require(value, "source_commit")),
        design_warnings=_string_tuple(
            value.get("design_warnings", ()), "design_warnings"
        ),
        requires_holdout=bool(value.get("requires_holdout", True)),
        feasibility_required=bool(value.get("feasibility_required", True)),
        market_benchmark_required=bool(
            value.get("market_benchmark_required", True)
        ),
    )
    if proposal.proposal_version != PROPOSAL_VERSION:
        raise ProposalFormatError(
            f"unsupported proposal_version: {proposal.proposal_version}"
        )
    return proposal


def proposal_payload(proposal: ExperimentProposal) -> dict[str, Any]:
    payload = deterministic_dict(proposal)
    _assert_no_hidden_reasoning(payload)
    return payload


def proposal_hash(proposal: ExperimentProposal) -> str:
    return hashlib.sha256(
        canonical_json(proposal_payload(proposal)).encode("utf-8")
    ).hexdigest()


def proposal_artifact(proposal: ExperimentProposal) -> dict[str, Any]:
    return {
        **proposal_payload(proposal),
        "proposal_hash": proposal_hash(proposal),
    }


def proposal_bytes(proposal: ExperimentProposal) -> bytes:
    return (canonical_json(proposal_artifact(proposal)) + "\n").encode("utf-8")


def revise_proposal(
    proposal: ExperimentProposal,
    *,
    reason: str,
    **changes: Any,
) -> ExperimentProposal:
    if not reason.strip():
        raise ProposalFormatError("proposal revision reason is required")
    forbidden = {"proposal_id", "revision", "revision_reason", "proposal_version"}
    attempted = forbidden.intersection(changes)
    if attempted:
        raise ProposalFormatError(
            f"revision identity fields cannot be overridden: {', '.join(sorted(attempted))}"
        )
    revised = replace(
        proposal,
        revision=proposal.revision + 1,
        revision_reason=reason.strip(),
        **changes,
    )
    if proposal_hash(revised) == proposal_hash(proposal):
        raise AssertionError("a proposal revision must change the proposal hash")
    return revised
