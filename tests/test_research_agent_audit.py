from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.research_agent.audit import (
    DuplicateRunKey,
    InMemoryAuditRepository,
    complete_run,
    create_run,
    deterministic_run_key,
    fail_run,
    input_fingerprint,
    run_record,
)
from app.research_agent.models import AgentRunStatus


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def running():
    fingerprint = input_fingerprint({"b": 2, "a": 1})
    return create_run(
        run_id=RUN_ID,
        run_key=deterministic_run_key(
            cadence="daily",
            trigger_reference="2026-09-25",
            fingerprint=fingerprint,
        ),
        source_commit="c54c19f",
        trigger="daily",
        trigger_reference="2026-09-25",
        started_at=NOW,
        input_fingerprint_value=fingerprint,
    )


def test_fingerprint_and_run_key_are_deterministic():
    assert input_fingerprint({"a": 1, "b": 2}) == input_fingerprint(
        {"b": 2, "a": 1}
    )
    assert running().run_key.startswith("research-agent:v1:daily:2026-09-25:")


def test_unique_run_keys_and_llm_usage_false():
    repo = InMemoryAuditRepository()
    run = running()
    repo.create(run)
    with pytest.raises(DuplicateRunKey):
        repo.create(run)
    assert run.llm_usage["invoked"] is False


def test_completion_and_failure_record_status_without_hidden_reasoning():
    run = complete_run(
        running(),
        status=AgentRunStatus.COMPLETED,
        rationale_summary={
            "conclusion": "deterministic review complete",
            "supporting_evidence": ["fixture"],
            "contradicting_evidence": [],
            "uncertainties": [],
        },
        completed_at=NOW,
    )
    record = run_record(run)
    assert record["status"] == "COMPLETED"
    assert record["completed_at"] == "2026-09-27T00:00:00Z"
    assert "chain_of_thought" not in record
    failed = fail_run(
        running(),
        code="TEST",
        message="synthetic",
        completed_at=NOW,
    )
    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_summary == {"code": "TEST", "message": "synthetic"}


def test_unapproved_rationale_field_is_rejected():
    with pytest.raises(ValueError):
        complete_run(
            running(),
            status=AgentRunStatus.COMPLETED,
            rationale_summary={"private_reasoning": "not permitted"},
        )

