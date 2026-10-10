"""Offline F3a scanner/journal reconciliation tests; simulated journal reader.

Tests validate the F3a scanner ledger and verifier. They DO NOT substitute
for CI against the authentic DecisionJournal implementation in RHEN #471.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from next_rhen.source_attestation import (
    SOURCE_SCHEMA, MAX_SCANS_PER_SESSION, SourceContractError,
    SourceIntegrityError, SourceScanLedger, canonical, digest,
    validate_scan, verify_scan_population,
)

W = "wrk_memberAAAA01"
R = "paper-f3a-001"
D = "2026-10-09"
MARKET = {"feed": "SYNTHETIC_PAPER", "data_status": "PARTIAL",
          "asof_timestamp": D+"T14:29:00Z", "universe_ref": "c"*64}
F_A = {"momentum_pct": 0.32, "above_vwap": True}
F_B = {"momentum_pct": -0.11, "above_vwap": False}


def source_scan(seq=1, workspace=W, run=R):
    """Pretend the scanner independently persisted these fields FIRST."""
    return {
        "schema_version": SOURCE_SCHEMA,
        "workspace_id": workspace, "run_id": run, "cycle_id": f"cycle-{seq:04d}",
        "sequence_no": seq, "session_date": D, "occurred_at": D+"T14:30:00Z",
        "scan_origin": "SYNTHETIC_FIXTURE", "strategy_version": "v5-paper-f3a",
        "config_sha256": "a"*64, "code_sha256": "b"*64,
        "market_source_sha256": digest(MARKET),
        "universe_symbols": ["AAPL", "MSFT"],
        "evaluations": [
            {"symbol":"AAPL", "decision":"QUALIFIED", "reason":"passes",
             "observed_at": D+"T14:29:30Z", "features_sha256": digest(F_A)},
            {"symbol":"MSFT", "decision":"REJECTED", "reason":"fails_momentum",
             "observed_at": D+"T14:29:31Z", "features_sha256": digest(F_B)},
        ],
    }


def decision_cycle(seq=1, workspace=W, run=R):
    """Separately authored F1-compatible decision; no production broker data."""
    return {
        "schema_version": "anevum.decision-cycle.v1",
        "workspace_id": workspace, "run_id": run, "cycle_id": f"cycle-{seq:04d}",
        "sequence_no": seq, "session_date": D, "occurred_at": D+"T14:30:00Z",
        "execution_mode": "PAPER_RESEARCH_ONLY", "strategy_version": "v5-paper-f3a",
        "config_sha256": "a"*64, "code_sha256": "b"*64,
        "market_source": deepcopy(MARKET), "universe_symbols": ["AAPL", "MSFT"],
        "candidates": [
            {"symbol": "AAPL", "decision": "QUALIFIED", "reason": "passes",
             "observed_at": D+"T14:29:30Z", "features": deepcopy(F_A)},
            {"symbol": "MSFT", "decision": "REJECTED", "reason": "fails_momentum",
             "observed_at": D+"T14:29:31Z", "features": deepcopy(F_B)},
        ],
    }


class FakeVerifiedJournal:
    """A tiny contract fake; genuine journal integration remains a PR CI gate."""
    def __init__(self, path, rows):
        self.path = Path(path)
        self.rows = rows
        self.chain_ok = True

    def export_session(self, *, workspace_id, run_id, session_date):
        rows = [deepcopy(row) for row in self.rows if
                row["workspace_id"] == workspace_id and row["run_id"] == run_id and
                row["session_date"] == session_date]
        if not rows:
            raise RuntimeError("no events in requested session")
        serialized = b"".join(canonical(row) + b"\n" for row in rows)
        return {"manifest": {
            "cycles_exported": len(rows),
            "journal_chain_verified": self.chain_ok,
            "jsonl_sha256": sha256(serialized).hexdigest(),
        }, "private_cycles": rows}


def test_source_first_record_and_locally_matching_population_remains_unproven(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.sqlite") as source:
        for seq in (1,2):
            source.record_scan(source_scan(seq))  # BEFORE journal acceptance
        original = source.read_session_verified(workspace_id=W, run_id=R, session_date=D)
        report = verify_scan_population(
            source, FakeVerifiedJournal(tmp_path/"journal.sqlite",[decision_cycle(1),decision_cycle(2)]),
            workspace_id=W,run_id=R,session_date=D,
            scheduler_expected_cycles=2,
            trusted_source_chain_sha256=original["source_run_chain_sha256"],
        )
    assert report["scanner_cycles"] == 2
    assert report["journal_cycles"] == 2
    assert report["candidate_count_compared"] == 4
    assert report["scanner_journal_population_agrees"] is True
    assert report["source_only_cycles"] == report["journal_only_cycles"] == 0
    assert report["evidence_state"] == "AWAITING_EVIDENCE"
    assert report["upstream_origin_independently_attested"] is False
    assert report["full_population_across_upstream_proven"] is False
    assert report["research_ready"] is False
    assert report["offhost_archive_verified"] is False
    assert report["broker_write_authority"] is False
    assert "UPSTREAM_SCANNER_INTEGRATION_NOT_ATTESTED" in report["issue_codes"]
    assert "SYNTHETIC_SCANNER_SOURCE_NOT_MARKET_EVIDENCE" in report["issue_codes"]


def test_missing_journal_scan_detected_even_if_journal_self_consistent(tmp_path):
    with SourceScanLedger(tmp_path/"source.db") as source:
        source.record_scan(source_scan(1))
        source.record_scan(source_scan(2))
        report = verify_scan_population(
            source, FakeVerifiedJournal(tmp_path/"decision.db",[decision_cycle(1)]),
            workspace_id=W,run_id=R,session_date=D,scheduler_expected_cycles=2)
    assert report["evidence_state"] == "BLOCKED"
    assert report["source_only_cycles"] == 1
    assert "JOURNAL_MISSING_SCANNER_CYCLE:2" in report["issue_codes"]
    assert "INDEPENDENT_SCHEDULER_COUNT_MISMATCH" in report["issue_codes"]


def test_missing_scanner_cycle_detected(tmp_path):
    with SourceScanLedger(tmp_path/"source.db") as source:
        source.record_scan(source_scan(1))
        report = verify_scan_population(
            source, FakeVerifiedJournal(tmp_path/"decision.db",[decision_cycle(1),decision_cycle(2)]),
            workspace_id=W,run_id=R,session_date=D)
    assert report["evidence_state"] == "BLOCKED"
    assert report["journal_only_cycles"] == 1
    assert "SCANNER_MISSING_JOURNAL_CYCLE:2" in report["issue_codes"]


def test_dual_loss_cannot_satisfy_independent_scheduler_count(tmp_path):
    with SourceScanLedger(tmp_path/"source.db") as source:
        source.record_scan(source_scan(1))
        source.record_scan(source_scan(2))
        report = verify_scan_population(
            source, FakeVerifiedJournal(tmp_path/"journal.db",[decision_cycle(1),decision_cycle(2)]),
            workspace_id=W,run_id=R,session_date=D,scheduler_expected_cycles=3)
    assert report["evidence_state"] == "BLOCKED"
    assert "INDEPENDENT_SCHEDULER_COUNT_MISMATCH" in report["issue_codes"]


@pytest.mark.parametrize("change,reason",[
    (lambda row: row["candidates"][1].__setitem__("reason","invented"),"CANDIDATE_REASON_MISMATCH"),
    (lambda row: row["candidates"][0]["features"].__setitem__("momentum_pct",9.0),"CANDIDATE_FEATURES_MISMATCH"),
    (lambda row: row["candidates"][0].__setitem__("decision","REJECTED"),"CANDIDATE_DECISION_MISMATCH"),
    (lambda row: row["market_source"].__setitem__("universe_ref","f"*64),"MARKET_SOURCE_PROVENANCE_MISMATCH"),
    (lambda row: row.__setitem__("strategy_version","altered"),"SOURCE_JOURNAL_STRATEGY_MISMATCH"),
])
def test_journal_candidate_or_market_provenance_disagreement_blocks(tmp_path,change,reason):
    row=decision_cycle()
    change(row)
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan())
        report=verify_scan_population(source,FakeVerifiedJournal(tmp_path/"journal.db",[row]),
                                      workspace_id=W,run_id=R,session_date=D)
    assert report["evidence_state"]=="BLOCKED"
    assert any(issue.startswith(reason) for issue in report["issue_codes"])


def test_source_conflicting_rewrite_missing_candidate_and_sequence_gap_rejected(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        row=source_scan()
        first=source.record_scan(row)
        assert source.record_scan(deepcopy(row))["status"]=="ALREADY_RECORDED"
        changed=deepcopy(row)
        changed["evaluations"][1]["reason"]="new_rejection"
        with pytest.raises(SourceIntegrityError,match="conflicts"):
            source.record_scan(changed)
        with pytest.raises(SourceIntegrityError,match="sequence gap"):
            source.record_scan(source_scan(3))
        omitted=source_scan(2)
        omitted["evaluations"].pop()
        with pytest.raises(SourceContractError,match="must equal"):
            source.record_scan(omitted)
        assert len(source.read_session_verified(workspace_id=W,run_id=R,session_date=D)["scans"])==1


def test_corrupted_scanner_hash_chain_fails_closed(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan())
        source.conn.execute("UPDATE source_scans SET manifest_json='{}' WHERE sequence_no=1")
        with pytest.raises(SourceIntegrityError,match="checksum"):
            source.read_session_verified(workspace_id=W,run_id=R,session_date=D)
        audit=verify_scan_population(source,FakeVerifiedJournal(tmp_path/"journal.db",[decision_cycle()]),
                                     workspace_id=W,run_id=R,session_date=D)
    assert audit["evidence_state"]=="BLOCKED"
    assert "SCANNER_LEDGER_INTEGRITY_BLOCKED" in audit["issue_codes"]


def test_independent_digest_wrong_blocks_and_missing_digest_never_passes(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan())
        journal=FakeVerifiedJournal(tmp_path/"decision.db",[decision_cycle()])
        wrong=verify_scan_population(source,journal,workspace_id=W,run_id=R,session_date=D,
                                     trusted_source_chain_sha256="0"*64)
        missing=verify_scan_population(source,journal,workspace_id=W,run_id=R,session_date=D)
    assert wrong["evidence_state"]=="BLOCKED"
    assert "EXTERNAL_SCANNER_CHAIN_DIGEST_MISMATCH" in wrong["issue_codes"]
    assert missing["evidence_state"]=="AWAITING_EVIDENCE"
    assert "EXTERNAL_SCANNER_DIGEST_NOT_PINNED" in missing["issue_codes"]


def test_false_journal_verification_is_blocked(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan())
        journal=FakeVerifiedJournal(tmp_path/"decision.db",[decision_cycle()])
        journal.chain_ok=False
        report=verify_scan_population(source,journal,workspace_id=W,run_id=R,session_date=D)
    assert report["evidence_state"]=="BLOCKED"
    assert "JOURNAL_CHAIN_OR_COUNT_NOT_VERIFIED" in report["issue_codes"]


def test_cross_tenant_source_is_not_a_match(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan(workspace=W))
        source.record_scan(source_scan(workspace="wrk_memberBBBB02"))
        report=verify_scan_population(source,
              FakeVerifiedJournal(tmp_path/"journal.db",[decision_cycle(workspace="wrk_memberBBBB02")]),
              workspace_id=W,run_id=R,session_date=D)
        b=source.read_session_verified(workspace_id="wrk_memberBBBB02",run_id=R,session_date=D)
    assert report["evidence_state"]=="BLOCKED"
    assert report["source_only_cycles"]==1
    assert b["scans"][0]["workspace_id"]=="wrk_memberBBBB02"


def test_no_source_or_journal_still_blocks(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        report=verify_scan_population(source,FakeVerifiedJournal(tmp_path/"journal.db",[]),
                                      workspace_id=W,run_id=R,session_date=D)
    assert report["evidence_state"]=="BLOCKED"
    assert "SCANNER_SOURCE_MISSING" in report["issue_codes"]
    assert "JOURNAL_SESSION_MISSING" in report["issue_codes"]


def test_two_ledger_files_must_be_distinct(tmp_path):
    with SourceScanLedger(tmp_path/"same.db") as source:
        with pytest.raises(SourceContractError,match="must not share"):
            verify_scan_population(source,FakeVerifiedJournal(tmp_path/"same.db",[decision_cycle()]),
                                   workspace_id=W,run_id=R,session_date=D)


def test_nonfinite_and_future_timestamp_rejected(tmp_path):
    s=source_scan()
    s["evaluations"][0]["observed_at"]=D+"T14:31:00Z"
    with pytest.raises(SourceContractError,match="future"):
        validate_scan(s)
    s=source_scan()
    s["evaluations"][1]["features_sha256"]="invalid"
    with pytest.raises(SourceContractError,match="provenance"):
        validate_scan(s)
    s=source_scan()
    s["universe_symbols"][1]="AAPL"
    with pytest.raises(SourceContractError,match="duplicate"):
        validate_scan(s)
    assert digest({"value":3.1}) == sha256(canonical({"value":3.1})).hexdigest()
    with pytest.raises(SourceContractError):
        canonical({"value":float("nan")})


def test_report_deterministic_bounded_no_public_trade_authority(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.db") as source:
        source.record_scan(source_scan())
        journal=FakeVerifiedJournal(tmp_path/"journal.db",[decision_cycle()])
        a=verify_scan_population(source,journal,workspace_id=W,run_id=R,session_date=D)
        b=verify_scan_population(source,journal,workspace_id=W,run_id=R,session_date=D)
    assert a==b
    assert len(a["reconciliation_sha256"])==64
    assert a["issue_count"]<=32
    assert "private_cycles" not in a
    assert a["broker_write_authority"] is False
    assert a["alpha_validated"] is False


def test_scanner_rejects_unhashable_origin_and_candidate_disposition():
    s=source_scan()
    s["scan_origin"]=["PRE_JOURNAL_SCANNER"]
    with pytest.raises(SourceContractError,match="origin"):
        validate_scan(s)
    s=source_scan()
    s["evaluations"][0]["decision"]=["QUALIFIED"]
    with pytest.raises(SourceContractError,match="disposition"):
        validate_scan(s)


def test_malformed_persisted_digest_fails_closed(tmp_path):
    with SourceScanLedger(tmp_path/"scanner.sqlite") as source:
        source.record_scan(source_scan())
        source.conn.execute("UPDATE source_scans SET chain_sha256='NOT_HEX' WHERE sequence_no=1")
        with pytest.raises(SourceIntegrityError,match="digest malformed"):
            source.read_session_verified(workspace_id=W,run_id=R,session_date=D)
        report=verify_scan_population(source,FakeVerifiedJournal(tmp_path/"journal.sqlite",[decision_cycle()]),
                        workspace_id=W,run_id=R,session_date=D)
    assert report["evidence_state"]=="BLOCKED"
    assert "SCANNER_LEDGER_INTEGRITY_BLOCKED" in report["issue_codes"]
