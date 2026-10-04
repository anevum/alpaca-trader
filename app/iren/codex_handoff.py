"""CODEX_HANDOFF v1: deterministic work packages, never execution authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

SCHEMA = "codex_handoff.v1"
REPOSITORY = "anevum/alpaca-trader"
TERMINAL = {"VERIFIED", "FAILED", "SUPERSEDED"}
LIFECYCLE = {"PREPARED", "IN_PROGRESS", "PR_OPEN", "MERGED", "VERIFYING", "VERIFIED", "FAILED", "SUPERSEDED"}
ALLOWED_PREFIXES = ("app/iren/", "foundation/iren_", "tests/test_iren_", "docs/iren/")
ALLOWED_FILES = {"foundation/command_iren.py"}
RESEARCH_ENGINEERING_ALLOWED_PREFIXES = (
    "app/graen/",
    "app/research_agent/",
    "graen/",
    "research/crypto/",
    "foundation/graen_",
    "foundation/research_agent_",
    "tests/test_graen_",
    "tests/test_research_",
    "tests/test_generated_",
    "docs/graen/",
)
RESEARCH_ENGINEERING_SERVICE_SCOPE = {
    "IREN", "IREN_EXECUTOR", "FOUNDATION",
    "GRAEN", "GRAEN_EXECUTOR", "RESEARCH_AGENT", "VELUM", "CRYPTO_EDGE",
}
PROTECTED = (
    "RHEN strategy logic, risk controls, stops, targets, position sizing, capital allocation, "
    "broker behavior, live execution permissions, crypto execution, credentials, API spending "
    "limits, destructive infrastructure actions, legal/publication actions and external capital behavior"
)
REQUIRED_CHECKS = {"test", "velum-graen", "graen-forward-shadow", "inventory", "codex-postgres"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def fresh(stamp: Any, now: datetime, seconds: int = 180) -> bool:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return parsed.tzinfo is not None and 0 <= (now - parsed).total_seconds() <= seconds
    except (ValueError, TypeError):
        return False


def protected(objective: dict) -> bool:
    meta = objective.get("metadata") or {}
    return bool(objective.get("protected_action") or objective.get("requires_human")
                or meta.get("requires_explicit_authorization") or meta.get("protected_action"))


def mode(action: dict | None) -> str:
    action = action or {}
    if protected(action):
        return "protected/requires Devon"
    return "codex/manual software" if action.get("job_type") in {"SOFTWARE_BUILD", "CODEX_HANDOFF"} else "deterministic"


def active_handoffs(snapshot: dict) -> list[dict]:
    return [row for row in snapshot.get("jobs", []) if row.get("job_type") == "CODEX_HANDOFF"
            and (row.get("result") or {}).get("handoff_status") not in TERMINAL
            and row.get("status") not in {"CANCELLED", "FAILED", "SUCCEEDED"}]


def package_for(objective: dict, *, handoff_id: str, command_id: str | None,
                main_sha: str, control: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    meta = objective.get("metadata") or {}
    if protected(objective):
        raise ValueError("protected_objective_requires_owner")
    if meta.get("job_type") != "SOFTWARE_BUILD":
        raise ValueError("objective_is_not_software")
    if not re.fullmatch(r"[0-9a-f]{40}", main_sha):
        raise ValueError("current_main_sha_required")
    criteria = objective.get("success_criteria")
    if not isinstance(criteria, dict) or not criteria:
        raise ValueError("objective_success_criteria_required")
    scope = meta.get("codex_scope") or {}
    research_engineering = (
        meta.get("manual_software_required") is True
        and isinstance(meta.get("engineering_requirement"), dict)
    )
    paths = scope.get("allowed_paths") or list(ALLOWED_PREFIXES) + sorted(ALLOWED_FILES)
    if not all(
        path_allowed(p, research_engineering=research_engineering)
        for p in paths
    ):
        raise ValueError(
            "scope_exceeds_research_engineering_boundary"
            if research_engineering
            else "scope_exceeds_iren_boundary"
        )
    services = scope.get("expected_services") or ["IREN"]
    allowed_services = (
        RESEARCH_ENGINEERING_SERVICE_SCOPE
        if research_engineering
        else {"IREN", "IREN_EXECUTOR", "FOUNDATION"}
    )
    if not services or not set(services) <= allowed_services:
        raise ValueError("deployment_scope_exceeds_reviewed_boundary")
    incidents = [{"key": k, "reason": v.get("reason"), "severity": v.get("severity")}
                 for k, v in (control.get("incidents") or {}).items() if v.get("status") == "OPEN"]
    rows = (control.get("topology") or {}).get("services") or []
    baseline = {r["service_id"]: {k: r.get(k) for k in ("deployment", "revision", "readiness", "service_name")}
                for r in rows if r.get("independent_runtime") and r.get("service_id")}
    result = {
        "schema_version": SCHEMA, "handoff_id": handoff_id, "source_job_id": handoff_id,
        "source_command_id": command_id, "objective_key": objective["objective_key"],
        "title": objective["title"], "description": objective.get("description") or "",
        "why_next": "Selected by the canonical IREN planner from dependency-satisfied executable objectives.",
        "repository": REPOSITORY, "repositories": [REPOSITORY], "base_branch": "main",
        "base_sha": main_sha, "created_at": now.isoformat(),
        "objective_identity": digest({k: objective.get(k) for k in
            ("objective_key", "description", "success_criteria", "dependencies", "protected_action", "metadata")}),
        "dependencies": deepcopy(objective.get("dependencies") or []),
        "success_criteria": deepcopy(criteria),
        "verification_checks": deepcopy(scope.get("verification_checks") or {}),
        "required_checks": sorted(REQUIRED_CHECKS),
        "allowed_paths": paths, "expected_services": services,
        "required_migrations": deepcopy(scope.get("required_migrations") or []),
        "active_incidents": incidents, "control_state": control.get("state"),
        "control_observed_at": control.get("observed_at"),
        "runtime_baseline": baseline,
        "inventory_complete": (control.get("topology") or {}).get("inventory_complete") is True,
        "configuration_identity": (control.get("configuration_baseline") or {}).get("fingerprint"),
        "protected_boundaries": PROTECTED, "protected_authority": False,
        "research_engineering": research_engineering,
        "manual_software_required": meta.get("manual_software_required") is True,
        "engineering_requirement": deepcopy(meta.get("engineering_requirement")),
        "paid_model_execution": False, "auto_merge": False,
        "branch": "codex/handoff/" + handoff_id,
    }
    result["prompt"] = render_prompt(result)
    result["package_digest"] = digest(result)
    return result


def path_allowed(path: str, *, research_engineering: bool = False) -> bool:
    if not isinstance(path, str) or ".." in path or path.startswith("/") or "\\" in path:
        return False
    if path in ALLOWED_FILES or any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return True
    return (
        research_engineering
        and any(path.startswith(prefix) for prefix in RESEARCH_ENGINEERING_ALLOWED_PREFIXES)
    )


def render_prompt(p: dict) -> str:
    encoded = json.dumps({k: v for k, v in p.items() if k != "prompt"}, indent=2, sort_keys=True)
    return f"""Implement this canonical IREN Codex handoff: {p['title']}

