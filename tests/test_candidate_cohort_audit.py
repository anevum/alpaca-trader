from __future__ import annotations

from app.research_agent.candidate_cohort_audit import audit_candidate_cohort


def candidate(i: int):
    return {
        "candidate_id": f"c-{i}",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "observed_at": "2026-10-09T14:30:00Z",
        "scan_cycle": {"data_status": "ok", "data_feed": "iex"},
        "outcomes": [{
            "horizon_minutes": 15,
            "status": "complete",
            "forward_return": "0.00045",
            "computed_at": "2026-10-09T14:47:00Z",
        }],
    }


def attested_population():
    return {
        "schema_version": "rhen-full-decision-population-v1",
        "source": "immutable_precompaction_decision_archive",
        "archive_sha256": "a" * 64,
        "complete_candidate_population": True,
        "rejected_candidates_preserved": True,
        "all_decision_cycles_reconciled": True,
        "retention_loss_verified_absent": True,
    }


def test_complete_real_15m_candidate_cohort_is_audited_but_not_live_validated():
    result = audit_candidate_cohort(
        [candidate(i) for i in range(30)],
        expected_strategy="LIVE-2026-09-25-003",
        population_attestation=attested_population(),
    )
    assert result["state"] == "READY"
    assert result["candidate_count"] == 30
    assert result["complete_15m"] == 30
    assert result["coverage_15m"] == "1"
    assert result["asof_universe_proven"] is False
    assert result["broker_fill_parity_proven"] is False
    assert result["validated_alpha"] is False


def test_missing_15m_forward_outcomes_stay_missing_not_zero():
    records = [candidate(i) for i in range(30)]
    records[0]["outcomes"] = []
    records[1]["outcomes"][0]["status"] = "insufficient_future_data"
    result = audit_candidate_cohort(
        records, expected_strategy="LIVE-2026-09-25-003"
    )
    assert result["state"] == "PARTIAL"
    assert result["complete_15m"] == 28
    assert result["missing_15m"] == 1
    assert result["incomplete_or_error_15m"] == 1
    assert "INSUFFICIENT_15M_FORWARD_COVERAGE" in result["blocking_reasons"]


def test_bad_time_unversioned_or_duplicate_cohort_blocks_research():
    records = [candidate(i) for i in range(30)]
    records[1]["candidate_id"] = records[0]["candidate_id"]
    records[2]["strategy_version_id"] = "old-live-version"
    records[3]["outcomes"][0]["computed_at"] = "2026-10-09T14:20:00Z"
    records[4]["scan_cycle"]["data_status"] = "partial_backfill"
    result = audit_candidate_cohort(
        records, expected_strategy="LIVE-2026-09-25-003"
    )
    blockers = result["blocking_reasons"]
    assert "CANDIDATE_IDS_DUPLICATED" in blockers
    assert "CANDIDATE_STRATEGY_PROVENANCE_MISMATCH" in blockers
    assert "FORWARD_OUTCOME_CAUSAL_TIME_INVALID" in blockers
    assert "CANDIDATE_MARKET_DATA_NOT_COMPLETE" in blockers


def test_invalid_forward_returns_are_not_counted_as_evidence():
    records = [candidate(i) for i in range(30)]
    records[0]["outcomes"][0]["forward_return"] = "NaN"
    records[1]["outcomes"][0]["forward_return"] = None
    result = audit_candidate_cohort(
        records, expected_strategy="LIVE-2026-09-25-003"
    )
    assert result["complete_15m"] == 28
    assert result["invalid_forward_return_15m"] == 2
    assert "FORWARD_RETURN_INVALID" in result["blocking_reasons"]


def test_actual_compact_core_schema_can_read_15m_outcomes_but_not_claim_population():
    records = [candidate(i) for i in range(30)]
    for record in records:
        outcome = record.pop("outcomes")[0]
        record["forward_outcomes"] = {
            "15": {
                "status": outcome["status"],
                "forward_return": outcome["forward_return"],
                "computed_at": outcome["computed_at"],
            }
        }
        record["scan_cycle"]["data_status"] = "compact_core_v3"
    result = audit_candidate_cohort(
        records, expected_strategy="LIVE-2026-09-25-003"
    )
    assert result["complete_15m"] == 30
    assert result["missing_15m"] == 0
    assert result["compacted_source_count"] == 30
    assert result["state"] == "PARTIAL"
    assert result["full_population_attested"] is False
    assert "CANDIDATE_POPULATION_NOT_ATTESTED" in result["blocking_reasons"]
    assert "CANDIDATE_DECISION_CONTEXT_SAMPLED_OR_COMPACTED" in result["blocking_reasons"]


def test_unattested_full_15m_array_is_still_unusable_for_claims():
    result = audit_candidate_cohort(
        [candidate(i) for i in range(30)],
        expected_strategy="LIVE-2026-09-25-003",
    )
    assert result["complete_15m"] == 30
    assert result["state"] == "PARTIAL"
    assert "CANDIDATE_POPULATION_NOT_ATTESTED" in result["blocking_reasons"]
