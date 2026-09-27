"""Bounded context for future Coordinator/Verifier consumption."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .integrity import evaluate


def build_snapshot(evidence: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    result = evaluate(evidence, now=now)
    runtime = evidence.get("production_runtime") or {}
    telemetry = evidence.get("telemetry") or {}
    readiness = evidence.get("research_readiness")
    readiness = readiness if isinstance(readiness, dict) else {}
    blockers = readiness.get("blockers")
    blockers = blockers if isinstance(blockers, list) else []
    services = evidence.get("railway_services") or []
    # A deliberately narrow allowlist. Never copy arbitrary service config, payloads or tokens.
    return {
        "schema_version": result["schema_version"],
        "observed_at": result["evaluated_at"],
        "integrity": {"state": result["state"], "reasons": result["reasons"]},
        "production": {
            "strategy_name": runtime.get("strategy_name"),
            "strategy_version_id": runtime.get("strategy_version_id"),
            "source_commit": runtime.get("git_commit"),
            "deployment_id": runtime.get("deployment_id"),
        },
        "services": [
            {"id": service.get("id"), "name": service.get("name"),
             "role": service.get("role"), "deployment_id": service.get("deployment_id"),
             "status": service.get("status")}
            for service in services if isinstance(service, dict)
        ],
        "telemetry": {"last_received_at": telemetry.get("last_received_at")},
        "research_readiness": {
            "state": readiness.get("state"), "cadence": readiness.get("cadence"),
            "blocker_count": readiness.get("blocker_count"),
            "blocker_codes": sorted({row.get("code") for row in blockers
                                     if isinstance(row, dict) and isinstance(row.get("code"), str)}),
            "reason_codes": sorted({code for row in blockers if isinstance(row, dict)
                                    for code in (row.get("reason_codes") if isinstance(row.get("reason_codes"), list) else [])
                                    if isinstance(code, str)}),
            "trigger_reference": readiness.get("trigger_reference"),
            "evidence_cutoff": readiness.get("evidence_cutoff"),
        },
        "reports": {
            "daily_sessions": sorted({row.get("payload", {}).get("session")
                                      for row in evidence.get("daily_reports", [])
                                      if isinstance(row, dict) and isinstance(row.get("payload"), dict)
                                      and isinstance(row["payload"].get("session"), str)}),
            "weekly_key": (evidence.get("weekly_report") or {}).get("report_key"),
        },
        "blocked_dependencies": [item["reference"] or item["source"]
                                 for item in result["reasons"] if item["state"] == "BLOCKED"],
    }
