"""ANEVUM V5 FOUNDATION F2a: sealed local archive and independent restore.

Paper/research-only successor RHEN. This has NO broker, Cloudflare, HTTP,
Railway, public member API, or live strategy integration. A local archive
cannot satisfy the separate off-host R2 and upstream source-attestation gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Protocol
import zlib

from .evidence_journal import (
    DecisionJournal, EvidenceContractError, EvidenceIntegrityError,
    TRACKING_ID, validate_cycle,
)

ARCHIVE_SCHEMA = "anevum.evidence-vault.local.v1"
MAX_BATCH_EVENTS = 250
MAX_BATCH_BYTES = 8_000_000
MAX_RESTORE_EVENTS = 10_000
MAX_RESTORE_BATCHES = 250
ZERO_SHA = "0" * 64
SCOPE = re.compile(r"^wrk_[A-Za-z0-9_-]{8,64}$")
RUN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HEX = re.compile(r"^[0-9a-f]{64}$")


class ArchiveIntegrityError(EvidenceIntegrityError):
    """Archive gap, changed object, invalid bytes or scope, or false ACK."""


class ImmutableObjectStore(Protocol):
    """Small private, write-once object contract. No network adapter in F2a."""

    def put_once(self, key: str, value: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _scope(workspace_id: str, run_id: str) -> None:
    if not isinstance(workspace_id, str) or not SCOPE.fullmatch(workspace_id):
        raise EvidenceContractError("invalid workspace scope")
    if not isinstance(run_id, str) or not RUN.fullmatch(run_id):
        raise EvidenceContractError("invalid run scope")


def _key(workspace_id: str, run_id: str, first: int, last: int, raw_sha: str) -> str:
    return (
        f"private/anevum-v5/{workspace_id}/{run_id}/"
        f"part-{first:012d}-{last:012d}-{raw_sha[:24]}.jsonl.gz"
    )


def _checked_key(key: str) -> tuple[str, ...]:
    if (
        not isinstance(key, str) or len(key) > 500 or key.startswith("/")
        or "\\" in key or "\x00" in key
    ):
        raise ArchiveIntegrityError("invalid object key")
    parts = tuple(key.split("/"))
    if any(p in ("", ".", "..") for p in parts):
        raise ArchiveIntegrityError("object key traversal is forbidden")
    return parts


class LocalImmutableObjectStore:
    """Test/staging file store with atomic write-once publication and fsync.

    Local disk is neither off-host backup nor private R2. Never mark a source
    as off-host ARCHIVED_VERIFIED merely because this store can restore it.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = self.root.joinpath(*_checked_key(key))
        if not path.resolve().is_relative_to(self.root):
            raise ArchiveIntegrityError("archive object escapes private store")
        return path

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def put_once(self, key: str, value: bytes) -> None:
        if not isinstance(value, bytes):
            raise ArchiveIntegrityError("archive objects must be bytes")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(self.root):
            raise ArchiveIntegrityError("archive parent escapes private store")
        tmp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".vault-", delete=False,
            ) as stream:
                tmp = stream.name
                os.chmod(tmp, 0o600)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(tmp, path)  # atomic NO-OVERWRITE publication
            except FileExistsError:
                if self.get(key) != value:
                    raise ArchiveIntegrityError("existing archive object differs")
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)
        if self.get(key) != value:
            raise ArchiveIntegrityError("archive content read-back mismatch")


