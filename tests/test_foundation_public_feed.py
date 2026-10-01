from foundation.public_feed import _public_event, _research_entry


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
