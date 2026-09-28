from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_support.integrity import SCHEMA_VERSION
from app.agent_support.railway import load_role_map
from app.verifier.adapters import bounded_railway_roles
from app.verifier.engine import verify
from app.verifier.explanation import explanation_context
from app.verifier.models import INPUT_VERSION, VerificationRequest

NOW = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
SHA = "a" * 40
OTHER = "b" * 40
MIGRATION = "20260927195537_rhen_agent_support_alerts"


def record(data, *, authority="CANONICAL", time=NOW, source="test-source"):
    return {"source_id": source, "source_version": "v1", "observed_at": time.isoformat(),
            "reference": "fixture://" + source, "authority": authority, "data": data}


def fixture():
    mapping = load_role_map()
    status, configs = [], {}
    for row in mapping["assignments"]:
        sid = row["service_id"]
        status.append({"id": sid, "name": row["expected_name"], "latestDeployment": {"id": sid + "-deploy", "status": "SUCCESS"}})
        configs[sid] = {"start_command": row["command_marker"], "source_repo": row.get("source_repo"),
                        "source_image": row.get("source_image"), "cron_schedule": "0 * * * *"}
    railway = bounded_railway_roles(status, configs, project_id=mapping["project_id"],
                                     environment_id=mapping["environment_id"])
    return {
        "github": record({"repository": "anevum/alpaca-trader", "main_sha": SHA, "branch_sha": OTHER,
                          "main_contains": [SHA], "branch_contains": [SHA, OTHER],
                          "pr": {"number": 55, "state": "closed", "draft": False, "merged": True, "head_sha": SHA}}),
        "release": record({"release_id": "r1", "commit_sha": SHA, "strategy_name": "RHEN", "strategy_version_id": "s1"}),
        "deployment": record({"deployment_id": "deploy1", "source_commit": SHA, "release_id": "r1",
                              "strategy_name": "RHEN", "strategy_version_id": "s1"}),
        "railway": record(railway),
        "readiness": record({"state": "READY", "blocker_count": 0, "blocker_codes": [], "limitation_codes": [],
                             "monitor_codes": [], "waiting_requirements": [], "evidence_cutoff": NOW.isoformat()}),
        "support": record({"schema_version": SCHEMA_VERSION, "observed_at": NOW.isoformat(),
                           "integrity": {"state": "HEALTHY", "reasons": []}}),
        "telemetry": record({"last_received_at": NOW.isoformat(), "duplicate_identities": 0,
                             "schema_version": "rhen-canonical-telemetry-v1"}),
        "reports": record({"daily_due_sessions": ["2026-09-25"], "daily_sessions": ["2026-09-25"],
                           "weekly_due": True, "weekly_key": "2026-W39", "weekly_complete": True}),
        "migration": record({"migration_id": MIGRATION, "artifact_present": True, "live_state": "APPLIED"}),
        "artifacts": record({"required": [MIGRATION], "present": [MIGRATION]}),
        "activation": record({"gates": {"bounded_read": True, "runner_verified": True}}),
    }


def request(profile="activation_gates", *, changes=None, assertions=None, removed=()):
    evidence = fixture()
    if changes:
        for section, value in changes.items():
            evidence[section] = value
    for name in removed:
        del evidence[name]
    return {"schema_version": INPUT_VERSION, "verification_id": "v1", "environment": "DEVELOPMENT",
            "profile": profile, "subject": "fixture", "assertions": assertions or {},
            "evidence": {key: value if isinstance(value, list) else [value] for key, value in evidence.items()}}


def result(profile="activation_gates", *, changes=None, assertions=None, removed=()):
    return verify(request(profile, changes=changes, assertions=assertions, removed=removed), now=NOW)


def test_healthy_fixture_and_all_profiles():
    for profile in ("repository", "release_deployment", "research_readiness", "agent_support",
                    "telemetry_reports", "migration_artifacts", "activation_gates"):
        value = result(profile)
        assert value.verdict == "VERIFIED", (profile, value.as_dict())
        assert value.schema_version == "rhen-verifier-result-v1"
        assert value.evidence_sources and value.invariant_matrix


