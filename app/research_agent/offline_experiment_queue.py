"""A small, broker-isolated durable queue for GRAEN offline experiments.

Separate from the production RHEN Core single-writer database. Proposals are
immutable, events append-only, and there is no bridge to live promotion.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping


STATES = frozenset({
    "PROPOSED",
    "AWAITING_EVIDENCE",
    "READY_FOR_OFFLINE_RUN",
    "RUNNING_OFFLINE",
    "REVIEW_REQUIRED",
    "REJECTED",
    "INCONCLUSIVE",
    "ELIGIBLE_FOR_HUMAN_VALIDATION",
})
TRANSITIONS = {
    "PROPOSED": frozenset({"AWAITING_EVIDENCE"}),
    "AWAITING_EVIDENCE": frozenset({"READY_FOR_OFFLINE_RUN", "INCONCLUSIVE"}),
    "READY_FOR_OFFLINE_RUN": frozenset({"RUNNING_OFFLINE", "INCONCLUSIVE"}),
    "RUNNING_OFFLINE": frozenset({"REVIEW_REQUIRED", "INCONCLUSIVE"}),
    "REVIEW_REQUIRED": frozenset({
        "REJECTED", "INCONCLUSIVE", "ELIGIBLE_FOR_HUMAN_VALIDATION",
    }),
    "REJECTED": frozenset(),
    "INCONCLUSIVE": frozenset(),
    "ELIGIBLE_FOR_HUMAN_VALIDATION": frozenset(),
}
EVIDENCE_READINESS_KEYS = frozenset({
    "data_frozen", "source_version_locked", "decision_time_features_verified",
})
FINAL_REVIEW_KEYS = frozenset({
    "holdout_evaluated", "execution_costs_stressed", "replay_limitations_reviewed",
})
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
SCHEMA = "rhen-offline-experiment-queue-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _hash(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError("an immutable 64-character SHA256 evidence fingerprint is required")
    return value.lower()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_spec(spec: Mapping[str, Any]) -> None:
    identifier = str(spec.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,79}", identifier):
        raise ValueError("experiment id must be a bounded stable identifier")
    for key in ("hypothesis", "control", "treatment", "required_data", "failure_stop"):
        if not spec.get(key):
            raise ValueError(f"experiment spec is missing {key}")
    if spec.get("authority") not in {"OFFLINE_RESEARCH_ONLY", "RESEARCH_ONLY"}:
        raise ValueError("offline experiment requires explicit research-only authority")
    for forbidden in (
        "live_change_authorized", "promotion_authorized",
        "execution_authority", "broker_write_authority",
        "automatic_promotion_authorized",
    ):
        if spec.get(forbidden) is not False and forbidden in spec:
            raise ValueError(f"experiment may not grant {forbidden}")
    if "live_change_authorized" not in spec or "promotion_authorized" not in spec:
        raise ValueError("live-change and promotion denials must be explicit")


class OfflineExperimentQueue:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    source_fingerprint TEXT NOT NULL,
                    spec_fingerprint TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS experiment_events (
                    event_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(id)
                )""")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS experiment_events_by_experiment "
                "ON experiment_events(experiment_id, occurred_at)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            return self._project(row) if row is not None else None

    @staticmethod
    def _project(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "state": row["state"],
            "source_fingerprint": row["source_fingerprint"],
            "spec_fingerprint": row["spec_fingerprint"],
            "spec": json.loads(row["spec_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "research_only": True,
            "execution_authority": False,
            "promotion_authorized": False,
        }

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("queue listing limit must be between 1 and 500")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC, id LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._project(row) for row in rows]

    def propose(
        self, spec: Mapping[str, Any], *, source_fingerprint: str
    ) -> dict[str, Any]:
        if not isinstance(spec, Mapping):
            raise ValueError("research experiment specification must be an object")
        _validate_spec(spec)
        source = _sha(source_fingerprint)
        fingerprint = _hash(spec)
        identifier = str(spec["id"])
        now = _timestamp()
        with self._connect() as db:
            current = db.execute(
                "SELECT * FROM experiments WHERE id=?", (identifier,)
            ).fetchone()
            if current is not None:
                if (
                    current["source_fingerprint"] != source
                    or current["spec_fingerprint"] != fingerprint
                ):
                    raise ValueError(
                        "experiment identity conflict: immutable source or spec changed"
                    )
                return self._project(current)
            db.execute(
                """INSERT INTO experiments
                   (id,source_fingerprint,spec_fingerprint,spec_json,state,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    identifier, source, fingerprint, _canonical(spec), "PROPOSED",
                    now, now,
                ),
            )
            event_id = _hash([SCHEMA, identifier, "PROPOSED", source])
            db.execute(
                """INSERT INTO experiment_events
                   (event_id,experiment_id,from_state,to_state,evidence_fingerprint,
                    evidence_json,occurred_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    event_id, identifier, None, "PROPOSED", source,
                    _canonical({"source": "immutable_proposal"}), now,
                ),
            )
        record = self.get(identifier)
        assert record is not None
        return record

    def transition(
        self,
        experiment_id: str,
        state: str,
        *,
        evidence_fingerprint: str,
        checks: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        fingerprint = _sha(evidence_fingerprint)
        if state not in STATES:
            raise ValueError("unrecognized research-only state")
        checks = dict(checks or {})
        if state == "READY_FOR_OFFLINE_RUN":
            if any(checks.get(key) is not True for key in EVIDENCE_READINESS_KEYS):
                raise ValueError("cannot start research without frozen point-in-time evidence")
        if state == "ELIGIBLE_FOR_HUMAN_VALIDATION":
            if any(checks.get(key) is not True for key in FINAL_REVIEW_KEYS):
                raise ValueError("cannot advance incomplete holdout/cost/parity review")
        now = _timestamp()
        with self._connect() as db:
            current = db.execute(
                "SELECT * FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if current is None:
                raise KeyError("experiment not found")
            old = current["state"]
            if state == old:
                return self._project(current)
            if state not in TRANSITIONS[old]:
                raise ValueError(f"protected offline research transition {old} -> {state}")
            event_id = _hash([SCHEMA, experiment_id, old, state, fingerprint])
            db.execute(
                """INSERT INTO experiment_events
                   (event_id,experiment_id,from_state,to_state,evidence_fingerprint,
                    evidence_json,occurred_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    event_id, experiment_id, old, state,
                    fingerprint, _canonical(checks), now,
                ),
            )
            db.execute(
                "UPDATE experiments SET state=?, updated_at=? WHERE id=? AND state=?",
                (state, now, experiment_id, old),
            )
        record = self.get(experiment_id)
        assert record is not None
        return record

    def history(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM experiment_events WHERE experiment_id=?
                   ORDER BY occurred_at, rowid""", (experiment_id,)
            ).fetchall()
            return [
                {
                    "event_id": row["event_id"],
                    "from_state": row["from_state"],
                    "to_state": row["to_state"],
                    "evidence_fingerprint": row["evidence_fingerprint"],
                    "evidence": json.loads(row["evidence_json"]),
                    "occurred_at": row["occurred_at"],
                }
                for row in rows
            ]
