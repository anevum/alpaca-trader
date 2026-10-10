"""F2c staging acceptance runner for real private R2 S3 conditional PUT.

Default: fully offline, synthetic decision cycles and an in-memory S3 simulator.
Real R2: --execute plus separate explicit env authorization and short-lived,
staging-bucket-only credentials. Nothing in this module connects to Alpaca,
deploys a service, migrates a database, or promotes research evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from typing import Any

from next_rhen.evidence_journal import DecisionJournal, SCHEMA_VERSION, TRACKING_ID
from next_rhen.evidence_vault import (
    ArchiveIntegrityError, LocalEvidenceVault, LocalImmutableObjectStore,
)
from next_rhen.remote_vault import (
    R2S3ImmutableStore, RemoteVaultReplicator, restore_remote_receipts,
)

BUCKET = "anevum-rhen-v5-evidence-staging"
WORKSPACE = "wrk_f2cstage000001"
EVENTS = 3
CANDIDATES = EVENTS * 2
MAX_DATA_WRITTEN = 128_000
MAX_WRITE_CALLS = 12
MAX_READ_CALLS = 100
_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")


class MockPreconditionFailed(Exception):
    def __init__(self) -> None:
        super().__init__("PreconditionFailed")
        self.response = {"Error": {"Code": "PreconditionFailed"}}


class MockMissingObject(Exception):
    def __init__(self) -> None:
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class StrictSyntheticS3:
    """Used only by default/offline tests. Refuses an unguarded overwrite."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.conditional_writes = 0

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("IfNoneMatch") != "*" or kwargs.get("Bucket") != BUCKET:
            raise ArchiveIntegrityError("mock S3 requires staging-only create-if-absent")
        key = (kwargs["Bucket"], kwargs["Key"])
        self.conditional_writes += 1
        if key in self.objects:
            raise MockPreconditionFailed()
        if not isinstance(kwargs.get("Body"), bytes):
            raise ArchiveIntegrityError("mock object body must be binary")
        self.objects[key] = kwargs["Body"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (kwargs.get("Bucket"), kwargs.get("Key"))
        if key not in self.objects:
            raise MockMissingObject()
        body = self.objects[key]
        return {"Body": BytesIO(body), "ContentLength": len(body)}


class MeteredStore:
    """Hard cap staging upload/read calls and total bytes, fail-closed."""

    def __init__(self, underlying: R2S3ImmutableStore) -> None:
        self.underlying = underlying
        self.bytes = 0
        self.writes = 0
        self.reads = 0

    def put_once(self, key: str, value: bytes) -> None:
        if not isinstance(value, bytes):
            raise ArchiveIntegrityError("staging object must be binary")
        if self.writes + 1 > MAX_WRITE_CALLS or self.bytes + len(value) > MAX_DATA_WRITTEN:
            raise ArchiveIntegrityError("staging budget exceeded before write")
        # Failed 412/network attempts still consume requests and potentially
        # provider budget; count them *before* invoking external storage.
        self.writes += 1
        self.bytes += len(value)
        self.underlying.put_once(key, value)

    def get(self, key: str) -> bytes | None:
        if self.reads + 1 > MAX_READ_CALLS:
            raise ArchiveIntegrityError("staging read budget exceeded")
        self.reads += 1
        return self.underlying.get(key)


def synthetic_cycle(*, sequence: int, run_id: str) -> dict[str, Any]:
    """Deliberately incomplete market evidence: no real feed provenance."""
    session = "2026-10-09"
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": WORKSPACE, "run_id": run_id,
        "cycle_id": f"f2c-synthetic-{sequence:04d}",
        "sequence_no": sequence,
        "session_date": session, "occurred_at": session+"T14:30:00Z",
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": "f2c-archive-acceptance-v1",
        "config_sha256": "a"*64, "code_sha256": "b"*64,
        "market_source": {
            "feed": "SYNTHETIC_NO_MARKET",
            "data_status": "PARTIAL",
            "asof_timestamp": session+"T14:29:00Z",
            "universe_ref": "c"*64,
        },
        "universe_symbols": ["AAAA", "BBBB"],
        "candidates": [
            {"symbol":"AAAA","decision":"QUALIFIED","reason":"synthetic_positive_case",
             "observed_at":session+"T14:29:30Z","features":{"synthetic_signal":0.2}},
            {"symbol":"BBBB","decision":"REJECTED","reason":"synthetic_rejected_case",
             "observed_at":session+"T14:29:31Z","features":{"synthetic_signal":-0.1}},
        ],
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ArchiveIntegrityError(message)


def run_staging_probe(
    client: Any,
    run_id: str,
    *,
    provider: str,
) -> dict[str, Any]:
    """Archive -> GET verification -> local DB closed -> remote-only restore.

    Every operation is scoped to the synthetic workspace and an isolated run.
    This does not update the source journal to off-host verified.
    """
    _assert(provider in {"MOCK_S3", "REAL_CLOUDFLARE_R2_STAGING"}, "unknown provider")
    _assert(re.fullmatch(r"f2c-[a-z0-9-]{8,48}", run_id) is not None, "invalid synthetic run scope")
    with tempfile.TemporaryDirectory(prefix="anevum-v5-f2c-") as directory:
        root = Path(directory)
        journal = DecisionJournal(root/"source.sqlite")
        local = LocalImmutableObjectStore(root/"local")
        vault = LocalEvidenceVault(journal, local, max_events=2)
        try:
            for seq in range(1,EVENTS+1):
                journal.append_cycle(synthetic_cycle(sequence=seq,run_id=run_id))
            batches = []
            while row := vault.archive_next(workspace_id=WORKSPACE,run_id=run_id):
                batches.append(row)
            _assert([x["archived_events"] for x in batches]==[2,1],"local archive boundary mismatch")

            remote = MeteredStore(R2S3ImmutableStore(client,BUCKET,workspace_id=WORKSPACE,run_id=run_id))
            replicator = RemoteVaultReplicator(vault,remote)
            receipts = []
            while row := replicator.replicate_next(workspace_id=WORKSPACE,run_id=run_id):
                receipts.append(row)
            _assert(len(receipts)==2,"remote archive receipts incomplete")
            keys = replicator.receipt_keys(workspace_id=WORKSPACE,run_id=run_id)
            anchors = {row["receipt_key"]:row["receipt_sha256"] for row in receipts}
            _assert(len(keys)==2,"lost remote receipt key")

            # Negative probe of real conditional-put semantics: exact original
            # bytes MUST remain unchanged even after a forbidden replacement.
            first_manifest=local.get(vault.manifest_keys(workspace_id=WORKSPACE,run_id=run_id)[0])
            _assert(first_manifest is not None,"lost local archive manifest")
            data_key=json.loads(first_manifest)["data_key"]
            original=remote.get(data_key)
            _assert(isinstance(original,bytes),"archive was not remotely readable")
            _assert(sha256(original).hexdigest()==json.loads(first_manifest)["compressed_sha256"],
                    "remote object does not match original compressed digest")
            try:
                remote.put_once(data_key,b"F2C_FORGED_REPLACEMENT")
            except ArchiveIntegrityError:
                pass
            else:
                raise ArchiveIntegrityError("remote conditional PUT accepted a conflicting replacement")
            _assert(remote.get(data_key)==original,
                    "remote object changed despite conditional overwrite rejection")

            _assert(all(row[0]=="LOCAL_ARCHIVE_VERIFIED" for row in journal.conn.execute(
                    "SELECT archive_state FROM decision_journal WHERE workspace_id=? AND run_id=?",
                    (WORKSPACE,run_id))), "incorrectly promoted source journal off-host state")
        finally:
            journal.close()
        # Destroy all local source files before recovery. This protects
        # against accidentally succeeding by consulting the original WAL
        # or local archive rather than the remote provider.
        shutil.rmtree(root/"local")
        for suffix in ("", "-wal", "-shm"):
            (root/("source.sqlite"+suffix)).unlink(missing_ok=True)

        # Restore ONLY from independently retrieved remote objects, with
        # trusted manifest digests that were pinned outside the object store.
        clean=MeteredStore(R2S3ImmutableStore(client,BUCKET,workspace_id=WORKSPACE,run_id=run_id))
        result=restore_remote_receipts(
            clean,keys,workspace_id=WORKSPACE,run_id=run_id,
            trusted_receipt_hashes=anchors,independent_expected_cycle_count=EVENTS,
        )
        proof=result["manifest"]
        _assert(proof["cycles_restored"]==EVENTS,"wrong restored cycle count")
        _assert(proof["candidates_restored"]==CANDIDATES,"lost candidates")
        _assert(all(c["execution_mode"]=="PAPER_RESEARCH_ONLY" for c in result["private_cycles"]),
                "unexpected execution capability in synthetic archive")
        _assert(all(c["market_source"]["data_status"]=="PARTIAL" for c in result["private_cycles"]),
                "false market source completeness")
        _assert(not proof["research_ready"] and not proof["offhost_archive_verified"],
                "a synthetic drill must not promote production evidence state")
        _assert(proof["evidence_state"]=="AWAITING_EVIDENCE","synthetic source status must remain gated")
        return {
            "tracking_id":TRACKING_ID,
            "provider_tested":provider,
            "workspace_id":WORKSPACE,"run_id":run_id,
            "synthetic_only":True,"broker_calls":0,"broker_write_authorized":False,
            "cycles":EVENTS,"candidates":CANDIDATES,"rejected":EVENTS,
            "local_batches":len(batches),"remote_receipts":len(receipts),
            "remote_source_restore_sha256":proof["restored_jsonl_sha256"],
            "receipt_anchors_for_independent_recovery":dict(sorted(anchors.items())),
            "sha256_receipts_pinned_outside_remote":True,
            "conflicting_conditional_put_rejected":True,
            "temporary_source_deleted_before_restore":True,
            "storage_operation_budget":{"put_requests":remote.writes,
                "get_requests":remote.reads+clean.reads,"uploaded_bytes":remote.bytes,
                "maximum_uploaded_bytes":MAX_DATA_WRITTEN},
            "restored":True,"upstream_market_attested":False,
            "production_wal_offhost_state_promoted":False,
            "evidence_state":"AWAITING_EVIDENCE",
            "claim":"STAGING_R2_CONDITIONAL_AND_RESTORE_ONLY" if provider=="REAL_CLOUDFLARE_R2_STAGING"
                     else "MOCK_PROTOCOL_ONLY",
        }


def _real_s3_client() -> Any:
    """Credentials supplied only by an explicitly approved isolated runner."""
    if os.environ.get("ANEVUM_F2C_STAGING_APPROVED") != "YES_SYNTHETIC_R2_ONLY":
        raise RuntimeError("real staging probe requires explicit synthetic-only authorization")
    account_id = os.environ.get("ANEVUM_F2C_R2_ACCOUNT_ID","")
    access_key = os.environ.get("ANEVUM_F2C_R2_ACCESS_KEY_ID","")
    secret_key = os.environ.get("ANEVUM_F2C_R2_SECRET_ACCESS_KEY","")
    if not _ACCOUNT_ID.fullmatch(account_id) or not access_key or not secret_key:
        raise RuntimeError("staging account and least-privilege R2 keys must be supplied via secrets")
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.config import Config  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("boto3 is required only for authorized real staging") from exc

    return boto3.client(
        "s3",endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        region_name="auto",aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4",connect_timeout=5,read_timeout=20,
                      retries={"mode":"standard","max_attempts":2}),
    )


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="ANEVUM V5 F2c synthetic R2 staging acceptance")
    parser.add_argument("--execute",action="store_true",
                        help="Use approved private staging R2 credentials; default is offline mock")
    arguments=parser.parse_args(argv)
    try:
        client=_real_s3_client() if arguments.execute else StrictSyntheticS3()
        result=run_staging_probe(
            client,"f2c-"+secrets.token_hex(7),
            provider="REAL_CLOUDFLARE_R2_STAGING" if arguments.execute else "MOCK_S3",
        )
    except Exception as exc:
        # Never print SDK exception messages: they may include endpoint or
        # authorization metadata. Show a type and a fixed fail-closed status.
        print(json.dumps({"status":"F2C_STAGING_PROBE_BLOCKED",
                          "error_type":type(exc).__name__,
                          "production_unchanged":True},sort_keys=True))
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
