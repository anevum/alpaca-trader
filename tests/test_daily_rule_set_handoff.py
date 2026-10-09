from __future__ import annotations

from pathlib import Path

import pytest

from app.research_agent.offline_experiment_queue import OfflineExperimentQueue
from app.research_agent.rule_set_agenda import build_rule_set_agenda
from scripts.daily_rule_set_handoff import (
    extract_daily_report, prepare_offline_handoff, persist_offline_handoff
)


def report():
    return {
        "report_type": "daily",
        "session": "2026-10-09",
        "source_fingerprint": "a" * 64,
        "strategy_version_id": "LIVE-2026-09-25-003",
        "broker_history": {"pagination": "EXHAUSTED_WITHIN_BOUNDS", "fill_count": 0},
        "reconstruction": {"unmatched_sell_qty": {}, "included_fill_count": 0, "excluded_fill_count": 0},
        "data_quality_warnings": [],
        "runtime": {"persistence": {"shed_count": 0}},
        "candidate_forward_evidence": {
            "status": {},
            "readiness": {"state": "READY"},
            "cohort_audit": {
                "audit_version": "rhen-candidate-cohort-audit-v1",
                "state": "READY",
                "candidate_count": 30,
                "complete_15m": 30,
                "coverage_15m": "1",
                "blocking_reasons": [],
                "cohort_fingerprint": "b" * 64,
            },
        },
        "live_vs_offline_consistency": {"summary": []},
    }


def test_canonical_nested_package_extracts_same_report_and_frozen_slate():
    original = report()
    package = {"evidence": {"latest_daily_report": original}}
    assert extract_daily_report(package) == original
    plan = prepare_offline_handoff(package)
    assert plan["agenda_state"] == "READY_FOR_BOUNDED_OFFLINE_SCREEN"
    assert len(plan["proposals"]) == 3
    assert all(spec["authority"] == "OFFLINE_RESEARCH_ONLY" for spec in plan["proposals"])
    assert all(spec["live_change_authorized"] is False for spec in plan["proposals"])
    assert all(spec["promotion_authorized"] is False for spec in plan["proposals"])


def test_offline_queue_persists_experiments_but_never_promotes(tmp_path):
    plan = prepare_offline_handoff(report())
    target = tmp_path / "isolated-research.sqlite"
    first = persist_offline_handoff(plan, db_path=target)
    second = persist_offline_handoff(plan, db_path=target)
    queue = OfflineExperimentQueue(target)
    assert first == second
    assert len(queue.list()) == 3
    assert all(item["state"] == "AWAITING_EVIDENCE" for item in queue.list())
    assert all(item["research_only"] for item in queue.list())
    assert all(item["execution_authority"] is False for item in queue.list())


def test_broken_data_report_queues_blocked_hypotheses_without_claiming_alpha(tmp_path):
    incomplete = report()
    incomplete["candidate_forward_evidence"].pop("cohort_audit")
    plan = prepare_offline_handoff(incomplete)
    assert plan["agenda_state"] == "AWAITING_COMPLETE_EVIDENCE"
    assert "CANONICAL_15M_COHORT_AUDIT_MISSING" in plan["blocking_evidence"]
    saved = persist_offline_handoff(
        plan, db_path=tmp_path / "offline.sqlite"
    )
    assert all(x["state"] == "AWAITING_EVIDENCE" for x in saved["experiment_queue"])


def test_mismatched_or_missing_canonical_agenda_identity_is_rejected():
    daily = report()
    daily["rule_set_research_agenda"] = build_rule_set_agenda(daily)
    assert len(prepare_offline_handoff(daily)["proposals"]) == 3
    daily["rule_set_research_agenda"]["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        prepare_offline_handoff(daily)
    daily = report()
    daily.pop("source_fingerprint")
    with pytest.raises(ValueError, match="identity"):
        prepare_offline_handoff(daily)
