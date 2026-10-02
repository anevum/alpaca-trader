from foundation.public_feed import _public_event, _public_nostra_state, _research_entry


def test_public_event_never_exposes_execution_details():
    event = _public_event("broker_fill", "2026-10-01T12:00:00+00:00")
    assert set(event) == {"at", "type", "kind", "label"}
    assert event["kind"] == "execute"


def test_public_research_entry_is_sanitized():
    row = _research_entry(
        "research_daily_report",
        "2026-10-01T12:00:00+00:00",
        {
            "session": "2026-10-01",
            "status": "PARTIAL",
            "next_action": "collect more evidence",
            "data_quality_warnings": ["missing canonical daily reports"],
            "private_symbol": "SPY",
        },
    )
    assert row["session"] == "2026-10-01"
    assert row["next_action"] == "collect more evidence"
    assert "private_symbol" not in row



def test_public_nostra_state_uses_canonical_independent_runtime():
    state = _public_nostra_state(
        {
            "status": "RUNNING",
            "readiness": True,
            "independent_runtime": True,
            "observed_at": "2026-10-02T19:54:00+00:00",
        },
        forecast_count=264,
        last_forecast_at="2026-10-02T19:53:00+00:00",
    )
    assert state == {
        "runtime_state": "RUNNING",
        "health_state": "HEALTHY",
        "tracking_state": "LIVE_BASELINE",
        "observed_at": "2026-10-02T19:54:00+00:00",
        "independent_runtime": True,
        "activity": "264 immutable forecasts / 24h",
    }


def test_public_nostra_state_does_not_invent_health_without_readiness():
    state = _public_nostra_state(
        {"status": "DEGRADED", "readiness": False, "independent_runtime": True},
        forecast_count=0,
        last_forecast_at="2026-10-02T19:53:00+00:00",
    )
    assert state["health_state"] == "DEGRADED"
    assert state["tracking_state"] == "READY_NO_FORECAST_SAMPLE"
    assert state["independent_runtime"] is True
