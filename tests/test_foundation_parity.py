from foundation.parity import compare_event_sets, promotion_gate


def event(key: str, event_type: str = "scan", value: int = 1):
    return {
        "event_key": key,
        "run_id": "run-1",
        "strategy_version_id": "strategy-1",
        "event_type": event_type,
        "occurred_at": "2026-10-01T14:00:00+00:00",
        "symbol": None,
        "correlation_id": None,
        "source": "alpaca-trader",
        "payload": {"value": value},
    }


def test_parity_passes_for_identical_sets():
    source = [event("a"), event("b", "order")]
    target = [event("b", "order"), event("a")]
    result = compare_event_sets(source, target)
    assert result.complete is True
    assert result.match_ratio == 1.0
    gate = promotion_gate(result, minimum_events=2)
    assert gate["gate"] == "PASS"


def test_parity_detects_missing_target_event():
    result = compare_event_sets([event("a"), event("b")], [event("a")])
    assert result.missing_in_target == ["b"]
    assert result.complete is False
    assert promotion_gate(result, minimum_events=2)["gate"] == "FAIL"


def test_parity_detects_payload_mismatch():
    source = [event("a", value=1)]
    target = [event("a", value=2)]
    result = compare_event_sets(source, target)
    assert result.digest_mismatches == ["a"]
    assert result.match_ratio == 0.0


def test_parity_detects_unexpected_target_event():
    result = compare_event_sets([event("a")], [event("a"), event("b")])
    assert result.unexpected_in_target == ["b"]
    assert result.complete is False
