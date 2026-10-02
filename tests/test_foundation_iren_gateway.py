from datetime import datetime, timezone

import pytest

from foundation.iren_gateway import (
    IREN_STATE_VERSION,
    _parse_datetime,
    _validate_state_commit,
    scheduler_claim,
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



class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RetryCursor:
    def __init__(self):
        self.query = ""
        self.args = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, args=None):
        self.query = str(query)
        self.args = args

    def fetchone(self):
        lowered = self.query.lower()
        if "insert into iren.scheduler_runs" in lowered:
            return None
        if "select" in lowered and "from iren.scheduler_runs" in lowered:
            return (
                "00000000-0000-0000-0000-000000000001",
                "FAILED",
                1,
                3,
                None,
                "evidence_unavailable",
                datetime.now(timezone.utc),
            )
        raise AssertionError(f"unexpected query: {self.query}")


class _RetryConnection:
    def __init__(self):
        self.cursor_instance = _RetryCursor()

    def transaction(self):
        return _Transaction()

    def cursor(self):
        return self.cursor_instance


def test_scheduler_claim_waits_for_retry_backoff_window():
    conn = _RetryConnection()
    result = scheduler_claim(
        conn,
        {
            "job_key": "rhen.research.daily:1.0.1:2026-10-02T20:25:00+00:00",
            "workflow_id": "rhen.research.daily",
            "workflow_version": "1.0.1",
            "scheduler_version": "anevum-scheduler-v1.0.1",
            "scheduled_at": "2026-10-02T20:25:00+00:00",
            "trigger_type": "recovery",
            "max_attempts": 3,
            "allow_retry": True,
            "retry_delay_seconds": 180,
        },
    )

    assert result["claimed"] is False
    assert result["retry_waiting"] is True
    assert result["attempt"] == 1
    assert result["max_attempts"] == 3
    assert result["retry_at"] is not None
