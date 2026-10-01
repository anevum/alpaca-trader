from foundation.research_agent_gateway import (
    LEDGER_VERSION,
    _canonical_hash,
    _contains_forbidden_reasoning,
)


def test_canonical_hash_is_key_order_stable():
    assert _canonical_hash({"a": 1, "b": 2}) == _canonical_hash({"b": 2, "a": 1})


def test_hidden_reasoning_keys_are_rejected_recursively():
    assert _contains_forbidden_reasoning({"nested": {"chain_of_thought": "x"}})
    assert not _contains_forbidden_reasoning({"rationale_summary": {"conclusion": "bounded"}})


def test_search_ledger_version_is_frozen():
    assert LEDGER_VERSION == "math001-search-ledger-v1"
