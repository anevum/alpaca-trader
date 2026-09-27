import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.agent_support.escalation import reconcile
from app.agent_support.adapter import from_canonical_sources
from app.agent_support.integrity import SCHEMA_VERSION, evaluate
from app.agent_support.railway import load_role_map, normalize_services
from app.agent_support.registry import load_registry
from app.agent_support.snapshot import build_snapshot


NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def evidence():
    return {
        "schema_version": SCHEMA_VERSION,
        "railway_services": [
            {"id": "prod-id", "name": "current-production", "role": "production_trading",
             "start_command": "uvicorn app.main:app", "status": "SUCCESS",
             "deployment_id": "deploy-1", "source_commit": "a" * 40},
            {"id": "pre-id", "name": "current-preopen", "role": "preopen_state",
             "start_command": "uvicorn app.preopen_state.service:app", "status": "SUCCESS"},
            {"id": "shadow-id", "name": "current-comparison", "role": "shadow_comparison",
             "start_command": "uvicorn app.main:app", "status": "SUCCESS"},
            {"id": "agent-id", "name": "current-agent", "role": "research_agent",
             "start_command": "uvicorn app.research_agent.service:app", "status": "SUCCESS"},
            {"id": "scheduler-id", "name": "current-scheduler", "role": "research_scheduler",
             "start_command": "./run.sh encoded", "status": "SUCCESS"},
        ],
        "production_runtime": {"service_id": "prod-id", "service_name": "current-production",
                               "deployment_id": "deploy-1", "git_commit": "a" * 40,
                               "strategy_name": "rolling_momentum_vwap", "strategy_version_id": "LIVE-1"},
        "research_readiness": {"state": "IDLE", "cadence": "daily", "blocker_count": 0,
                               "blockers": [], "trigger_reference": "2026-09-28",
                               "evidence_cutoff": "2026-09-28T19:59:00Z",
                               "read_only": True, "model_invoked": False, "persisted": False},
        "telemetry": {"schema_version": "rhen-canonical-telemetry-v1",
                      "last_received_at": "2026-09-28T19:59:00Z", "duplicate_identities": 0},
        "preopen_state": {"health": True, "expected_after": "2026-09-28T12:00:00Z",
                          "last_observed_at": "2026-09-28T12:01:00Z"},
        "expected_sessions": ["2026-09-28"],
        "daily_reports": [{"event_id": "event-1", "occurred_at": "2026-09-28T19:55:00Z",
                           "payload": {"session": "2026-09-28", "strategy_version_id": "LIVE-1",
                                       "generated_at": "2026-09-28T19:54:00Z"}}],
        "weekly_report_due": False,
        "weekly_report": None,
        "required_research_artifacts": [],
        "evidence_available": True,
        "market_session_active": False,
    }


def codes(result):
    return {row["code"] for row in result["reasons"]}


class IntegrityTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(evaluate(evidence(), now=NOW)["state"], "HEALTHY")

    def test_session_representation_not_bar_density(self):
        item = evidence()
        item["expected_sessions"].append("2026-09-25")
        result = evaluate(item, now=NOW)
        self.assertEqual(result["state"], "DEGRADED")
        self.assertIn("DAILY_REPORT_MISSING", codes(result))
        self.assertNotIn("BAR_DENSITY", codes(result))

    def test_report_not_due_yet(self):
        item = evidence()
        item["expected_sessions"] = ["2026-09-29"]
        item["daily_reports"] = []
        self.assertEqual(evaluate(item, now=NOW)["state"], "HEALTHY")

    def test_stale_telemetry_during_market(self):
        item = evidence()
        item["market_session_active"] = True
        item["telemetry"]["last_received_at"] = "2026-09-28T19:40:00Z"
        self.assertIn("TELEMETRY_STALE", codes(evaluate(item, now=NOW)))

    def test_deployment_and_commit_disagreement(self):
        item = evidence()
        item["production_runtime"]["deployment_id"] = "older-deploy"
        item["production_runtime"]["git_commit"] = "b" * 40
        self.assertTrue({"DEPLOYMENT_MISMATCH", "SOURCE_COMMIT_MISMATCH"} <= codes(evaluate(item, now=NOW)))

    def test_service_rename_requires_runtime_name_update(self):
        item = evidence()
        item["railway_services"][0]["name"] = "new-current-name"
        self.assertIn("SERVICE_IDENTITY_DRIFT", codes(evaluate(item, now=NOW)))
        item["production_runtime"]["service_name"] = "new-current-name"
        self.assertEqual(evaluate(item, now=NOW)["state"], "HEALTHY")

    def test_duplicate_service_and_bad_command(self):
        item = evidence()
        item["railway_services"].append(dict(item["railway_services"][0]))
        item["railway_services"][0]["start_command"] = "uvicorn app.research_agent.service:app"
        self.assertTrue({"DUPLICATE_SERVICE_IDENTITY", "SERVICE_ROLE_COMMAND_MISMATCH"} <= codes(evaluate(item, now=NOW)))

    def test_missing_critical_evidence_fails_closed(self):
        item = evidence()
        item.pop("telemetry")
        item.pop("railway_services")
        self.assertEqual(evaluate(item, now=NOW)["state"], "BLOCKED")

    def test_weekly_and_preopen_freshness(self):
        item = evidence()
        item["weekly_report_due"] = True
        item["preopen_state"]["last_observed_at"] = "2026-09-25T12:00:00Z"
        self.assertTrue({"WEEKLY_REPORT_MISSING", "PREOPEN_SNAPSHOT_STALE"} <= codes(evaluate(item, now=NOW)))

    def test_malformed_report_and_research_dependency(self):
        item = evidence()
        item["daily_reports"] = [{"payload": {"session": "bogus"}}]
        item["required_research_artifacts"] = [{"reference": "artifact-A", "available": False}]
        self.assertTrue({"MALFORMED_DAILY_REPORT", "RESEARCH_ARTIFACT_UNAVAILABLE"} <= codes(evaluate(item, now=NOW)))

    def test_bounded_snapshot_has_no_secret_or_full_payload(self):
        item = evidence()
        item["production_runtime"]["admin_token"] = "secret-value"
        item["railway_services"][0]["variables"] = {"ALPACA_API_SECRET": "secret-value"}
        item["research_readiness"]["research_question_id"] = "secret-value"
        self.assertNotIn("secret-value", json.dumps(build_snapshot(item, now=NOW)))

    def test_canonical_research_readiness_is_consumed_without_reclassification(self):
        item = evidence()
        item["research_readiness"].update({
            "state": "BLOCKED", "blocker_count": 2,
            "blockers": [
                {"scope": "report", "code": "REPORT_INTEGRITY",
                 "reason_codes": ["INCOMPLETE_FORWARD_OUTCOMES"]},
                {"scope": "research_question", "code": "QUEUE_EVIDENCE_INTEGRITY",
                 "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"]},
            ],
        })
        result = evaluate(item, now=NOW)
        self.assertIn("RESEARCH_READINESS_BLOCKED", codes(result))
        snapshot = build_snapshot(item, now=NOW)["research_readiness"]
        self.assertEqual(snapshot["blocker_codes"], ["QUEUE_EVIDENCE_INTEGRITY", "REPORT_INTEGRITY"])
        self.assertEqual(snapshot["reason_codes"], ["CANONICAL_OPERATIONAL_INCIDENTS", "INCOMPLETE_FORWARD_OUTCOMES"])
        self.assertNotIn("research_question_id", snapshot)

    def test_research_readiness_missing_or_inconsistent_fails_closed(self):
        item = evidence()
        item.pop("research_readiness")
        self.assertIn("RESEARCH_READINESS_UNAVAILABLE", codes(evaluate(item, now=NOW)))
        item["research_readiness"] = {"state": "READY", "cadence": "daily", "blocker_count": 1,
                                      "blockers": [{"code": "REPORT_INTEGRITY", "reason_codes": []}],
                                      "evidence_cutoff": "2026-09-28T19:00:00Z",
                                      "read_only": True, "model_invoked": False, "persisted": False}
        self.assertIn("RESEARCH_READINESS_INCONSISTENT", codes(evaluate(item, now=NOW)))
        item["research_readiness"]["model_invoked"] = True
        self.assertIn("RESEARCH_READINESS_MALFORMED", codes(evaluate(item, now=NOW)))

    def test_current_role_map_verifies_source_and_command(self):
        mapping = load_role_map()
        status = [{"id": row["service_id"], "name": row["expected_name"],
                   "latestDeployment": {"status": "SUCCESS"},
                   "cronSchedule": "5 22 * * 1-5" if row["role"] == "research_scheduler" else None}
                  for row in mapping["assignments"]]
        configs = {row["service_id"]: {
            "start_command": f"uvicorn {row['command_marker']}" if row["role"] != "research_scheduler" else "./run.sh encoded",
            "source": {"repo": row["source_repo"]} if row.get("source_repo") else {"image": row["source_image"]},
        } for row in mapping["assignments"]}
        normalized = normalize_services(status, configs, project_id=mapping["project_id"],
                                        environment_id=mapping["environment_id"])
        self.assertEqual({row["role"] for row in normalized},
                         {"production_trading", "research_agent", "preopen_state", "research_scheduler"})
        self.assertIn("SHADOW_SERVICE_UNRESOLVED", codes(evaluate({**evidence(), "railway_services": normalized}, now=NOW)))
        # The old private endpoint label does not override current source/command.
        configs[status[1]["id"]]["privateNetworkEndpoint"] = "alpaca-trader-shadow"
        self.assertEqual(normalize_services(status, configs)[1]["role"], "research_agent")
        # A reused stable ID with a changed command loses the old mapped role.
        configs[status[1]["id"]]["start_command"] = "uvicorn app.main:app"
        repurposed = normalize_services(status, configs)
        self.assertEqual(repurposed[1]["role"], "unclassified")
        self.assertIn("SERVICE_ROLE_MAPPING_DRIFT", codes(evaluate({**evidence(), "railway_services": repurposed}, now=NOW)))
        configs[status[1]["id"]]["start_command"] = "uvicorn app.research_agent.service:app"
        status[1]["name"] = "renamed-current-agent"
        renamed = normalize_services(status, configs)
        self.assertEqual(renamed[1]["role"], "research_agent")
        self.assertIn("SERVICE_NAME_DRIFT", codes(evaluate({**evidence(), "railway_services": renamed}, now=NOW)))

    def test_role_map_rejects_duplicate_identity_and_wrong_project(self):
        mapping = load_role_map()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "roles.json"
            mapping["assignments"][1]["service_id"] = mapping["assignments"][0]["service_id"]
            path.write_text(json.dumps(mapping))
            with self.assertRaises(ValueError):
                load_role_map(path)
        with self.assertRaises(ValueError):
            normalize_services([], {}, project_id="wrong-project")


