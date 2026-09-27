"""Convert existing report-read and Railway observations to the bounded contract.

The caller obtains `latest=command`, weekly period inputs, the real exchange
calendar, and Railway status/config through existing authenticated read paths.
No research record body is required or copied to the output.
"""
from __future__ import annotations

from typing import Any

from .integrity import SCHEMA_VERSION
from .railway import normalize_services


def from_canonical_sources(
    *,
    command_evidence: dict[str, Any],
    period_inputs: dict[str, Any],
    expected_sessions: list[str],
    railway_status: list[dict[str, Any]],
    railway_configs: dict[str, dict[str, Any]],
    railway_project_id: str,
    railway_environment_id: str,
    preopen_health: dict[str, Any],
    preopen_expected_after: str | None,
    market_session_active: bool,
    weekly_report_due: bool,
    expected_week_end: str | None = None,
) -> dict[str, Any]:
    if command_evidence.get("evidence_version") != "rhen-command-evidence-v1":
        raise ValueError("canonical command evidence version unavailable")
    runtime = (command_evidence.get("provenance") or {}).get("runtime") or {}
    scan = (command_evidence.get("provenance") or {}).get("latest_scan_cycle") or {}
    versions = period_inputs.get("strategy_versions") or []
    active = [row for row in versions if row.get("version_id") == runtime.get("strategy_version_id")]
    health = command_evidence.get("telemetry_health") or {}
    duplicates = period_inputs.get("duplicate_checks") or {}
    last_snapshot = preopen_health.get("last_snapshot") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "railway_services": normalize_services(
            railway_status, railway_configs,
            project_id=railway_project_id, environment_id=railway_environment_id,
        ),
        "production_runtime": {
            "service_id": runtime.get("service_id"), "service_name": runtime.get("service_name"),
            "deployment_id": runtime.get("deployment_id"), "git_commit": runtime.get("git_commit"),
            "strategy_version_id": runtime.get("strategy_version_id"),
            "strategy_name": active[0].get("strategy_name") if len(active) == 1 else None,
        },
        "telemetry": {
            "schema_version": "rhen-canonical-telemetry-v1" if isinstance(health, dict) and health.get("latest_received_at") else None,
            "last_received_at": health.get("latest_received_at"),
            "duplicate_identities": sum(int(value) for value in duplicates.values() if isinstance(value, int)),
        },
        "preopen_state": {
            "health": preopen_health.get("ok") is True,
            "last_observed_at": last_snapshot.get("observed_at"),
            "expected_after": preopen_expected_after,
        },
        "expected_sessions": expected_sessions,
        "daily_reports": period_inputs.get("daily_reports"),
        "weekly_report": command_evidence.get("latest_weekly"),
        "weekly_report_due": weekly_report_due,
        "expected_week_end": expected_week_end,
        "required_research_artifacts": [],
        "evidence_available": bool(runtime and scan and isinstance(period_inputs.get("daily_reports"), list)),
        "market_session_active": market_session_active,
    }