class LocalEvidenceVault:
    """Batched WAL journal drain into sealed LOCAL objects (F2a, not R2).

    This class is intended for a single offline F2a writer. It does not
    import/execute broker code. Archive ACKs are LOCAL_ARCHIVE_VERIFIED only.
    """

    def __init__(
        self, journal: DecisionJournal, store: ImmutableObjectStore, *,
        max_events: int = MAX_BATCH_EVENTS,
        max_raw_bytes: int = MAX_BATCH_BYTES,
    ):
        if isinstance(max_events, bool) or not 1 <= max_events <= MAX_BATCH_EVENTS:
            raise ValueError("invalid bounded batch event count")
        if isinstance(max_raw_bytes, bool) or not 1 <= max_raw_bytes <= MAX_BATCH_BYTES:
            raise ValueError("invalid bounded batch byte count")
        self.journal, self.store = journal, store
        self.max_events, self.max_raw_bytes = max_events, max_raw_bytes
        self.journal.conn.execute(
            """CREATE TABLE IF NOT EXISTS vault_local_batches(
                 workspace_id TEXT NOT NULL,
                 run_id TEXT NOT NULL,
                 first_sequence INTEGER NOT NULL,
                 last_sequence INTEGER NOT NULL,
                 end_chain_sha256 TEXT NOT NULL,
                 manifest_key TEXT NOT NULL,
                 manifest_sha256 TEXT NOT NULL,
                 PRIMARY KEY(workspace_id, run_id, first_sequence),
                 UNIQUE(manifest_key)
               )"""
        )

    def manifest_keys(self, *, workspace_id: str, run_id: str) -> list[str]:
        _scope(workspace_id, run_id)
        return [row[0] for row in self.journal.conn.execute(
            "SELECT manifest_key FROM vault_local_batches "
            "WHERE workspace_id=? AND run_id=? ORDER BY first_sequence",
            (workspace_id, run_id),
        )]

    def pressure_snapshot(
        self, *, workspace_id: str, run_id: str,
        warning_bytes: int = 64_000_000,
        blocked_bytes: int = 128_000_000,
    ) -> dict[str, Any]:
        """Read-only native RHEN Operations signal; never changes live risk."""
        _scope(workspace_id, run_id)
        if not 0 < warning_bytes < blocked_bytes:
            raise ValueError("pressure limits must increase")
        row = self.journal.conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload_json AS BLOB))),0) "
            "FROM decision_journal WHERE workspace_id=? AND run_id=? "
            "AND archive_state!='ARCHIVED_VERIFIED'",
            (workspace_id, run_id),
        ).fetchone()
        count, size = int(row[0]), int(row[1])
        level = ("BLOCKED" if size >= blocked_bytes else
                 "WARNING" if size >= warning_bytes else "NORMAL")
        return {
            "workspace_id": workspace_id, "run_id": run_id,
            "not_offhost_events": count, "not_offhost_bytes": size,
            "pressure": level, "offhost_verified": False,
            "evidence_state": "BLOCKED" if level == "BLOCKED" else "AWAITING_EVIDENCE",
            "action": "REVIEW_ARCHIVE_PRESSURE" if level != "NORMAL" else "NONE",
        }

    def archive_next(self, *, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        """Seal the next ordered pending WAL range; ACK only after read-back.

        Idempotently retries a crash after the data object or manifest was
        written but before the database transaction committed.
        """
        _scope(workspace_id, run_id)
        conn = self.journal.conn
        previous = conn.execute(
            "SELECT last_sequence,end_chain_sha256 FROM vault_local_batches "
            "WHERE workspace_id=? AND run_id=? ORDER BY last_sequence DESC LIMIT 1",
            (workspace_id, run_id),
        ).fetchone()
        expected = 1 if previous is None else previous[0] + 1
        prior_chain = ZERO_SHA if previous is None else previous[1]
        pending = conn.execute(
            "SELECT sequence_no,cycle_id,session_date,payload_json,"
            "payload_sha256,chain_sha256,archive_state "
            "FROM decision_journal "
            "WHERE workspace_id=? AND run_id=? AND archive_state='PENDING_OFFHOST_ARCHIVE' "
            "ORDER BY sequence_no LIMIT ?",
            (workspace_id, run_id, self.max_events),
        ).fetchall()
        if not pending:
            return None
        if pending[0][0] != expected:
            raise ArchiveIntegrityError("unverified sequence gap before next archive batch")

        first = expected
        raw_lines: list[bytes] = []
        dates: set[str] = set()
        candidates = rejected = unmeasurable = 0
        market_partial = 0
        for seq, cycle_id, session, raw_text, payload_hash, chain_hash, state in pending:
            if seq != expected or state != "PENDING_OFFHOST_ARCHIVE":
                raise ArchiveIntegrityError("noncontiguous or previously acknowledged archive row")
            raw = raw_text.encode("utf-8")
            if len(raw) + 1 > self.max_raw_bytes:
                raise ArchiveIntegrityError("single event exceeds bounded archive size")
            if sum(len(line) + 1 for line in raw_lines) + len(raw) + 1 > self.max_raw_bytes:
                break
            try:
                payload = json.loads(raw)
                checked = validate_cycle(payload)
            except (ValueError, TypeError) as exc:
                raise ArchiveIntegrityError("invalid stored decision envelope") from exc
            if checked != raw:
                raise ArchiveIntegrityError("stored event is not canonical")
            actual_sha = sha256(raw).hexdigest()
            if payload_hash != actual_sha:
                raise ArchiveIntegrityError("stored payload SHA mismatch")
            actual_chain = sha256(
                bytes.fromhex(prior_chain) + bytes.fromhex(payload_hash)
            ).hexdigest()
            if chain_hash != actual_chain:
                raise ArchiveIntegrityError("decision chain hash mismatch")
            if (payload["workspace_id"] != workspace_id or payload["run_id"] != run_id or
                    payload["sequence_no"] != seq or payload["cycle_id"] != cycle_id or
                    payload["session_date"] != session):
                raise ArchiveIntegrityError("stored row scope/provenance mismatch")
            raw_lines.append(raw)
            dates.add(session)
            candidates += len(payload["candidates"])
            rejected += sum(x["decision"] == "REJECTED" for x in payload["candidates"])
            unmeasurable += sum(x["decision"] == "UNMEASURABLE" for x in payload["candidates"])
            market_partial += payload["market_source"]["data_status"] != "COMPLETE"
            expected += 1
            prior_chain = chain_hash
        if not raw_lines:
            raise ArchiveIntegrityError("archive batch has no fully verified rows")
        raw_jsonl = b"\n".join(raw_lines) + b"\n"
        packed = gzip.compress(raw_jsonl, compresslevel=6, mtime=0)
        raw_hash = sha256(raw_jsonl).hexdigest()
        packed_hash = sha256(packed).hexdigest()
        last = expected - 1
        data_key = _key(workspace_id, run_id, first, last, raw_hash)
        manifest_key = data_key + ".manifest.json"
        manifest = {
            "schema_version": ARCHIVE_SCHEMA, "tracking_id": TRACKING_ID,
            "workspace_id": workspace_id, "run_id": run_id,
            "first_sequence": first, "last_sequence": last,
            "event_count": len(raw_lines), "candidate_count": candidates,
            "rejected_count": rejected, "unmeasurable_count": unmeasurable,
            "market_incomplete_count": market_partial,
            "session_dates": sorted(dates),
            "previous_chain_sha256": (ZERO_SHA if first == 1 else
                                      conn.execute(
                                          "SELECT chain_sha256 FROM decision_journal "
                                          "WHERE workspace_id=? AND run_id=? AND sequence_no=?",
                                          (workspace_id, run_id, first - 1),
                                      ).fetchone()[0]),
            "last_chain_sha256": prior_chain,
            "raw_sha256": raw_hash, "raw_bytes": len(raw_jsonl),
            "compressed_sha256": packed_hash, "compressed_bytes": len(packed),
            "data_key": data_key, "durability": "LOCAL_ONLY",
            "offhost_verified": False, "market_objects_verified": False,
            "upstream_attested": False, "alpha_validated": False,
            "broker_write_authority": False,
        }
        canonical_manifest = _canonical(manifest)

        # Publish immutable objects and verify content before any WAL ACK.
        self.store.put_once(data_key, packed)
        if self.store.get(data_key) != packed:
            raise ArchiveIntegrityError("compressed archive postwrite verification failed")
        self.store.put_once(manifest_key, canonical_manifest)
        if self.store.get(manifest_key) != canonical_manifest:
            raise ArchiveIntegrityError("archive manifest postwrite verification failed")

        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT sequence_no,archive_state FROM decision_journal "
                "WHERE workspace_id=? AND run_id=? AND sequence_no BETWEEN ? AND ? "
                "ORDER BY sequence_no",
                (workspace_id, run_id, first, last),
            ).fetchall()
            if len(rows) != len(raw_lines) or any(
                seq != first + index or state != "PENDING_OFFHOST_ARCHIVE"
                for index, (seq, state) in enumerate(rows)
            ):
                raise ArchiveIntegrityError("WAL rows changed before local archive ACK")
            conn.execute(
                "INSERT INTO vault_local_batches VALUES (?,?,?,?,?,?,?)",
                (workspace_id, run_id, first, last, prior_chain,
                 manifest_key, sha256(canonical_manifest).hexdigest()),
            )
            update = conn.execute(
                "UPDATE decision_journal SET archive_state='LOCAL_ARCHIVE_VERIFIED' "
                "WHERE workspace_id=? AND run_id=? AND sequence_no BETWEEN ? AND ? "
                "AND archive_state='PENDING_OFFHOST_ARCHIVE'",
                (workspace_id, run_id, first, last),
            )
            if update.rowcount != len(raw_lines):
                raise ArchiveIntegrityError("archive ACK count mismatch")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "manifest_key": manifest_key, "first_sequence": first,
            "last_sequence": last, "archived_events": len(raw_lines),
            "ack_status": "LOCAL_VERIFIED_NOT_OFFHOST",
            "offhost_verified": False, "research_ready": False,
        }


