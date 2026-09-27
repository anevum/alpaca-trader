from datetime import datetime, timezone

from app.research_agent.models import ResearchQuestionSnapshot
from app.research_agent.queue import build_queue, latest_question_snapshots


def snapshot(
    record_id,
    stable_id,
    created_at,
    *,
    status="MONITOR",
    evidence=None,
    reviewed=None,
):
    return ResearchQuestionSnapshot(
        question_record_id=record_id,
        research_question_id=stable_id,
        created_on="2026-09-27",
        question=stable_id,
        why_it_matters="fixture",
        status=status,
        evidence_summary=evidence or {},
        created_at=created_at,
        last_reviewed_at=reviewed,
    )


def test_latest_snapshot_per_stable_id_wins_without_duplicate_queue_items():
    rows = [
        snapshot("old", "RQ-1", "2026-09-26T00:00:00Z"),
        snapshot(
            "new",
            "RQ-1",
            "2026-09-27T00:00:00Z",
            evidence={"all_recorded_as_operational": True},
        ),
        snapshot("second", "RQ-2", "2026-09-27T00:00:00Z"),
    ]
    latest = latest_question_snapshots(rows)
    queue = build_queue(rows)
    assert {row.question_record_id for row in latest} == {"new", "second"}
    assert [row.research_question_id for row in queue].count("RQ-1") == 1


def test_deterministic_tie_breaking_prefers_integrity_severity_recurrence_then_oldest():
    rows = [
        snapshot(
            "q1",
            "RQ-1",
            "2026-09-27T00:00:00Z",
            reviewed="2026-09-26T12:00:00Z",
            evidence={
                "all_recorded_as_operational": True,
                "priority_inputs": {"severity": 2, "recurrence": 1},
            },
        ),
        snapshot(
            "q2",
            "RQ-2",
            "2026-09-27T00:00:00Z",
            reviewed="2026-09-25T12:00:00Z",
            evidence={
                "all_recorded_as_operational": True,
                "priority_inputs": {"severity": 2, "recurrence": 1},
            },
        ),
    ]
    queue = build_queue(rows)
    assert [row.research_question_id for row in queue] == ["RQ-2", "RQ-1"]


def test_closed_historical_snapshot_is_not_open_queue_work():
    rows = [snapshot("closed", "RQ-CLOSED", "2026-09-27T00:00:00Z", status="CLOSED")]
    assert build_queue(rows) == []


def test_monitor_operational_question_is_visible_but_nonblocking():
    queue = build_queue(
        [
            snapshot(
                "monitor",
                "RQ-OP",
                "2026-09-27T00:00:00Z",
                status="MONITOR",
                evidence={"all_recorded_as_operational": True},
            )
        ]
    )
    assert len(queue) == 1
    assert queue[0].classification.category.value == "OPERATIONAL_DEFECT"
    assert queue[0].classification.evidence_integrity_blocker is False
