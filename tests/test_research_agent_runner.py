import json

from app.research_agent.evidence import CanonicalEvidenceReader
from app.research_agent.runner import ResearchAgentRunner
from scripts.rhen_research_agent import main


def canonical_fixture():
    return {
        "evidence_cutoff": "2026-09-27T02:27:26Z",
        "current_strategy": {
            "version_id": "LIVE-2026-09-25-003",
            "strategy_name": "rolling_momentum_vwap",
            "status": "active",
        },
        "latest_daily_report": {
            "report_key": "daily-1",
            "session": "2026-09-25",
            "generated_at": "2026-09-26T16:22:24Z",
            "candidate_forward_evidence": {
                "status": {
                    "complete_rows": 135,
                    "incomplete_rows": 9,
                    "error_rows": 0
                }
            },
            "live_vs_offline_consistency": {
                "summary": [{"unreconstructable": 1}]
            },
        },
        "latest_weekly_report": {
            "report_key": "weekly-1",
            "period_end": "2026-09-25",
            "generated_at": "2026-09-27T02:27:25Z",
            "completeness_state": "PARTIAL",
            "missing_trading_sessions": ["2026-09-21"],
        },
        "research_questions": [
            {
                "question_record_id": "old-op",
                "research_question_id": "RQ-OP",
                "created_on": "2026-09-26",
                "source_weekly_report_id": "weekly-old",
                "evidence_summary": {"all_recorded_as_operational": True},
                "sample_size": 1,
                "question": "fixture",
                "why_it_matters": "fixture",
                "required_data": [],
                "status": "MONITOR",
                "created_at": "2026-09-26T00:00:00Z",
            },
            {
                "question_record_id": "new-op",
                "research_question_id": "RQ-OP",
                "created_on": "2026-09-27",
                "source_weekly_report_id": "weekly-new",
                "evidence_summary": {
                    "all_recorded_as_operational": True,
                    "priority_inputs": {"severity": 2, "recurrence": 1},
                },
                "sample_size": 2,
                "question": "fixture",
                "why_it_matters": "fixture",
                "required_data": [],
                "status": "MONITOR",
                "created_at": "2026-09-27T00:00:00Z",
            },
            {
                "question_record_id": "strategy",
                "research_question_id": "RQ-STRATEGY",
                "created_on": "2026-09-27",
                "source_weekly_report_id": "weekly-new",
                "evidence_summary": {
                    "thesis_exit_count": 9,
                    "sessions_observed": 1,
                    "evidence_strength": "observed once"
                },
                "sample_size": 9,
                "question": "fixture",
                "why_it_matters": "fixture",
                "required_data": ["multiple independent sessions"],
                "status": "MONITOR",
                "created_at": "2026-09-27T00:00:00Z",
            },
        ],
        "experiments": [
            {
                "experiment_key": "edge-corpus-v1",
                "status": "rejected",
                "survivor_state": "all_rejected",
                "created_at": "2026-09-26T13:28:46Z",
            },
            {
                "experiment_key": "edge-discovery-v2-residual-downshock-rebound-v2.1",
                "status": "completed",
                "stage_reached": "development_corpus_gate",
                "survivor_state": "corpus_quality_failed_pre_performance",
                "terminal_decision": "DEVELOPMENT CORPUS FAIL; performance evaluation not run",
                "validation_eligible": False,
                "validation_opened": False,
                "holdout_opened": False,
                "quarantine_accessed": False,
                "created_at": "2026-09-26T13:28:46Z",
            },
        ],
        "research_decisions": [
            {
                "decision_key": "edge-discovery-v1-terminal",
                "status": "final",
                "decided_at": "2026-09-26T06:21:41Z",
            },
            {
                "decision_key": "obsolete",
                "status": "draft",
                "decided_at": "2026-09-26T06:21:41Z",
            },
        ],
        "agent_runs": [],
    }


def runner():
    return ResearchAgentRunner(CanonicalEvidenceReader(canonical_fixture()).read())