class ContractTests(unittest.TestCase):
    def test_adapter_uses_canonical_report_and_current_service_ids(self):
        base = evidence()
        mapping = load_role_map()
        prod_id = mapping["assignments"][0]["service_id"]
        pre_id = mapping["assignments"][2]["service_id"]
        base["production_runtime"]["service_id"] = prod_id
        base["production_runtime"]["service_name"] = "alpaca-trader"
        status = [{"id": prod_id, "name": "alpaca-trader",
                   "latestDeployment": {"id": "deploy-1", "status": "SUCCESS"}},
                  {"id": pre_id, "name": "rhen-preopen-state",
                   "latestDeployment": {"status": "SUCCESS"}}]
        configs = {prod_id: {"start_command": "uvicorn app.main:app", "source_commit": "a" * 40,
                             "source_repo": "anevum/alpaca-trader"},
                   pre_id: {"start_command": "uvicorn app.preopen_state.service:app",
                            "source_repo": "anevum/alpaca-trader"}}
        adapted = from_canonical_sources(
            command_evidence={"evidence_version": "rhen-command-evidence-v1",
                              "provenance": {"runtime": {**base["production_runtime"]},
                                             "latest_scan_cycle": {"scan_cycle_id": "scan-1"}},
                              "telemetry_health": {"latest_received_at": "2026-09-28T19:59:00Z"}},
            period_inputs={"strategy_versions": [{"version_id": "LIVE-1", "strategy_name": "rolling_momentum_vwap"}],
                           "daily_reports": base["daily_reports"], "duplicate_checks": {}},
            expected_sessions=["2026-09-28"], railway_status=status, railway_configs=configs,
            railway_project_id=mapping["project_id"], railway_environment_id=mapping["environment_id"],
            research_readiness={**base["research_readiness"], "research_question_id": "private-id"},
            preopen_health={"ok": True}, preopen_expected_after=None,
            market_session_active=False, weekly_report_due=False,
        )
        self.assertEqual(adapted["railway_services"][0]["id"], prod_id)
        self.assertEqual(adapted["production_runtime"]["strategy_name"], "rolling_momentum_vwap")
        self.assertNotIn("private-id", json.dumps(adapted))
        self.assertIn("SHADOW_SERVICE_UNRESOLVED", codes(evaluate(adapted, now=NOW)))

    def test_registry_validation(self):
        registry = load_registry()
        self.assertEqual(len(registry["agents"]), 3)
        self.assertFalse(next(a for a in registry["agents"] if a["role"] == "coordination")["automatic_invocation_allowed"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "registry.json"
            path.write_text(json.dumps({**registry, "agents": registry["agents"][:2]}))
            with self.assertRaises(ValueError):
                load_registry(path)

    def test_alert_dedup_and_resolution(self):
        reason = {"code": "TELEMETRY_STALE", "state": "BLOCKED", "source": "telemetry", "reference": ""}
        first = reconcile([reason], {}, observed_at=NOW, assessed_sources={"telemetry"})
        self.assertEqual(len(first), 1)
        again = reconcile([reason], {first[0]["alert_key"]: first[0]}, observed_at=NOW,
                          assessed_sources={"telemetry"})
        self.assertEqual(again[0]["alert_key"], first[0]["alert_key"])
        self.assertEqual(again[0]["first_observed_at"], first[0]["first_observed_at"])
        self.assertEqual(reconcile([], {first[0]["alert_key"]: first[0]}, observed_at=NOW,
                                   assessed_sources=set()), [])
        resolved = reconcile([], {first[0]["alert_key"]: first[0]}, observed_at=NOW,
                             assessed_sources={"telemetry"})
        self.assertEqual(resolved[0]["state"], "RESOLVED")
        self.assertEqual(reconcile([], {}, observed_at=NOW, assessed_sources={"telemetry"}), [])

    def test_static_current_state_invariants(self):
        package = Path(__file__).parents[1] / "app" / "agent_support"
        for path in package.glob("*.py"):
            self.assertNotIn("alpaca-trader-shadow", path.read_text())
        migration = (Path(__file__).parents[1] / "database" /
                     "20260927195537_rhen_agent_support_alerts.sql").read_text()
        self.assertIn("enable row level security", migration)
        self.assertNotIn("grant all", migration.lower())


if __name__ == "__main__":
    unittest.main()
