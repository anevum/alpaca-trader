"""ANEVUM V5 FOUNDATION F1: fail-closed, no-broker decision capture."""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from next_rhen.evidence_journal import (
    DecisionJournal, EvidenceContractError, EvidenceIntegrityError,
    SCHEMA_VERSION, validate_cycle,
)


def cycle(*, sequence=1, workspace="wrk_owner000001", session="2026-10-09"):
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace,
        "run_id": "paper-rhen-next-001",
        "cycle_id": f"cycle-{sequence:04d}",
        "sequence_no": sequence,
        "session_date": session,
        "occurred_at": session + "T14:30:00Z",
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": "rhen-next-paper-v0",
        "config_sha256": "a" * 64,
        "code_sha256": "b" * 64,
        "market_source": {
            "feed": "ALPACA_IEX_PAPER",
            "data_status": "COMPLETE",
            "asof_timestamp": session + "T14:29:00Z",
            "universe_ref": "c" * 64,
            "bars_ref": "d" * 64,
            "quotes_ref": "e" * 64,
        },
        "universe_symbols": ["AAPL", "MSFT"],
        "candidates": [
            {
                "symbol": "AAPL", "decision": "QUALIFIED",
                "reason": "passes_frozen_rule_set",
                "observed_at": session + "T14:29:30Z",
                "features": {"momentum_pct": 0.32, "above_vwap": True},
            },
            {
                "symbol": "MSFT", "decision": "REJECTED",
                "reason": "fails_momentum_gate",
                "observed_at": session + "T14:29:31Z",
                "features": {"momentum_pct": -0.11, "above_vwap": False},
            },
        ],
    }


def test_persists_both_winner_candidate_and_rejected_counterfactual(tmp_path):
    with DecisionJournal(tmp_path / "successor-journal.sqlite") as journal:
        accepted = journal.append_cycle(cycle())
        assert accepted["status"] == "RECORDED"
        assert accepted["archive_state"] == "PENDING_OFFHOST_ARCHIVE"
        result = journal.export_session(
            workspace_id="wrk_owner000001",
            run_id="paper-rhen-next-001",
            session_date="2026-10-09",
            independent_expected_cycle_count=1,
        )
    assert result["manifest"]["cycles_exported"] == 1
    assert result["manifest"]["candidates_exported"] == 2
    assert result["manifest"]["rejected_exported"] == 1
    assert result["manifest"]["journal_chain_verified"] is True
    assert result["manifest"]["source_completeness"] == "AWAITING_OFFHOST_ARCHIVE_AND_UPSTREAM_ATTESTATION"
    assert result["manifest"]["full_population_across_all_cycles_proven"] is False
    assert result["manifest"]["alpha_validated"] is False
    assert result["manifest"]["broker_write_authority"] is False
    assert result["private_cycles"][0]["candidates"][1]["reason"] == "fails_momentum_gate"


def test_exact_retry_after_restart_idempotent_not_double_counted(tmp_path):
    path = tmp_path / "survives-restart.sqlite"
    row = cycle()
    with DecisionJournal(path) as writer:
        original = writer.append_cycle(row)
    with DecisionJournal(path) as restarted:
        duplicate = restarted.append_cycle(row)
        again = restarted.export_session(
            workspace_id=row["workspace_id"],
            run_id=row["run_id"], session_date=row["session_date"],
        )
        assert original["payload_sha256"] == duplicate["payload_sha256"]
        assert original["chain_sha256"] == duplicate["chain_sha256"]
        assert duplicate["status"] == "ALREADY_RECORDED"
        assert again["manifest"]["cycles_exported"] == 1


def test_sequence_gap_and_rewind_are_forbidden(tmp_path):
    with DecisionJournal(tmp_path / "gap.sqlite") as journal:
        journal.append_cycle(cycle())
        with pytest.raises(EvidenceIntegrityError, match="sequence gap"):
            journal.append_cycle(cycle(sequence=3))
        row = cycle(sequence=2)
        assert journal.append_cycle(row)["status"] == "RECORDED"
        with pytest.raises(EvidenceIntegrityError, match="overwrite forbidden"):
            journal.append_cycle(cycle())


def test_conflicting_reuse_of_cycle_id_is_blocked(tmp_path):
    first = cycle()
    second = cycle(sequence=2)
    second["cycle_id"] = first["cycle_id"]
    with DecisionJournal(tmp_path / "dupes.sqlite") as journal:
        journal.append_cycle(first)
        with pytest.raises(EvidenceIntegrityError, match="overwrite forbidden"):
            journal.append_cycle(second)


