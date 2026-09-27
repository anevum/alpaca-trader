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
                "status": {"incomplete_rows": 2, "error_rows": 0}
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
                "evidence_summary": {"thesis_exit_count": 9},
                "sample_size": 9,
                "question": "fixture",
                "why_it_matters": "fixture",
                "required_data": [],
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
    assert daily["queue_blocker_count"] == 1
    assert daily["report_integrity_blocker"] is True
    assert daily["blocker_count"] == 2
    assert weekly["semantic_review_warranted"] is False
    assert weekly["queue_blocker_count"] == 1
    assert weekly["report_integrity_blocker"] is True
    assert weekly["blocker_count"] == 2


def test_readiness_exposes_current_blockers_without_model_or_mutation():
    readiness = runner().readiness(cadence="daily")
    assert readiness["state"] == "BLOCKED"
    assert readiness["gpt_would_run_now"] is False
    assert readiness["blocker_count"] == 2
    assert readiness["strategy_question_count"] == 1
    assert readiness["read_only"] is True
    assert readiness["model_invoked"] is False
    assert readiness["persisted"] is False
    scopes = {row["scope"] for row in readiness["blockers"]}
    assert scopes == {"report", "research_question"}
    assert any(
        row.get("research_question_id") == "RQ-OP"
        for row in readiness["blockers"]
    )
    assert any(
        row.get("research_question_id") == "RQ-STRATEGY"
        for row in readiness["strategy_questions"]
    )


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