def test_repository_main_match():
    assert result("repository", assertions={"main_sha": SHA}).verdict == "VERIFIED"


def test_stale_branch_cannot_claim_main():
    assert result("repository", assertions={"change_scope": "main", "change_commit": OTHER}).verdict == "BLOCKED"


@pytest.mark.parametrize("state,draft,merged", [("open", True, False), ("open", False, False), ("closed", False, False)])
def test_unmerged_pr_is_not_main(state, draft, merged):
    row = fixture()["github"]
    row["data"]["pr"].update(state=state, draft=draft, merged=merged)
    value = result("repository", changes={"github": row}, assertions={"change_scope": "main", "change_commit": SHA})
    assert value.verdict == "BLOCKED"
    assert "PR_MERGED" in value.failed_invariants


def test_branch_and_pr_distinct_from_main():
    assert result("repository", assertions={"change_scope": "branch", "change_commit": OTHER}).verdict == "VERIFIED"
    assert result("repository", assertions={"change_scope": "pr", "change_commit": SHA}).verdict == "VERIFIED"


def test_main_containment_does_not_prove_production_rollout():
    row = fixture()["deployment"]
    row["data"]["source_commit"] = OTHER
    value = result("repository", changes={"deployment": row},
                   assertions={"change_scope": "production", "change_commit": SHA})
    assert value.verdict == "BLOCKED"
    assert "CHANGE_DEPLOYED" in value.failed_invariants


def test_production_claim_without_live_deployment_is_inconclusive():
    assert result("repository", removed=("deployment",),
                  assertions={"change_scope": "production", "change_commit": SHA}).verdict == "INCONCLUSIVE"


def test_deployment_source_mismatch_is_blocked():
    row = fixture()["deployment"]
    row["data"]["source_commit"] = OTHER
    assert "DEPLOYMENT_SOURCE_COMMIT" in result("release_deployment", changes={"deployment": row}).failed_invariants


def test_missing_deployment_is_inconclusive():
    assert result("release_deployment", removed=("deployment",)).verdict == "INCONCLUSIVE"


def test_stale_telemetry_is_blocked():
    row = fixture()["telemetry"]
    row["data"]["last_received_at"] = (NOW - timedelta(hours=1)).isoformat()
    assert result("telemetry_reports", changes={"telemetry": row}).verdict == "BLOCKED"


def test_missing_daily_report_preserves_degraded_severity():
    row = fixture()["reports"]
    row["data"]["daily_sessions"] = []
    assert result("telemetry_reports", changes={"reports": row}).verdict == "DEGRADED"


def test_malformed_support_snapshot_is_inconclusive():
    row = fixture()["support"]
    row["data"]["integrity"] = "oops"
    assert result("agent_support", changes={"support": row}).verdict == "INCONCLUSIVE"


def waiting():
    row = fixture()["readiness"]
    row["data"].update(state="WAITING", limitation_codes=["KNOWN_EVIDENCE_LIMITATION"],
                       limitation_reason_codes=["INCOMPLETE_FORWARD_OUTCOMES", "UNRECONSTRUCTABLE_EVIDENCE"],
                       monitor_codes=["NONBLOCKING_MONITOR"],
                       monitor_reason_codes=["CANONICAL_OPERATIONAL_INCIDENTS"],
                       waiting_requirements=["MULTIPLE_INDEPENDENT_SESSIONS"])
    return row


def test_waiting_zero_blockers_is_verified_for_nonblocking_claim():
    value = result("research_readiness", changes={"readiness": waiting()},
                   assertions={"readiness_nonblocking": True})
    assert value.verdict == "VERIFIED"
    assert "RESEARCH_READINESS_WAITING_NONBLOCKING" in [r.code for r in value.invariant_matrix]


def test_blocked_with_canonical_blocker():
    row = waiting()
    row["data"].update(state="BLOCKED", blocker_count=1, blocker_codes=["CANONICAL_BLOCKER"])
    assert result("research_readiness", changes={"readiness": row}).verdict == "BLOCKED"


