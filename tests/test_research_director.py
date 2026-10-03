from __future__ import annotations

from app.research_agent.director import (
    ResearchDirectorError,
    validate_director_decision,
)


def _decision(**overrides):
    value = {
        "schema_version": "graen.research-director.v1",
        "action": "REPLICATE_EXTERNAL_METHOD",
        "research_question": "Can a published method reproduce and transfer to BTC?",
        "rationale": "External replication is more informative than another arbitrary family.",
        "evidence_quality": "HIGH",
        "selected_method": {
            "name": "Example Method",
            "category": "machine_learning",
            "alpha_or_execution": "ALPHA",
            "source_title": "Example Paper",
            "source_url": "https://example.org/paper",
            "code_repository_url": "",
            "original_market": "equities",
            "original_horizon": "daily",
            "original_data": "market and characteristic data",
            "original_methodology": "out-of-sample prediction",
            "btc_transfer_rationale": "Mechanism can be tested without changing gates.",
            "required_data": ["BTC market data"],
            "reproduction_plan": ["reproduce original result"],
            "btc_transfer_plan": ["change market only after reproduction"],
            "cost_model_requirements": ["realistic spread and slippage"],
            "falsification_conditions": ["fails original replication"],
        },
        "negative_evidence": ["performance may decay after costs"],
        "exhausted_mechanisms": ["simple OHLCV momentum"],
        "implementation_class": "TRUSTED_COMPILER_EXTENSION_REQUIRED",
        "trusted_compiler_mechanism": "",
        "next_action": "REQUEST_COMPILER_EXTENSION",
        "research_only": True,
        "execution_authority": False,
        "live_execution_authorized": False,
    }
    value.update(overrides)
    return value


def test_director_replication_requires_actual_retrieved_source():
    result = validate_director_decision(
        _decision(),
        retrieved_sources=[{"url": "https://example.org/paper", "title": "Example"}],
        web_search_calls=2,
    )
    assert result["action"] == "REPLICATE_EXTERNAL_METHOD"
    assert result["execution_authority"] is False
    assert result["live_execution_authorized"] is False


def test_director_rejects_replication_without_web_search():
    try:
        validate_director_decision(
            _decision(),
            retrieved_sources=[{"url": "https://example.org/paper"}],
            web_search_calls=0,
        )
    except ResearchDirectorError as exc:
        assert "requires a web search" in str(exc)
    else:
        raise AssertionError("external replication must require web search")


def test_director_rejects_unretrieved_source():
    try:
        validate_director_decision(
            _decision(),
            retrieved_sources=[{"url": "https://other.example/paper"}],
            web_search_calls=1,
        )
    except ResearchDirectorError as exc:
        assert "not returned by web search" in str(exc)
    else:
        raise AssertionError("director must not invent a replication source")


def test_director_cannot_claim_live_authority():
    value = _decision()
    value["live_execution_authorized"] = True
    try:
        validate_director_decision(
            value,
            retrieved_sources=[{"url": "https://example.org/paper"}],
            web_search_calls=1,
        )
    except ResearchDirectorError as exc:
        assert "live execution authorization is forbidden" in str(exc)
    else:
        raise AssertionError("director must remain research-only")
