from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from typing import Mapping
from uuid import uuid4


RHEN_VERSION = "0.8.2"


def _value(env: Mapping[str, str], name: str) -> str | None:
    value = str(env.get(name, "") or "").strip()
    return value or None


@dataclass(frozen=True)
class RuntimeProvenance:
    runtime_instance_id: str
    runtime_started_at: str
    system: str
    system_version: str
    source: str
    git_commit: str | None
    branch: str | None
    repository: str | None
    deployment_id: str | None
    snapshot_id: str | None
    project_id: str | None
    project_name: str | None
    environment_id: str | None
    environment_name: str | None
    service_id: str | None
    service_name: str | None
    replica_id: str | None
    replica_region: str | None
    metadata_quality: str
    missing_critical_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["missing_critical_fields"] = list(self.missing_critical_fields)
        return payload


def capture_runtime_provenance(
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    instance_id: str | None = None,
) -> RuntimeProvenance:
    values = env or os.environ
    owner = _value(values, "RAILWAY_GIT_REPO_OWNER")
    repo = _value(values, "RAILWAY_GIT_REPO_NAME")
    repository = f"{owner}/{repo}" if owner and repo else None

    git_commit = _value(values, "RAILWAY_GIT_COMMIT_SHA")
    deployment_id = _value(values, "RAILWAY_DEPLOYMENT_ID")
    missing = tuple(
        name
        for name, value in (
            ("git_commit", git_commit),
            ("deployment_id", deployment_id),
        )
        if value is None
    )

    railway_present = any(
        _value(values, name)
        for name in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_DEPLOYMENT_ID",
        )
    )

    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        raise ValueError("runtime start timestamp must include a timezone")
    started = started.astimezone(timezone.utc)

    return RuntimeProvenance(
        runtime_instance_id=instance_id or str(uuid4()),
        runtime_started_at=started.isoformat(),
        system="RHEN",
        system_version=RHEN_VERSION,
        source="railway" if railway_present else "unknown",
        git_commit=git_commit,
        branch=_value(values, "RAILWAY_GIT_BRANCH"),
        repository=repository,
        deployment_id=deployment_id,
        snapshot_id=_value(values, "RAILWAY_SNAPSHOT_ID"),
        project_id=_value(values, "RAILWAY_PROJECT_ID"),
        project_name=_value(values, "RAILWAY_PROJECT_NAME"),
        environment_id=_value(values, "RAILWAY_ENVIRONMENT_ID"),
        environment_name=_value(values, "RAILWAY_ENVIRONMENT_NAME"),
        service_id=_value(values, "RAILWAY_SERVICE_ID"),
        service_name=_value(values, "RAILWAY_SERVICE_NAME"),
        replica_id=_value(values, "RAILWAY_REPLICA_ID"),
        replica_region=_value(values, "RAILWAY_REPLICA_REGION"),
        metadata_quality="complete" if not missing else "partial",
        missing_critical_fields=missing,
    )


def classify_runtime_transition(
    previous: RuntimeProvenance | None,
    current: RuntimeProvenance,
) -> str:
    if previous is None:
        return "initial_start"
    if (
        previous.deployment_id
        and current.deployment_id
        and previous.deployment_id != current.deployment_id
    ):
        return "redeploy"
    if (
        previous.deployment_id
        and current.deployment_id
        and previous.deployment_id == current.deployment_id
        and previous.runtime_instance_id != current.runtime_instance_id
    ):
        return "restart"
    if previous.runtime_instance_id == current.runtime_instance_id:
        return "same_instance"
    return "unknown"
