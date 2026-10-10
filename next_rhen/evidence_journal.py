"""ANEVUM V5 F1: append-only local evidence journal for PAPER research only.

A self-contained successor package. No import from the legacy app, broker,
Railway, Cloudflare, HTTP, trading, or public website code. A successful
append never represents proof that an entire upstream scan was captured or
that an off-host archive exists. Those are separate F2 gates.
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
from typing import Any, Mapping
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "anevum.decision-cycle.v1"
TRACKING_ID = "ANEVUM.V5.FOUNDATION.2026-10-09.001"
_MAX_EVENT_BYTES = 4_000_000
_MAX_CYCLES_PER_EXPORT = 3000
_MAX_RUN_ROWS_VERIFIED = 100_000
_ZERO_SHA = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE = re.compile(r"^wrk_[A-Za-z0-9_-]{8,64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,14}$")
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_EVENT_KEYS = {
    "schema_version", "workspace_id", "run_id", "cycle_id", "sequence_no",
    "session_date", "occurred_at", "execution_mode", "strategy_version",
    "config_sha256", "code_sha256", "market_source", "universe_symbols",
    "candidates",
}
_SOURCE_KEYS = {
    "feed", "data_status", "asof_timestamp", "universe_ref",
    "bars_ref", "quotes_ref",
}
_CANDIDATE_KEYS = {
    "symbol", "decision", "reason", "observed_at", "features",
}
_DECISIONS = {"QUALIFIED", "REJECTED", "UNMEASURABLE"}
_REJECTED_FEATURE_KEYS = (
    "token", "secret", "password", "authorization", "api_key", "credential",
    "broker_account", "access_key",
)


class EvidenceContractError(ValueError):
    """Source observation is invalid or incomplete within its recorded cycle."""


class EvidenceIntegrityError(RuntimeError):
    """A duplicate, gap, conflicting overwrite, or corrupted journal is found."""


def _asof(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceContractError(f"{label} must have an explicit timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceContractError(f"{label} has invalid ISO timestamp") from exc
    if result.tzinfo is None:
        raise EvidenceContractError(f"{label} must have an explicit timezone")
    return result.astimezone(timezone.utc)


def _sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise EvidenceContractError(f"{label} must be a lowercase SHA256 hex digest")


def _id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise EvidenceContractError(f"{label} is missing or not a safe stable ID")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        data = json.dumps(
            value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError("event cannot be canonicalized") from exc
    if len(data) > _MAX_EVENT_BYTES:
        raise EvidenceContractError("event exceeds bounded journal envelope")
    return data


def validate_cycle(cycle: Mapping[str, Any]) -> bytes:
    """Validate one complete observed cycle; return exactly persisted bytes."""
    if not isinstance(cycle, dict) or set(cycle) != _EVENT_KEYS:
        raise EvidenceContractError("cycle envelope fields do not match v1")
    if cycle["schema_version"] != SCHEMA_VERSION:
        raise EvidenceContractError("unknown decision-cycle schema version")
    if not isinstance(cycle["workspace_id"], str) or not _WORKSPACE.fullmatch(cycle["workspace_id"]):
        raise EvidenceContractError("invalid workspace ID")
    _id(cycle["run_id"], "run_id")
    _id(cycle["cycle_id"], "cycle_id")
    sequence = cycle["sequence_no"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise EvidenceContractError("sequence_no must be a positive integer")
    if cycle["execution_mode"] != "PAPER_RESEARCH_ONLY":
        raise EvidenceContractError("this journal forbids live execution")
    if not isinstance(cycle["strategy_version"], str) or not 1 <= len(cycle["strategy_version"]) <= 128:
        raise EvidenceContractError("missing strategy version")
    _sha(cycle["config_sha256"], "config_sha256")
    _sha(cycle["code_sha256"], "code_sha256")

    occurred = _asof(cycle["occurred_at"], "occurred_at")
    try:
        session = date.fromisoformat(cycle["session_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError("invalid session_date") from exc
    if session.isoformat() != cycle["session_date"]:
        raise EvidenceContractError("session_date must be YYYY-MM-DD")
    if occurred.astimezone(ZoneInfo("America/New_York")).date() != session:
        raise EvidenceContractError("session_date does not match New York observation")

    market = cycle["market_source"]
    if not isinstance(market, dict) or not {"feed", "data_status", "asof_timestamp", "universe_ref"} <= set(market):
        raise EvidenceContractError("market source identity is missing")
    if not set(market) <= _SOURCE_KEYS:
        raise EvidenceContractError("unrecognized market source fields")
    if not isinstance(market["feed"], str) or not 1 <= len(market["feed"]) <= 64:
        raise EvidenceContractError("invalid market feed")
    if market["data_status"] not in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}:
        raise EvidenceContractError("invalid market data status")
    market_at = _asof(market["asof_timestamp"], "market.asof_timestamp")
    if market_at > occurred:
        raise EvidenceContractError("market data from the future")
    for name in ("universe_ref", "bars_ref", "quotes_ref"):
        if name in market:
            _sha(market[name], name)
    if market["data_status"] == "COMPLETE" and not {"bars_ref", "quotes_ref"} <= set(market):
        raise EvidenceContractError("complete market state must identify raw bars and quotes")

    universe, candidates = cycle["universe_symbols"], cycle["candidates"]
    if not isinstance(universe, list) or not isinstance(candidates, list):
        raise EvidenceContractError("universe and full candidate population must be arrays")
    if len(universe) > 5000 or len(candidates) != len(universe):
        raise EvidenceContractError("full candidate count must match universe size")
    if any(not isinstance(sym, str) or not _SYMBOL.fullmatch(sym) for sym in universe):
        raise EvidenceContractError("invalid universe symbol")
    if len(set(universe)) != len(universe):
        raise EvidenceContractError("duplicate universe symbols")
    observed_symbols = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_KEYS:
            raise EvidenceContractError("candidate evidence fields incomplete")
        symbol = candidate["symbol"]
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) or symbol in observed_symbols:
            raise EvidenceContractError("invalid or duplicate candidate symbol")
        observed_symbols.add(symbol)
        if candidate["decision"] not in _DECISIONS:
            raise EvidenceContractError("candidate requires explicit disposition")
        if not isinstance(candidate["reason"], str) or not 1 <= len(candidate["reason"]) <= 512:
            raise EvidenceContractError("candidate rejection/qualification reason missing")
        if _asof(candidate["observed_at"], "candidate.observed_at") > occurred:
            raise EvidenceContractError("candidate timestamp from the future")
        features = candidate["features"]
        if not isinstance(features, dict) or len(features) > 150:
            raise EvidenceContractError("candidate feature vector malformed")
        for key, val in features.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise EvidenceContractError("feature name malformed")
            if any(word in key.lower() for word in _REJECTED_FEATURE_KEYS):
                raise EvidenceContractError("credential-like feature name rejected")
            if val is None or isinstance(val, bool):
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if not math.isfinite(val):
                    raise EvidenceContractError("nonfinite feature evidence")
            elif isinstance(val, str) and len(val) <= 1024:
                continue
            else:
                raise EvidenceContractError("unsupported feature value")
    if observed_symbols != set(universe):
        raise EvidenceContractError("candidate population omits or adds a universe symbol")
    return _json_bytes(cycle)


class DecisionJournal:
    """Account-scoped single-writer SQLite WAL; immutable committed source rows.

    Protected against torn inserts, mutation and silent sequence gaps.
    This is *not* a trading broker, R2 archive, or completeness authority.
    """

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        self.conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS decision_journal(
                workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                cycle_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                chain_sha256 TEXT NOT NULL,
                archive_state TEXT NOT NULL DEFAULT 'PENDING_OFFHOST_ARCHIVE',
                PRIMARY KEY(workspace_id, run_id, sequence_no),
                UNIQUE(workspace_id, run_id, cycle_id)
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS decision_session ON "
            "decision_journal(workspace_id,run_id,session_date,sequence_no)"
        )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DecisionJournal":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def append_cycle(self, cycle: Mapping[str, Any]) -> dict[str, Any]:
        raw = validate_cycle(cycle)
        payload_sha = sha256(raw).hexdigest()
        workspace, run, number, cycle_id = (
            cycle["workspace_id"], cycle["run_id"],
            cycle["sequence_no"], cycle["cycle_id"],
        )
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            duplicates = self.conn.execute(
                "SELECT sequence_no,cycle_id,payload_sha256,chain_sha256 FROM decision_journal "
                "WHERE workspace_id=? AND run_id=? AND (sequence_no=? OR cycle_id=?)",
                (workspace, run, number, cycle_id),
            ).fetchall()
            if duplicates:
                if (
                    len(duplicates) == 1
                    and duplicates[0][0] == number
                    and duplicates[0][1] == cycle_id
                    and duplicates[0][2] == payload_sha
                ):
                    self.conn.rollback()
                    return {
                        "status": "ALREADY_RECORDED", "payload_sha256": payload_sha,
                        "chain_sha256": duplicates[0][3], "sequence_no": number,
                        "archive_state": "PENDING_OFFHOST_ARCHIVE",
                    }
                raise EvidenceIntegrityError("conflicting cycle ID or sequence; overwrite forbidden")
            previous = self.conn.execute(
                "SELECT sequence_no,chain_sha256 FROM decision_journal "
                "WHERE workspace_id=? AND run_id=? ORDER BY sequence_no DESC LIMIT 1",
                (workspace, run),
            ).fetchone()
            expected = 1 if previous is None else previous[0] + 1
            if number != expected:
                raise EvidenceIntegrityError(f"sequence gap or rewind: expected {expected}, found {number}")
            prev_hash = _ZERO_SHA if previous is None else previous[1]
            chain = sha256(bytes.fromhex(prev_hash) + bytes.fromhex(payload_sha)).hexdigest()
            self.conn.execute(
                "INSERT INTO decision_journal (workspace_id,run_id,sequence_no,cycle_id,"
                "session_date,payload_json,payload_sha256,chain_sha256) VALUES (?,?,?,?,?,?,?,?)",
                (workspace, run, number, cycle_id, cycle["session_date"], raw.decode(), payload_sha, chain),
            )
            self.conn.commit()
            return {
                "status": "RECORDED", "payload_sha256": payload_sha,
                "chain_sha256": chain, "sequence_no": number,
                "archive_state": "PENDING_OFFHOST_ARCHIVE",
            }
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def export_session(
        self, *, workspace_id: str, run_id: str, session_date: str,
        independent_expected_cycle_count: int | None = None,
    ) -> dict[str, Any]:
        """Validate the whole run's chain; return a *private* research export.

        Does NOT attest that unobserved upstream cycles exist, that market refs
        are uploaded, or that off-host R2 archival and restore have succeeded.
        """
        if not _WORKSPACE.fullmatch(workspace_id) or not _ID.fullmatch(run_id):
            raise EvidenceContractError("invalid scope")
        try:
            if date.fromisoformat(session_date).isoformat() != session_date:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise EvidenceContractError("invalid session") from exc
        if independent_expected_cycle_count is not None and (
            isinstance(independent_expected_cycle_count, bool)
            or not isinstance(independent_expected_cycle_count, int)
            or independent_expected_cycle_count < 0
        ):
            raise EvidenceContractError("invalid independent cycle count")
        cursor = self.conn.execute(
            "SELECT sequence_no,cycle_id,session_date,payload_json,payload_sha256,"
            "chain_sha256,archive_state FROM decision_journal "
            "WHERE workspace_id=? AND run_id=? ORDER BY sequence_no",
            (workspace_id, run_id),
        )
        previous = _ZERO_SHA
        expected_seq = 1
        checked = 0
        events = []
        raw_lines = []
        quality_issues = []
        for seq, cycle_id, stored_session, payload_json, payload_sha, chain_sha, archive_state in cursor:
            checked += 1
            if checked > _MAX_RUN_ROWS_VERIFIED:
                raise EvidenceIntegrityError("run verification exceeds bounded limit")
            if seq != expected_seq:
                raise EvidenceIntegrityError("run contains missing sequence")
            actual_payload_sha = sha256(payload_json.encode("utf-8")).hexdigest()
            if actual_payload_sha != payload_sha:
                raise EvidenceIntegrityError("stored payload hash mismatch")
            actual_chain = sha256(bytes.fromhex(previous) + bytes.fromhex(payload_sha)).hexdigest()
            if chain_sha != actual_chain:
                raise EvidenceIntegrityError("chain integrity failure")
            payload = json.loads(payload_json)
            if validate_cycle(payload) != payload_json.encode("utf-8"):
                raise EvidenceIntegrityError("stored payload is not canonical")
            if cycle_id != payload["cycle_id"] or stored_session != payload["session_date"]:
                raise EvidenceIntegrityError("record provenance mismatch")
            if stored_session == session_date:
                if len(events) >= _MAX_CYCLES_PER_EXPORT:
                    raise EvidenceIntegrityError("session exceeds bounded export; never silently truncate")
                events.append(payload)
                raw_lines.append(payload_json)
                if payload["market_source"]["data_status"] != "COMPLETE":
                    quality_issues.append(f"cycle:{seq}:MARKET_SOURCE_NOT_COMPLETE")
                if archive_state != "ARCHIVED_VERIFIED":
                    quality_issues.append(f"cycle:{seq}:OFFHOST_ARCHIVE_NOT_VERIFIED")
            expected_seq += 1
            previous = chain_sha

        if not events:
            raise EvidenceIntegrityError("no events in requested session")
        if independent_expected_cycle_count is None:
            quality_issues.append("INDEPENDENT_UPSTREAM_CYCLE_COUNT_MISSING")
        elif independent_expected_cycle_count != len(events):
            quality_issues.append("INDEPENDENT_UPSTREAM_CYCLE_COUNT_MISMATCH")
        digest = sha256(("\n".join(raw_lines) + "\n").encode("utf-8")).hexdigest()
        manifest = {
            "schema_version": "anevum.evidence-export.v1",
            "tracking_id": TRACKING_ID,
            "scope": {"workspace_id": workspace_id, "run_id": run_id, "session_date": session_date},
            "cycles_exported": len(events),
            "candidates_exported": sum(len(e["candidates"]) for e in events),
            "rejected_exported": sum(sum(c["decision"] == "REJECTED" for c in e["candidates"]) for e in events),
            "first_sequence": events[0]["sequence_no"],
            "last_sequence": events[-1]["sequence_no"],
            "journal_chain_verified": True,
            "jsonl_sha256": digest,
            "independent_cycle_count": independent_expected_cycle_count,
            "quality_issues": sorted(set(quality_issues)),
            "source_completeness": "AWAITING_OFFHOST_ARCHIVE_AND_UPSTREAM_ATTESTATION",
            "full_population_across_all_cycles_proven": False,
            "alpha_validated": False,
            "broker_write_authority": False,
        }
        return {"manifest": manifest, "private_cycles": events}