def test_status_reads_current_strategy_terminal_rdr_and_closed_edge_discovery():
    status = runner().status()
    assert status["current_strategy"] == {
        "version_id": "LIVE-2026-09-25-003",
        "strategy_name": "rolling_momentum_vwap",
    }
    assert status["rdr_v2_1"]["interpretation"] == (
        "CORPUS_QUALITY_FAILURE_NOT_STRATEGY_REJECTION"
    )
    assert status["edge_discovery_v1"]["closed"] is True
    assert status["llm_usage"]["invoked"] is False
    asc = status["adaptive_strategy_control"]
    assert asc["methodology_version"] == "asc-strategy-health-v1"
    assert asc["read_only"] is True
    assert asc["execution_authority"] is False
    assert asc["live_configuration_changed"] is False
    assert asc["proposal_count"] == 0
    assert asc["automatic_application_authorized"] is False


def test_daily_and_weekly_dry_runs_have_no_mutations_or_model_calls():
    daily = runner().daily_review(dry_run=True)
    weekly = runner().weekly_review(dry_run=True)
    for result in (daily, weekly):
        assert result["llm_usage"]["invoked"] is False
        assert result["experiment_recommendation"] is None
        assert result["mutations"] == {
            "audit_record_persisted": False,
            "research_questions_written": 0,
            "experiments_written": 0,
            "research_stages_opened": 0,
            "strategy_changes": 0,
            "broker_calls": 0,
            "market_bar_reads": 0,
        }
        assert len(result["queue"]) == 2
    assert daily["semantic_review_warranted"] is False
    assert daily["queue_blocker_count"] == 0
    assert daily["report_integrity_blocker"] is False
    assert daily["blocker_count"] == 0
    assert daily["ready_strategy_question_count"] == 0
    assert daily["waiting_strategy_question_count"] == 1
    assert weekly["semantic_review_warranted"] is False
    assert weekly["queue_blocker_count"] == 0
    assert weekly["report_integrity_blocker"] is True
    assert weekly["blocker_count"] == 1


def test_readiness_exposes_waiting_state_and_nonblocking_limitations():
    readiness = runner().readiness(cadence="daily")
    assert readiness["state"] == "WAITING"
    assert readiness["gpt_would_run_now"] is False
    assert readiness["blocker_count"] == 0
    assert readiness["limitation_count"] == 1
    assert readiness["monitor_count"] == 1
    assert readiness["strategy_question_count"] == 1
    assert readiness["ready_strategy_question_count"] == 0
    assert readiness["waiting_strategy_question_count"] == 1
    assert readiness["read_only"] is True
    assert readiness["model_invoked"] is False
    assert readiness["persisted"] is False
    assert readiness["limitations"][0]["code"] == "KNOWN_EVIDENCE_LIMITATION"
    assert readiness["monitors"][0]["research_question_id"] == "RQ-OP"
    strategy = readiness["strategy_questions"][0]
    assert strategy["research_question_id"] == "RQ-STRATEGY"
    assert strategy["semantic_readiness"] == "WAITING"
    assert strategy["missing_requirements"] == ["MULTIPLE_INDEPENDENT_SESSIONS"]


def test_strategy_question_becomes_ready_after_second_independent_session():
    fixture = canonical_fixture()
    strategy = next(
        row for row in fixture["research_questions"]
        if row["research_question_id"] == "RQ-STRATEGY"
    )
    strategy["evidence_summary"]["sessions_observed"] = 2
    value = ResearchAgentRunner(CanonicalEvidenceReader(fixture).read())
    review = value.daily_review(dry_run=True)
    readiness = value.readiness(cadence="daily")
    assert review["semantic_review_warranted"] is True
    assert review["ready_strategy_question_count"] == 1
    assert review["waiting_strategy_question_count"] == 0
    assert readiness["state"] == "READY"
    assert readiness["gpt_would_run_now"] is True


