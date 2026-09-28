"""Bounded, versioned input and output types; no infrastructure clients."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from app.agent_support.integrity import stamp

INPUT_VERSION = "rhen-verifier-input-v1"
OUTPUT_VERSION = "rhen-verifier-result-v1"
PROFILES = frozenset({
    "repository", "release_deployment", "research_readiness", "agent_support",
    "telemetry_reports", "migration_artifacts", "activation_gates",
})
# Allowlisted, sanitized fields only. Source adapters must remove all other data.
FIELDS = {
    "github": {"repository", "main_sha", "branch_sha", "main_contains", "branch_contains", "pr"},
    "release": {"release_id", "commit_sha", "strategy_name", "strategy_version_id"},
    "deployment": {"deployment_id", "source_commit", "release_id", "strategy_name", "strategy_version_id"},
    "runtime": {"service_id", "service_name", "deployment_id", "source_commit", "healthy"},
    "broker_account": {"account_blocked", "trading_blocked", "status", "trading_eligible", "equity"},
    "railway": {"services", "project_id", "environment_id"},
    "readiness": {"state", "blocker_count", "blocker_codes", "limitation_codes", "limitation_reason_codes",
                  "monitor_codes", "monitor_reason_codes", "waiting_requirements", "evidence_cutoff"},
    "support": {"schema_version", "observed_at", "integrity", "production", "services", "telemetry",
                "research_readiness", "reports", "blocked_dependencies"},
    "telemetry": {"last_received_at", "duplicate_identities", "schema_version"},
    "reports": {"daily_sessions", "daily_due_sessions", "weekly_key", "weekly_due", "weekly_complete",
                "weekly_generated_at"},
    "migration": {"migration_id", "artifact_present", "live_state"},
    "artifacts": {"required", "present"},
    "activation": {"gates"},
}
ASSERTIONS = frozenset({"repository", "main_sha", "change_commit", "change_scope", "pr_number",
                       "release_id", "deployment_id", "source_commit", "strategy_name", "strategy_version_id",
                       "readiness", "readiness_nonblocking", "migration_id", "migration_applied",
                       "required_artifacts", "required_roles", "max_telemetry_age_seconds",
                       "runtime_healthy", "broker_account_machine_verified"})


def _freeze(value: Any) -> Any:
    """Recursively seal values embedded in the immutable result contract."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a JSON-friendly copy without exposing mutable result state."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _safe(value: Any, *, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or type(value) in (bool, int, float):
        return True
    if isinstance(value, str):
        return len(value) <= 2048
    if isinstance(value, list):
        return len(value) <= 128 and all(_safe(v, depth=depth + 1) for v in value)
    if isinstance(value, dict):
        return len(value) <= 64 and all(isinstance(k, str) and len(k) <= 80 and
                                         not any(s in k.lower() for s in ("secret", "token", "password", "credential", "api_key"))
                                         and _safe(v, depth=depth + 1) for k, v in value.items())
    return False


@dataclass(frozen=True)
class Evidence:
    section: str
    source_id: str
    source_version: str
    observed_at: str
    reference: str
    authority: str  # CANONICAL, OPERATOR_ATTESTED, REPORTED, UNAVAILABLE
    data: dict[str, Any]

    @classmethod
    def parse(cls, section: str, raw: Mapping[str, Any]) -> "Evidence":
        if section not in FIELDS or not isinstance(raw, Mapping):
            raise ValueError("unknown evidence section or malformed record")
        if set(raw) != {"source_id", "source_version", "observed_at", "reference", "authority", "data"}:
            raise ValueError("evidence metadata contract mismatch")
        for key in ("source_id", "source_version", "reference"):
            if not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 256:
                raise ValueError(f"invalid evidence {key}")
        if stamp(raw["observed_at"]) is None:
            raise ValueError("invalid evidence timestamp")
        if raw["authority"] not in ("CANONICAL", "OPERATOR_ATTESTED", "REPORTED", "UNAVAILABLE"):
            raise ValueError("invalid evidence authority")
        data = raw["data"]
        if not isinstance(data, dict) or set(data) - FIELDS[section] or not _safe(data):
            raise ValueError("unbounded or malformed evidence data")
        return cls(section, raw["source_id"], raw["source_version"], raw["observed_at"],
                   raw["reference"], raw["authority"], dict(data))


@dataclass(frozen=True)
class VerificationRequest:
    verification_id: str
    environment: str
    profile: str
    subject: str
    assertions: dict[str, Any]
    evidence: dict[str, tuple[Evidence, ...]]

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "VerificationRequest":
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "verification_id", "environment",
                                                        "profile", "subject", "assertions", "evidence"}:
            raise ValueError("verification input contract mismatch")
        if raw["schema_version"] != INPUT_VERSION or raw["profile"] not in PROFILES:
            raise ValueError("unsupported verification input/profile")
        for key in ("verification_id", "environment", "subject"):
            if not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 256:
                raise ValueError(f"invalid {key}")
        assertions = raw["assertions"]
        if not isinstance(assertions, dict) or set(assertions) - ASSERTIONS or not _safe(assertions):
            raise ValueError("unbounded assertions")
        records = raw["evidence"]
        if not isinstance(records, dict) or set(records) - set(FIELDS) or len(records) > len(FIELDS):
            raise ValueError("unknown evidence section")
        parsed = {}
        for section, rows in records.items():
            if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
                raise ValueError("evidence section must contain one to four observations")
            parsed[section] = tuple(Evidence.parse(section, row) for row in rows)
        return cls(raw["verification_id"], raw["environment"], raw["profile"], raw["subject"],
                   dict(assertions), parsed)