Inspect CURRENT main, applicable AGENTS.md, production health, canonical objectives and this handoff before editing.
Do not trust stale assumptions or blindly target the captured SHA. If the objective, scope or protected baseline changed,
ask IREN to supersede this handoff. Continue current architecture; do not redesign or merge legacy PRs.

Why next: {p['why_next']}
Objective: {p['description']}
Repository: {p['repository']}; base branch: main; SHA at preparation: {p['base_sha']}.
Use branch {p['branch']} and put these exact lines in the PR body:
IREN-Handoff: {p['handoff_id']}
IREN-Objective: {p['objective_key']}

Architecture: GitHub main -> Railway runtimes -> Foundation v2 Railway PostgreSQL.
IREN owns orchestration. Specialized subsystems remain beneath IREN.
Reuse iren.objectives, jobs, commands, job_events, planner, gateway and verifier.
Legacy infrastructure is noncanonical and must not be reintroduced.
Inspect app/iren/work.py, app/iren/codex_handoff.py, foundation/iren_work_gateway.py,
foundation/command_iren.py, existing tests and the objective-specific areas below.

Scope and implementation requirements: implement the objective within allowed_paths below.
Preserve the exact success_criteria and dependencies. Use explicit deterministic evidence for each criterion.
If additional scope, migration or service deployment is required, stop and request a superseding reviewed package.
Out of scope: unrelated refactors, architecture replacement, legacy PR cleanup and all protected boundaries.
This handoff grants NO authority over {PROTECTED}.
Keep both model budgets zero; never invoke the paid worker or enable spending.
Protect live trading. No trader restart/rebuild. Only expected_services may deploy.
No credential changes or destructive database operations.

Tests: add focused positive/negative tests for the objective, absent/stale/malformed evidence and authority denial;
run the full relevant suite and all required CI checks. Do not infer success from mocks or a merged PR.
Open a PR containing the handoff metadata. No autonomous merge authority is granted by this package.
Devon must authorize merge unless the current session already explicitly authorizes it.

