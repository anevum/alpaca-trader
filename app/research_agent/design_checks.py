from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .models import DesignCheckStatus, ExperimentProposal, ResearchWindow
from .multiplicity import MultiplicityError, multiplicity_plan_from_proposal


SUPPORTED_FEEDS: Mapping[str, frozenset[str]] = {
    "alpaca": frozenset({"iex", "sip"}),
}
OUTCOME_CONTINGENT_MARKERS = (
    "choose after",
    "decide after",
    "optimize later",
    "select after",
    "tbd",
    "todo",
    "tune after",
)


@dataclass(frozen=True, slots=True)
class DesignCheckResult:
    code: str
    status: DesignCheckStatus
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class DesignReview:
    results: tuple[DesignCheckResult, ...]

    @property
    def freeze_eligible(self) -> bool:
        return not any(item.status is DesignCheckStatus.FAIL for item in self.results)

    @property
    def failures(self) -> tuple[DesignCheckResult, ...]:
        return tuple(
            item for item in self.results if item.status is DesignCheckStatus.FAIL
        )

    @property
    def warnings(self) -> tuple[DesignCheckResult, ...]:
        return tuple(
            item for item in self.results if item.status is DesignCheckStatus.WARNING
        )


class DesignValidationError(ValueError):
    pass


def _result(
    code: str,
    passed: bool,
    message: str,
    *,
    path: str | None = None,
) -> DesignCheckResult:
    return DesignCheckResult(
        code=code,
        status=DesignCheckStatus.PASS if passed else DesignCheckStatus.FAIL,
        message=message,
        path=path,
    )


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _bounds(window: ResearchWindow) -> tuple[date, date]:
    return _as_date(window.starts_on), _as_date(window.ends_on)


def _overlaps(left: ResearchWindow, right: ResearchWindow) -> bool:
    left_start, left_end = _bounds(left)
    right_start, right_end = _bounds(right)
    return left_start <= right_end and right_start <= left_end


def _all_pairs(
    left: Iterable[ResearchWindow],
    right: Iterable[ResearchWindow],
) -> Iterable[tuple[ResearchWindow, ResearchWindow]]:
    for left_item in left:
        for right_item in right:
            yield left_item, right_item


