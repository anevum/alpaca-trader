"""F2a independent archive, corruption, retry, tenant, and cost tests."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
import pytest
from next_rhen.evidence_journal import DecisionJournal
from next_rhen.evidence_vault import (
 LocalEvidenceVault, LocalImmutableObjectStore, restore_local_vault, ArchiveIntegrityError,
)
from test_v5_decision_journal import cycle

W="wrk_owner000001"
R="paper-rhen-next-001"

def setup(tmp_path, *, max_events=2, max_raw_bytes=8_000_000):
    journal=DecisionJournal(tmp_path/"journal.db")
    store=LocalImmutableObjectStore(tmp_path/"store")
    vault=LocalEvidenceVault(journal,store,max_events=max_events,max_raw_bytes=max_raw_bytes)
    return journal,store,vault

def test_sealed_batches_restore_all_candidates_and_rejections(tmp_path):
    journal,store,vault=setup(tmp_path)
    for i in range(1,6): journal.append_cycle(cycle(sequence=i))
    batches=[]
    while row:=vault.archive_next(workspace_id=W,run_id=R): batches.append(row)
    assert [x["archived_events"] for x in batches]==[2,2,1]
    assert all(x["ack_status"]=="LOCAL_VERIFIED_NOT_OFFHOST" for x in batches)
    restored=restore_local_vault(store,vault.manifest_keys(workspace_id=W,run_id=R),workspace_id=W,run_id=R,independent_expected_cycle_count=5)
    assert restored["manifest"]["cycles_restored"]==5
    assert restored["manifest"]["candidates_restored"]==10
    assert not restored["manifest"]["research_ready"]
    assert not restored["manifest"]["offhost_archive_verified"]
    assert restored["manifest"]["evidence_state"]=="AWAITING_EVIDENCE"
    assert [x["candidates"][1]["decision"] for x in restored["private_cycles"]]==["REJECTED"]*5
    assert vault.archive_next(workspace_id=W,run_id=R) is None
    journal.close()

def test_determinism_independent_store_and_restart(tmp_path):
    results=[]
    for n in ("a","b"):
        journal,store,vault=setup(tmp_path/n)
        for i in range(1,4): journal.append_cycle(cycle(sequence=i))
        while vault.archive_next(workspace_id=W,run_id=R): pass
        keys=vault.manifest_keys(workspace_id=W,run_id=R)
        results.append((keys,[store.get(k) for k in keys],restore_local_vault(store,keys,workspace_id=W,run_id=R)))
        journal.close()
    assert results[0]==results[1]

def test_retry_after_manifest_written_before_ack(tmp_path):
    journal,store,vault=setup(tmp_path)
    journal.append_cycle(cycle())
    original=vault.journal.conn.execute
    # Simulate a crash after immutable objects are persisted but before database ACK.
    class FaultStore:
        def __init__(self): self.fail=True
        def put_once(self,key,value):
            store.put_once(key,value)
            if key.endswith(".manifest.json") and self.fail:
                self.fail=False
                raise ConnectionError("worker stopped after immutable write")
        def get(self,key): return store.get(key)
    vault.store=FaultStore()
    with pytest.raises(ConnectionError): vault.archive_next(workspace_id=W,run_id=R)
    assert vault.manifest_keys(workspace_id=W,run_id=R)==[]
    row=vault.archive_next(workspace_id=W,run_id=R)
    assert row["archived_events"]==1
    assert len(vault.manifest_keys(workspace_id=W,run_id=R))==1
    journal.close()

def test_corrupt_object_and_missing_object_fail_closed(tmp_path):
    journal,store,vault=setup(tmp_path)
    journal.append_cycle(cycle())
    vault.archive_next(workspace_id=W,run_id=R)
    key=vault.manifest_keys(workspace_id=W,run_id=R)[0]
    manifest=json.loads(store.get(key))
    data_path=store._path(manifest["data_key"])
    data_path.write_bytes(b"bad")
    with pytest.raises(ArchiveIntegrityError):
        restore_local_vault(store,[key],workspace_id=W,run_id=R)
    with pytest.raises(ArchiveIntegrityError):
        store.put_once(manifest["data_key"],b"replacement")
    journal.close()

def test_manifest_loss_gap_and_cross_tenant_blocked(tmp_path):
    journal,store,vault=setup(tmp_path,max_events=1)
    for i in range(1,3): journal.append_cycle(cycle(sequence=i))
    while vault.archive_next(workspace_id=W,run_id=R): pass
    keys=vault.manifest_keys(workspace_id=W,run_id=R)
    with pytest.raises(ArchiveIntegrityError): restore_local_vault(store,keys[1:],workspace_id=W,run_id=R)
    with pytest.raises(ArchiveIntegrityError): restore_local_vault(store,keys,workspace_id="wrk_otheraccount123",run_id=R)
    with pytest.raises(ArchiveIntegrityError): restore_local_vault(store,keys,workspace_id=W,run_id=R,independent_expected_cycle_count=3)
    journal.close()

def test_journal_corruption_is_not_exported(tmp_path):
    journal,store,vault=setup(tmp_path)
    journal.append_cycle(cycle())
    journal.conn.execute("UPDATE decision_journal SET payload_json='{}' WHERE sequence_no=1")
    with pytest.raises(ArchiveIntegrityError): vault.archive_next(workspace_id=W,run_id=R)
    assert not vault.manifest_keys(workspace_id=W,run_id=R)
    journal.close()

def test_oversized_row_refused_without_sampling(tmp_path):
    journal,store,vault=setup(tmp_path,max_raw_bytes=32)
    journal.append_cycle(cycle())
    with pytest.raises(ArchiveIntegrityError,match="single event"): vault.archive_next(workspace_id=W,run_id=R)
    journal.close()

def test_pressure_does_not_erase_source(tmp_path):
    journal,store,vault=setup(tmp_path)
    journal.append_cycle(cycle())
    s=vault.pressure_snapshot(workspace_id=W,run_id=R,warning_bytes=1,blocked_bytes=2)
    assert s["pressure"]=="BLOCKED" and s["not_offhost_events"]==1
    assert s["evidence_state"]=="BLOCKED" and not s["offhost_verified"]
    assert journal.export_session(workspace_id=W,run_id=R,session_date="2026-10-09")["manifest"]["cycles_exported"]==1
    journal.close()

def test_path_traversal_forbidden(tmp_path):
    store=LocalImmutableObjectStore(tmp_path)
    with pytest.raises(ArchiveIntegrityError): store.put_once("../bad",b"x")
    with pytest.raises(ArchiveIntegrityError): store.get("/tmp/secret")
