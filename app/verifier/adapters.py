"""Pure sanitizers for authorized upstream observations; no fetchers or credentials."""
from __future__ import annotations

from typing import Any

from app.agent_support.railway import normalize_services


def bounded_railway_roles(status: list[dict[str, Any]], configs: dict[str, dict[str, Any]],
                          *, project_id: str, environment_id: str) -> dict[str, Any]:
    """Use the maintained role map and source/command checks, then discard configs.

    This adapter does not establish provenance: the authorized caller must attach
    a current CANONICAL evidence envelope, or the engine will mark it inconclusive.
    """
    services = normalize_services(status, configs, project_id=project_id, environment_id=environment_id)
    keys = ("id", "name", "role", "mapped_name", "mapped_role", "mapping_mismatch", "status",
            "deployment_id", "source_commit")
    return {"project_id": project_id, "environment_id": environment_id,
            "services": [{**{key: service.get(key) for key in keys},
                          "source_command_validated": service.get("role") != "unclassified"
                          and service.get("mapping_mismatch") is False}
                         for service in services]}


def bounded_readiness(sanitized: dict[str, Any]) -> dict[str, Any]:
    """Copy the Research Agent's sanitized verdict; never classify its raw queue."""
    keys = ("state", "blocker_count", "blocker_codes", "limitation_codes", "limitation_reason_codes",
            "monitor_codes", "monitor_reason_codes", "waiting_requirements", "evidence_cutoff")
    return {key: sanitized.get(key) for key in keys}