def _bounded_gunzip(data: bytes, limit: int = MAX_BATCH_BYTES) -> bytes:
    try:
        reader = zlib.decompressobj(16 + zlib.MAX_WBITS)
        result = reader.decompress(data, limit + 1)
        if (len(result) > limit or not reader.eof or reader.unused_data or
                reader.unconsumed_tail):
            raise ArchiveIntegrityError("compressed archive truncated, chained, or too large")
        return result
    except zlib.error as exc:
        raise ArchiveIntegrityError("corrupt compressed archive") from exc


def restore_local_vault(
    store: ImmutableObjectStore, manifest_keys: Iterable[str], *,
    workspace_id: str, run_id: str, independent_expected_cycle_count: int | None = None,
) -> dict[str, Any]:
    """Reconstruct private decision envelopes from objects ALONE, not SQLite.

    Missing true upstream attestation, verified raw bars/quotes, or private R2
    keeps this return AWAITING_EVIDENCE. No profit or live authorization.
    """
    _scope(workspace_id, run_id)
    if independent_expected_cycle_count is not None and (
        isinstance(independent_expected_cycle_count, bool) or
        not isinstance(independent_expected_cycle_count, int) or
        independent_expected_cycle_count < 0
    ):
        raise EvidenceContractError("invalid independent source-cycle count")
    keys = list(manifest_keys)
    if not 1 <= len(keys) <= MAX_RESTORE_BATCHES or len(set(keys)) != len(keys):
        raise ArchiveIntegrityError("missing, duplicate, or unbounded archive manifests")
    expected = 1
    previous_chain = ZERO_SHA
    cycles: list[dict[str, Any]] = []
    issues: set[str] = {"NO_OFFHOST_R2_BACKUP", "RAW_MARKET_REFERENCES_UNVERIFIED"}
    if independent_expected_cycle_count is None:
        issues.add("UPSTREAM_CYCLE_COUNT_NOT_ATTESTED")
    for manifest_key in keys:
        prefix = f"private/anevum-v5/{workspace_id}/{run_id}/"
        if not manifest_key.startswith(prefix) or not manifest_key.endswith(".jsonl.gz.manifest.json"):
            raise ArchiveIntegrityError("cross-workspace or noncanonical manifest key")
        manifest_bytes = store.get(manifest_key)
        if manifest_bytes is None:
            raise ArchiveIntegrityError("missing immutable archive manifest")
        try:
            manifest = json.loads(manifest_bytes)
        except (ValueError, TypeError) as exc:
            raise ArchiveIntegrityError("malformed archive manifest") from exc
        if not isinstance(manifest, dict) or _canonical(manifest) != manifest_bytes:
            raise ArchiveIntegrityError("noncanonical archive manifest")
        if (manifest.get("schema_version") != ARCHIVE_SCHEMA or
                manifest.get("tracking_id") != TRACKING_ID or
                manifest.get("workspace_id") != workspace_id or
                manifest.get("run_id") != run_id or
                manifest.get("first_sequence") != expected or
                manifest.get("event_count", 0) < 1 or
                manifest.get("event_count", 0) > MAX_BATCH_EVENTS or
                manifest.get("last_sequence") !=
                expected + manifest.get("event_count", 0) - 1 or
                manifest.get("data_key") + ".manifest.json" != manifest_key or
                manifest.get("durability") != "LOCAL_ONLY" or
                manifest.get("offhost_verified") is not False or
                manifest.get("previous_chain_sha256") != previous_chain):
            raise ArchiveIntegrityError("archive manifest scope/sequence/durability mismatch")
        packed = store.get(manifest["data_key"])
        if packed is None or len(packed) != manifest.get("compressed_bytes"):
            raise ArchiveIntegrityError("missing or incomplete compressed archive")
        if sha256(packed).hexdigest() != manifest.get("compressed_sha256"):
            raise ArchiveIntegrityError("compressed archive SHA mismatch")
        raw = _bounded_gunzip(packed)
        if len(raw) != manifest.get("raw_bytes") or sha256(raw).hexdigest() != manifest.get("raw_sha256"):
            raise ArchiveIntegrityError("decompressed evidence SHA/size mismatch")
        if not raw.endswith(b"\n"):
            raise ArchiveIntegrityError("archive JSONL missing final newline")
        lines = raw[:-1].split(b"\n")
        if len(lines) != manifest["event_count"]:
            raise ArchiveIntegrityError("archive candidate line count mismatch")
        candidates = rejected = unmeasurable = market_partial = 0
        dates: set[str] = set()
        for line in lines:
            try:
                payload = json.loads(line)
                canonical = validate_cycle(payload)
            except (ValueError, TypeError) as exc:
                raise ArchiveIntegrityError("archived cycle schema mismatch") from exc
            if canonical != line:
                raise ArchiveIntegrityError("archived payload differs from canonical bytes")
            if (payload["workspace_id"] != workspace_id or
                    payload["run_id"] != run_id or payload["sequence_no"] != expected):
                raise ArchiveIntegrityError("restored row scope or sequence mismatch")
            previous_chain = sha256(
                bytes.fromhex(previous_chain) + sha256(line).digest()
            ).hexdigest()
            dates.add(payload["session_date"])
            candidates += len(payload["candidates"])
            rejected += sum(x["decision"] == "REJECTED" for x in payload["candidates"])
            unmeasurable += sum(x["decision"] == "UNMEASURABLE" for x in payload["candidates"])
            market_partial += payload["market_source"]["data_status"] != "COMPLETE"
            cycles.append(payload)
            expected += 1
            if len(cycles) > MAX_RESTORE_EVENTS:
                raise ArchiveIntegrityError("restore exceeds bounded event limit")
        if (previous_chain != manifest.get("last_chain_sha256") or
                candidates != manifest.get("candidate_count") or
                rejected != manifest.get("rejected_count") or
                unmeasurable != manifest.get("unmeasurable_count") or
                market_partial != manifest.get("market_incomplete_count") or
                sorted(dates) != manifest.get("session_dates")):
            raise ArchiveIntegrityError("archive batch population/chain verification failed")
        if market_partial:
            issues.add("MARKET_REFERENCE_STATUS_INCOMPLETE")
    if independent_expected_cycle_count is not None and independent_expected_cycle_count != len(cycles):
        raise ArchiveIntegrityError("independent upstream cycle-count mismatch")
    raw_all = b"".join(_canonical(cycle) + b"\n" for cycle in cycles)
    return {
        "manifest": {
            "schema_version": "anevum.local-restore.v1",
            "tracking_id": TRACKING_ID,
            "workspace_id": workspace_id, "run_id": run_id,
            "cycles_restored": len(cycles),
            "candidates_restored": sum(len(c["candidates"]) for c in cycles),
            "last_sequence": expected - 1,
            "last_chain_sha256": previous_chain,
            "restored_jsonl_sha256": sha256(raw_all).hexdigest(),
            "journal_chain_verified": True,
            "local_object_restore_verified": True,
            "upstream_cycle_count_attested": independent_expected_cycle_count is not None,
            "raw_market_objects_verified": False,
            "offhost_archive_verified": False,
            "source_completeness": "AWAITING_OFFHOST_ARCHIVE_AND_MARKET_ATTESTATION",
            "evidence_state": "AWAITING_EVIDENCE",
            "quality_issues": sorted(issues),
            "research_ready": False,
            "alpha_validated": False,
            "broker_write_authority": False,
        },
        "private_cycles": cycles,
    }