def test_missing_rejected_candidate_or_universe_member_is_error():
    row = cycle()
    row["candidates"] = row["candidates"][:1]
    with pytest.raises(EvidenceContractError, match="full candidate count"):
        validate_cycle(row)
    row = cycle()
    row["candidates"][1]["symbol"] = "AAPL"
    with pytest.raises(EvidenceContractError, match="duplicate candidate"):
        validate_cycle(row)
    row = cycle()
    row["candidates"][1]["symbol"] = "TSLA"
    with pytest.raises(EvidenceContractError, match="omits or adds"):
        validate_cycle(row)


def test_no_live_execution_or_broker_secret_in_evidence():
    row = cycle()
    row["execution_mode"] = "LIVE"
    with pytest.raises(EvidenceContractError, match="forbids live"):
        validate_cycle(row)
    row = cycle()
    row["candidates"][0]["features"]["alpaca_api_key"] = "dont-store-this"
    with pytest.raises(EvidenceContractError, match="credential-like"):
        validate_cycle(row)


def test_future_data_and_missing_market_refs_never_masquerade_as_complete(tmp_path):
    row = cycle()
    row["market_source"]["asof_timestamp"] = "2026-10-09T14:31:00Z"
    with pytest.raises(EvidenceContractError, match="from the future"):
        validate_cycle(row)
    row = cycle()
    row["market_source"].pop("quotes_ref")
    with pytest.raises(EvidenceContractError, match="raw bars and quotes"):
        validate_cycle(row)
    row = cycle()
    row["market_source"]["data_status"] = "PARTIAL"
    row["market_source"].pop("quotes_ref")
    with DecisionJournal(tmp_path / "partial.sqlite") as journal:
        journal.append_cycle(row)
        report = journal.export_session(
            workspace_id=row["workspace_id"],
            run_id=row["run_id"], session_date=row["session_date"],
        )
        assert "cycle:1:MARKET_SOURCE_NOT_COMPLETE" in report["manifest"]["quality_issues"]
        assert "INDEPENDENT_UPSTREAM_CYCLE_COUNT_MISSING" in report["manifest"]["quality_issues"]


def test_independent_source_count_mismatch_is_reported_not_passed(tmp_path):
    row = cycle()
    with DecisionJournal(tmp_path / "count.sqlite") as journal:
        journal.append_cycle(row)
        report = journal.export_session(
            workspace_id=row["workspace_id"], run_id=row["run_id"],
            session_date=row["session_date"], independent_expected_cycle_count=2,
        )
    assert "INDEPENDENT_UPSTREAM_CYCLE_COUNT_MISMATCH" in report["manifest"]["quality_issues"]
    assert not report["manifest"]["full_population_across_all_cycles_proven"]


def test_corruption_of_stored_payload_is_detected(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    with DecisionJournal(path) as journal:
        journal.append_cycle(cycle())
    with sqlite3.connect(str(path)) as conn:
        conn.execute("UPDATE decision_journal SET payload_json = '{}' WHERE sequence_no = 1")
    with DecisionJournal(path) as journal:
        with pytest.raises(EvidenceIntegrityError, match="payload hash mismatch"):
            journal.export_session(
                workspace_id="wrk_owner000001",
                run_id="paper-rhen-next-001",
                session_date="2026-10-09",
            )


def test_scoping_does_not_return_another_workspace(tmp_path):
    path = tmp_path / "tenant-private.sqlite"
    with DecisionJournal(path) as journal:
        journal.append_cycle(cycle(workspace="wrk_memberAAAA01"))
        journal.append_cycle(cycle(workspace="wrk_memberBBBB02"))
        a = journal.export_session(
            workspace_id="wrk_memberAAAA01", run_id="paper-rhen-next-001",
            session_date="2026-10-09",
        )
        b = journal.export_session(
            workspace_id="wrk_memberBBBB02", run_id="paper-rhen-next-001",
            session_date="2026-10-09",
        )
    assert a["private_cycles"][0]["workspace_id"] == "wrk_memberAAAA01"
    assert b["private_cycles"][0]["workspace_id"] == "wrk_memberBBBB02"
    assert a["manifest"]["jsonl_sha256"] != b["manifest"]["jsonl_sha256"]


def test_same_data_generates_identical_recovery_digest(tmp_path):
    first, second = tmp_path / "journal1.sqlite", tmp_path / "journal2.sqlite"
    reports = []
    for path in (first, second):
        with DecisionJournal(path) as journal:
            journal.append_cycle(deepcopy(cycle()))
            reports.append(
                journal.export_session(
                    workspace_id="wrk_owner000001",
                    run_id="paper-rhen-next-001",
                    session_date="2026-10-09",
                )
            )
    assert reports[0] == reports[1]


def test_no_production_or_broker_adapter_imports():
    source = Path(__file__).resolve().parents[1] / "next_rhen" / "evidence_journal.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in ("from app.", "from alpaca", "import alpaca", "import boto3", "import httpx"):
        assert forbidden not in text
