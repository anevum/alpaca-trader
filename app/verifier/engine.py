"""Deterministic, side-effect-free verifier over bounded observations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.agent_support.integrity import SCHEMA_VERSION as SUPPORT_VERSION, stamp
from app.agent_support.railway import load_role_map

from .models import INPUT_VERSION, OUTPUT_VERSION, Evidence, Invariant, VerificationRequest, VerificationResult, utc_time

CANONICAL_REPO = "anevum/alpaca-trader"
MAX_SOURCE_AGE = timedelta(hours=24)
REQUIRED_ROLES = ("production_trading", "preopen_state", "research_agent", "research_scheduler")


class Checks:
    def __init__(self, request: VerificationRequest, now: datetime):
        self.request, self.now = request, now
        self.rows: list[Invariant] = []

    def add(self, code: str, expected: Any, observed: Any, status: str,
            severity: str = "NONE", refs: tuple[str, ...] = ()) -> None:
        self.rows.append(Invariant(code, expected, observed, severity, status, refs))

    def source(self, section: str) -> Evidence | None:
        rows = self.request.evidence.get(section, ())
        canonical = [r for r in rows if r.authority == "CANONICAL" and
                     stamp(r.observed_at) <= self.now + timedelta(minutes=5) and
                     self.now - stamp(r.observed_at) <= MAX_SOURCE_AGE]
        if len(canonical) > 1 and any(r.data != canonical[0].data for r in canonical[1:]):
            self.add(f"{section.upper()}_EVIDENCE_CONTRADICTORY", "consistent canonical state",
                     "conflicting canonical observations", "CONTRADICTORY", refs=tuple(r.reference for r in canonical))
            return None
        if canonical:
            return canonical[0]
        reason = "reported only" if any(r.authority == "REPORTED" for r in rows) else (
            "stale or future observation" if rows and any(r.authority == "CANONICAL" for r in rows)
            else "unavailable")
        self.add(f"{section.upper()}_EVIDENCE_MISSING", "current canonical observation", reason,
                 "MISSING", refs=tuple(r.reference for r in rows))
        return None

    def match(self, code: str, expected: Any, observed: Any, ref: str, *, severity: str = "BLOCKED") -> None:
        if observed is None:
            self.add(code, expected, None, "MISSING", refs=(ref,))
        else:
            self.add(code, expected, observed, "PASS" if expected == observed else "FAIL",
                     "NONE" if expected == observed else severity, (ref,))


def _github(c: Checks) -> None:
    row = c.source("github")
    if row is None:
        return
    d, a = row.data, c.request.assertions
    c.match("REPOSITORY_IDENTITY", CANONICAL_REPO, d.get("repository"), row.reference)
    if "repository" in a:
        c.match("CLAIMED_REPOSITORY", a["repository"], d.get("repository"), row.reference)
    if not isinstance(d.get("main_sha"), str) or len(d["main_sha"]) != 40:
        c.add("CANONICAL_MAIN_MISSING", "40-character main SHA", d.get("main_sha"), "MISSING", refs=(row.reference,))
    elif "main_sha" in a:
        c.match("MAIN_SHA", a["main_sha"], d["main_sha"], row.reference)
    scope, commit = a.get("change_scope"), a.get("change_commit")
    if scope is not None or commit is not None:
        if scope not in ("branch", "pr", "main", "production") or not isinstance(commit, str):
            c.add("CHANGE_CLAIM_MALFORMED", "scope and commit", (scope, commit), "MISSING", refs=(row.reference,))
        else:
            field = "main_contains" if scope in ("main", "production") else "branch_contains"
            contained = d.get(field)
            if not isinstance(contained, list):
                c.add("CHANGE_CONTAINMENT_UNKNOWN", commit, None, "MISSING", refs=(row.reference,))
            else:
                c.match("CHANGE_ON_CANONICAL_MAIN" if scope in ("main", "production") else "CHANGE_ON_BRANCH",
                        True, commit in contained, row.reference)
            if scope in ("pr", "main", "production") and (scope == "pr" or "pr_number" in a or "pr" in d):
                pr = d.get("pr")
                if not isinstance(pr, dict) or not all(k in pr for k in ("number", "state", "draft", "merged", "head_sha")):
                    c.add("PR_STATE_UNKNOWN", "complete PR state", pr, "MISSING", refs=(row.reference,))
                else:
                    if "pr_number" in a:
                        c.match("PR_IDENTITY", a["pr_number"], pr["number"], row.reference)
                    if scope in ("main", "production"):
                        c.match("PR_MERGED", True, pr["merged"] is True and pr["state"] == "closed", row.reference)
                    elif scope == "pr":
                        c.match("CHANGE_ON_PR_HEAD", commit, pr["head_sha"], row.reference)
            if scope == "production":
                # Repository containment is necessary but never proof of rollout.
                _release(c)
                deployed = c.source("deployment")
                if deployed is not None:
                    c.match("CHANGE_DEPLOYED", commit, deployed.data.get("source_commit"), deployed.reference)
    # An open/draft PR never grants main identity even when it contains the change.


def _railway(c: Checks) -> None:
    row = c.source("railway")
    if row is None:
        return
    services = row.data.get("services")
    if not isinstance(services, list) or any(not isinstance(s, dict) for s in services):
        c.add("RAILWAY_SERVICES_MALFORMED", "normalized service list", services, "MISSING", refs=(row.reference,))
        return
    mapping = load_role_map()
    if row.data.get("project_id") != mapping["project_id"] or row.data.get("environment_id") != mapping["environment_id"]:
        c.add("RAILWAY_TOPOLOGY_IDENTITY_DRIFT", (mapping["project_id"], mapping["environment_id"]),
              (row.data.get("project_id"), row.data.get("environment_id")), "FAIL", "BLOCKED", (row.reference,))
    assignments = {s["service_id"]: s for s in mapping["assignments"]}
    ids = [s.get("id") for s in services]
    roles = [s.get("role") for s in services if s.get("role") in (*REQUIRED_ROLES, "shadow_comparison")]
    c.match("DUPLICATE_CANONICAL_IDENTITY", False,
            len(ids) != len(set(ids)) or len(roles) != len(set(roles)), row.reference)
    for s in services:
        assigned = assignments.get(s.get("id"))
        if s.get("role") in REQUIRED_ROLES and (not assigned or assigned["role"] != s.get("role")
                                                   or s.get("source_command_validated") is not True):
            c.add("SERVICE_ROLE_UNVERIFIED", "maintained map plus current source/command", s.get("id"),
                  "FAIL", "BLOCKED", (row.reference,))
        if s.get("mapping_mismatch") is True:
            c.add("SERVICE_ROLE_MAPPING_DRIFT", "maintained map plus source and command match",
                  s.get("id"), "FAIL", "BLOCKED", (row.reference,))
        if s.get("role") in REQUIRED_ROLES and s.get("mapped_name") and s.get("name") != s["mapped_name"]:
            c.add("SERVICE_NAME_DRIFT", s["mapped_name"], s.get("name"), "DEGRADED", "DEGRADED", (row.reference,))
    for role in c.request.assertions.get("required_roles", REQUIRED_ROLES):
        if role not in (*REQUIRED_ROLES, "shadow_comparison"):
            c.add("ROLE_CLAIM_UNKNOWN", "known semantic role", role, "MISSING", refs=(row.reference,))
            continue
        present = role in roles
        severity = "DEGRADED" if role in ("research_agent", "research_scheduler", "shadow_comparison") else "BLOCKED"
        code = "SHADOW_SERVICE_UNRESOLVED" if role == "shadow_comparison" and not present else "REQUIRED_SERVICE_MISSING"
        c.add(code if not present else f"{role.upper()}_PRESENT", True, present,
              "PASS" if present else ("DEGRADED" if severity == "DEGRADED" else "FAIL"),
              "NONE" if present else severity, (row.reference,))


def _release(c: Checks) -> None:
    release, deployment = c.source("release"), c.source("deployment")
    if release is None or deployment is None:
        return
    a, r, d = c.request.assertions, release.data, deployment.data
    for key, code in (("release_id", "RELEASE_IDENTITY"), ("strategy_name", "STRATEGY_NAME"),
                      ("strategy_version_id", "STRATEGY_VERSION")):
        if key in a:
            c.match(code, a[key], r.get(key), release.reference)
        c.match(f"{code}_DEPLOYED", r.get(key), d.get(key), deployment.reference)
    c.match("DEPLOYMENT_SOURCE_COMMIT", r.get("commit_sha"), d.get("source_commit"), deployment.reference)
    for key, code in (("deployment_id", "DEPLOYMENT_IDENTITY"), ("source_commit", "CLAIMED_SOURCE_COMMIT")):
        if key in a:
            c.match(code, a[key], d.get(key), deployment.reference)
    if not d.get("deployment_id"):
        c.add("DEPLOYMENT_ID_MISSING", "current deployment ID", None, "MISSING", refs=(deployment.reference,))
    _railway(c)


def _readiness(c: Checks) -> None:
    row = c.source("readiness")
    if row is None:
        return
    d, a = row.data, c.request.assertions
    state, count, blockers = d.get("state"), d.get("blocker_count"), d.get("blocker_codes")
    if state not in ("IDLE", "READY", "WAITING", "BLOCKED") or type(count) is not int or count < 0 or not isinstance(blockers, list) or len(blockers) != count:
        c.add("READINESS_MALFORMED", "sanitized canonical readiness", d, "MISSING", refs=(row.reference,))
        return
    if (state == "BLOCKED") != bool(count):
        c.add("READINESS_CONTRADICTORY", "BLOCKED iff canonical blockers", (state, count), "CONTRADICTORY", refs=(row.reference,))
        return
    for key in ("limitation_codes", "monitor_codes", "waiting_requirements"):
        if not isinstance(d.get(key), list) or not all(isinstance(v, str) for v in d[key]):
            c.add("READINESS_MALFORMED", f"{key} list", d.get(key), "MISSING", refs=(row.reference,))
            return
    if "readiness" in a:
        c.match("READINESS_CLAIM", a["readiness"], state, row.reference)
    if a.get("readiness_nonblocking", True):
        c.match("RESEARCH_READINESS_BLOCKED", False, state == "BLOCKED", row.reference)
    # Limitations, monitors, and waiting requirements are canonical context, not blockers.
    if state == "WAITING" and count == 0:
        c.add("RESEARCH_READINESS_WAITING_NONBLOCKING", "WAITING with no blockers", state,
              "PASS", refs=(row.reference,))


def _support(c: Checks) -> None:
    row = c.source("support")
    if row is None:
        return
    d = row.data
    integrity = d.get("integrity")
    if d.get("schema_version") != SUPPORT_VERSION or stamp(d.get("observed_at")) is None or not isinstance(integrity, dict) or integrity.get("state") not in ("HEALTHY", "DEGRADED", "BLOCKED") or not isinstance(integrity.get("reasons"), list):
        c.add("SUPPORT_SNAPSHOT_MALFORMED", "bounded Agent Support snapshot", d, "MISSING", refs=(row.reference,))
        return
    if any(not isinstance(reason, dict) or reason.get("state") not in ("DEGRADED", "BLOCKED") or not isinstance(reason.get("code"), str) for reason in integrity["reasons"]):
        c.add("SUPPORT_SNAPSHOT_MALFORMED", "structured canonical reasons", integrity["reasons"], "MISSING", refs=(row.reference,))
        return
    canonical_state = "BLOCKED" if any(r["state"] == "BLOCKED" for r in integrity["reasons"]) else (
        "DEGRADED" if any(r["state"] == "DEGRADED" for r in integrity["reasons"]) else "HEALTHY")
    if integrity["state"] != canonical_state:
        c.add("SUPPORT_STATE_CONTRADICTORY", canonical_state, integrity["state"], "CONTRADICTORY", refs=(row.reference,))
        return
    if self_observed := stamp(d["observed_at"]):
        if self_observed > c.now + timedelta(minutes=5) or c.now - self_observed > MAX_SOURCE_AGE:
            c.add("SUPPORT_SNAPSHOT_STALE", "current snapshot", d["observed_at"], "MISSING", refs=(row.reference,))
            return
    for reason in integrity["reasons"]:
        code, severity = reason["code"], reason["state"]
        if code == "SHADOW_SERVICE_UNRESOLVED" and severity != "DEGRADED":
            c.add("SUPPORT_SEVERITY_CONTRADICTORY", "DEGRADED", severity, "CONTRADICTORY", refs=(row.reference,))
            continue
        c.add(code, "absent", "present", "FAIL" if severity == "BLOCKED" else "DEGRADED",
              severity, (row.reference,))
    c.add("SUPPORT_STATE_VALID", "consistent integrity state", integrity["state"], "PASS", refs=(row.reference,))


def _telemetry_reports(c: Checks) -> None:
    telemetry, reports = c.source("telemetry"), c.source("reports")
    if telemetry is not None:
        d = telemetry.data
        received = stamp(d.get("last_received_at"))
        if received is None:
            c.add("TELEMETRY_TIME_MISSING", "timestamp", d.get("last_received_at"), "MISSING", refs=(telemetry.reference,))
        else:
            max_age = c.request.assertions.get("max_telemetry_age_seconds", 900)
            if type(max_age) is not int or not 0 < max_age <= 86400:
                c.add("TELEMETRY_WINDOW_INVALID", "1..86400 seconds", max_age, "MISSING", refs=(telemetry.reference,))
            else:
                c.match("TELEMETRY_FRESH", True, timedelta(0) <= c.now - received <= timedelta(seconds=max_age), telemetry.reference)
        if type(d.get("duplicate_identities")) is int:
            c.match("TELEMETRY_IDENTITIES_UNIQUE", 0, d["duplicate_identities"], telemetry.reference)
    if reports is not None:
        d = reports.data
        due, actual = d.get("daily_due_sessions"), d.get("daily_sessions")
        if not isinstance(due, list) or not isinstance(actual, list):
            c.add("DAILY_REPORT_EVIDENCE_MISSING", "due and present sessions", None, "MISSING", refs=(reports.reference,))
        else:
            for session in due:
                c.add("DAILY_REPORT_MISSING" if session not in actual else "DAILY_REPORT_PRESENT", True,
                      session in actual, "DEGRADED" if session not in actual else "PASS",
                      "DEGRADED" if session not in actual else "NONE", (reports.reference,))
        if d.get("weekly_due") is True:
            if d.get("weekly_key") is None:
                c.add("WEEKLY_REPORT_MISSING", True, False, "DEGRADED", "DEGRADED", (reports.reference,))
            elif type(d.get("weekly_complete")) is bool:
                c.match("WEEKLY_REPORT_COMPLETE", True, d["weekly_complete"], reports.reference, severity="DEGRADED")
            else:
                c.add("WEEKLY_COMPLETENESS_UNKNOWN", True, None, "MISSING", refs=(reports.reference,))


def _migration_artifacts(c: Checks) -> None:
    artifact = c.source("artifacts")
    if artifact is not None:
        required, present = artifact.data.get("required"), artifact.data.get("present")
        if not isinstance(required, list) or not isinstance(present, list):
            c.add("ARTIFACT_EVIDENCE_MALFORMED", "required/present lists", None, "MISSING", refs=(artifact.reference,))
        else:
            for name in c.request.assertions.get("required_artifacts", required):
                c.match("REQUIRED_ARTIFACT_PRESENT", True, name in present, artifact.reference)
    migration = c.source("migration")
    if migration is not None:
        d = migration.data
        if "migration_id" in c.request.assertions:
            c.match("MIGRATION_IDENTITY", c.request.assertions["migration_id"], d.get("migration_id"), migration.reference)
        if d.get("artifact_present") is not True:
            c.match("MIGRATION_ARTIFACT_PRESENT", True, d.get("artifact_present"), migration.reference)
        if c.request.assertions.get("migration_applied", True):
            if d.get("live_state") not in ("APPLIED", "NOT_APPLIED"):
                c.add("LIVE_MIGRATION_STATE_UNKNOWN", "canonical live application", d.get("live_state"), "MISSING", refs=(migration.reference,))
            else:
                c.match("MIGRATION_APPLIED", "APPLIED", d["live_state"], migration.reference)


def _activation(c: Checks) -> None:
    _github(c)
    _release(c)
    _readiness(c)
    _support(c)
    _telemetry_reports(c)
    _migration_artifacts(c)
    row = c.source("activation")
    if row is not None:
        gates = row.data.get("gates")
        if not isinstance(gates, dict) or not gates:
            c.add("ACTIVATION_GATES_UNKNOWN", "named gate booleans", gates, "MISSING", refs=(row.reference,))
        else:
            for name, value in sorted(gates.items()):
                c.add("ACTIVATION_GATE_" + name.upper(), True, value,
                      "PASS" if value is True else "FAIL" if value is False else "MISSING",
                      "BLOCKED" if value is False else "NONE", (row.reference,))


PROFILES = {
    "repository": _github,
    "release_deployment": _release,
    "research_readiness": _readiness,
    "agent_support": _support,
    "telemetry_reports": _telemetry_reports,
    "migration_artifacts": _migration_artifacts,
    "activation_gates": _activation,
}


def verify(raw: VerificationRequest | Mapping[str, Any], *, now: datetime) -> VerificationResult:
    """Verify a claim without reads, writes, network calls, or mutable global state."""
    request = raw if isinstance(raw, VerificationRequest) else VerificationRequest.parse(raw)
    now = utc_time(now)
    c = Checks(request, now)
    PROFILES[request.profile](c)
    rows = tuple(c.rows)
    failed = tuple(r.code for r in rows if r.status == "FAIL")
    degraded = tuple(r.code for r in rows if r.status == "DEGRADED")
    missing = tuple(r.code for r in rows if r.status == "MISSING")
    contradictory = tuple(r.code for r in rows if r.status == "CONTRADICTORY")
    verdict = "BLOCKED" if any(r.status == "FAIL" and r.canonical_severity == "BLOCKED" for r in rows) else (
        "INCONCLUSIVE" if missing or contradictory or not rows else "DEGRADED" if degraded else "VERIFIED")
    action = {"VERIFIED": "No action required; recheck when evidence changes.",
              "DEGRADED": "Review the canonical degraded findings before proceeding.",
              "BLOCKED": "Stop the claimed transition and review proven violations.",
              "INCONCLUSIVE": "Obtain current canonical evidence and re-verify."}[verdict]
    sources = tuple({"section": section, "source_id": r.source_id, "source_version": r.source_version,
                     "observed_at": r.observed_at, "authority": r.authority, "reference": r.reference}
                    for section, records in sorted(request.evidence.items()) for r in records)
    github = next((r.data for r in request.evidence.get("github", ()) if r.authority == "CANONICAL"), {})
    release = next((r.data for r in request.evidence.get("release", ()) if r.authority == "CANONICAL"), {})
    return VerificationResult(OUTPUT_VERSION, request.verification_id, now.isoformat(), request.environment,
                              request.profile, request.subject, request.assertions, verdict, rows, failed,
                              degraded, missing, contradictory,
                              tuple(sorted({ref for r in rows for ref in r.evidence_references})), sources,
                              github.get("main_sha"), release.get("release_id"), release.get("strategy_version_id"),
                              tuple("Noncanonical evidence cannot prove live state." for _ in [0] if any(
                                  r.authority == "REPORTED" for records in request.evidence.values() for r in records)),
                              action, verdict != "VERIFIED")
