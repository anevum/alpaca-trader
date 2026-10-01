from pathlib import Path

from foundation.outbox import DurableEventOutbox


def sample(key: str) -> dict:
    return {
        "event_key": key,
        "event_type": "probe",
        "occurred_at": "2026-10-01T13:00:00+00:00",
        "source": "test",
        "payload": {"key": key},
    }


def test_outbox_enqueue_is_idempotent(tmp_path: Path):
    path = tmp_path / "outbox.sqlite3"
    outbox = DurableEventOutbox(path)
    assert outbox.enqueue(sample("a")) is True
    assert outbox.enqueue(sample("a")) is False
    assert outbox.stats()["queued"] == 1


def test_outbox_survives_reopen(tmp_path: Path):
    path = tmp_path / "outbox.sqlite3"
    DurableEventOutbox(path).enqueue(sample("persisted"))

    reopened = DurableEventOutbox(path)
    batch = reopened.claim_batch()
    assert batch is not None
    assert [event["event_key"] for event in batch.events] == ["persisted"]


def test_outbox_acknowledge_removes_only_claimed_rows(tmp_path: Path):
    outbox = DurableEventOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(sample("a"))
    outbox.enqueue(sample("b"))
    batch = outbox.claim_batch(limit=1)
    assert batch is not None
    assert outbox.acknowledge(batch) == 1
    assert outbox.stats()["queued"] == 1


def test_outbox_failure_keeps_evidence(tmp_path: Path):
    outbox = DurableEventOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(sample("retry"))
    batch = outbox.claim_batch()
    assert batch is not None
    assert outbox.fail(batch, "destination unavailable") == 1
    stats = outbox.stats()
    assert stats["queued"] == 1
    assert stats["max_attempts"] == 1
