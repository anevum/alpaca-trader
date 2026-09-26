from datetime import datetime, timezone

from app.state import RuntimeState


def test_universe_change_emits_reconstructable_snapshot():
    state = RuntimeState()
    events = []
    state.set_event_emitter(events.append)
    stamp = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)

    state.set_universe(
        symbols=["AAPL", "MSFT", "QQQ"],
        candidate_count=300,
        eligible_count=6200,
        source="dynamic",
        at=stamp,
        error=None,
    )

    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "universe"
    assert event["action"] == "snapshot"
    assert event["at"] == stamp.isoformat()
    assert event["payload"]["active_symbols"] == ["AAPL", "MSFT", "QQQ"]
    assert event["payload"]["active_count"] == 3
    assert event["payload"]["candidate_count"] == 300
    assert event["payload"]["eligible_count"] == 6200
    assert event["payload"]["source"] == "dynamic"


def test_unchanged_universe_does_not_emit_duplicate_snapshot():
    state = RuntimeState()
    events = []
    state.set_event_emitter(events.append)

    state.set_universe(
        symbols=["AAPL", "MSFT"],
        candidate_count=300,
        eligible_count=6200,
        source="dynamic",
        error=None,
    )
    state.set_universe(
        symbols=["AAPL", "MSFT"],
        candidate_count=300,
        eligible_count=6200,
        source="dynamic",
        error=None,
    )

    assert len(events) == 1


def test_universe_source_or_error_change_is_persisted():
    state = RuntimeState()
    events = []
    state.set_event_emitter(events.append)

    state.set_universe(
        symbols=["AAPL"],
        candidate_count=300,
        eligible_count=6200,
        source="dynamic",
        error=None,
    )
    state.set_universe(
        symbols=["AAPL"],
        candidate_count=300,
        eligible_count=6200,
        source="fallback",
        error="RuntimeError: market data unavailable",
    )

    assert len(events) == 2
    payload = events[-1]["payload"]
    assert payload["source"] == "fallback"
    assert payload["error"] == "RuntimeError: market data unavailable"
