"""Bounded context for future Coordinator/Verifier consumption."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .integrity import evaluate


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def build_snapshot(evidence: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    result = evaluate(evidence, now=now)
    evidence = evidence if isinstance(evidence, Mapping) else {}
    runtime = evidence.get("production_runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    telemetry = evidence.get("telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    readiness = evidence.get("research_readiness")
    readiness = readiness if isinstance(readiness, dict) else {}
    groups = {
        name: readiness[name] if isinstance(readiness.get(name), list) else []
        for name in ("blockers", "limitations", "monitors")
    }
    def codes(name: str, key: str) -> list[str]:
        return sorted({code for row in groups[name] if isinstance(row, dict)
                       for code in (row.get(key) if isinstance(row.get(key), list) else [row.get(key)])
                       if isinstance(code, str)})
    services = evidence.get("railway_services")
    services = services if isinstance(services, list) else []
    daily_reports = evidence.get("daily_reports")
    daily_reports = daily_reports if isinstance(daily_reports, list) else []
    weekly = evidence.get("weekly_report")
    weekly = weekly if isinstance(weekly, dict) else {}
    # A deliberately narrow allowlist. Never copy arbitrary service config, payloads or tokens.
    return {
        "schema_version": result["schema_version"],
        "observed_at": result["evaluated_at"],
        "integrity": {"state": result["state"], "reasons": result["reasons"]},
        "production": {
            "strategy_name": _text(runtime.get("strategy_name")),
            "strategy_version_id": _text(runtime.get("strategy_version_id")),
            "source_commit": _text(runtime.get("git_commit")),
            "deployment_id": _text(runtime.get("deployment_id")),
        },
        "services": [
            {"id": _text(service.get("id")), "name": _text(service.get("name")),
             "role": _text(service.get("role")), "deployment_id": _text(service.get("deployment_id")),
             "status": _text(service.get("status"))}
            for service in services if isinstance(service, dict)
        ],
        "telemetry": {"last_received_at": _text(telemetry.get("last_received_at"))},
        "research_readiness": {
            "state": _text(readiness.get("state")), "cadence": _text(readiness.get("cadence")),
            **{key: readiness.get(key) if type(readiness.get(key)) is int else None
               for key in ("blocker_count", "limitation_count", "monitor_count",
                           "strategy_question_count", "ready_strategy_question_count",
                           "waiting_strategy_question_count")},
            "blocker_codes": codes("blockers", "code"),
            "reason_codes": codes("blockers", "reason_codes"),
            "limitation_codes": codes("limitations", "code"),
            "limitation_reason_codes": codes("limitations", "reason_codes"),
            "monitor_codes": codes("monitors", "code"),
            "monitor_reason_codes": codes("monitors", "reason_codes"),
            "waiting_requirements": sorted({code for code in readiness.get("waiting_requirements", [])
                                             if isinstance(code, str)})
                                    if isinstance(readiness.get("waiting_requirements"), list) else [],
            "trigger_reference": _text(readiness.get("trigger_reference")),
            "evidence_cutoff": _text(readiness.get("evidence_cutoff")),
        },
        "reports": {
            "daily_sessions": sorted({row["payload"]["session"]
                                      for row in daily_reports
                                      if isinstance(row, dict) and isinstance(row.get("payload"), dict)
                                      and isinstance(row["payload"].get("session"), str)}),
            "weekly_key": _text(weekly.get("report_key")),
        },
        "blocked_dependencies": [item["reference"] or item["source"]
                                 for item in result["reasons"] if item["state"] == "BLOCKED"],
    }
