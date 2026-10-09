"""Frozen daily research decisions must not imply proven alpha or trading authority."""
from __future__ import annotations

from app.research_agent.rule_set_agenda import build_rule_set_agenda
from app.rule_set_lab import parse_rule_sets


def complete_report():
    return {
        "session": "2026-10-09",
        "source_fingerprint": "a" * 64,
        "strategy_version_id": "LIVE-2026-09-25-003",
        "broker_history": {"pagination": "EXHAUSTED_WITHIN_BOUNDS", "fill_count": 0},
        "candidate_forward_evidence": {
            "status": {"incomplete_rows": 0, "error_rows": 0},
            "readiness": {"state": "READY"},
            "cohort_audit": {
                "audit_version": "rhen-candidate-cohort-audit-v1",
                "state": "READY",
                "candidate_count": 30,
                "complete_15m": 30,
                "coverage_15m": "1",
                "blocking_reasons": [],
                "cohort_fingerprint": "c" * 64,
            },
        },
        "reconstruction": {"unmatched_sell_qty": {}, "included_fill_count": 0, "excluded_fill_count": 0},
        "runtime": {"persistence": {"shed_count": 0}},
        "live_vs_offline_consistency": {"summary": []},
        "data_quality_warnings": [],
    }


def test_agenda_uses_predeclared_structural_gates_and_frozen_control():
    report = complete_report()
    agenda = build_rule_set_agenda(report)
    assert agenda["research_state"] == "READY_FOR_BOUNDED_OFFLINE_SCREEN"
    assert agenda["blocking_evidence"] == []
    assert agenda["research_only"] is True
    assert agenda["execution_authority"] is False
    assert agenda["automatic_promotion_authorized"] is False
    assert agenda["live_broker_fill_parity"] == "UNVERIFIED"
    assert agenda["frozen_research_manifest"]["control_strategy"] == report["strategy_version_id"]
    slate = agenda["frozen_research_manifest"]["rule_sets"]
    assert len(slate) == 3
    assert all(len(x["entry_rules"]) <= 2 for x in slate)
    assert len(parse_rule_sets(list(slate))) == 3
    assert agenda["manifest_sha256"] == build_rule_set_agenda(report)["manifest_sha256"]


def test_agenda_does_not_unlock_research_without_broker_and_candidate_evidence():
    report = complete_report()
    report.pop("broker_history")
    report["candidate_forward_evidence"]["readiness"]["state"] = "PARTIAL"
    report["candidate_forward_evidence"]["status"]["incomplete_rows"] = 3
    report["runtime"]["persistence"]["shed_count"] = 1
    report["data_quality_warnings"] = ["Canonical storage shed analytics"]
    result = build_rule_set_agenda(report)
    assert result["research_state"] == "AWAITING_COMPLETE_EVIDENCE"
    assert "BROKER_FILL_ORDER_PAGINATION_UNVERIFIED" in result["blocking_evidence"]
    assert "ANALYTICS_RETENTION_LOSS" in result["blocking_evidence"]
    assert "CANDIDATE_FORWARD_OUTCOMES_INCOMPLETE" in result["blocking_evidence"]
    assert "CANDIDATE_FORWARD_READINESS_NOT_VERIFIED" in result["blocking_evidence"]
    assert result["automatic_promotion_authorized"] is False


def test_invalid_source_or_unreconstructable_trades_blocks_research():
    report = complete_report()
    report["source_fingerprint"] = None
    report["reconstruction"]["unmatched_sell_qty"] = {"SPY": "0.125"}
    report["live_vs_offline_consistency"]["summary"] = [
        {"unreconstructable": 2}
    ]
    result = build_rule_set_agenda(report)
    assert "SOURCE_FINGERPRINT_MISSING" in result["blocking_evidence"]
    assert "UNMATCHED_BROKER_FILLS" in result["blocking_evidence"]
    assert "LIVE_VS_REPLAY_EVENTS_UNRECONSTRUCTABLE" in result["blocking_evidence"]


def test_agenda_identity_changes_with_immutable_evidence_source():
    a = complete_report()
    b = complete_report()
    b["source_fingerprint"] = "b" * 64
    assert build_rule_set_agenda(a)["manifest_sha256"] != build_rule_set_agenda(b)["manifest_sha256"]

def test_unverified_15m_cohort_cannot_claim_screening_readiness():
    report = complete_report()
    report["candidate_forward_evidence"].pop("cohort_audit")
    agenda = build_rule_set_agenda(report)
    assert agenda["research_state"] == "AWAITING_COMPLETE_EVIDENCE"
    assert "CANONICAL_15M_COHORT_AUDIT_MISSING" in agenda["blocking_evidence"]


def test_15m_coverage_does_not_pass_when_sample_is_small():
    report = complete_report()
    report["candidate_forward_evidence"]["cohort_audit"]["candidate_count"] = 4
    result = build_rule_set_agenda(report)
    assert "CANONICAL_15M_COHORT_COVERAGE_INSUFFICIENT" in result["blocking_evidence"]


def test_unreconstructable_state_count_is_read_from_actual_projection():
    report = complete_report()
    report["live_vs_offline_consistency"]["summary"] = [
        {"session": "2026-10-09", "match_state": "UNRECONSTRUCTABLE", "count": 2}
    ]
    result = build_rule_set_agenda(report)
    assert "LIVE_VS_REPLAY_EVENTS_UNRECONSTRUCTABLE" in result["blocking_evidence"]

def test_fill_audit_cannot_hide_unattributed_or_missing_activity():
    report = complete_report()
    report["broker_history"]["fill_count"] = 1
    result = build_rule_set_agenda(report)
    assert "BROKER_FILL_AUDIT_COUNTS_DO_NOT_RECONCILE" in result["blocking_evidence"]
    report["reconstruction"]["excluded_fill_count"] = 1
    result = build_rule_set_agenda(report)
    assert "BROKER_FILL_ATTRIBUTION_EXCLUSIONS_REQUIRE_AUDIT" in result["blocking_evidence"]
    report["reconstruction"].pop("included_fill_count")
    result = build_rule_set_agenda(report)
    assert "BROKER_FILL_AUDIT_COUNTS_MISSING_OR_INVALID" in result["blocking_evidence"]
