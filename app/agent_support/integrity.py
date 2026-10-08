"""Pure checks over already authorized canonical evidence, with no trading authority.

Input is an explicit, bounded adapter contract; missing critical fields fail closed.
The caller supplies the exchange calendar and current Railway service observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCHEMA_VERSION = "rhen-support-context-v1"
ROLE_COMMANDS = {
    "production_trading": "app.rhen_core.supervisor",
}
KNOWN_ROLES = set(ROLE_COMMANDS) | {
    "shadow_comparison",
    "preopen_state",
    "research_agent",
    "research_scheduler",
}
RANK = {"HEALTHY": 0, "DEGRADED": 1, "BLOCKED": 2}


@dataclass(frozen=True)
class Finding:
    code: str
    state: str
    source: str
    reference: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "state": self.state,
                "source": self.source, "reference": self.reference}


def stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def _list(value: Any) -> list | None:
    return value if isinstance(value, list) else None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def evaluate(evidence: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("evaluation time must have a timezone")
    now = now.astimezone(timezone.utc)
    findings: list[Finding] = []

    def add(code: str, state: str, source: str, reference: str = "") -> None:
        findings.append(Finding(code, state, source, reference))

    if not isinstance(evidence, Mapping) or evidence.get("schema_version") != SCHEMA_VERSION:
        add("SCHEMA_VERSION_DRIFT", "BLOCKED", "context")
        evidence = {}
    services = _list(evidence.get("railway_services"))
    if services is None:
        add("RAILWAY_EVIDENCE_UNAVAILABLE", "BLOCKED", "railway")
        services = []
    by_id: dict[str, dict] = {}
    by_role: dict[str, dict] = {}
    for item in services:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key]
                                                 for key in ("id", "name", "role")):
            add("MALFORMED_SERVICE", "BLOCKED", "railway")
            continue
        service_id, role = item["id"], item["role"]
        if item.get("mapping_mismatch"):
            add("SERVICE_ROLE_MAPPING_DRIFT", "BLOCKED", "railway", str(service_id))
        elif role not in KNOWN_ROLES:
            add("SERVICE_ROLE_UNMAPPED", "DEGRADED", "railway", str(service_id))
        if item.get("mapped_name") and item["name"] != item["mapped_name"]:
            add("SERVICE_NAME_DRIFT", "DEGRADED", "railway", str(service_id))
        if service_id in by_id or (role in KNOWN_ROLES and role in by_role):
            add("DUPLICATE_SERVICE_IDENTITY", "BLOCKED", "railway", str(service_id))
        by_id[service_id] = item
        by_role[role] = item
        command = str(item.get("start_command") or "")
        expected = ROLE_COMMANDS.get(role)
        if expected and expected not in command:
            add("SERVICE_ROLE_COMMAND_MISMATCH", "BLOCKED", "railway", str(service_id))
        if item.get("status") != "SUCCESS":
            add("SERVICE_UNHEALTHY", "BLOCKED" if role == "production_trading" else "DEGRADED", "railway", str(service_id))
        if role == "shadow_comparison" and "app.main:app" not in command:
            add("SERVICE_ROLE_COMMAND_MISMATCH", "BLOCKED", "railway", str(service_id))
    if "production_trading" not in by_role:
        add("REQUIRED_SERVICE_MISSING", "BLOCKED", "railway", "production_trading")
    if "shadow_comparison" in by_role:
        add(
            "UNEXPECTED_PERSISTENT_SHADOW",
            "DEGRADED",
            "railway",
            str(by_role["shadow_comparison"].get("id") or "shadow_comparison"),
        )

    # Consume the Research Agent's canonical readiness verdict; never recreate
    # its report/queue classification or turn READY into an invocation.
    readiness = evidence.get("research_readiness")
    if not isinstance(readiness, dict):
        add("RESEARCH_READINESS_UNAVAILABLE", "BLOCKED", "research_agent")
    else:
        state = readiness.get("state")
        blockers = readiness.get("blockers")
        cutoff = stamp(readiness.get("evidence_cutoff"))
        groups = ("blockers", "limitations", "monitors")
        counts = ("blocker_count", "limitation_count", "monitor_count",
                  "strategy_question_count", "ready_strategy_question_count",
                  "waiting_strategy_question_count")
        requirements = readiness.get("waiting_requirements")
        valid = (
            isinstance(state, str) and state in {"IDLE", "READY", "WAITING", "BLOCKED"}
            and readiness.get("cadence") == "daily"
            and readiness.get("read_only") is True
            and readiness.get("model_invoked") is False
            and readiness.get("persisted") is False
            and type(readiness.get("gpt_would_run_now")) is bool
            and all(type(readiness.get(key)) is int and readiness[key] >= 0 for key in counts)
            and all(isinstance(readiness.get(group), list)
                    and len(readiness[group]) == readiness[f"{group[:-1]}_count"]
                    for group in groups)
            and isinstance(requirements, list)
            and all(isinstance(code, str) for code in requirements)
            and readiness.get("ready_strategy_question_count", 0)
                + readiness.get("waiting_strategy_question_count", 0)
                <= readiness.get("strategy_question_count", -1)
            and cutoff is not None
        )
        if not valid:
            add("RESEARCH_READINESS_MALFORMED", "BLOCKED", "research_agent")
        else:
            if cutoff > now + timedelta(minutes=5):
                add("RESEARCH_READINESS_TIME_INVALID", "BLOCKED", "research_agent")
            if (state == "BLOCKED") != bool(blockers):
                add("RESEARCH_READINESS_INCONSISTENT", "BLOCKED", "research_agent")
            if (state == "READY") != readiness["gpt_would_run_now"]:
                add("RESEARCH_READINESS_INCONSISTENT", "BLOCKED", "research_agent")
            if any(not isinstance(row, dict) or not isinstance(row.get("code"), str)
                   or not isinstance(row.get("reason_codes"), list)
                   or not all(isinstance(code, str) for code in row["reason_codes"])
                   for group in groups for row in readiness[group]):
                add("RESEARCH_READINESS_MALFORMED", "BLOCKED", "research_agent")
            if state == "BLOCKED":
                add("RESEARCH_READINESS_BLOCKED", "BLOCKED", "research_agent",
                    str(readiness.get("trigger_reference") or ""))
            elif state == "WAITING":
                add("RESEARCH_READINESS_WAITING", "DEGRADED", "research_agent",
                    str(readiness.get("trigger_reference") or ""))

    runtime = evidence.get("production_runtime")
    if not isinstance(runtime, dict):
        add("RUNTIME_EVIDENCE_UNAVAILABLE", "BLOCKED", "runtime")
        runtime = {}
    production = by_role.get("production_trading", {})
    if production and runtime:
        if runtime.get("service_id") != production["id"] or runtime.get("service_name") != production["name"]:
            add("SERVICE_IDENTITY_DRIFT", "BLOCKED", "runtime", str(runtime.get("service_id") or ""))
        if runtime.get("deployment_id") and production.get("deployment_id") and runtime["deployment_id"] != production["deployment_id"]:
            add("DEPLOYMENT_MISMATCH", "BLOCKED", "runtime", str(production["id"]))
        if runtime.get("git_commit") and production.get("source_commit") and runtime["git_commit"] != production["source_commit"]:
            add("SOURCE_COMMIT_MISMATCH", "BLOCKED", "runtime", str(production["id"]))
    if not runtime.get("deployment_id") or not runtime.get("git_commit"):
        add("RELEASE_IDENTITY_MISSING", "BLOCKED", "runtime")
    if not runtime.get("strategy_version_id") or not runtime.get("strategy_name"):
        add("STRATEGY_IDENTITY_MISSING", "BLOCKED", "runtime")

    telemetry = evidence.get("telemetry")
    if not isinstance(telemetry, dict):
        add("TELEMETRY_EVIDENCE_UNAVAILABLE", "BLOCKED", "telemetry")
        telemetry = {}
    last_received = stamp(telemetry.get("last_received_at"))
    if last_received is None:
        add("TELEMETRY_TIMESTAMP_MISSING", "BLOCKED", "telemetry")
    elif last_received > now + timedelta(minutes=5):
        add("TIMESTAMP_OUT_OF_SEQUENCE", "BLOCKED", "telemetry")
    elif evidence.get("market_session_active") is True and now - last_received > timedelta(minutes=15):
        add("TELEMETRY_STALE", "BLOCKED", "telemetry")
    if telemetry.get("duplicate_identities", 0):
        add("DUPLICATE_CANONICAL_IDENTITY", "BLOCKED", "telemetry")
    if telemetry.get("schema_version") != "rhen-canonical-telemetry-v1":
        add("TELEMETRY_SCHEMA_DRIFT", "BLOCKED", "telemetry")

    preopen = evidence.get("preopen_state")
    if not isinstance(preopen, dict):
        add("PREOPEN_EVIDENCE_UNAVAILABLE", "DEGRADED", "preopen")
    else:
        if preopen.get("health") is not True:
            add("PREOPEN_UNHEALTHY", "DEGRADED", "preopen")
        expected_after = stamp(preopen.get("expected_after"))
        observed = stamp(preopen.get("last_observed_at"))
        if expected_after and now >= expected_after and (observed is None or observed < expected_after):
            add("PREOPEN_SNAPSHOT_STALE", "DEGRADED", "preopen")

    calendar = _list(evidence.get("expected_sessions"))
    reports = _list(evidence.get("daily_reports"))
    if calendar is None or reports is None:
        add("SESSION_EVIDENCE_UNAVAILABLE", "BLOCKED", "reports")
        calendar, reports = calendar or [], reports or []
    report_sessions: set[str] = set()
    for row in reports:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            add("MALFORMED_DAILY_REPORT", "BLOCKED", "reports")
            continue
        payload = row["payload"]
        session = payload.get("session")
        try:
            date.fromisoformat(session)
        except (TypeError, ValueError):
            add("MALFORMED_DAILY_REPORT", "BLOCKED", "reports")
            continue
        report_sessions.add(session)
        if not row.get("event_id") or not payload.get("strategy_version_id"):
            add("DAILY_REPORT_IDENTITY_MISSING", "BLOCKED", "reports", session)
        occurred = stamp(row.get("occurred_at"))
        generated = stamp(payload.get("generated_at"))
        if occurred is None or (generated and generated > occurred + timedelta(minutes=5)) or occurred > now + timedelta(minutes=5):
            add("REPORT_TIMESTAMP_INVALID", "BLOCKED", "reports", session)
    for session in calendar:
        try:
            day = date.fromisoformat(session)
        except (TypeError, ValueError):
            add("MALFORMED_SESSION_CALENDAR", "BLOCKED", "reports")
            continue
        due = datetime.combine(day, time(16, 50), NY).astimezone(timezone.utc)
        if now >= due and session not in report_sessions:
            add("DAILY_REPORT_MISSING", "DEGRADED", "reports", session)

    weekly = evidence.get("weekly_report")
    if weekly is not None:
        if not isinstance(weekly, dict) or not weekly.get("report_key") or not weekly.get("report_version"):
            add("WEEKLY_REPORT_MALFORMED", "BLOCKED", "reports")
        else:
            expected = weekly.get("expected_trading_sessions")
            included = weekly.get("included_trading_sessions")
            if (not isinstance(expected, list) or not isinstance(included, list)
                or not all(_day(day) is not None for day in [*expected, *included])):
                add("WEEKLY_SESSION_EVIDENCE_MISSING", "BLOCKED", "reports")
            elif set(expected) - set(included) or weekly.get("completeness_state") != "COMPLETE":
                add("WEEKLY_REPORT_INCOMPLETE", "DEGRADED", "reports", str(weekly.get("week_end") or ""))
            generated = stamp(weekly.get("generated_at"))
            if generated is None or generated > now + timedelta(minutes=5):
                add("WEEKLY_REPORT_TIMESTAMP_INVALID", "BLOCKED", "reports")
    elif evidence.get("weekly_report_due") is True:
        add("WEEKLY_REPORT_MISSING", "DEGRADED", "reports")
    if weekly and isinstance(weekly, dict) and evidence.get("weekly_report_due") is True:
        required_end = evidence.get("expected_week_end")
        if required_end and weekly.get("week_end") != required_end:
            add("WEEKLY_REPORT_STALE", "DEGRADED", "reports", str(required_end))

    for artifact in _list(evidence.get("required_research_artifacts")) or []:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("reference"), str) or not artifact["reference"]:
            add("RESEARCH_ARTIFACT_REFERENCE_MALFORMED", "BLOCKED", "research")
        elif artifact.get("available") is not True:
            add("RESEARCH_ARTIFACT_UNAVAILABLE", "DEGRADED", "research", artifact["reference"])
    if evidence.get("evidence_available") is not True:
        add("AGENT_EVIDENCE_UNAVAILABLE", "BLOCKED", "context")

    findings = sorted(set(findings), key=lambda x: (x.code, x.reference, x.source))
    state = max((item.state for item in findings), key=RANK.__getitem__, default="HEALTHY")
    return {"schema_version": SCHEMA_VERSION, "evaluated_at": now.isoformat(),
            "state": state, "reasons": [item.as_dict() for item in findings]}
