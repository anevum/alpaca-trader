from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import UUID

from .models import ResearchLease


DEFAULT_LEASE_DURATION = timedelta(minutes=30)
HEARTBEAT_INTERVAL = timedelta(minutes=5)
SQL_FUNCTIONS = {
    "acquire": "private.rhen_research_lease_acquire",
    "heartbeat": "private.rhen_research_lease_heartbeat",
    "renew": "private.rhen_research_lease_renew",
    "release": "private.rhen_research_lease_release",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryLeaseManager:
    """Thread-safe behavioral model matching the atomic PostgreSQL primitives."""

    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._leases: dict[str, ResearchLease] = {}
        self._lock = RLock()

    def acquire(
        self,
        *,
        lease_key: str,
        holder_run_id: UUID,
        operator_identity: str | None = None,
        duration: timedelta = DEFAULT_LEASE_DURATION,
        scope: Mapping[str, Any] | None = None,
        takeover_from_run_id: UUID | None = None,
    ) -> ResearchLease | None:
        if not lease_key.strip():
            raise ValueError("lease_key is required")
        if duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        now = self._clock()
        with self._lock:
            existing = self._leases.get(lease_key)
            if existing is not None and existing.expires_at > now:
                return None
            if (
                existing is not None
                and takeover_from_run_id is not None
                and takeover_from_run_id != existing.holder_run_id
            ):
                return None
            takeover = None
            if existing is not None and existing.holder_run_id != holder_run_id:
                takeover = takeover_from_run_id or existing.holder_run_id
            lease = ResearchLease(
                lease_key=lease_key,
                holder_run_id=holder_run_id,
                operator_identity=operator_identity,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + duration,
                scope=dict(scope or {}),
                takeover_from_run_id=takeover,
            )
            self._leases[lease_key] = lease
            return lease

    def heartbeat(
        self,
        *,
        lease_key: str,
        holder_run_id: UUID,
        duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> bool:
        if duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        now = self._clock()
        with self._lock:
            lease = self._leases.get(lease_key)
            if (
                lease is None
                or lease.holder_run_id != holder_run_id
                or lease.expires_at <= now
            ):
                return False
            self._leases[lease_key] = replace(
                lease,
                heartbeat_at=now,
                expires_at=now + duration,
            )
            return True

    def renew(
        self,
        *,
        lease_key: str,
        holder_run_id: UUID,
        duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> bool:
        return self.heartbeat(
            lease_key=lease_key,
            holder_run_id=holder_run_id,
            duration=duration,
        )

    def release(self, *, lease_key: str, holder_run_id: UUID) -> bool:
        with self._lock:
            lease = self._leases.get(lease_key)
            if lease is None or lease.holder_run_id != holder_run_id:
                return False
            del self._leases[lease_key]
            return True

    def get(self, lease_key: str) -> ResearchLease | None:
        with self._lock:
            return self._leases.get(lease_key)


def acquire_parameters(
    *,
    lease_key: str,
    holder_run_id: UUID,
    operator_identity: str | None = None,
    duration: timedelta = DEFAULT_LEASE_DURATION,
    scope: Mapping[str, Any] | None = None,
    takeover_from_run_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "p_lease_key": lease_key,
        "p_holder_run_id": str(holder_run_id),
        "p_operator_identity": operator_identity,
        "p_ttl": f"{int(duration.total_seconds())} seconds",
        "p_scope": dict(scope or {}),
        "p_takeover_from_run_id": (
            str(takeover_from_run_id) if takeover_from_run_id else None
        ),
    }
