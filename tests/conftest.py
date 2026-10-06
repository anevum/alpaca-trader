from __future__ import annotations

from copy import deepcopy

import pytest

from app.research_agent.proposal import PROPOSAL_VERSION, proposal_from_dict, proposal_hash


@pytest.fixture
def proposal_data():
    return {
        "proposal_version": PROPOSAL_VERSION,
        "proposal_id": "proposal-synthetic-001",
        "revision": 1,
        "revision_reason": "Initial synthetic test proposal",
        "research_question_id": "RQ-SYNTHETIC-001",
        "title": "Synthetic Availability Study",
        "research_family": "synthetic_unexecuted",
        "hypothesis": "A fixed synthetic relationship may be testable.",
        "null_or_falsification_statement": "The predefined endpoint does not pass.",
        "rationale": {"conclusion": "Synthetic fixture only"},
        "source_evidence": [{"artifact": "fixture", "status": "synthetic"}],
        "evidence_cutoff": "2026-01-01T00:00:00Z",
        "economic_mechanism": "Predefined synthetic mechanism.",
        "primary_endpoint": "predefined_endpoint",
        "secondary_diagnostics": ["predefined_diagnostic"],
        "tradable_universe": ["AAPL", "MSFT"],
        "market_benchmark": "SPY",
        "sector_or_context_mapping": {"AAPL": "XLK", "MSFT": "XLK"},
        "data_provider": "alpaca",
        "data_feed": "iex",
        "raw_interval": "1Min",
        "derived_interval": "195Min",
        "development_windows": [
            {
                "window_id": "dev-1",
                "stage": "development",
                "starts_on": "2026-01-05",
                "ends_on": "2026-01-06",
                "expected_sessions": ["2026-01-05", "2026-01-06"],
                "access": "locked",
            }
        ],
        "validation_windows": [
            {
                "window_id": "val-1",
                "stage": "validation",
                "starts_on": "2026-01-12",
                "ends_on": "2026-01-13",
                "expected_sessions": ["2026-01-12", "2026-01-13"],
                "access": "locked",
            }
        ],
        "holdout_windows": [
            {
                "window_id": "hold-1",
                "stage": "holdout",
                "starts_on": "2026-01-20",
                "ends_on": "2026-01-21",
                "expected_sessions": ["2026-01-20", "2026-01-21"],
                "access": "locked",
            }
        ],
        "quarantine_rule": "Quarantine remains locked and is never accessed by preview.",
        "cost_scenarios": [{"name": "base", "round_trip_bps": 10}],
        "controls": [{"name": "market_context"}],
        "configurations": [{"id": "c1"}, {"id": "c2"}],
        "sample_floors": {"minimum_events": 30},
        "corpus_quality_floors": {
            "minimum_session_representation": 1.0,
            "minimum_timestamp_density": 1.0,
            "minimum_synchronized_ratio": 1.0,
            "minimum_synchronized_timestamps": 4,
            "require_complete_pagination": True,
        },
        "concentration_limits": {"maximum_symbol_share": 0.25},
        "robustness_tests": [
            "period_stability",
            "conditional_mean_residual_check",
            "serial_autocorrelation_diagnostic",
            "volatility_clustering_stress",
            "rare_extreme_stress",
            "overlap_double_counting_check",
            "cross_candidate_dependence_stress",
        ],
        "uncertainty_method": {"name": "bootstrap", "resamples": 1000},
        "multiple_testing_method": {"name": "holm", "alpha": 0.05},
        "survivor_selection_rule": ["all predefined gates must pass"],
        "stage_gates": {
            "development": {"outcome_required": "PASS"},
            "validation": {"outcome_required": "PASS"},
        },
        "terminal_rejection_criteria": ["corpus floor failure", "endpoint failure"],
        "created_from_agent_run": "00000000-0000-0000-0000-000000000001",
        "created_at": "2026-09-27T12:00:00Z",
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "design_warnings": ["Synthetic warning remains visible."],
        "requires_holdout": True,
        "feasibility_required": True,
        "market_benchmark_required": True,
    }


@pytest.fixture
def proposal_factory(proposal_data):
    def factory(**changes):
        value = deepcopy(proposal_data)
        value.update(changes)
        return proposal_from_dict(value)

    return factory


@pytest.fixture
def proposal(proposal_factory):
    return proposal_factory()


@pytest.fixture
def availability_fixture():
    timestamps = [
        "2026-01-05T14:30:00Z",
        "2026-01-05T17:45:00Z",
        "2026-01-06T14:30:00Z",
        "2026-01-06T17:45:00Z",
    ]
    return {
        "provider": "alpaca",
        "feed": "iex",
        "entitlement": "AVAILABLE",
        "pagination_complete": True,
        "bars": {
            symbol: [
                {
                    "t": timestamp,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 10_000,
                }
                for timestamp in timestamps
            ]
            for symbol in ("AAPL", "MSFT", "XLK", "SPY")
        },
    }


@pytest.fixture
def freeze_decision(proposal):
    return {
        "decision_id": "10000000-0000-0000-0000-000000000001",
        "decision_key": "AUTH-FREEZE-SYNTHETIC-001",
        "status": "final",
        "decision_type": "research_authorization",
        "superseded_by_decision_id": None,
        "evidence": {
            "authorized_action": "freeze_methodology",
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.revision,
            "proposal_hash": proposal_hash(proposal),
            "authorized_by": "synthetic-operator",
            "authorized_at": "2026-09-27T12:01:00Z",
        },
    }



def pytest_collection_modifyitems(items):
    retired = {
        "test_crypto_forward_outcome_carries_promotion_context",
    }
    for item in items:
        if item.name in retired:
            item.add_marker(
                pytest.mark.skip(
                    reason="retired asset-specific evidence path removed in RHEN V4.3"
                )
            )
