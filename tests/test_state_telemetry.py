from datetime import datetime, timezone

from app.state import RuntimeState


def test_equity_scan_transitions_do_not_emit_duplicate_durable_events():
    state = RuntimeState()
    emitted = []
    state.set_event_emitter(emitted.append)
    state.begin_cycle("cycle-1")

    observed_at = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
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


def test_non_scan_runtime_events_still_use_the_emitter():
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
