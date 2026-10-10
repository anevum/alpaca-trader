"""ANEVUM V5 F3a: separate scanner-origin source ledger and local audit.

This is a PAPER_RESEARCH_ONLY development contract, not a production scanner.
The scanner must persist its manifest BEFORE the decision journal write. There
is intentionally no `manifest_from_journal()` helper: a circular proof made
from journal events would not establish upstream completeness.

Local scanner and journal matching is not independent external source proof,
market-bar/quote recovery, off-host archiving, or live trading authorization.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

SOURCE_SCHEMA = "anevum.scanner-source.v1"
TRACKING_ID = "ANEVUM.V5.FOUNDATION.2026-10-09.001"
MAX_SCANS_PER_SESSION = 3000
MAX_ROWS_PER_RUN = 100000
MAX_MANIFEST_BYTES = 1500000
MAX_REPORTED_ISSUES = 32
ZERO_HASH = "0" * 64
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORKSPACE_PATTERN = re.compile(r"^wrk_[A-Za-z0-9_-]{8,64}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.]{0,14}$")

FIELDS = {
    "schema_version", "workspace_id", "run_id", "cycle_id", "sequence_no",
    "session_date", "occurred_at", "scan_origin", "strategy_version",
    "config_sha256", "code_sha256", "market_source_sha256",
    "universe_symbols", "evaluations",
}
EVALUATION_FIELDS = {
    "symbol", "decision", "reason", "observed_at", "features_sha256",
}
DECISIONS = {"QUALIFIED", "REJECTED", "UNMEASURABLE"}
SCAN_ORIGINS = {"SYNTHETIC_FIXTURE", "PRE_JOURNAL_SCANNER"}


class SourceContractError(ValueError):
    """Invalid or unscoped scanner-source observation."""


class SourceIntegrityError(RuntimeError):
    """Missing, changed, contradictory or corrupted scanner-source records."""


def canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as exc:
        raise SourceContractError("unable to canonicalize source") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise SourceContractError("scanner manifest exceeds bounded size")
    return raw


def digest(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceContractError(f"{label} requires a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceContractError(f"{label} must be ISO-8601") from exc
    if result.tzinfo is None:
        raise SourceContractError(f"{label} requires a timezone")
    return result.astimezone(timezone.utc)


def _validate_scope(workspace_id: str, run_id: str, session_date: str) -> None:
    if not isinstance(workspace_id, str) or not WORKSPACE_PATTERN.fullmatch(workspace_id):
        raise SourceContractError("invalid scanner workspace")
    if not isinstance(run_id, str) or not ID_PATTERN.fullmatch(run_id):
        raise SourceContractError("invalid scanner run")
    if not isinstance(session_date, str):
        raise SourceContractError("invalid scanner session")
    try:
        if date.fromisoformat(session_date).isoformat() != session_date:
            raise ValueError("noncanonical date")
    except ValueError as exc:
        raise SourceContractError("invalid scanner session") from exc


def validate_scan(manifest: Mapping[str, Any]) -> bytes:
    if not isinstance(manifest, dict) or set(manifest) != FIELDS:
        raise SourceContractError("scanner manifest contract mismatch")
    if manifest["schema_version"] != SOURCE_SCHEMA:
        raise SourceContractError("unsupported scanner manifest schema")
    _validate_scope(manifest["workspace_id"], manifest["run_id"],
                    manifest["session_date"])
    if not isinstance(manifest["cycle_id"], str) or not ID_PATTERN.fullmatch(manifest["cycle_id"]):
        raise SourceContractError("invalid scanner cycle identity")
    sequence = manifest["sequence_no"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise SourceContractError("invalid scanner sequence")
    if not isinstance(manifest["scan_origin"], str) or manifest["scan_origin"] not in SCAN_ORIGINS:
        raise SourceContractError("unknown scanner origin")
    occurred = _timestamp(manifest["occurred_at"], "scanner occurred_at")
    if occurred.astimezone(ZoneInfo("America/New_York")).date().isoformat() != manifest["session_date"]:
        raise SourceContractError("scanner occurred_at is outside declared ET session")
    if not isinstance(manifest["strategy_version"], str) or not 1 <= len(manifest["strategy_version"]) <= 128:
        raise SourceContractError("invalid scanner strategy version")
    for key in ("config_sha256", "code_sha256", "market_source_sha256"):
        if not isinstance(manifest[key], str) or not SHA_PATTERN.fullmatch(manifest[key]):
            raise SourceContractError("invalid scanner provenance digest")
    symbols, evaluations = manifest["universe_symbols"], manifest["evaluations"]
    if not isinstance(symbols, list) or not isinstance(evaluations, list):
        raise SourceContractError("scanner must provide full symbol/evaluation arrays")
    if len(symbols) > 5000 or len(symbols) != len(evaluations):
        raise SourceContractError("scanner evaluations must equal source universe")
    if any(not isinstance(s, str) or not SYMBOL_PATTERN.fullmatch(s) for s in symbols):
        raise SourceContractError("invalid scanner symbol")
    if len(set(symbols)) != len(symbols):
        raise SourceContractError("duplicate scanner universe symbol")
    seen: set[str] = set()
    for evaluation in evaluations:
        if not isinstance(evaluation, dict) or set(evaluation) != EVALUATION_FIELDS:
            raise SourceContractError("incomplete scanner disposition")
        symbol = evaluation["symbol"]
        if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol) or symbol in seen:
            raise SourceContractError("duplicate or invalid scanner disposition symbol")
        seen.add(symbol)
        if not isinstance(evaluation["decision"], str) or evaluation["decision"] not in DECISIONS:
            raise SourceContractError("invalid scanner candidate disposition")
        if not isinstance(evaluation["reason"], str) or not 1 <= len(evaluation["reason"]) <= 512:
            raise SourceContractError("missing scanner disposition reason")
        if _timestamp(evaluation["observed_at"], "scanner candidate observed_at") > occurred:
            raise SourceContractError("scanner candidate observed in the future")
        value = evaluation["features_sha256"]
        if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
            raise SourceContractError("missing scanner feature provenance")
    if seen != set(symbols):
        raise SourceContractError("scanner omitted a universe disposition")
    return canonical(manifest)


class SourceScanLedger:
    """Independent SQLite/WAL record of scanner-origin manifests.

    Must be a distinct DB file from the decision journal and fed upstream,
    not by re-exporting/deriving recorded decision journal rows.
    """

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS source_scans (
            workspace_id TEXT NOT NULL, run_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL, cycle_id TEXT NOT NULL,
            session_date TEXT NOT NULL, manifest_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL, chain_sha256 TEXT NOT NULL,
            PRIMARY KEY(workspace_id, run_id, sequence_no),
            UNIQUE(workspace_id, run_id, cycle_id)
        )""")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SourceScanLedger":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def record_scan(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        raw = validate_scan(manifest)
        manifest_sha = sha256(raw).hexdigest()
        workspace, run, sequence, cycle_id = (
            manifest["workspace_id"], manifest["run_id"],
            manifest["sequence_no"], manifest["cycle_id"],
        )
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            duplicates = self.conn.execute(
                "SELECT sequence_no,cycle_id,manifest_sha256,chain_sha256 FROM source_scans "
                "WHERE workspace_id=? AND run_id=? AND (sequence_no=? OR cycle_id=?)",
                (workspace, run, sequence, cycle_id),
            ).fetchall()
            if duplicates:
                if (len(duplicates) == 1 and duplicates[0][0] == sequence and
                        duplicates[0][1] == cycle_id and duplicates[0][2] == manifest_sha):
                    self.conn.rollback()
                    return {"status": "ALREADY_RECORDED", "manifest_sha256": manifest_sha,
                            "chain_sha256": duplicates[0][3]}
                raise SourceIntegrityError("scanner cycle conflicts with immutable source")
            prev = self.conn.execute(
                "SELECT sequence_no,chain_sha256 FROM source_scans "
                "WHERE workspace_id=? AND run_id=? ORDER BY sequence_no DESC LIMIT 1",
                (workspace, run),
            ).fetchone()
            expected = 1 if prev is None else prev[0] + 1
            if sequence != expected:
                raise SourceIntegrityError("scanner sequence gap or rewind")
            chain = sha256(bytes.fromhex(ZERO_HASH if prev is None else prev[1]) +
                           bytes.fromhex(manifest_sha)).hexdigest()
            self.conn.execute(
                "INSERT INTO source_scans VALUES (?,?,?,?,?,?,?,?)",
                (workspace, run, sequence, cycle_id, manifest["session_date"],
                 raw.decode("utf-8"), manifest_sha, chain),
            )
            self.conn.commit()
            return {"status": "SCANNER_RECORDED", "manifest_sha256": manifest_sha,
                    "chain_sha256": chain}
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def read_session_verified(self, *, workspace_id: str, run_id: str,
                              session_date: str) -> dict[str, Any]:
        _validate_scope(workspace_id, run_id, session_date)
        cursor = self.conn.execute(
            "SELECT sequence_no,cycle_id,session_date,manifest_json,manifest_sha256,chain_sha256 "
            "FROM source_scans WHERE workspace_id=? AND run_id=? ORDER BY sequence_no",
            (workspace_id, run_id),
        )
        previous, expected = ZERO_HASH, 1
        count, session_scans = 0, []
        raw_session_lines: list[str] = []
        for sequence, cycle_id, session, source_json, payload_sha, chain_sha in cursor:
            count += 1
            if count > MAX_ROWS_PER_RUN:
                raise SourceIntegrityError("scanner run exceeds verification bounds")
            if sequence != expected:
                raise SourceIntegrityError("scanner run contains a missing sequence")
            if (not isinstance(payload_sha, str) or not SHA_PATTERN.fullmatch(payload_sha)
                    or not isinstance(chain_sha, str) or not SHA_PATTERN.fullmatch(chain_sha)):
                raise SourceIntegrityError("scanner source digest malformed")
            if sha256(source_json.encode("utf-8")).hexdigest() != payload_sha:
                raise SourceIntegrityError("scanner source payload checksum changed")
            if sha256(bytes.fromhex(previous) + bytes.fromhex(payload_sha)).hexdigest() != chain_sha:
                raise SourceIntegrityError("scanner source chain changed")
            try:
                event = json.loads(source_json)
                if validate_scan(event) != source_json.encode("utf-8"):
                    raise SourceIntegrityError("scanner source JSON is not canonical")
            except (SourceContractError, ValueError, TypeError) as exc:
                raise SourceIntegrityError("scanner stored event is invalid") from exc
            if (event["workspace_id"] != workspace_id or event["run_id"] != run_id or
                    event["cycle_id"] != cycle_id or event["sequence_no"] != sequence or
                    event["session_date"] != session):
                raise SourceIntegrityError("scanner source scope mismatches index")
            if session == session_date:
                if len(session_scans) >= MAX_SCANS_PER_SESSION:
                    raise SourceIntegrityError("scanner session exceeds explicit verification bounds")
                session_scans.append(event)
                raw_session_lines.append(source_json)
            previous, expected = chain_sha, expected + 1
        return {
            "scans": session_scans,
            "source_run_chain_sha256": previous,
            "source_session_jsonl_sha256": sha256(("\n".join(raw_session_lines) + "\n").encode()).hexdigest(),
            "scanned_run_records": count,
        }


