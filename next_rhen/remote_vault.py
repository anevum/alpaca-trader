"""ANEVUM V5 FOUNDATION F2b: private R2-compatible object protocol and remote receipts.

Independent PAPER_RESEARCH_ONLY successor: no config, network client, bucket, secret,
broker, live runtime or production deployment is constructed here. A caller
injects a specifically authorized S3-compatible client in future staging.

A mock client passing these tests proves protocol logic only, NOT an off-host
R2 backup. Nor does remote-object readback attest missing upstream decisions,
market bars/quotes/universe, or trading alpha.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol

from .evidence_journal import EvidenceContractError, TRACKING_ID
from .evidence_vault import (
    ArchiveIntegrityError, LocalEvidenceVault, ImmutableObjectStore,
    _canonical, _checked_key, _scope, restore_local_vault,
)

RECEIPT_SCHEMA = "anevum.evidence-vault.remote-receipt.v1"
MAX_OBJECT_BYTES = 8_400_000
MAX_MANIFEST_BYTES = 128_000
PRIVATE_PREFIX = "private/anevum-v5/"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class S3CompatibleClient(Protocol):
    def put_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


def _private_key(key: str) -> None:
    parts = _checked_key(key)
    if len(parts) < 5 or not key.startswith(PRIVATE_PREFIX) or parts[:2] != ("private", "anevum-v5"):
        raise ArchiveIntegrityError("object key outside private RHEN evidence namespace")


class R2S3ImmutableStore:
    """S3 PutObject conditional-create + bounded exact-byte GET adapter.

    No boto3 import or client creation. The caller must separately supply an
    isolated staging R2 client, endpoint, least-privilege credentials and
    approved bucket. Never infer off-host attestation from this object alone.
    """

    def __init__(
        self, client: S3CompatibleClient, bucket: str, *,
        workspace_id: str, run_id: str,
    ):
        if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
            raise EvidenceContractError("invalid private storage bucket name")
        _scope(workspace_id, run_id)
        self._client = client
        self.bucket = bucket
        self.workspace_id, self.run_id = workspace_id, run_id
        self.key_prefix = f"{PRIVATE_PREFIX}{workspace_id}/{run_id}/"

    def _assert_scope(self, key: str) -> None:
        _private_key(key)
        if not key.startswith(self.key_prefix):
            raise ArchiveIntegrityError("cross-workspace remote storage access forbidden")

    def _get_bounded(self, key: str) -> bytes | None:
        self._assert_scope(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise ArchiveIntegrityError("private object GET unavailable") from exc
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ArchiveIntegrityError("private object GET has no readable body")
        length = response.get("ContentLength")
        if length is not None and (
            isinstance(length, bool) or not isinstance(length, int)
            or length < 0 or length > MAX_OBJECT_BYTES
        ):
            raise ArchiveIntegrityError("private object has invalid or oversized length")
        try:
            result = body.read(MAX_OBJECT_BYTES + 1)
        except Exception as exc:
            raise ArchiveIntegrityError("private object stream read failed") from exc
        finally:
            if callable(getattr(body, "close", None)):
                body.close()
        if not isinstance(result, bytes) or len(result) > MAX_OBJECT_BYTES:
            raise ArchiveIntegrityError("private object exceeded bounded read")
        if length is not None and len(result) != length:
            raise ArchiveIntegrityError("private object truncated during read")
        return result

    def get(self, key: str) -> bytes | None:
        return self._get_bounded(key)

    def put_once(self, key: str, value: bytes) -> None:
        self._assert_scope(key)
        if not isinstance(value, bytes) or not 0 < len(value) <= MAX_OBJECT_BYTES:
            raise ArchiveIntegrityError("invalid remote object size")
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=value, IfNoneMatch="*",
            )
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code not in ("412", "PreconditionFailed"):
                # A provider lacking conditional writes must fail closed; do
                # not silently overwrite or downgrade to HEAD+unconditional PUT.
                raise ArchiveIntegrityError("conditional write unavailable or failed") from exc
        # The ETag is not evidence of object identity. Always GET exact bytes.
        existing = self._get_bounded(key)
        if existing != value:
            raise ArchiveIntegrityError("remote object missing or differs after conditional write")


class RemoteVaultReplicator:
    """Publish F2a batches to an injected remote store and record honest receipts.

    Local WAL and immutable local objects are not modified or deleted.
    Records REMOTE_PROTOCOL_VERIFIED separately from ARCHIVED_VERIFIED because
    full off-host provider attestation and upstream evidence remain unproven.
    """

    def __init__(self, vault: LocalEvidenceVault, remote: ImmutableObjectStore):
        self.vault = vault
        self.remote = remote
        self.conn = vault.journal.conn
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS vault_remote_receipts(
              workspace_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              first_sequence INTEGER NOT NULL,
              last_sequence INTEGER NOT NULL,
              manifest_key TEXT NOT NULL,
              local_manifest_sha256 TEXT NOT NULL,
              receipt_key TEXT NOT NULL,
              receipt_sha256 TEXT NOT NULL,
              verification_state TEXT NOT NULL,
              PRIMARY KEY(workspace_id,run_id,first_sequence),
              UNIQUE(receipt_key)
            )"""
        )

    def receipt_keys(self, *, workspace_id: str, run_id: str) -> list[str]:
        _scope(workspace_id, run_id)
        return [row[0] for row in self.conn.execute(
            "SELECT receipt_key FROM vault_remote_receipts "
            "WHERE workspace_id=? AND run_id=? ORDER BY first_sequence",
            (workspace_id, run_id),
        )]

    def replicate_next(self, *, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        """Readback of both source objects and receipt precedes database ACK.

        If an object upload succeeds but the process stops before its SQLite
        receipt, a retry creates no conflicting remote object.
        """
        _scope(workspace_id, run_id)
        local_rows = self.conn.execute(
            "SELECT first_sequence,last_sequence,manifest_key,manifest_sha256 "
            "FROM vault_local_batches WHERE workspace_id=? AND run_id=? "
            "ORDER BY first_sequence",
            (workspace_id, run_id),
        ).fetchall()
        recorded = self.conn.execute(
            "SELECT first_sequence,last_sequence,manifest_key,local_manifest_sha256,"
            "receipt_key,receipt_sha256,verification_state "
            "FROM vault_remote_receipts WHERE workspace_id=? AND run_id=? "
            "ORDER BY first_sequence",
            (workspace_id, run_id),
        ).fetchall()
        if len(recorded) > len(local_rows):
            raise ArchiveIntegrityError("remote receipt population exceeds local source")
        expected = 1
        for index, row in enumerate(local_rows):
            first, last, local_key, local_sha = row
            if first != expected or last < first:
                raise ArchiveIntegrityError("local archive index sequence gap")
            _private_key(local_key)
            if index < len(recorded):
                remote_row = recorded[index]
                if (remote_row[0] != first or remote_row[1] != last or
                        remote_row[2] != local_key or remote_row[3] != local_sha or
                        remote_row[6] != "REMOTE_PROTOCOL_VERIFIED"):
                    raise ArchiveIntegrityError("remote receipt index conflict")
            expected = last + 1
        if len(recorded) == len(local_rows):
            return None

        first, last, local_key, local_sha = local_rows[len(recorded)]
        data = self.vault.store.get(local_key)
        if data is None or len(data) > MAX_MANIFEST_BYTES or sha256(data).hexdigest() != local_sha:
            raise ArchiveIntegrityError("source manifest hash/size mismatch")
        try:
            local_manifest = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise ArchiveIntegrityError("source manifest unreadable") from exc
        if not isinstance(local_manifest, dict) or _canonical(local_manifest) != data:
            raise ArchiveIntegrityError("source manifest noncanonical")
        data_key = local_manifest.get("data_key")
        if not isinstance(data_key, str):
            raise ArchiveIntegrityError("source data key missing")
        _private_key(data_key)
        if (local_manifest.get("workspace_id") != workspace_id or
                local_manifest.get("run_id") != run_id or
                local_manifest.get("first_sequence") != first or
                local_manifest.get("last_sequence") != last or
                data_key + ".manifest.json" != local_key or
                local_manifest.get("durability") != "LOCAL_ONLY" or
                local_manifest.get("offhost_verified") is not False):
            raise ArchiveIntegrityError("invalid local batch scope or durability")
        packed = self.vault.store.get(data_key)
        if packed is None or len(packed) != local_manifest.get("compressed_bytes") or (
                sha256(packed).hexdigest() != local_manifest.get("compressed_sha256")):
            raise ArchiveIntegrityError("source compressed object hash/size mismatch")
        # Validate the full original local chain before copying the next batch.
        prior_keys = [row[2] for row in local_rows[:len(recorded)+1]]
        restore_local_vault(
            self.vault.store, prior_keys, workspace_id=workspace_id, run_id=run_id,
        )
        # Ensure all rows remain local-ACKed (never silently promote unsealed data).
        count = self.conn.execute(
            "SELECT COUNT(*) FROM decision_journal "
            "WHERE workspace_id=? AND run_id=? AND sequence_no BETWEEN ? AND ? "
            "AND archive_state='LOCAL_ARCHIVE_VERIFIED'",
            (workspace_id, run_id, first, last),
        ).fetchone()[0]
        if count != last - first + 1:
            raise ArchiveIntegrityError("journal batch not locally verified before replication")

        receipt_key = data_key + ".remote-receipt.json"
        receipt = {
            "schema_version": RECEIPT_SCHEMA, "tracking_id": TRACKING_ID,
            "workspace_id": workspace_id, "run_id": run_id,
            "first_sequence": first, "last_sequence": last,
            "local_manifest_key": local_key,
            "local_manifest_sha256": local_sha,
            "data_key": data_key,
            "compressed_sha256": local_manifest["compressed_sha256"],
            "raw_sha256": local_manifest["raw_sha256"],
            "event_count": local_manifest["event_count"],
            "candidate_count": local_manifest["candidate_count"],
            "storage_status": "REMOTE_PROTOCOL_VERIFIED",
            "offhost_provider_attested": False,
            "upstream_attested": False,
            "market_objects_verified": False,
            "research_ready": False,
            "broker_write_authority": False,
        }
        receipt_bytes = _canonical(receipt)
        for key, value in ((data_key, packed), (local_key, data),
                           (receipt_key, receipt_bytes)):
            self.remote.put_once(key, value)
            if self.remote.get(key) != value:
                raise ArchiveIntegrityError("remote exact-byte readback rejected")

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            fresh = self.conn.execute(
                "SELECT last_sequence,manifest_key,manifest_sha256 FROM vault_local_batches "
                "WHERE workspace_id=? AND run_id=? AND first_sequence=?",
                (workspace_id, run_id, first),
            ).fetchone()
            count = self.conn.execute(
                "SELECT COUNT(*) FROM decision_journal "
                "WHERE workspace_id=? AND run_id=? AND sequence_no BETWEEN ? AND ? "
                "AND archive_state='LOCAL_ARCHIVE_VERIFIED'",
                (workspace_id, run_id, first, last),
            ).fetchone()[0]
            if fresh != (last, local_key, local_sha) or count != last-first+1:
                raise ArchiveIntegrityError("local batch changed before remote receipt ACK")
            self.conn.execute(
                "INSERT INTO vault_remote_receipts VALUES (?,?,?,?,?,?,?,?,?)",
                (workspace_id, run_id, first, last, local_key, local_sha,
                 receipt_key, sha256(receipt_bytes).hexdigest(), "REMOTE_PROTOCOL_VERIFIED"),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {
            "first_sequence": first, "last_sequence": last,
            "receipt_key": receipt_key,
            "receipt_sha256": sha256(receipt_bytes).hexdigest(),
            "status": "REMOTE_PROTOCOL_VERIFIED_UPSTREAM_UNATTESTED",
            "offhost_provider_attested": False,
            "research_ready": False,
            "broker_write_authority": False,
            "put_operations": 3,
            "minimum_readbacks": 3,
            "payload_bytes": len(packed) + len(data) + len(receipt_bytes),
        }


def restore_remote_receipts(
    remote: ImmutableObjectStore,
    receipt_keys: list[str], *,
    workspace_id: str, run_id: str,
    trusted_receipt_hashes: Mapping[str, str] | None = None,
    independent_expected_cycle_count: int | None = None,
) -> dict[str, Any]:
    """Restore through the object protocol ONLY; original WAL may be destroyed.

    Pinned receipt hashes from a *separate trusted source* establish whether
    receipt metadata has changed. They do not prove upstream market evidence,
    provider identity or trading alpha.
    """
    _scope(workspace_id, run_id)
    if not 1 <= len(receipt_keys) <= 250 or len(set(receipt_keys)) != len(receipt_keys):
        raise ArchiveIntegrityError("invalid remote receipt count or duplicates")
    manifests: list[str] = []
    trusted = trusted_receipt_hashes is not None
    for key in receipt_keys:
        _private_key(key)
        if not key.startswith(f"{PRIVATE_PREFIX}{workspace_id}/{run_id}/") or not key.endswith(".jsonl.gz.remote-receipt.json"):
            raise ArchiveIntegrityError("cross-workspace remote receipt key")
        raw = remote.get(key)
        if raw is None or len(raw) > MAX_MANIFEST_BYTES:
            raise ArchiveIntegrityError("remote receipt unavailable or oversized")
        if trusted:
            expected_hash = trusted_receipt_hashes.get(key)
            if expected_hash is None or sha256(raw).hexdigest() != expected_hash:
                raise ArchiveIntegrityError("remote receipt differs from independently pinned digest")
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ArchiveIntegrityError("remote receipt is malformed") from exc
        if not isinstance(obj, dict) or _canonical(obj) != raw:
            raise ArchiveIntegrityError("remote receipt not canonical")
        if (obj.get("schema_version") != RECEIPT_SCHEMA or
                obj.get("tracking_id") != TRACKING_ID or
                obj.get("workspace_id") != workspace_id or
                obj.get("run_id") != run_id or
                obj.get("storage_status") != "REMOTE_PROTOCOL_VERIFIED" or
                obj.get("offhost_provider_attested") is not False or
                obj.get("research_ready") is not False or
                obj.get("broker_write_authority") is not False or
                obj.get("upstream_attested") is not False):
            raise ArchiveIntegrityError("remote receipt status/scope not valid")
        manifest_key = obj.get("local_manifest_key")
        if not isinstance(manifest_key, str) or not manifest_key.startswith(
                f"{PRIVATE_PREFIX}{workspace_id}/{run_id}/"):
            raise ArchiveIntegrityError("remote source manifest path outside scope")
        data = remote.get(manifest_key)
        if data is None or sha256(data).hexdigest() != obj.get("local_manifest_sha256"):
            raise ArchiveIntegrityError("remote source manifest changed")
        manifest = json.loads(data)
        if (manifest.get("data_key") != obj.get("data_key") or
                manifest.get("compressed_sha256") != obj.get("compressed_sha256") or
                manifest.get("raw_sha256") != obj.get("raw_sha256") or
                manifest.get("first_sequence") != obj.get("first_sequence") or
                manifest.get("last_sequence") != obj.get("last_sequence") or
                manifest.get("event_count") != obj.get("event_count") or
                manifest.get("candidate_count") != obj.get("candidate_count")):
            raise ArchiveIntegrityError("remote receipt conflicts with source manifest")
        manifests.append(manifest_key)
    restored = restore_local_vault(
        remote, manifests, workspace_id=workspace_id, run_id=run_id,
        independent_expected_cycle_count=independent_expected_cycle_count,
    )
    proof = restored["manifest"]
    proof["remote_receipt_count"] = len(receipt_keys)
    proof["receipt_digest_pinned"] = trusted
    proof["remote_protocol_restore_verified"] = True
    proof["offhost_archive_verified"] = False
    proof["upstream_source_coverage_proven"] = False
    proof["evidence_state"] = "AWAITING_EVIDENCE"
    proof["research_ready"] = False
    return restored
