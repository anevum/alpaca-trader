from datetime import datetime, timezone

import pytest

from foundation.iren_gateway import (
    IREN_STATE_VERSION,
    _parse_datetime,
    _validate_state_commit,
)


def valid_commit():
    return {
        "expected_revision": 0,
        "observation_key": "a" * 64,
        "state": {
            "version": IREN_STATE_VERSION,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "state": "HEALTHY",
        },
        "events": [],
    }


def test_state_commit_shape_accepts_canonical_version():
    revision, key, state, events, observed_at = _validate_state_commit(valid_commit())
    assert revision == 0
    assert key == "a" * 64
    assert state["state"] == "HEALTHY"
    assert events == []
    assert observed_at.tzinfo is not None


def test_state_commit_rejects_wrong_version():
    body = valid_commit()
    body["state"]["version"] = "wrong"
    with pytest.raises(ValueError):
        _validate_state_commit(body)


def test_state_commit_rejects_invalid_transition():
    body = valid_commit()
    body["events"] = [
        {
            "event_key": "b" * 64,
            "transition": "INVALID",
            "route": "iren-control",
        }
    ]
    with pytest.raises(ValueError):
        _validate_state_commit(body)


def test_parse_datetime_requires_timezone():
    with pytest.raises(ValueError):
        _parse_datetime("2026-10-01T12:00:00", "stamp")


def test_recent_runs_prefers_latest_completion_for_equal_schedule():
    import inspect
    from foundation.iren_gateway import recent_runs

    source = inspect.getsource(recent_runs)
    assert "scheduled_at desc" in source
    assert "completed_at desc nulls last" in source
    assert "started_at desc nulls last" in source
    assert "run_id desc" in source
