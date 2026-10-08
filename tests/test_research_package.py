from datetime import datetime, timezone

from app.research_agent.package import (
    SCHEMA_VERSION,
    build_research_package,
)


UTC = timezone.utc


def test_research_package_is_bounded_read_only_and_work_ready():
    canonical = {
        "current_strategy": {
            "strategy_version_id": "RHEN-TEST",
            "strategy_name": "test-strategy",
            "git_commit": "abc123",
        },
        "evidence_cutoff": "2026-10-08T20:30:00+00:00",
        "latest_daily_report": {
            "session": "2026-10-08",
            "strategy_version_id": "RHEN-TEST",
            "classification": "INVESTIGATE",
            "performance": {
                "closed_trades": 8,
                "return_pct": -0.0004,
                "win_rate": 0.375,
            },
            "evidence": {
                "candidate_count": 42,
                "qualified_count": 8,
                "blocker_count": 0,
            },
        },
        "latest_weekly_report": {"session": "2026-10-08"},
        "agent_runs": [{"run_key": "daily:2026-10-08"}],
        "search_ledger": {
            "exposure": {
                "hypothesis_count": 2,
                "production_authority": False,
            },
            "recent_hypotheses": [
                {"id": "h1", "title": "Entry selectivity"},
                {"id": "h2", "title": "Exit timing"},
            ],
            "recent_events": [{"event": "review"}],
        },
    }
    readiness = {
        "state": "WAITING",
        "blocker_count": 0,
        "limitation_count": 1,
        "strategy_question_count": 1,
        "strategy_questions": [
            {
                "research_question_id": "q1",
                "question": "Does stricter entry quality improve expectancy?",
                "semantic_readiness": "WAITING",
            }
        ],
    }
    forecasts = {
        "forecasts": [
            {
                "forecast_id": "f1",
                "research_only": True,
                "execution_authority": False,
            }
        ]
    }

    package = build_research_package(
        canonical,
        readiness,
        forecasts,
        generated_at=datetime(2026, 10, 8, 21, 0, tzinfo=UTC),
    )

    assert package["ok"] is True
    assert package["schema_version"] == SCHEMA_VERSION
    assert package["research_only"] is True
    assert package["execution_authority"] is False
    assert package["broker_write_authority"] is False
    assert package["live_strategy_mutation_authority"] is False
    assert package["production_promotion_authority"] is False
    assert package["model_required"] is False
    assert package["summary"]["strategy_version_id"] == "RHEN-TEST"
    assert package["summary"]["readiness_state"] == "WAITING"
    assert package["summary"]["active_forecast_count"] == 1
    assert package["summary"]["hypothesis_count"] == 2
    assert package["summary"]["latest_session"]["closed_trades"] == 8
    assert len(package["content_fingerprint"]) == 64

    markdown = package["handoff_markdown"]
    assert "# RHEN Research Handoff" in markdown
    assert "No broker-write authority." in markdown
    assert "No automatic promotion authority." in markdown
    assert "Does stricter entry quality improve expectancy?" in markdown
    assert "identify at most one highest-information bounded experiment" in markdown


def test_research_package_handles_missing_session_evidence_without_fabrication():
    package = build_research_package(
        {
            "current_strategy": {"strategy_version_id": "RHEN-TEST"},
            "evidence_cutoff": None,
            "latest_daily_report": None,
            "latest_weekly_report": None,
            "agent_runs": [],
            "search_ledger": {},
        },
        {
            "state": "IDLE",
            "blocker_count": 0,
            "limitation_count": 0,
            "strategy_question_count": 0,
            "strategy_questions": [],
        },
        {"forecasts": []},
        generated_at=datetime(2026, 10, 8, 21, 0, tzinfo=UTC),
    )

    assert package["summary"]["latest_session"]["available"] is False
    assert package["summary"]["latest_session"].get("session") is None
    assert "Session: unavailable" in package["handoff_markdown"]
    assert package["summary"]["active_forecast_count"] == 0
    assert package["summary"]["hypothesis_count"] == 0
