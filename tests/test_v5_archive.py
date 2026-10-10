"""ANEVUM V5 F2 local transport fixture tests: no real R2 or broker calls."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from next_rhen.archive import (
    LocalTestObjectStore, archive_private_session, EvidenceIntegrityError,
)
from next_rhen.evidence_journal import DecisionJournal


def event(number=1, workspace="wrk_testuser0001"):
    return {
        "schema_version": "anevum.decision-cycle.v1",
        "workspace_id": workspace,
        "run_id": "rhenpaper",
        "cycle_id": f"c{number}",
        "sequence_no": number,
        "session_date": "2026-10-09",
        "occurred_at": "2026-10-09T15:05:00Z",
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": "paper-v1",
        "config_sha256": "1" * 64,
        "code_sha256": "2" * 64,
        "market_source": {
            "feed": "TEST_IEX",
            "data_status": "COMPLETE",
            "asof_timestamp": "2026-10-09T15:04:00Z",
            "universe_ref": "3" * 64,
            "bars_ref": "4" * 64,
            "quotes_ref": "5" * 64,
        },
        "universe_symbols": ["AAPL", "MSFT", "NVDA"],
        "candidates": [
            {
                "symbol": symbol, "decision": decision,
                "reason": reason, "observed_at": "2026-10-09T15:04:30Z",
                "features": {"momentum": value},
            }
            for symbol, decision, reason, value in [
                ("AAPL", "QUALIFIED", "passes", 0.5),
                ("MSFT", "REJECTED", "momentum_below_threshold", 0.1),
                ("NVDA", "UNMEASURABLE", "feed_stale", None),
            ]
        ],
    }


def archive(tmp_path, *, workspace="wrk_testuser0001", count=2, store=None):
    store = store or LocalTestObjectStore(tmp_path / "remote")
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        for seq in range(1, count + 1):
            journal.append_cycle(event(seq, workspace))
        receipt = archive_private_session(
            journal, store,
            workspace_id=workspace, run_id="rhenpaper", session_date="2026-10-09",
            independent_expected_cycle_count=count,
        )
    return receipt, store


def test_archive_replays_full_candidate_population_and_deterministic_manifest(tmp_path):
    receipt, store = archive(tmp_path)
    assert receipt.verified
    assert not receipt.independent_upstream_proven
    assert receipt.records == 2 and receipt.candidates == 6
    assert receipt.object_key.endswith(".jsonl.gz")
    manifest = json.loads(store.get(receipt.manifest_key))
    assert manifest["cycles"] == 2
    assert manifest["candidates"] == 6
    assert not manifest["full_population_proven"]
    assert not manifest["market_references_restored"]


def test_identical_archive_retry_is_idempotent(tmp_path):
    first, store = archive(tmp_path)
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        second = archive_private_session(
            journal, store,
            workspace_id="wrk_testuser0001", run_id="rhenpaper",
            session_date="2026-10-09",
            independent_expected_cycle_count=2,
        )
    assert second == first


def test_tampered_remote_object_rejected_on_retry(tmp_path):
    receipt, store = archive(tmp_path)
    path = store._path(receipt.object_key)
    path.write_bytes(b"forged")
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        with pytest.raises(EvidenceIntegrityError, match="checksum/content"):
            archive_private_session(
                journal, store,
                workspace_id="wrk_testuser0001", run_id="rhenpaper",
                session_date="2026-10-09", independent_expected_cycle_count=2,
            )


def test_manifest_corruption_detected(tmp_path):
    receipt, store = archive(tmp_path)
    store._path(receipt.manifest_key).write_bytes(b'{"forged":true}')
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        with pytest.raises(EvidenceIntegrityError, match="checksum/content"):
            archive_private_session(
                journal, store,
                workspace_id="wrk_testuser0001", run_id="rhenpaper",
                session_date="2026-10-09", independent_expected_cycle_count=2,
            )


def test_offhost_failure_never_creates_verified_receipt(tmp_path):
    class BrokenStore:
        def put_if_absent(self, key, data):
            raise ConnectionError("network down")
        def get(self, key):
            raise AssertionError("should never GET")
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        journal.append_cycle(event())
        with pytest.raises(EvidenceIntegrityError, match="archive write failed"):
            archive_private_session(
                journal, BrokenStore(),
                workspace_id="wrk_testuser0001", run_id="rhenpaper",
                session_date="2026-10-09",
            )


def test_missing_expected_cycle_does_not_claim_complete_population(tmp_path):
    store = LocalTestObjectStore(tmp_path / "remote")
    with DecisionJournal(tmp_path / "private.sqlite") as journal:
        journal.append_cycle(event())
        receipt = archive_private_session(
            journal, store,
            workspace_id="wrk_testuser0001", run_id="rhenpaper",
            session_date="2026-10-09", independent_expected_cycle_count=2,
        )
    manifest = json.loads(store.get(receipt.manifest_key))
    assert "INDEPENDENT_UPSTREAM_CYCLE_COUNT_MISMATCH" in manifest["source_quality_issues"]
    assert not receipt.independent_upstream_proven


def test_account_scope_is_not_shared_between_archives(tmp_path):
    store = LocalTestObjectStore(tmp_path / "remote")
    with DecisionJournal(tmp_path / "db.sqlite") as journal:
        journal.append_cycle(event(workspace="wrk_userAAAA0001"))
        journal.append_cycle(event(workspace="wrk_userBBBB0002"))
        ra = archive_private_session(
            journal, store, workspace_id="wrk_userAAAA0001",
            run_id="rhenpaper", session_date="2026-10-09",
        )
        rb = archive_private_session(
            journal, store, workspace_id="wrk_userBBBB0002",
            run_id="rhenpaper", session_date="2026-10-09",
        )
    assert ra.object_key != rb.object_key
    assert "wrk_userAAAA0001" in ra.object_key
    assert "wrk_userBBBB0002" in rb.object_key


def test_no_credential_or_broker_side_effect_dependency():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "next_rhen" / "archive.py").read_text()
    for forbidden in ["import alpaca", "import boto3", "from app.", "requests.", "cloudflare.request("]:
        assert forbidden not in text
