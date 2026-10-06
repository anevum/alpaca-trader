from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.rhen_core.store import RhenCoreStore


UTC = timezone.utc


def _job() -> dict:
    return {
        "job_key": "rhen.research.daily:1.0.4:2026-10-05T20:25:00+00:00",
        "workflow_id": "rhen.research.daily",
        "workflow_version": "1.0.4",
        "scheduler_version": "anevum-scheduler-v1.0.5",
        "scheduled_at": "2026-10-05T20:25:00+00:00",
        "trigger_type": "schedule",
        "max_attempts": 3,
        "lease_seconds": 1800,
        "allow_retry": True,
        "retry_delay_seconds": 180,
        "details": {"session": "2026-10-05"},
    }


def _age_updated_at(store: RhenCoreStore, seconds: int) -> None:
    stamp = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with store.connect() as conn:
        conn.execute(
            "update scheduler_runs set updated_at=? where job_key=?",
            (stamp, _job()["job_key"]),
        )
        conn.commit()


def test_native_scheduler_ledger_applies_retry_backoff_and_attempt_cap(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-core.db")
    job = _job()

    first = store.scheduler_claim(job)
    assert first["claimed"] is True
    assert first["attempt"] == 1

    store.scheduler_complete(
        {
            "job_key": job["job_key"],
            "status": "FAILED",
            "error_classification": "dependency_unavailable",
            "error_summary": {"message": "temporary"},
        }
    )
    waiting = store.scheduler_claim(job)
    assert waiting["claimed"] is False
    assert waiting["retry_waiting"] is True
    assert waiting["attempt"] == 1

    _age_updated_at(store, 181)
    second = store.scheduler_claim(job)
    assert second["claimed"] is True
    assert second["attempt"] == 2
    assert second["retry"] is True

    store.scheduler_complete(
        {
            "job_key": job["job_key"],
            "status": "FAILED",
            "error_classification": "dependency_unavailable",
            "error_summary": {"message": "temporary again"},
        }
    )
    _age_updated_at(store, 361)
    third = store.scheduler_claim(job)
    assert third["claimed"] is True
    assert third["attempt"] == 3

    store.scheduler_complete(
        {
            "job_key": job["job_key"],
            "status": "FAILED",
            "error_classification": "dependency_unavailable",
            "error_summary": {"message": "still failing"},
        }
    )
    exhausted = store.scheduler_claim(job)
    assert exhausted["claimed"] is False
    assert exhausted["exhausted"] is True
    assert exhausted["attempt"] == 3


def test_native_scheduler_does_not_retry_non_retryable_failure(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-core.db")
    job = _job()

    assert store.scheduler_claim(job)["claimed"] is True
    store.scheduler_complete(
        {
            "job_key": job["job_key"],
            "status": "FAILED",
            "error_classification": "authentication",
            "error_summary": {"message": "bad auth"},
        }
    )

    result = store.scheduler_claim(job)
    assert result["claimed"] is False
    assert result["retryable"] is False
    assert result["attempt"] == 1
