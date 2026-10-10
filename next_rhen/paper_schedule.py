"""ANEVUM V5 F3b: isolated append-only paper scheduler intent ledger.

A schedule slot exists BEFORE observing the market or recording scanner decisions.
No broker, network, production runtime, order or cloud credentials are imported.
Even a locally complete schedule is NOT independent scheduler-origin proof.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .source_attestation import _validate_scope, ID_PATTERN, ZERO_HASH

SLOT_SCHEMA = "anevum.paper-schedule.v1"
SLOT_FIELDS = {
    "schema_version", "workspace_id", "run_id", "sequence_no", "cycle_id",
    "session_date", "expected_at", "execution_mode", "planner_origin",
}
ORIGINS = {"SYNTHETIC_PLAN", "INJECTED_SCHEDULER_UNVERIFIED"}
MAX_SLOTS_PER_RUN = 100_000
MAX_SLOTS_PER_SESSION = 3_000
HEX = re.compile(r"^[0-9a-f]{64}$")


class PaperScheduleError(ValueError):
    """Invalid paper schedule event."""


class PaperScheduleIntegrityError(RuntimeError):
    """A planned scan was overwritten, omitted, reordered or corrupted."""


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise PaperScheduleError("expected_at requires explicit timezone")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperScheduleError("expected_at invalid ISO timestamp") from exc
    if stamp.tzinfo is None:
        raise PaperScheduleError("expected_at requires explicit timezone")
    return stamp.astimezone(timezone.utc)


def validate_slot(slot: Mapping[str, Any]) -> bytes:
    if not isinstance(slot, dict) or set(slot) != SLOT_FIELDS:
        raise PaperScheduleError("schedule must contain exact slot fields")
    _validate_scope(slot["workspace_id"], slot["run_id"], slot["session_date"])
    if slot["schema_version"] != SLOT_SCHEMA:
        raise PaperScheduleError("unsupported schedule schema")
    if slot["execution_mode"] != "PAPER_RESEARCH_ONLY":
        raise PaperScheduleError("schedule cannot authorize trading")
    if not isinstance(slot["planner_origin"], str) or slot["planner_origin"] not in ORIGINS:
        raise PaperScheduleError("unknown planner origin")
    n = slot["sequence_no"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise PaperScheduleError("invalid scheduled sequence")
    if not isinstance(slot["cycle_id"], str) or not ID_PATTERN.fullmatch(slot["cycle_id"]):
        raise PaperScheduleError("invalid scheduled cycle ID")
    stamp = _utc(slot["expected_at"])
    if stamp.astimezone(ZoneInfo("America/New_York")).date() != date.fromisoformat(slot["session_date"]):
        raise PaperScheduleError("expected scan belongs to another session")
    raw = json.dumps(slot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 2048:
        raise PaperScheduleError("scheduled slot oversized")
    return raw


class PaperScheduleLedger:
    """Distinct SQLite WAL containing predeclared paper scan intents."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        self.conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS paper_schedule (
            workspace_id TEXT NOT NULL, run_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL, cycle_id TEXT NOT NULL,
            session_date TEXT NOT NULL, payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL, chain_sha256 TEXT NOT NULL,
            PRIMARY KEY(workspace_id,run_id,sequence_no),
            UNIQUE(workspace_id,run_id,cycle_id)
        )""")

    def __enter__(self) -> "PaperScheduleLedger":
        return self

    def __exit__(self, *_args: object) -> None:
        self.conn.close()

    def close(self) -> None:
        self.conn.close()

    def record_slot(self, slot: Mapping[str, Any]) -> dict[str, Any]:
        raw = validate_slot(slot)
        h = sha256(raw).hexdigest()
        workspace, run, seq, cid = (
            slot["workspace_id"], slot["run_id"],
            slot["sequence_no"], slot["cycle_id"],
        )
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            dup = self.conn.execute(
                "SELECT sequence_no,cycle_id,payload_sha256,chain_sha256 FROM paper_schedule "
                "WHERE workspace_id=? AND run_id=? AND (sequence_no=? OR cycle_id=?)",
                (workspace, run, seq, cid),
            ).fetchall()
            if dup:
                if len(dup) == 1 and dup[0][:3] == (seq, cid, h):
                    self.conn.rollback()
                    return {"status": "ALREADY_PLANNED", "payload_sha256": h,
                            "chain_sha256": dup[0][3]}
                raise PaperScheduleIntegrityError("conflicting scheduled slot overwrite")
            prev = self.conn.execute(
                "SELECT sequence_no,chain_sha256 FROM paper_schedule "
                "WHERE workspace_id=? AND run_id=? ORDER BY sequence_no DESC LIMIT 1",
                (workspace, run),
            ).fetchone()
            expected = 1 if prev is None else prev[0] + 1
            if seq != expected:
                raise PaperScheduleIntegrityError("scheduled sequence gap or rewind")
            chain = sha256(bytes.fromhex(ZERO_HASH if prev is None else prev[1])
                           + bytes.fromhex(h)).hexdigest()
            self.conn.execute(
                "INSERT INTO paper_schedule VALUES (?,?,?,?,?,?,?,?)",
                (workspace, run, seq, cid, slot["session_date"], raw.decode(), h, chain),
            )
            self.conn.commit()
            return {"status": "PLANNED", "payload_sha256": h,
                    "chain_sha256": chain}
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def read_session_verified(self, *, workspace_id: str, run_id: str,
                              session_date: str) -> dict[str, Any]:
        _validate_scope(workspace_id, run_id, session_date)
        cursor = self.conn.execute(
            "SELECT sequence_no,cycle_id,session_date,payload_json,payload_sha256,chain_sha256 "
            "FROM paper_schedule WHERE workspace_id=? AND run_id=? ORDER BY sequence_no",
            (workspace_id, run_id),
        )
        prev, expected = ZERO_HASH, 1
        slots: list[dict[str, Any]] = []
        rows = 0
        for seq, cid, session, raw, payload_sha, chain in cursor:
            rows += 1
            if rows > MAX_SLOTS_PER_RUN or seq != expected:
                raise PaperScheduleIntegrityError("scheduled sequence missing or exceeds bound")
            if (not isinstance(payload_sha, str) or not HEX.fullmatch(payload_sha) or
                    not isinstance(chain, str) or not HEX.fullmatch(chain)):
                raise PaperScheduleIntegrityError("malformed schedule digest")
            if (sha256(raw.encode()).hexdigest() != payload_sha or
                    sha256(bytes.fromhex(prev) + bytes.fromhex(payload_sha)).hexdigest() != chain):
                raise PaperScheduleIntegrityError("schedule hash chain tampered")
            try:
                slot = json.loads(raw)
                if validate_slot(slot) != raw.encode():
                    raise PaperScheduleIntegrityError("schedule not canonical")
            except (ValueError, TypeError) as exc:
                raise PaperScheduleIntegrityError("invalid stored scheduled event") from exc
            if (slot["workspace_id"] != workspace_id or slot["run_id"] != run_id
                    or slot["sequence_no"] != seq or slot["cycle_id"] != cid
                    or slot["session_date"] != session):
                raise PaperScheduleIntegrityError("indexed schedule scope conflict")
            if session == session_date:
                if len(slots) >= MAX_SLOTS_PER_SESSION:
                    raise PaperScheduleIntegrityError("session has too many planned slots")
                slots.append(slot)
            prev, expected = chain, seq + 1
        return {"slots": slots, "chain_sha256": prev, "run_slots": rows,
                "independent_scheduler_origin_proven": False}
