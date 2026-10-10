"""F2b remote protocol tests: entirely synthetic; no R2 credentials or live broker."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json

import pytest

from next_rhen.evidence_journal import DecisionJournal
from next_rhen.evidence_vault import (
    ArchiveIntegrityError, LocalEvidenceVault, LocalImmutableObjectStore,
)
from next_rhen.remote_vault import (
    R2S3ImmutableStore, RemoteVaultReplicator, restore_remote_receipts,
)
from test_v5_decision_journal import cycle

WORKSPACE = "wrk_owner000001"
RUN = "paper-rhen-next-001"


def make_stack(root, *, max_events=2, remote=None):
    journal = DecisionJournal(root / "private.sqlite")
    local = LocalImmutableObjectStore(root / "local")
    vault = LocalEvidenceVault(journal, local, max_events=max_events)
    remote = remote or LocalImmutableObjectStore(root / "remote-simulator")
    return journal, vault, remote, RemoteVaultReplicator(vault, remote)


def create_local(journal, vault, *, cycles=5):
    for index in range(1, cycles + 1):
        journal.append_cycle(cycle(sequence=index))
    while vault.archive_next(workspace_id=WORKSPACE, run_id=RUN):
        pass


def replicate_all(replicator):
    receipts = []
    while receipt := replicator.replicate_next(workspace_id=WORKSPACE, run_id=RUN):
        receipts.append(receipt)
    return receipts


def test_remote_protocol_full_restore_from_object_store_without_sqlite(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path)
    create_local(journal, vault)
    receipts = replicate_all(replicator)
    assert [(x["first_sequence"], x["last_sequence"]) for x in receipts] == [(1, 2), (3, 4), (5, 5)]
    assert all(x["status"] == "REMOTE_PROTOCOL_VERIFIED_UPSTREAM_UNATTESTED" for x in receipts)
    keys = replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN)
    anchors = {x["receipt_key"]: x["receipt_sha256"] for x in receipts}
    assert len(keys) == 3
    assert replicator.replicate_next(workspace_id=WORKSPACE, run_id=RUN) is None

    # The source database and local objects are no longer used for recovery.
    journal.close()
    result = restore_remote_receipts(
        remote, keys, workspace_id=WORKSPACE, run_id=RUN,
        trusted_receipt_hashes=anchors, independent_expected_cycle_count=5,
    )
    report = result["manifest"]
    assert report["cycles_restored"] == 5
    assert report["candidates_restored"] == 10
    assert report["remote_protocol_restore_verified"] is True
    assert report["receipt_digest_pinned"] is True
    assert report["upstream_cycle_count_attested"] is True
    assert report["offhost_archive_verified"] is False
    assert report["research_ready"] is False
    assert report["evidence_state"] == "AWAITING_EVIDENCE"
    assert report["broker_write_authority"] is False
    assert [x["candidates"][1]["decision"] for x in result["private_cycles"]] == ["REJECTED"] * 5


def test_mock_remote_receipt_does_not_upgrade_wal_source_or_pressure(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path)
    create_local(journal, vault, cycles=1)
    replicate_all(replicator)
    states = journal.conn.execute(
        "SELECT archive_state FROM decision_journal WHERE workspace_id=?",
        (WORKSPACE,),
    ).fetchall()
    assert states == [("LOCAL_ARCHIVE_VERIFIED",)]
    pressure = vault.pressure_snapshot(workspace_id=WORKSPACE, run_id=RUN)
    assert pressure["not_offhost_events"] == 1
    assert pressure["evidence_state"] == "AWAITING_EVIDENCE"
    journal.close()


def test_crash_after_remote_put_before_db_receipt_replays_idempotently(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path)
    create_local(journal, vault, cycles=1)

    class FailingAfterWrite:
        def __init__(self, delegate):
            self.delegate, self.failing = delegate, True

        def put_once(self, key, raw):
            self.delegate.put_once(key, raw)
            if self.failing and key.endswith(".remote-receipt.json"):
                self.failing = False
                raise ConnectionError("worker crashed after remote write")

        def get(self, key):
            return self.delegate.get(key)

    replicator.remote = FailingAfterWrite(remote)
    with pytest.raises(ConnectionError, match="worker crashed"):
        replicator.replicate_next(workspace_id=WORKSPACE, run_id=RUN)
    assert replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN) == []
    replicator.remote = remote
    receipt = replicator.replicate_next(workspace_id=WORKSPACE, run_id=RUN)
    assert receipt["first_sequence"] == 1
    assert len(replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN)) == 1
    journal.close()


def test_bad_local_payload_fails_before_remote_upload(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path)
    create_local(journal, vault, cycles=1)
    local_key = vault.manifest_keys(workspace_id=WORKSPACE, run_id=RUN)[0]
    vault.store._path(local_key).write_bytes(b"forged")
    with pytest.raises(ArchiveIntegrityError, match="manifest hash"):
        replicator.replicate_next(workspace_id=WORKSPACE, run_id=RUN)
    assert replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN) == []
    journal.close()


def test_corrupt_remote_objects_or_receipts_cannot_restore(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path)
    create_local(journal, vault, cycles=1)
    receipts = replicate_all(replicator)
    receipt_key = receipts[0]["receipt_key"]
    receipt_sha = receipts[0]["receipt_sha256"]
    receipt = json.loads(remote.get(receipt_key))
    path = remote._path(receipt["data_key"])
    path.write_bytes(b"altered")
    with pytest.raises(ArchiveIntegrityError):
        restore_remote_receipts(
            remote, [receipt_key], workspace_id=WORKSPACE, run_id=RUN,
            trusted_receipt_hashes={receipt_key: receipt_sha},
        )
    journal.close()


def test_bad_receipt_digest_truncation_gap_and_cross_workspace_fail(tmp_path):
    journal, vault, remote, replicator = make_stack(tmp_path, max_events=1)
    create_local(journal, vault, cycles=2)
    receipts = replicate_all(replicator)
    keys = replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN)
    with pytest.raises(ArchiveIntegrityError, match="pinned digest"):
        restore_remote_receipts(remote, keys, workspace_id=WORKSPACE, run_id=RUN,
                                trusted_receipt_hashes={key: "0"*64 for key in keys})
    with pytest.raises(ArchiveIntegrityError):
        restore_remote_receipts(remote, keys[1:], workspace_id=WORKSPACE, run_id=RUN)
    with pytest.raises(ArchiveIntegrityError):
        restore_remote_receipts(remote, keys, workspace_id="wrk_othermember01234", run_id=RUN)
    with pytest.raises(ArchiveIntegrityError):
        restore_remote_receipts(remote, keys + keys, workspace_id=WORKSPACE, run_id=RUN)
    with pytest.raises(ArchiveIntegrityError, match="upstream cycle-count"):
        restore_remote_receipts(remote, keys, workspace_id=WORKSPACE, run_id=RUN,
                                independent_expected_cycle_count=3)
    journal.close()


class FakeS3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def put_object(self, **kwargs):
        self.writes.append(kwargs)
        if kwargs.get("IfNoneMatch") != "*":
            raise FakeS3Error("InvalidRequest")
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise FakeS3Error("PreconditionFailed")
        self.objects[key] = kwargs["Body"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            raise FakeS3Error("NoSuchKey")
        value = self.objects[key]
        return {"Body": BytesIO(value), "ContentLength": len(value)}


def test_r2_injected_s3_client_uses_atomic_create_and_full_readback():
    fake = FakeS3()
    store = R2S3ImmutableStore(fake, "private-staging-bucket")
    key = "private/anevum-v5/wrk_member012345/testrun/part-0001.jsonl.gz"
    assert store.get(key) is None
    store.put_once(key, b"first")
    store.put_once(key, b"first")
    assert store.get(key) == b"first"
    assert len(fake.writes) == 2
    assert all(item["IfNoneMatch"] == "*" for item in fake.writes)
    with pytest.raises(ArchiveIntegrityError, match="differs"):
        store.put_once(key, b"replacement")


def test_r2_missing_conditional_support_refuses_unconditional_fallback():
    class UnsafeS3(FakeS3):
        def put_object(self, **kwargs):
            raise FakeS3Error("InvalidRequest")
    store = R2S3ImmutableStore(UnsafeS3(), "private-staging-bucket")
    key = "private/anevum-v5/wrk_member012345/testrun/part-0001.jsonl.gz"
    with pytest.raises(ArchiveIntegrityError, match="conditional write"):
        store.put_once(key, b"secret")
    with pytest.raises(ArchiveIntegrityError, match="outside private"):
        store.put_once("public/another/file", b"secret")


def test_r2_truncated_and_oversize_get_rejected():
    class TruncatedS3(FakeS3):
        def get_object(self, **kwargs):
            return {"Body": BytesIO(b"abc"), "ContentLength": 10}
    key = "private/anevum-v5/wrk_member012345/testrun/part-0001.jsonl.gz"
    with pytest.raises(ArchiveIntegrityError, match="truncated"):
        R2S3ImmutableStore(TruncatedS3(), "private-staging-bucket").get(key)

    class LargeS3(FakeS3):
        def get_object(self, **kwargs):
            return {"Body": BytesIO(b"abc"), "ContentLength": 99_999_999}
    with pytest.raises(ArchiveIntegrityError, match="oversized"):
        R2S3ImmutableStore(LargeS3(), "private-staging-bucket").get(key)


def test_r2_like_mock_transport_enforces_same_remote_restore_contract(tmp_path):
    fake = FakeS3()
    r2 = R2S3ImmutableStore(fake, "private-staging-bucket")
    journal, vault, _, replicator = make_stack(tmp_path, remote=r2)
    create_local(journal, vault, cycles=3)
    receipts = replicate_all(replicator)
    keys = replicator.receipt_keys(workspace_id=WORKSPACE, run_id=RUN)
    proof = restore_remote_receipts(
        R2S3ImmutableStore(fake, "private-staging-bucket"), keys,
        workspace_id=WORKSPACE, run_id=RUN,
        trusted_receipt_hashes={receipt["receipt_key"]:receipt["receipt_sha256"] for receipt in receipts},
        independent_expected_cycle_count=3,
    )
    assert proof["manifest"]["cycles_restored"] == 3
    assert proof["manifest"]["offhost_archive_verified"] is False  # Fake S3 != real R2.
    journal.close()


def test_no_broker_or_live_import_or_deployment_code():
    content = (Path(__file__).resolve().parents[1] / "next_rhen" / "remote_vault.py").read_text()
    for fragment in ("import alpaca", "from app.", "import boto3", "os.environ", "create_bucket("):
        assert fragment not in content
