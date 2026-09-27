from copy import deepcopy

import pytest

from app.research_agent.feasibility import (
    FeasibilityError,
    ResearchFeasibilityAdapter,
    assert_availability_only,
    availability_request,
    evaluate_feasibility,
    feasibility_artifact,
)


def evaluate(proposal, fixture):
    adapter = ResearchFeasibilityAdapter(lambda **_request: fixture)
    snapshot = adapter.fetch(availability_request(proposal))
    return evaluate_feasibility(proposal, snapshot)


def test_passing_synthetic_corpus_emits_availability_only(proposal, availability_fixture):
    result = evaluate(proposal, availability_fixture)
    artifact = feasibility_artifact(result)
    assert result.status == "PASS"
    assert result.synchronized_timestamp_count == 4
    assert result.completeness_ratio == 1.0
    text = str(artifact).casefold()
    for forbidden in ("'open'", "'close'", "'volume'", "expectancy", "forward_return", "signal"):
        assert forbidden not in text
    assert_availability_only(artifact)


@pytest.mark.parametrize(
    ("entitlement", "reason"),
    [("UNAVAILABLE", "FEED_UNAVAILABLE"), ("UNAUTHORIZED", "FEED_UNAUTHORIZED")],
)
def test_feed_unavailable_or_unauthorized_fails(
    proposal, availability_fixture, entitlement, reason
):
    availability_fixture["entitlement"] = entitlement
    result = evaluate(proposal, availability_fixture)
    assert result.status == "FAIL"
    assert reason in result.reason_codes


def test_missing_sessions_and_benchmark_context_absence_are_detected(
    proposal, availability_fixture
):
    availability_fixture["bars"]["AAPL"] = availability_fixture["bars"]["AAPL"][:2]
    availability_fixture["bars"]["XLK"] = []
    availability_fixture["bars"]["SPY"] = []
    result = evaluate(proposal, availability_fixture)
    assert result.status == "FAIL"
    assert "MISSING_SESSIONS:AAPL" in result.reason_codes
    assert "BENCHMARK_UNAVAILABLE" in result.reason_codes
    assert "CONTEXT_UNAVAILABLE" in result.reason_codes


def test_pagination_and_synchronization_failures_are_detected(
    proposal, availability_fixture
):
    availability_fixture["pagination_complete"] = {"AAPL": False}
    availability_fixture["bars"]["MSFT"] = [
        {"t": "2026-01-05T15:00:00Z", "c": 999.0}
    ]
    result = evaluate(proposal, availability_fixture)
    assert result.status == "FAIL"
    assert "PAGINATION_INCOMPLETE:AAPL" in result.reason_codes
    assert "SYNCHRONIZED_TIMESTAMPS_INSUFFICIENT" in result.reason_codes


def test_adapter_rejects_feed_substitution(proposal, availability_fixture):
    changed = deepcopy(availability_fixture)
    changed["feed"] = "sip"
    adapter = ResearchFeasibilityAdapter(lambda **_request: changed)
    with pytest.raises(FeasibilityError, match="substitution"):
        adapter.fetch(availability_request(proposal))


def test_feasibility_schema_rejects_price_or_outcome_fields():
    with pytest.raises(FeasibilityError):
        assert_availability_only({"close": 100})
    with pytest.raises(FeasibilityError):
        assert_availability_only({"metrics": {"expectancy": 0.1}})
