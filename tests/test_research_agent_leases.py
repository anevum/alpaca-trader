from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.research_agent.leases import InMemoryLeaseManager


RUN_1 = UUID("00000000-0000-0000-0000-000000000001")
RUN_2 = UUID("00000000-0000-0000-0000-000000000002")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 27, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def test_first_holder_acquires_and_second_cannot_steal_active_lease():
    clock = Clock()
    manager = InMemoryLeaseManager(clock)
    assert manager.acquire(lease_key="weekly", holder_run_id=RUN_1) is not None
    assert manager.acquire(lease_key="weekly", holder_run_id=RUN_2) is None
    assert manager.get("weekly").holder_run_id == RUN_1


def test_only_current_holder_can_heartbeat_renew_and_release():
    clock = Clock()
    manager = InMemoryLeaseManager(clock)
    lease = manager.acquire(lease_key="weekly", holder_run_id=RUN_1)
    clock.now += timedelta(minutes=5)
    assert manager.heartbeat(lease_key="weekly", holder_run_id=RUN_2) is False
    assert manager.heartbeat(lease_key="weekly", holder_run_id=RUN_1) is True
    assert manager.get("weekly").expires_at > lease.expires_at
    assert manager.renew(lease_key="weekly", holder_run_id=RUN_1) is True
    assert manager.release(lease_key="weekly", holder_run_id=RUN_2) is False
    assert manager.release(lease_key="weekly", holder_run_id=RUN_1) is True


def test_expired_lease_can_be_reclaimed_with_takeover_identity():
    clock = Clock()
    manager = InMemoryLeaseManager(clock)
    manager.acquire(
        lease_key="weekly",
        holder_run_id=RUN_1,
        duration=timedelta(minutes=30),
    )
    clock.now += timedelta(minutes=31)
    lease = manager.acquire(lease_key="weekly", holder_run_id=RUN_2)
    assert lease is not None
    assert lease.holder_run_id == RUN_2
    assert lease.takeover_from_run_id == RUN_1


def test_expired_lease_rejects_mismatched_explicit_takeover_reference():
    clock = Clock()
    manager = InMemoryLeaseManager(clock)
    manager.acquire(lease_key="weekly", holder_run_id=RUN_1)
    clock.now += timedelta(minutes=31)
    assert (
        manager.acquire(
            lease_key="weekly",
            holder_run_id=RUN_2,
            takeover_from_run_id=RUN_2,
        )
        is None
    )

