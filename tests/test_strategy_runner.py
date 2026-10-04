import pytest
from datetime import datetime, timezone

from app.research_agent.hypothesis_planner import plan_next
from app.research_agent.strategy_grammar import manifest_from_dict
from app.research_agent.strategy_runner import (
    StrategyRunnerError,
    compile_candidate,
)


def first_manifest():
    result = plan_next(
        {"artifacts": []},
        {
            "complete": True,
            "inspected_intervals": [],
            "strategy_manifest_hashes": [],
        },
        now=datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc),
    )
    return manifest_from_dict(result["manifest"])


def test_trusted_manifest_compiles_to_bounded_existing_candidate_engine():
    manifest = first_manifest()
    candidate = compile_candidate(manifest)
    assert candidate.candidate_id == manifest.hypothesis_id
    assert candidate.mode == "lead_lag"
    assert candidate.hold_minutes in {60, 120, 240}
    assert candidate.leader_symbol == "BTC/USD"


def test_manifest_hold_and_exit_cannot_drift():
    manifest = first_manifest()
    payload = manifest.as_dict()
    payload["parameters"]["hold_minutes"] = 240
    drifted = manifest_from_dict(payload)
    with pytest.raises(StrategyRunnerError, match="hold_minutes"):
        compile_candidate(drifted)


def test_untrusted_family_cannot_execute_even_when_primitives_are_known():
    manifest = first_manifest()
    payload = manifest.as_dict()
    payload["family"] = "unreviewed_family"
    altered = manifest_from_dict(payload)
    with pytest.raises(StrategyRunnerError, match="trusted compiler profile"):
        compile_candidate(altered)