@pytest.mark.parametrize("key,code", [
    ("limitation_reason_codes", "INCOMPLETE_FORWARD_OUTCOMES"),
    ("limitation_reason_codes", "UNRECONSTRUCTABLE_EVIDENCE"),
    ("monitor_reason_codes", "CANONICAL_OPERATIONAL_INCIDENTS"),
    ("waiting_requirements", "MULTIPLE_INDEPENDENT_SESSIONS"),
])
def test_nonblocking_canonical_context(key, code):
    row = waiting()
    assert code in row["data"][key]
    assert result("research_readiness", changes={"readiness": row}).verdict == "VERIFIED"


def test_shadow_severity_is_degraded():
    row = fixture()["support"]
    row["data"]["integrity"] = {"state": "DEGRADED", "reasons": [{"code": "SHADOW_SERVICE_UNRESOLVED",
                                                                          "state": "DEGRADED", "source": "railway", "reference": "shadow_comparison"}]}
    value = result("agent_support", changes={"support": row})
    assert value.verdict == "DEGRADED"
    assert value.invariant_matrix[0].canonical_severity == "DEGRADED"


def test_support_waiting_preserves_canonical_degraded_state():
    row = fixture()["support"]
    row["data"]["integrity"] = {"state": "DEGRADED", "reasons": [{"code": "RESEARCH_READINESS_WAITING",
                                                                          "state": "DEGRADED"}]}
    assert result("agent_support", changes={"support": row}).verdict == "DEGRADED"


def test_inconsistent_support_state_is_inconclusive():
    row = fixture()["support"]
    row["data"]["integrity"] = {"state": "HEALTHY", "reasons": [{"code": "SHADOW_SERVICE_UNRESOLVED",
                                                                         "state": "DEGRADED"}]}
    assert result("agent_support", changes={"support": row}).verdict == "INCONCLUSIVE"


def test_missing_required_railway_role():
    row = fixture()["railway"]
    row["data"]["services"] = [s for s in row["data"]["services"] if s["role"] != "production_trading"]
    assert result("release_deployment", changes={"railway": row}).verdict == "BLOCKED"


def test_missing_research_role_retains_degraded_severity():
    row = fixture()["railway"]
    row["data"]["services"] = [s for s in row["data"]["services"] if s["role"] != "research_agent"]
    assert result("release_deployment", changes={"railway": row}).verdict == "DEGRADED"


def test_repurposed_service_id_not_a_role():
    mapping = load_role_map()
    assignment = mapping["assignments"][0]
    role = bounded_railway_roles([{"id": assignment["service_id"], "name": "alpaca-trader",
                                  "latestDeployment": {"status": "SUCCESS"}}],
                                 {assignment["service_id"]: {"source_repo": "other/repository",
                                                              "start_command": "app.main:app"}},
                                 project_id=mapping["project_id"], environment_id=mapping["environment_id"])
    assert role["services"][0]["role"] == "unclassified"
    assert result("release_deployment", changes={"railway": record(role)}).verdict == "BLOCKED"


def test_domain_and_name_cannot_grant_role():
    mapping = load_role_map()
    role = bounded_railway_roles([{"id": "new-id", "name": "alpaca-trader", "domain": "alpaca-trader.example",
                                  "latestDeployment": {"status": "SUCCESS"}}], {},
                                 project_id=mapping["project_id"], environment_id=mapping["environment_id"])
    assert role["services"][0]["role"] == "unclassified"


def test_unvalidated_role_assertion_cannot_grant_trading_role():
    row = fixture()["railway"]
    row["data"]["services"][0]["source_command_validated"] = False
    assert "SERVICE_ROLE_UNVERIFIED" in result("release_deployment", changes={"railway": row}).failed_invariants


def test_duplicate_canonical_identity():
    row = fixture()["railway"]
    row["data"]["services"].append(deepcopy(row["data"]["services"][0]))
    assert "DUPLICATE_CANONICAL_IDENTITY" in result("release_deployment", changes={"railway": row}).failed_invariants


def test_missing_migration_artifact():
    row = fixture()["migration"]
    row["data"]["artifact_present"] = False
    assert result("migration_artifacts", changes={"migration": row}).verdict == "BLOCKED"