Deployment verification: capture actual Railway deployment IDs before/after, application readiness, deployed commit,
migration identity if applicable, incidents and unchanged RHEN strategy/configuration.
Return to Command and request "verify Codex handoff". IREN discovers the exact branch/metadata or accepts
"associate Codex handoff {p['handoff_id']} PR <number>". Association alone is not completion.
Only IREN's evidence-gated verifier may mark VERIFIED/COMPLETE. Missing or ambiguous evidence remains WAITING/BLOCKED.
After verified completion IREN re-runs the existing planner and exposes the next objective.

Rollback: record the immediately previous affected-service deployment. If health or protected invariants regress,
stop rollout and restore only the affected authorized service through the owner-approved deployment procedure.
Preserve durable objectives, job history and evidence. Never roll back RHEN as part of this handoff.

Canonical package (acceptance, verification, dependencies, incidents, scope and runtime baselines):
{encoded}
"""


def criteria_from_observations(package: dict, observations: dict) -> dict:
    """Only a fixed read-only observation namespace; no caller-supplied truth flags."""
    criteria = {
        "manual_software_handoff": package.get("paid_model_execution") is False
        and package.get("auto_merge") is False,
        "protected_authority": package.get("protected_authority") is True,
    }
    for name, check in package.get("verification_checks", {}).items():
        if not isinstance(check, dict) or check.get("source") not in {"IREN", "IREN_EXECUTOR", "FOUNDATION"}:
            continue
        value = observations.get(check["source"])
        path = check.get("path")
        if not isinstance(path, list) or not path or not all(isinstance(k, str) for k in path):
            continue
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            criteria[name] = value
    return criteria


def verification(package: dict, github: dict, control: dict, observations: dict,
                 migrations: list[str], *, now: datetime | None = None) -> dict:
    from .work import criteria_satisfied
    now = now or datetime.now(timezone.utc)
    blockers = []
    def require(condition, reason):
        if not condition:
            blockers.append(reason)
    require(fresh(github.get("observed_at"), now), "github_evidence_stale")
    require(github.get("association_valid") is True, "github_association_missing_or_ambiguous")
    require(github.get("merged") is True and github.get("landed") is True, "expected_commit_not_on_main")
    require(github.get("ci_passed") is True, "required_ci_evidence_missing")
    files = github.get("files") or []
    require(
        bool(files)
        and all(
            path_allowed(
                f,
                research_engineering=package.get("research_engineering") is True,
            )
            and any(f == p or f.startswith(p) for p in package["allowed_paths"])
            for f in files
        ),
        "changed_files_outside_reviewed_scope",
    )
    require(fresh(control.get("observed_at"), now), "control_observation_stale")
    require(control.get("state") == "HEALTHY", "control_not_healthy")
    require(not any(v.get("status") == "OPEN" for v in (control.get("incidents") or {}).values()), "active_incidents")
    require(bool(package.get("configuration_identity")) and
            package["configuration_identity"] == (control.get("configuration_baseline") or {}).get("fingerprint"),
            "protected_configuration_unverified")
    require(package.get("inventory_complete") is True and (control.get("topology") or {}).get("inventory_complete") is True,
            "deployment_inventory_incomplete")
    require(bool((package.get("runtime_baseline") or {}).get("RHEN", {}).get("deployment")), "protected_runtime_baseline_missing")
    current = {r.get("service_id"): r for r in (control.get("topology") or {}).get("services", [])}
    for key, before in package.get("runtime_baseline", {}).items():
        after = current.get(key, {})
        if key in package["expected_services"]:
            require(after.get("readiness") is True and after.get("revision") == github.get("main_sha"),
                    "deployment_not_verified:" + key)
        else:
            require(bool(before.get("deployment")) and before.get("deployment") == after.get("deployment"),
                    "unaffected_runtime_not_verified:" + key)
    for key in package["expected_services"]:
        if key not in package.get("runtime_baseline", {}):
            observed = observations.get(key) or {}
            require(observed.get("ok") is True and observed.get("revision") == github.get("main_sha"),
                    "deployment_not_verified:" + key)
    require(set(package.get("required_migrations") or []) <= set(migrations), "migration_evidence_missing")
    worker = observations.get("IREN_EXECUTOR") or {}
    require(worker.get("spending_authority") is False and
            (worker.get("software_worker") or {}).get("daily_budget_usd") == 0 and
            (worker.get("software_worker") or {}).get("job_budget_usd") == 0,
            "zero_budget_boundary_unverified")
    criteria = criteria_from_observations(package, observations)
    require(criteria_satisfied(package["success_criteria"], criteria), "objective_criteria_not_satisfied")
    return {"verifier": SCHEMA, "verified": not blockers, "blockers": blockers,
            "criteria": criteria, "github": github, "observed_at": now.isoformat(),
            "handoff_status": "VERIFIED" if not blockers else "VERIFYING" if github.get("merged") else
                "PR_OPEN" if github.get("association_valid") else "PREPARED"}
