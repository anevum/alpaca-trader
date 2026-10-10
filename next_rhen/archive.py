"""ANEVUM V5 F2: safe, bounded, write-once session archive protocol.

No broker imports, network clients, credentials, account permissions or production
side effects. A caller supplies a private object store with write-once/GET support.
Local staging tests must pass before supplying an authenticated R2 adapter.

This is an off-host transport proof only. An archived *journal* is not a
complete trading session without independent upstream scan and market provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol
import gzip
import json
import re

from .evidence_journal import DecisionJournal, EvidenceIntegrityError

_MAX_BATCH_BYTES = 4_000_000
_SCOPE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PrivateObjectStore(Protocol):
    def put_if_absent(self, key: str, content: bytes) -> bool:
        """Atomically create key; False only if it already exists."""

    def get(self, key: str) -> bytes:
        """Read exact bytes from private remote or local test store."""


@dataclass(frozen=True)
class ArchiveReceipt:
    object_key: str
    compressed_sha256: str
    jsonl_sha256: str
    manifest_key: str
    records: int
    candidates: int
    verified: bool
    independent_upstream_proven: bool = False


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_manifest(data: bytes) -> dict:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise EvidenceIntegrityError("archive manifest invalid") from exc
    if not isinstance(obj, dict):
        raise EvidenceIntegrityError("archive manifest must be an object")
    return obj


def _verify_existing_or_write(store: PrivateObjectStore, key: str, data: bytes) -> None:
    try:
        written = store.put_if_absent(key, data)
    except Exception as exc:
        raise EvidenceIntegrityError("off-host archive write failed") from exc
    if not isinstance(written, bool):
        raise EvidenceIntegrityError("object store did not supply reliable create-only acknowledgment")
    try:
        remote = store.get(key)
    except Exception as exc:
        raise EvidenceIntegrityError("off-host archive readback failed") from exc
    if sha256(remote).digest() != sha256(data).digest() or remote != data:
        raise EvidenceIntegrityError("off-host archive checksum/content mismatch")
    if not written and remote != data:
        raise EvidenceIntegrityError("conflicting immutable archive key")


def archive_private_session(
    journal: DecisionJournal,
    store: PrivateObjectStore,
    *,
    workspace_id: str,
    run_id: str,
    session_date: str,
    independent_expected_cycle_count: int | None = None,
) -> ArchiveReceipt:
    """Export verified WAL events and read back each write-once object.

    Explicitly refuses partial per-run archives; the local WAL export bounds
    and integrity are checked before contacting storage. Never ACK journal rows
    in F2: durable archival acknowledgment requires a separate transaction,
    independent source counts, and R2 adapter protocol proof in F2b.
    """
    if not workspace_id.startswith("wrk_") or not _SCOPE.fullmatch(workspace_id) or not _SCOPE.fullmatch(run_id) or not _DATE.fullmatch(session_date):
        raise EvidenceIntegrityError("unsafe archive scope")
    export = journal.export_session(
        workspace_id=workspace_id, run_id=run_id, session_date=session_date,
        independent_expected_cycle_count=independent_expected_cycle_count,
    )
    original = export["manifest"]
    raw = b"".join(_encode(event) + b"\n" for event in export["private_cycles"])
    if not raw or len(raw) > _MAX_BATCH_BYTES:
        raise EvidenceIntegrityError("session too large for atomic F2 archive; needs bounded batch archiver")
    if sha256(raw).hexdigest() != original["jsonl_sha256"]:
        raise EvidenceIntegrityError("WAL export digest does not match canonical source")
    compressed = gzip.compress(raw, mtime=0, compresslevel=6)
    compressed_digest = sha256(compressed).hexdigest()
    scope = f"staging/{workspace_id}/{run_id}/{session_date}/anevum.decision-cycle.v1"
    key = f"{scope}/{compressed_digest}.jsonl.gz"
    manifest = {
        "schema_version": "anevum.f2.archive-manifest.v1",
        "source_schema_version": "anevum.decision-cycle.v1",
        "workspace_id": workspace_id,
        "run_id": run_id,
        "session_date": session_date,
        "object_key": key,
        "compressed_sha256": compressed_digest,
        "jsonl_sha256": original["jsonl_sha256"],
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "cycles": original["cycles_exported"],
        "candidates": original["candidates_exported"],
        "source_quality_issues": original["quality_issues"],
        "independent_expected_cycle_count": independent_expected_cycle_count,
        "independent_upstream_proven": False,
        "full_population_proven": False,
        "market_references_restored": False,
        "broker_write_authority": False,
    }
    manifest_raw = _encode(manifest)
    manifest_key = f"{scope}/{sha256(manifest_raw).hexdigest()}.manifest.json"
    _verify_existing_or_write(store, key, compressed)
    _verify_existing_or_write(store, manifest_key, manifest_raw)
    restored_manifest = _load_manifest(store.get(manifest_key))
    restored_compressed = store.get(key)
    if restored_manifest != manifest or sha256(restored_compressed).hexdigest() != compressed_digest:
        raise EvidenceIntegrityError("restored archive manifest/bytes differ")
    try:
        restored_raw = gzip.decompress(restored_compressed)
    except OSError as exc:
        raise EvidenceIntegrityError("compressed archive unreadable") from exc
    if restored_raw != raw or sha256(restored_raw).hexdigest() != manifest["jsonl_sha256"]:
        raise EvidenceIntegrityError("restored source events do not reproduce")
    return ArchiveReceipt(
        object_key=key,
        compressed_sha256=compressed_digest,
        jsonl_sha256=manifest["jsonl_sha256"],
        manifest_key=manifest_key,
        records=manifest["cycles"],
        candidates=manifest["candidates"],
        verified=True,
        independent_upstream_proven=False,
    )


class LocalTestObjectStore:
    """Strict immutable simulator for offline staging tests. NEVER public storage."""
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(not x or x in (".", "..") or not re.fullmatch(r"[a-zA-Z0-9_.-]+", x) for x in parts):
            raise EvidenceIntegrityError("invalid object key")
        return self.root.joinpath(*parts)

    def put_if_absent(self, key: str, content: bytes) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as f:
                f.write(content)
            return True
        except FileExistsError:
            return False

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()