def test_artifact_present_live_state_unknown():
    row = fixture()["migration"]
    row["data"]["live_state"] = "UNKNOWN"
    assert result("migration_artifacts", changes={"migration": row}).verdict == "INCONCLUSIVE"


def test_reported_migration_not_canonical_proof():
    row = fixture()["migration"]
    row["authority"] = "REPORTED"
    assert result("migration_artifacts", changes={"migration": row}).verdict == "INCONCLUSIVE"


def test_contradictory_canonical_sources():
    rows = [fixture()["github"], fixture()["github"]]
    rows[1]["data"]["main_sha"] = OTHER
    value = result("repository", changes={"github": rows})
    assert value.verdict == "INCONCLUSIVE"
    assert "GITHUB_EVIDENCE_CONTRADICTORY" in value.contradictory_evidence


def test_incomplete_evidence():
    assert result("migration_artifacts", removed=("migration", "artifacts")).verdict == "INCONCLUSIVE"


def test_unavailable_live_source():
    row = fixture()["support"]
    row["authority"] = "UNAVAILABLE"
    assert result("agent_support", changes={"support": row}).verdict == "INCONCLUSIVE"


def test_stale_source():
    row = fixture()["github"]
    row["observed_at"] = (NOW - timedelta(days=3)).isoformat()
    assert result("repository", changes={"github": row}).verdict == "INCONCLUSIVE"


def test_historical_documentation_does_not_prove_live_absence():
    row = fixture()["migration"]
    row.update(authority="REPORTED", source_id="old-doc", reference="docs://historical")
    row["data"]["live_state"] = "NOT_APPLIED"
    assert result("migration_artifacts", changes={"migration": row}).verdict == "INCONCLUSIVE"


def test_model_cannot_override_verdict_or_severity():
    row = fixture()["support"]
    row["data"]["integrity"] = {"state": "DEGRADED", "reasons": [{"code": "SHADOW_SERVICE_UNRESOLVED",
                                                                          "state": "DEGRADED"}]}
    value = result("agent_support", changes={"support": row})
    formatted = explanation_context(value, {"verdict": "BLOCKED", "severity": "BLOCKED", "note": "I disagree"})
    assert formatted["verification"]["verdict"] == "DEGRADED"
    assert formatted["verification"]["invariant_matrix"][0]["canonical_severity"] == "DEGRADED"
    assert "verdict" not in formatted or formatted["verdict"] != "BLOCKED"


def test_claimed_release_mismatch():
    assert result("release_deployment", assertions={"release_id": "r2"}).verdict == "BLOCKED"


def test_claimed_strategy_mismatch():
    assert result("release_deployment", assertions={"strategy_version_id": "s2"}).verdict == "BLOCKED"


def test_activation_gate_failure():
    row = fixture()["activation"]
    row["data"]["gates"]["runner_verified"] = False
    assert result("activation_gates", changes={"activation": row}).verdict == "BLOCKED"


def test_missing_gate_is_inconclusive():
    row = fixture()["activation"]
    row["data"]["gates"]["runner_verified"] = None
    assert result("activation_gates", changes={"activation": row}).verdict == "INCONCLUSIVE"


def test_forged_severity_on_shadow_is_contradictory():
    row = fixture()["support"]
    row["data"]["integrity"] = {"state": "BLOCKED", "reasons": [{"code": "SHADOW_SERVICE_UNRESOLVED",
                                                                         "state": "BLOCKED"}]}
    assert result("agent_support", changes={"support": row}).verdict == "INCONCLUSIVE"


def test_bounded_input_rejects_secret_and_arbitrary_fields():
    raw = request("repository")
    raw["evidence"]["github"][0]["data"]["api_key"] = "would be a secret"
    with pytest.raises(ValueError):
        VerificationRequest.parse(raw)


def test_no_broker_or_mutation_adapter():
    import app.verifier.adapters as adapters
    import app.verifier.engine as engine
    assert not any(hasattr(adapters, name) or hasattr(engine, name) for name in
                   ("alpaca_client", "write_alert", "apply_migration", "deploy", "place_order"))
