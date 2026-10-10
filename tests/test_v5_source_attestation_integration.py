"""F3a integration tests for use in the actual ANEVUM RHEN F1 repository.

Locally skipped if the authentic next_rhen.evidence_journal is unavailable.
These tests MUST be executed in the RHEN draft branch before a merge/release.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json

import pytest

from next_rhen.source_attestation import SourceScanLedger, digest, verify_scan_population

HAS_REAL_F1 = importlib.util.find_spec("next_rhen.evidence_journal") is not None
pytestmark = pytest.mark.skipif(not HAS_REAL_F1, reason="Real RHEN F1 journal is not installed locally")


def scanner_fixture_from_independent_scan_input(row):
    """Integration TEST fixture only; never call this from production scanner.

    In production scanner manifests MUST be assembled upstream before calling
    the F1 journal, from original scan events and an independent scheduler.
    """
    return {
        "schema_version": "anevum.scanner-source.v1",
        "workspace_id": row["workspace_id"], "run_id": row["run_id"],
        "cycle_id": row["cycle_id"], "sequence_no": row["sequence_no"],
        "session_date": row["session_date"], "occurred_at": row["occurred_at"],
        "scan_origin": "SYNTHETIC_FIXTURE", "strategy_version": row["strategy_version"],
        "config_sha256": row["config_sha256"], "code_sha256": row["code_sha256"],
        "market_source_sha256": digest(row["market_source"]),
        "universe_symbols": row["universe_symbols"],
        "evaluations": [
            {"symbol": candidate["symbol"], "decision": candidate["decision"],
             "reason": candidate["reason"], "observed_at": candidate["observed_at"],
             "features_sha256": digest(candidate["features"])}
            for candidate in row["candidates"]
        ],
    }


def test_authentic_f1_journal_scanner_first_local_equality_never_research_ready(tmp_path):
    from next_rhen.evidence_journal import DecisionJournal
    from test_v5_decision_journal import cycle

    row = cycle(sequence=1)
    source_path, journal_path = tmp_path/"upstream.sqlite", tmp_path/"journal.sqlite"
    with SourceScanLedger(source_path) as source, DecisionJournal(journal_path) as journal:
        scanner_ack = source.record_scan(scanner_fixture_from_independent_scan_input(row))
        assert scanner_ack["status"] == "SCANNER_RECORDED"
        assert journal.append_cycle(row)["status"] == "RECORDED"
        actual = verify_scan_population(
            source,journal,workspace_id=row["workspace_id"],run_id=row["run_id"],
            session_date=row["session_date"],scheduler_expected_cycles=1,
            trusted_source_chain_sha256=scanner_ack["chain_sha256"],
        )
    assert actual["scanner_journal_population_agrees"] is True
    assert actual["candidate_count_compared"] == 2
    assert actual["evidence_state"] == "AWAITING_EVIDENCE"
    assert actual["upstream_origin_independently_attested"] is False
    assert not actual["broker_write_authority"]


def test_authentic_f1_journal_detects_synthetic_extra_source_candidate(tmp_path):
    from next_rhen.evidence_journal import DecisionJournal
    from test_v5_decision_journal import cycle

    row = cycle(sequence=1)
    scan = scanner_fixture_from_independent_scan_input(row)
    scan["universe_symbols"].append("TSLA")
    scan["evaluations"].append({
        "symbol": "TSLA", "decision": "REJECTED",
        "reason": "source_only_rejected_candidate",
        "observed_at": row["occurred_at"],
        "features_sha256": digest({"signal": -0.3}),
    })
    with SourceScanLedger(tmp_path/"upstream.sqlite") as source, DecisionJournal(tmp_path/"journal.sqlite") as journal:
        source.record_scan(scan)
        journal.append_cycle(row)
        result=verify_scan_population(source,journal,workspace_id=row["workspace_id"],
                        run_id=row["run_id"],session_date=row["session_date"])
    assert result["evidence_state"] == "BLOCKED"
    assert any("UNIVERSE_MISMATCH" in issue or "CANDIDATE_COUNT_MISMATCH" in issue
               for issue in result["issue_codes"])


def test_authentic_f1_journal_hash_corruption_is_not_a_pass(tmp_path):
    from next_rhen.evidence_journal import DecisionJournal
    from test_v5_decision_journal import cycle

    row = cycle(sequence=1)
    with SourceScanLedger(tmp_path/"upstream.sqlite") as source, DecisionJournal(tmp_path/"journal.sqlite") as journal:
        source.record_scan(scanner_fixture_from_independent_scan_input(row))
        journal.append_cycle(row)
        journal.conn.execute("UPDATE decision_journal SET payload_json='{}' WHERE sequence_no=1")
        result=verify_scan_population(source,journal,workspace_id=row["workspace_id"],
                        run_id=row["run_id"],session_date=row["session_date"])
    assert result["evidence_state"] == "BLOCKED"
    assert "JOURNAL_EXPORT_INTEGRITY_BLOCKED" in result["issue_codes"]