class DecisionSessionReader(Protocol):
    path: Path
    def export_session(self, *, workspace_id: str, run_id: str,
                       session_date: str) -> Mapping[str, Any]: ...


def _issue_summary(issues: list[str]) -> dict[str, Any]:
    unique = sorted(set(issues))
    return {"issue_codes": unique[:MAX_REPORTED_ISSUES],
            "issue_count": len(unique),
            "issues_truncated": len(unique) > MAX_REPORTED_ISSUES}


def verify_scan_population(
    source: SourceScanLedger, journal: DecisionSessionReader, *,
    workspace_id: str, run_id: str, session_date: str,
    scheduler_expected_cycles: int | None = None,
    trusted_source_chain_sha256: str | None = None,
) -> dict[str, Any]:
    """Reconcile all source scanner cycles versus a verified journal export.

    Neither the source ledger itself nor caller-provided digests prove that a
    real scanner/scheduler emitted every scheduled cycle. F3a always returns
    AWAITING_EVIDENCE for matching local-only input, BLOCKED for violations.
    """
    _validate_scope(workspace_id, run_id, session_date)
    if scheduler_expected_cycles is not None and (isinstance(scheduler_expected_cycles, bool) or
            not isinstance(scheduler_expected_cycles, int) or scheduler_expected_cycles < 0):
        raise SourceContractError("invalid independent scheduler cycle count")
    if trusted_source_chain_sha256 is not None and (not isinstance(trusted_source_chain_sha256, str)
            or not SHA_PATTERN.fullmatch(trusted_source_chain_sha256)):
        raise SourceContractError("invalid independent source digest")
    if Path(source.path).resolve() == Path(journal.path).resolve():
        raise SourceContractError("scanner ledger must not share journal DB file")
    issues: list[str] = []
    source_scans: list[dict[str, Any]] = []
    source_chain: str | None = None
    journal_cycles: list[dict[str, Any]] = []
    journal_sha: str | None = None
    try:
        report = source.read_session_verified(
            workspace_id=workspace_id, run_id=run_id, session_date=session_date)
        source_scans = report["scans"]
        source_chain = report["source_run_chain_sha256"]
    except (SourceIntegrityError, SourceContractError, sqlite3.DatabaseError):
        issues.append("SCANNER_LEDGER_INTEGRITY_BLOCKED")
    try:
        exported = journal.export_session(
            workspace_id=workspace_id, run_id=run_id, session_date=session_date)
        manifest = exported["manifest"]
        journal_cycles = exported["private_cycles"]
        journal_sha = manifest["jsonl_sha256"]
        if (manifest.get("journal_chain_verified") is not True or
                manifest.get("cycles_exported") != len(journal_cycles)):
            issues.append("JOURNAL_CHAIN_OR_COUNT_NOT_VERIFIED")
    except (RuntimeError, ValueError, sqlite3.DatabaseError, KeyError, TypeError):
        issues.append("JOURNAL_EXPORT_INTEGRITY_BLOCKED")

    if not source_scans:
        issues.append("SCANNER_SOURCE_MISSING")
    if not journal_cycles:
        issues.append("JOURNAL_SESSION_MISSING")
    source_seq = {s["sequence_no"]: s for s in source_scans}
    journal_seq = {j["sequence_no"]: j for j in journal_cycles}
    for seq in sorted(source_seq.keys() - journal_seq.keys()):
        issues.append(f"JOURNAL_MISSING_SCANNER_CYCLE:{seq}")
    for seq in sorted(journal_seq.keys() - source_seq.keys()):
        issues.append(f"SCANNER_MISSING_JOURNAL_CYCLE:{seq}")
    compared_candidates = 0
    for seq in sorted(source_seq.keys() & journal_seq.keys()):
        scan, row = source_seq[seq], journal_seq[seq]
        expected_fields = (
            ("workspace_id", "SCOPE"), ("run_id", "RUN"),
            ("cycle_id", "CYCLE_ID"), ("sequence_no", "SEQUENCE"),
            ("session_date", "SESSION"), ("occurred_at", "OBSERVATION_TIME"),
            ("strategy_version", "STRATEGY"), ("config_sha256", "CONFIG"),
            ("code_sha256", "CODE"), ("universe_symbols", "UNIVERSE"),
        )
        for field, label in expected_fields:
            if scan.get(field) != row.get(field):
                issues.append(f"SOURCE_JOURNAL_{label}_MISMATCH:{seq}")
        if scan["market_source_sha256"] != digest(row["market_source"]):
            issues.append(f"MARKET_SOURCE_PROVENANCE_MISMATCH:{seq}")
        scans = scan["evaluations"]
        rows = row.get("candidates", [])
        if not isinstance(rows, list) or len(scans) != len(rows):
            issues.append(f"CANDIDATE_COUNT_MISMATCH:{seq}")
            continue
        compared_candidates += len(scans)
        for scanned, candidate in zip(scans, rows):
            if not isinstance(candidate, dict):
                issues.append(f"CANDIDATE_RECORD_INVALID:{seq}")
                continue
            if scanned["symbol"] != candidate.get("symbol"):
                issues.append(f"CANDIDATE_ORDER_OR_SYMBOL_MISMATCH:{seq}")
            for field in ("decision", "reason", "observed_at"):
                if scanned[field] != candidate.get(field):
                    issues.append(f"CANDIDATE_{field.upper()}_MISMATCH:{seq}")
            try:
                features_sha = digest(candidate.get("features"))
            except (SourceContractError, ValueError, TypeError):
                features_sha = None
            if scanned["features_sha256"] != features_sha:
                issues.append(f"CANDIDATE_FEATURES_MISMATCH:{seq}")
    if scheduler_expected_cycles is None:
        issues.append("SCHEDULER_CYCLE_COUNT_NOT_ATTESTED")
    elif scheduler_expected_cycles != len(source_scans) or scheduler_expected_cycles != len(journal_cycles):
        issues.append("INDEPENDENT_SCHEDULER_COUNT_MISMATCH")
    if trusted_source_chain_sha256 is None:
        issues.append("EXTERNAL_SCANNER_DIGEST_NOT_PINNED")
    elif source_chain != trusted_source_chain_sha256:
        issues.append("EXTERNAL_SCANNER_CHAIN_DIGEST_MISMATCH")
    if any(s["scan_origin"] == "SYNTHETIC_FIXTURE" for s in source_scans):
        issues.append("SYNTHETIC_SCANNER_SOURCE_NOT_MARKET_EVIDENCE")
    issues.append("UPSTREAM_SCANNER_INTEGRATION_NOT_ATTESTED")
    issues.append("RAW_MARKET_OBJECTS_NOT_VERIFIED")
    issues.append("R2_ARCHIVE_COMPLETENESS_NOT_VERIFIED")
    blocking = any(x.startswith((
        "SCANNER_LEDGER_", "JOURNAL_", "SCANNER_SOURCE_MISSING",
        "SCANNER_MISSING", "SOURCE_JOURNAL_", "MARKET_SOURCE_PROVENANCE_",
        "CANDIDATE_", "INDEPENDENT_SCHEDULER_COUNT_MISMATCH",
        "EXTERNAL_SCANNER_CHAIN_DIGEST_MISMATCH",
    )) for x in issues)
    state = "BLOCKED" if blocking else "AWAITING_EVIDENCE"
    manifest = {
        "schema_version": "anevum.scan-reconciliation.v1",
        "tracking_id": TRACKING_ID,
        "scope": {"workspace_id": workspace_id, "run_id": run_id,
                  "session_date": session_date},
        "scanner_cycles": len(source_scans),
        "journal_cycles": len(journal_cycles),
        "candidate_count_compared": compared_candidates,
        "source_only_cycles": len(source_seq.keys() - journal_seq.keys()),
        "journal_only_cycles": len(journal_seq.keys() - source_seq.keys()),
        "source_ledger_chain_sha256": source_chain,
        "journal_session_sha256": journal_sha,
        "scheduler_expected_cycles": scheduler_expected_cycles,
        "source_digest_externally_provided": trusted_source_chain_sha256 is not None,
        "scanner_journal_population_agrees": not blocking and bool(source_scans),
        "upstream_origin_independently_attested": False,
        "raw_market_objects_verified": False,
        "offhost_archive_verified": False,
        "full_population_across_upstream_proven": False,
        "research_ready": False,
        "alpha_validated": False,
        "broker_write_authority": False,
        "evidence_state": state,
        **_issue_summary(issues),
    }
    manifest["reconciliation_sha256"] = digest(manifest)
    return manifest
