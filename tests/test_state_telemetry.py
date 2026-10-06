from datetime import datetime, timezone

from app.state import RuntimeState


def test_equity_scan_transitions_stay_local_to_operator_history():
    state = RuntimeState()
    emitted = []
    state.set_event_emitter(emitted.append)
    state.begin_cycle("cycle-1")

    observed_at = datetime(2026, 10, 6, 16, 45, tzinfo=timezone.utc)
    state.record_scan(
        {
            "AAPL": {
                "action": "hold",
                "reason": "fast trend is not above slow trend",
                "metadata": {"example": True},
            }
        },
        at=observed_at,
    )

    assert emitted == []
    assert state.decision_history[0]["kind"] == "scan"
    assert state.decision_history[0]["symbol"] == "AAPL"
    assert state.last_scan["AAPL"]["action"] == "hold"


def test_non_scan_runtime_events_still_emit_durably():
    state = RuntimeState()
    emitted = []
    state.set_event_emitter(emitted.append)

    state.record_event(
        kind="runtime",
        action="warning",
        message="example",
    )

    assert len(emitted) == 1
    assert emitted[0]["kind"] == "runtime"
