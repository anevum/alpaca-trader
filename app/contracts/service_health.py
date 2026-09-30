"""Wire contract; deliberately imports no runtime, settings, database or broker code."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

class ServiceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["service_observation.v1", "service_heartbeat.v1"] = "service_observation.v1"
    service_id: str
    runtime_kind: Literal["SERVICE", "WORKER", "SUBSYSTEM", "MODULE", "DEPENDENCY"]
    independent_runtime: StrictBool
    service_name: str | None = None
    service_version: str | None = None
    deployment: str | None = None
    revision: str | None = None
    started_at: str | None = None
    observed_at: str
    last_heartbeat_at: str | None = None
    liveness: StrictBool | None = None
    readiness: StrictBool | None = None
    status: Literal["IDLE", "RUNNING", "DEGRADED", "INCIDENT", "OFFLINE", "STALE", "UNKNOWN"]
    current_activity: str | None = None
    last_success: str | None = None
    last_failure: str | None = None
    dependency_state: dict[str, str] = {}
    configuration_identity: str | None = None
    observation_source: str
    scope: str

    @field_validator("observed_at", "started_at", "last_heartbeat_at", "last_success")
    @classmethod
    def timestamp(cls, value):
        if value is None:
            return value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp_requires_timezone")
        return parsed.astimezone(timezone.utc).isoformat()

    @field_validator("service_id")
    @classmethod
    def identifier(cls, value):
        if not value or len(value) > 64 or not value.replace("_", "").isalnum():
            raise ValueError("invalid_service_id")
        return value
