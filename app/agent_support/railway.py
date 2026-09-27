"""Validate an explicit role map against current Railway source and command.

An old service ID, displayed name, or generated domain cannot grant a role.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROLE_MAP_PATH = Path(__file__).with_name("railway_roles.json")
EXPECTED_ROLES = {"production_trading", "research_agent", "preopen_state", "research_scheduler"}


def load_role_map(path: Path = ROLE_MAP_PATH) -> dict[str, Any]:
    mapping = json.loads(path.read_text(encoding="utf-8"))
    assignments = mapping.get("assignments")
    if mapping.get("schema_version") != "rhen-railway-roles-v1" or not mapping.get("project_id") or not mapping.get("environment_id"):
        raise ValueError("invalid Railway role-map identity")
    if not isinstance(assignments, list) or len(assignments) != 4:
        raise ValueError("four current role assignments required")
    ids = set()
    roles = set()
    for item in assignments:
        if not isinstance(item, dict) or not all(item.get(k) for k in ("service_id", "role", "expected_name", "command_marker")):
            raise ValueError("incomplete Railway role assignment")
        if bool(item.get("source_repo")) == bool(item.get("source_image")):
            raise ValueError("exactly one source identity required")
        ids.add(item["service_id"])
        roles.add(item["role"])
    if len(ids) != len(assignments) or roles != EXPECTED_ROLES:
        raise ValueError("duplicate or missing Railway role assignment")
    return mapping


def normalize_services(
    status: list[dict[str, Any]],
    configs: dict[str, dict[str, Any]],
    *,
    role_map: dict[str, Any] | None = None,
    project_id: str | None = None,
    environment_id: str | None = None,
) -> list[dict[str, Any]]:
    mapping = role_map if role_map is not None else load_role_map()
    if not isinstance(status, list) or not isinstance(configs, dict):
        raise ValueError("Railway status and config observations are required")
    if project_id and mapping["project_id"] != project_id:
        raise ValueError("Railway project differs from maintained role map")
    if environment_id and mapping["environment_id"] != environment_id:
        raise ValueError("Railway environment differs from maintained role map")
    assigned = {row["service_id"]: row for row in mapping["assignments"]}
    normalized = []
    for item in status:
        if not isinstance(item, dict):
            normalized.append({"id": None, "name": None, "role": "unclassified", "mapping_mismatch": True})
            continue
        service_id = item.get("id")
        if not isinstance(service_id, str) or not service_id:
            normalized.append({"id": None, "name": item.get("name"), "role": "unclassified", "mapping_mismatch": True})
            continue
        config = configs.get(service_id, {})
        config = config if isinstance(config, dict) else {}
        deploy = config.get("deploy") or {}
        source = config.get("source") or {}
        deploy = deploy if isinstance(deploy, dict) else {}
        source = source if isinstance(source, dict) else {}
        command = str(config.get("start_command") or deploy.get("startCommand") or "")
        repo = config.get("source_repo") or source.get("repo")
        image = config.get("source_image") or source.get("image")
        cron = config.get("cron_schedule") or deploy.get("cronSchedule") or item.get("cronSchedule")
        assignment = assigned.get(service_id)
        config_matches = bool(
            assignment and assignment["command_marker"] in command
            and (repo == assignment.get("source_repo") if assignment.get("source_repo") else image == assignment.get("source_image"))
            and (not assignment.get("cron_required") or bool(cron))
        )
        normalized.append({
            "id": service_id, "name": item.get("name"),
            "role": assignment["role"] if config_matches else "unclassified",
            "mapped_role": assignment["role"] if assignment else None,
            "mapped_name": assignment["expected_name"] if assignment else None,
            "mapping_mismatch": bool(assignment and not config_matches),
            "start_command": command,
            "status": (item.get("latestDeployment") or {}).get("status"),
            "deployment_id": (item.get("latestDeployment") or {}).get("id"),
            "source_commit": config.get("source_commit") or source.get("commitSha"),
        })
    return normalized