@dataclass(frozen=True)
class Invariant:
    code: str
    expected: Any
    observed: Any
    canonical_severity: str  # NONE, DEGRADED, BLOCKED; never raised by explanation
    status: str  # PASS, FAIL, DEGRADED, MISSING, CONTRADICTORY
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", _freeze(self.expected))
        object.__setattr__(self, "observed", _freeze(self.observed))


@dataclass(frozen=True)
class VerificationResult:
    schema_version: str
    verification_id: str
    timestamp: str
    environment: str
    profile: str
    subject: str
    assertions: Mapping[str, Any]
    verdict: str
    invariant_matrix: tuple[Invariant, ...]
    failed_invariants: tuple[str, ...]
    degraded_findings: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    contradictory_evidence: tuple[str, ...]
    evidence_references: tuple[str, ...]
    evidence_sources: tuple[Mapping[str, str], ...]
    repository_sha: str | None
    release_id: str | None
    strategy_version_id: str | None
    warnings: tuple[str, ...]
    recommended_human_next_action: str
    re_verification_required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "assertions", _freeze(self.assertions))
        object.__setattr__(self, "evidence_sources", tuple(_freeze(row) for row in self.evidence_sources))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verification_id": self.verification_id,
            "timestamp": self.timestamp,
            "environment": self.environment,
            "profile": self.profile,
            "subject": self.subject,
            "assertions": _thaw(self.assertions),
            "verdict": self.verdict,
            "invariant_matrix": [
                {
                    "code": row.code,
                    "expected": _thaw(row.expected),
                    "observed": _thaw(row.observed),
                    "canonical_severity": row.canonical_severity,
                    "status": row.status,
                    "evidence_references": list(row.evidence_references),
                }
                for row in self.invariant_matrix
            ],
            "failed_invariants": list(self.failed_invariants),
            "degraded_findings": list(self.degraded_findings),
            "missing_evidence": list(self.missing_evidence),
            "contradictory_evidence": list(self.contradictory_evidence),
            "evidence_references": list(self.evidence_references),
            "evidence_sources": _thaw(self.evidence_sources),
            "repository_sha": self.repository_sha,
            "release_id": self.release_id,
            "strategy_version_id": self.strategy_version_id,
            "warnings": list(self.warnings),
            "recommended_human_next_action": self.recommended_human_next_action,
            "re_verification_required": self.re_verification_required,
        }


def utc_time(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("verification time must have a timezone")
    return now.astimezone(timezone.utc)