def test_duplicate_fingerprint_is_recognized_without_duplicate_state():
    value = runner()
    first = value.daily_review(dry_run=True)
    second = value.daily_review(dry_run=True)
    assert first["run_key"] == second["run_key"]
    assert first["input_fingerprint"] == second["input_fingerprint"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True


def test_cli_foundation_commands(tmp_path, capsys):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(canonical_fixture()))
    assert main(["--evidence-file", str(path), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "DETERMINISTIC_ONLY"
    assert main(
        ["--evidence-file", str(path), "daily-review", "--dry-run"]
    ) == 0
    daily = json.loads(capsys.readouterr().out)
    assert daily["mode"] == "DRY_RUN"
    assert main(
        ["--evidence-file", str(path), "weekly-review", "--dry-run"]
    ) == 0
    weekly = json.loads(capsys.readouterr().out)
    assert weekly["mode"] == "DRY_RUN"



def test_status_exposes_bounded_proposal_only_when_control_state_escalates():
    fixture = canonical_fixture()
    fixture["latest_weekly_report"]["metrics"] = {
        "completed_trades": 50,
        "expectancy": "-0.10",
        "profit_factor": "0.70",
        "win_rate": "0.30",
    }
    fixture["latest_daily_report"]["counterfactual_lab"] = {
        "baseline_parameters": {
            "min_momentum_pct": "0.0020",
        },
        "proposal_ready_parameters": ["min_momentum_pct"],
        "rolling_searches": {
            "min_momentum_pct": {
                "validity_passed": True,
                "results": [
                    {
                        "counterfactual_id": "CFA-READY",
                        "requested_value": "0.0018",
                        "evidence_score": "0.8",
                        "expected_improvement": "0.001",
                        "confidence": "0.8",
                        "selection_bias_status": "CONTROLLED",
                        "dependence_status": "CONTROLLED",
                        "validity_passed": True,
                    }
                ],
            }
        },
    }
    value = ResearchAgentRunner(CanonicalEvidenceReader(fixture).read())
    status = value.status()
    asc = status["adaptive_strategy_control"]
    assert asc["control_state"] == "RESEARCH"
    assert asc["proposal_count"] == 1
    proposal = asc["parameter_proposals"]["min_momentum_pct"]
    assert proposal["authorization_required"] is True
    assert proposal["automatic_application_authorized"] is False
    assert proposal["execution_authority"] is False
    assert proposal["source_strategy_version"] == "LIVE-2026-09-25-003"


def test_missing_daily_report_is_a_fail_closed_blocker_not_a_crash():
    fixture = canonical_fixture()
    fixture["latest_daily_report"] = None
    value = ResearchAgentRunner(CanonicalEvidenceReader(fixture).read())

    review = value.daily_review(dry_run=True)
    assert review["trigger_reference"] == "CANONICAL_DAILY_REPORT_MISSING"
    assert review["report_integrity_blocker"] is True
    assert review["blocker_count"] >= 1
    assert review["semantic_review_warranted"] is False
    assert review["llm_usage"]["invoked"] is False
    assert review["mutations"]["research_stages_opened"] == 0
    assert review["mutations"]["strategy_changes"] == 0
    assert review["mutations"]["broker_calls"] == 0
    assert "INCOMPLETE_CANONICAL_EVIDENCE" in review["classification"]["reason_codes"]

    readiness = value.readiness(cadence="daily")
    assert readiness["state"] == "BLOCKED"
    assert readiness["gpt_would_run_now"] is False
    assert readiness["blocker_count"] >= 1
    assert readiness["model_invoked"] is False
    assert readiness["persisted"] is False


def test_missing_weekly_report_is_also_fail_closed():
    fixture = canonical_fixture()
    fixture["latest_weekly_report"] = None
    value = ResearchAgentRunner(CanonicalEvidenceReader(fixture).read())

    review = value.weekly_review(dry_run=True)
    assert review["trigger_reference"] == "CANONICAL_WEEKLY_REPORT_MISSING"
    assert review["report_integrity_blocker"] is True
    assert review["semantic_review_warranted"] is False
    assert review["llm_usage"]["invoked"] is False


def test_foundation_verification_strategy_blocks_semantic_review():
    fixture = canonical_fixture()
    fixture["current_strategy"] = {
        "version_id": "FOUNDATION-RECONCILE-PROBE-001",
        "strategy_name": "FOUNDATION-RECONCILE-PROBE-001",
        "run_id": "foundation-order-test",
    }
    fixture["research_questions"][2]["status"] = "READY_FOR_RESEARCH"
    fixture["research_questions"][2]["evidence_summary"]["sessions_observed"] = 3
    value = ResearchAgentRunner(CanonicalEvidenceReader(fixture).read())

    review = value.daily_review(dry_run=True)
    assert review["strategy_identity_blocker"] is True
    assert review["semantic_review_warranted"] is False
    assert review["blocker_count"] >= 1
    assert review["llm_usage"]["invoked"] is False

    readiness = value.readiness(cadence="daily")
    assert readiness["state"] == "BLOCKED"
    assert readiness["gpt_would_run_now"] is False
    assert any(
        row["code"] == "NON_PRODUCTION_STRATEGY_IDENTITY"
        for row in readiness["blockers"]
    )
