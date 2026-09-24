from datetime import datetime, timezone

from app.state import RuntimeState


def test_record_scan_only_adds_events_when_decision_changes():
    state = RuntimeState()
    at = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    first = {
        "SPY": {"action": "hold", "reason": "no fresh close above opening-range high"},
        "QQQ": {"action": "hold", "reason": "opening range is too wide"},
    }
    state.record_scan(first, at=at)
    assert len(state.decision_history) == 2

    state.record_scan(first, at=at)
    assert len(state.decision_history) == 2

    changed = {
        **first,
        "SPY": {"action": "buy", "reason": "fresh opening-range breakout above VWAP with confirmations"},
    }
    state.record_scan(changed, at=at)
    assert len(state.decision_history) == 3
    assert state.decision_history[0]["symbol"] == "SPY"
    assert state.decision_history[0]["action"] == "buy"


def test_entries_are_enabled_by_default():
    assert RuntimeState().entries_enabled is True