def _text_values(value: Any) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _text_values_at(item, str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _text_values_at(item, str(index))
    elif isinstance(value, str):
        yield "", value


def _text_values_at(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _text_values_at(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _text_values_at(item, f"{path}.{index}")
    elif isinstance(value, str):
        yield path, value


def validate_design(proposal: ExperimentProposal) -> DesignReview:
    results: list[DesignCheckResult] = []

    required_text = {
        "proposal_id": proposal.proposal_id,
        "research_question_id": proposal.research_question_id,
        "title": proposal.title,
        "hypothesis": proposal.hypothesis,
        "null_or_falsification_statement": proposal.null_or_falsification_statement,
        "economic_mechanism": proposal.economic_mechanism,
        "primary_endpoint": proposal.primary_endpoint,
        "data_provider": proposal.data_provider,
        "data_feed": proposal.data_feed,
        "raw_interval": proposal.raw_interval,
        "derived_interval": proposal.derived_interval,
        "quarantine_rule": proposal.quarantine_rule,
        "source_commit": proposal.source_commit,
    }
    for name, value in required_text.items():
        results.append(
            _result(
                f"required_{name}",
                bool(str(value).strip()),
                f"{name} must be fixed before freeze",
                path=name,
            )
        )

    window_groups = {
        "development": proposal.development_windows,
        "validation": proposal.validation_windows,
        "holdout": proposal.holdout_windows,
    }
    results.append(
        _result(
            "development_present",
            bool(proposal.development_windows),
            "at least one development window is required",
            path="development_windows",
        )
    )
    results.append(
        _result(
            "validation_present",
            bool(proposal.validation_windows),
            "at least one validation window is required",
            path="validation_windows",
        )
    )
    results.append(
        _result(
            "holdout_present",
            bool(proposal.holdout_windows) or not proposal.requires_holdout,
            "a holdout window is required by this methodology",
            path="holdout_windows",
        )
    )

    dates_valid = True
    stage_labels_valid = True
    protected_locked = True
    for stage, windows in window_groups.items():
        for window in windows:
            try:
                starts_on, ends_on = _bounds(window)
                dates_valid = dates_valid and starts_on <= ends_on
            except (TypeError, ValueError):
                dates_valid = False
            stage_labels_valid = stage_labels_valid and window.stage == stage
            protected_locked = protected_locked and window.access == "locked"
    results.extend(
        (
            _result(
                "valid_window_bounds",
                dates_valid,
                "every window must use valid dates with starts_on <= ends_on",
            ),
            _result(
                "unambiguous_window_stages",
                stage_labels_valid,
                "every window must identify its protected stage exactly",
            ),
            _result(
                "protected_windows_locked",
                protected_locked,
                "all windows must remain locked in a pre-freeze proposal",
            ),
        )
    )

    within_overlap = False
    if dates_valid:
        for windows in window_groups.values():
            for index, left in enumerate(windows):
                within_overlap = within_overlap or any(
                    _overlaps(left, right) for right in windows[index + 1 :]
                )
    results.append(
        _result(
            "no_within_stage_overlap",
            not within_overlap,
            "windows within a stage must not overlap",
        )
    )

    stage_overlap = False
    development_validation_overlap = False
    if dates_valid:
        pairs = (
            ("development", "validation"),
            ("development", "holdout"),
            ("validation", "holdout"),
        )
        for left_stage, right_stage in pairs:
            for left, right in _all_pairs(
                window_groups[left_stage], window_groups[right_stage]
            ):
                if _overlaps(left, right):
                    stage_overlap = True
                    if (left_stage, right_stage) == ("development", "validation"):
                        development_validation_overlap = True
    results.extend(
        (
            _result(
                "no_stage_window_overlap",
                not stage_overlap,
                "development, validation, and holdout windows must be disjoint",
            ),
            _result(
                "no_development_validation_reuse",
                not development_validation_overlap,
                "the same dates cannot serve development and validation",
            ),
        )
    )

    chronology_valid = dates_valid
    ordered_groups = [
        proposal.development_windows,
        proposal.validation_windows,
        proposal.holdout_windows,
    ]
    present_groups = [group for group in ordered_groups if group]
    if dates_valid:
        for left_group, right_group in zip(present_groups, present_groups[1:]):
            chronology_valid = chronology_valid and max(
                _bounds(window)[1] for window in left_group
            ) < min(_bounds(window)[0] for window in right_group)
    results.append(
        _result(
            "valid_stage_chronology",
            chronology_valid,
            "development must precede validation, which must precede holdout",
        )
    )

    normalized_symbols = [symbol.strip().upper() for symbol in proposal.tradable_universe]
    universe_nonempty = bool(normalized_symbols) and all(normalized_symbols)
    universe_unique = len(normalized_symbols) == len(set(normalized_symbols))
    results.extend(
        (
            _result(
                "universe_present",
                universe_nonempty,
                "tradable_universe cannot be empty",
                path="tradable_universe",
            ),
            _result(
                "universe_unique",
                universe_unique,
                "tradable_universe cannot contain duplicate symbols",
                path="tradable_universe",
            ),
        )
    )

    benchmark_present = bool(proposal.market_benchmark and proposal.market_benchmark.strip())
    results.append(
        _result(
            "market_benchmark_present",
            benchmark_present or not proposal.market_benchmark_required,
            "the methodology requires an explicit market benchmark",
            path="market_benchmark",
        )
    )
    normalized_mapping = {
        key.strip().upper(): value.strip().upper()
        for key, value in proposal.sector_or_context_mapping.items()
    }
    mapping_complete = all(
        symbol in normalized_mapping and bool(normalized_mapping[symbol])
        for symbol in normalized_symbols
    )
    results.append(
        _result(
            "context_mapping_complete",
            mapping_complete,
            "every tradable symbol requires a fixed sector/context symbol",
            path="sector_or_context_mapping",
        )
    )

    provider = proposal.data_provider.strip().casefold()
    feed = proposal.data_feed.strip().casefold()
    supported_feed = feed in SUPPORTED_FEEDS.get(provider, frozenset())
    results.append(
        _result(
            "supported_feed",
            supported_feed,
            "provider/feed must be explicitly supported; substitution is forbidden",
            path="data_feed",
        )
    )
    results.extend(
        (
            _result(
                "costs_present",
                bool(proposal.cost_scenarios),
                "at least one fixed cost scenario is required",
                path="cost_scenarios",
            ),
            _result(
                "sample_floor_present",
                bool(proposal.sample_floors),
                "sample floors must be fixed before freeze",
                path="sample_floors",
            ),
            _result(
                "corpus_floor_present",
                bool(proposal.corpus_quality_floors),
                "corpus-quality floors must be fixed before freeze",
                path="corpus_quality_floors",
            ),
            _result(
                "terminal_rejection_present",
                bool(proposal.terminal_rejection_criteria),
                "terminal rejection criteria must be fixed before freeze",
                path="terminal_rejection_criteria",
            ),
        )
    )
    multiple_configurations = len(proposal.configurations) > 1
    multiple_testing_present = bool(proposal.multiple_testing_method)
    survivor_rule_present = bool(proposal.survivor_selection_rule)
    multiplicity_valid = True
    multiplicity_message = "multiplicity plan is deterministic and fully specified"
    if multiple_configurations:
        try:
            multiplicity_plan_from_proposal(proposal)
        except MultiplicityError as exc:
            multiplicity_valid = False
            multiplicity_message = str(exc)
    results.extend(
        (
            _result(
                "multiple_testing_fixed",
                not multiple_configurations or multiple_testing_present,
                "multiple configurations require a fixed multiple-testing treatment",
                path="multiple_testing_method",
            ),
            _result(
                "multiplicity_plan_valid",
                not multiple_configurations or multiplicity_valid,
                multiplicity_message,
                path="multiple_testing_method",
            ),
            _result(
                "survivor_rule_fixed",
                not multiple_configurations or survivor_rule_present,
                "multiple possible survivors require a fixed survivor-selection rule",
                path="survivor_selection_rule",
            ),
        )
    )

    contingent_fields: list[str] = []
    outcome_sensitive = {
        "primary_endpoint": proposal.primary_endpoint,
        "cost_scenarios": proposal.cost_scenarios,
        "configurations": proposal.configurations,
        "sample_floors": proposal.sample_floors,
        "corpus_quality_floors": proposal.corpus_quality_floors,
        "stage_gates": proposal.stage_gates,
        "terminal_rejection_criteria": proposal.terminal_rejection_criteria,
    }
    for path, text in _text_values(outcome_sensitive):
        lowered = text.casefold()
        if any(marker in lowered for marker in OUTCOME_CONTINGENT_MARKERS):
            contingent_fields.append(path)
    results.append(
        _result(
            "no_outcome_contingent_placeholders",
            not contingent_fields,
            "methodology cannot defer choices until after outcomes are inspected"
            + (f": {', '.join(sorted(contingent_fields))}" if contingent_fields else ""),
        )
    )

    for index, warning in enumerate(proposal.design_warnings):
        results.append(
            DesignCheckResult(
                code=f"proposal_warning_{index + 1}",
                status=DesignCheckStatus.WARNING,
                message=warning,
                path="design_warnings",
            )
        )
    return DesignReview(results=tuple(results))


def assert_freeze_eligible(proposal: ExperimentProposal) -> DesignReview:
    review = validate_design(proposal)
    if review.failures:
        codes = ", ".join(item.code for item in review.failures)
        raise DesignValidationError(f"proposal is not freeze-eligible: {codes}")
    return review
