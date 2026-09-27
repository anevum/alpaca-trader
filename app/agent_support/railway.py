"""Normalize live Railway observations by source and command, not service name."""
from __future__ import annotations

from typing import Any


def normalize_services(status: list[dict[str, Any]], configs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(status, list) or not isinstance(configs, dict):
        raise ValueError("Railway status and config observations are required")
    normalized = []
    main_services = []
    for item in status:
        service_id = item.get("id")
        config = configs.get(service_id, {})
        command = str(config.get("start_command") or "")
        if "app.research_agent.service:app" in command:
            role = "research_agent"
        elif "app.preopen_state.service:app" in command:
            role = "preopen_state"
        elif "app.main:app" in command:
            role = None
            main_services.append(service_id)
        else:
            role = "unclassified"
        normalized.append({
            "id": service_id, "name": item.get("name"), "role": role,
            "start_command": command,
            "status": (item.get("latestDeployment") or {}).get("status"),
            "deployment_id": (item.get("latestDeployment") or {}).get("id"),
            "source_commit": config.get("source_commit"),
        })
    # A lone trading runtime is production; with multiple app.main services,
    # scan-only value must be explicitly observed before roles can be assigned.
    if len(main_services) == 1:
        next(row for row in normalized if row["id"] == main_services[0])["role"] = "production_trading"
    elif main_services:
        for row in normalized:
            if row["id"] not in main_services:
                continue
            scan_only = configs[row["id"]].get("scan_only")
            if isinstance(scan_only, bool):
                row["role"] = "shadow_comparison" if scan_only else "production_trading"
            else:
                row["role"] = "unclassified"
    return normalized
