from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")

CRITICAL_EVENT_TYPES = {
    "order_intent", "broker_order", "broker_fill", "order_update",
    "position_opened", "position_closed", "reconciliation",
    "runtime_error", "crypto_runtime_error", "strategy_promotion",
}


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _iso(value: Any = None) -> str:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(UTC)
    else:
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class RhenCoreStore:
    """Single-writer bounded RHEN Core state store."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = str(path or os.getenv("RHEN_CORE_DB_PATH", "/data/rhen-core.db"))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.warning_bytes = int(os.getenv("RHEN_CORE_STORAGE_WARNING_MB", "500")) * 1024 * 1024
        self.shed_bytes = int(os.getenv("RHEN_CORE_STORAGE_SHED_MB", "750")) * 1024 * 1024
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma synchronous=NORMAL")
        conn.execute("pragma foreign_keys=ON")
        conn.execute("pragma busy_timeout=15000")
        conn.execute("pragma wal_autocheckpoint=1000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                create table if not exists kv_state (
                    namespace text not null,
                    key text not null,
                    value_json text not null,
                    revision integer not null default 1,
                    updated_at text not null,
                    primary key(namespace,key)
                );

                create table if not exists events (
                    event_key text primary key,
                    event_type text not null,
                    occurred_at text not null,
                    run_id text,
                    strategy_version_id text,
                    symbol text,
                    correlation_id text,
                    source text,
                    payload_json text not null,
                    critical integer not null default 0,
                    created_at text not null
                );
                create index if not exists events_type_time on events(event_type, occurred_at desc);
                create index if not exists events_run_time on events(run_id, occurred_at desc);

                create table if not exists candidates (
                    candidate_key text primary key,
                    observed_at text not null,
                    cycle_key text,
                    run_id text,
                    strategy_version_id text,
                    symbol text not null,
                    market_lane text,
                    action text,
                    qualified integer not null default 0,
                    final_decision text,
                    reason text,
                    reference_price text,
                    stop_price text,
                    target_price text,
                    feature_json text not null default '{}',
                    created_at text not null
                );
                create index if not exists candidates_time on candidates(observed_at desc);
                create index if not exists candidates_symbol_time on candidates(symbol, observed_at desc);

                create table if not exists graen_problems (
                    problem_id text primary key,
                    problem_key text not null unique,
                    status text not null,
                    priority integer not null default 50,
                    domain text not null,
                    body_json text not null,
                    metadata_json text not null default '{}',
                    created_at text not null,
                    updated_at text not null,
                    started_at text,
                    completed_at text
                );
                create index if not exists graen_problem_queue on graen_problems(status, priority desc, created_at);

                create table if not exists graen_runs (
                    run_id text primary key,
                    problem_id text not null,
                    worker_id text,
                    runtime_version text,
                    methodology_version text,
                    status text not null,
                    source_commit text,
                    deployment_id text,
                    result_json text not null default '{}',
                    model_usage_json text not null default '{}',
                    started_at text not null,
                    completed_at text,
                    foreign key(problem_id) references graen_problems(problem_id)
                );
                create index if not exists graen_runs_problem on graen_runs(problem_id, started_at desc);

                create table if not exists graen_artifacts (
                    artifact_id text primary key,
                    artifact_key text not null unique,
                    problem_id text not null,
                    run_id text,
                    artifact_type text not null,
                    methodology_version text,
                    source_commit text,
                    content_hash text not null,
                    content_json text not null,
                    created_at text not null
                );
                create index if not exists graen_artifacts_problem on graen_artifacts(problem_id, created_at desc);
                create index if not exists graen_artifacts_type on graen_artifacts(artifact_type, created_at desc);

                create table if not exists scheduler_runs (
                    job_key text primary key,
                    status text not null,
                    payload_json text not null,
                    scheduled_at text,
                    started_at text,
                    completed_at text,
                    updated_at text not null
                );
                create index if not exists scheduler_runs_time on scheduler_runs(scheduled_at desc);

                create table if not exists research_runs (
                    run_key text primary key,
                    payload_json text not null,
                    created_at text not null
                );
                create table if not exists research_ledgers (
                    ledger_hash text primary key,
                    payload_json text not null,
                    created_at text not null
                );
                create table if not exists nostra_records (
                    record_id text primary key,
                    kind text not null,
                    observed_at text not null,
                    payload_json text not null
                );
                create index if not exists nostra_kind_time on nostra_records(kind, observed_at desc);
                """
            )

    def file_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def storage_state(self) -> dict[str, Any]:
        size = self.file_size_bytes()
        return {
            "bytes": size,
            "mb": round(size / 1024 / 1024, 3),
            "warning": size >= self.warning_bytes,
            "analytics_shedding": size >= self.shed_bytes,
            "warning_mb": self.warning_bytes // 1024 // 1024,
            "shed_mb": self.shed_bytes // 1024 // 1024,
        }

    @staticmethod
    def compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        features = candidate.get("features") or {}
        quality = features.get("market_quality") or {}
        compact_features = {
            k: features.get(k)
            for k in (
                "market", "momentum_pct", "vwap_edge_pct", "relative_volume_ratio",
                "trend_persistence", "quality_score", "current_close",
                "confirmation_passes", "regime_passes",
            )
            if features.get(k) is not None
        }
        if quality:
            compact_features["market_quality"] = {
                k: quality.get(k)
                for k in ("spread_pct", "bar_age_seconds", "quote_age_seconds")
                if quality.get(k) is not None
            }
        return {
            "candidate_key": candidate.get("candidate_id") or candidate.get("candidate_key"),
            "symbol": str(candidate.get("symbol") or "").upper(),
            "observed_at": candidate.get("observed_at"),
            "action": candidate.get("action"),
            "qualified": bool(candidate.get("qualified")),
            "final_decision": candidate.get("final_decision"),
            "reason": candidate.get("reason"),
            "reference_price": candidate.get("decision_reference_price"),
            "stop_price": candidate.get("stop_price"),
            "target_price": candidate.get("target_price"),
            "market_lane": candidate.get("market_lane"),
            "strategy_version_id": candidate.get("strategy_version_id"),
            "features": compact_features,
        }

    @staticmethod
    def compact_cycle(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        candidates = [x for x in payload.get("candidates") or [] if isinstance(x, dict)]
        qualified = [
            x for x in candidates
            if x.get("qualified") or str(x.get("final_decision") or "") != "rejected"
        ]
        rejected = sorted(
            [x for x in candidates if x not in qualified],
            key=lambda x: str(x.get("symbol") or ""),
        )[:3]
        sampled = qualified + rejected
        summary = {
            k: payload.get(k)
            for k in (
                "cycle_key", "cycle_started_at", "cycle_ended_at", "market_is_open",
                "active_universe_size", "symbols_evaluated", "execution_mode",
                "market_session", "data_source", "data_feed", "bar_interval",
                "methodology_version", "market_lane", "strategy_family", "model_version",
                "candidate_count", "qualified_count", "rejected_count", "cycle_outcome",
                "data_status", "degraded", "error", "cycle_duration_ms",
            )
            if payload.get(k) is not None
        }
        runtime = payload.get("runtime") or {}
        summary["runtime"] = {
            k: runtime.get(k)
            for k in ("runtime_instance_id", "deployment_id", "git_commit", "market")
            if runtime.get(k) is not None
        }
        return summary, sampled

    def ingest_events(self, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
        inserted = candidates_inserted = shed = 0
        storage = self.storage_state()
        now = _iso()
        with self._lock, self.connect() as conn:
            for event in events:
                event = dict(event)
                event_type = str(event.get("event_type") or "")
                critical = event_type in CRITICAL_EVENT_TYPES
                if storage["analytics_shedding"] and not critical:
                    shed += 1
                    continue
                payload = dict(event.get("payload") or {})
                candidate_rows: list[dict[str, Any]] = []
                if event_type == "decision_cycle":
                    payload, candidate_rows = self.compact_cycle(payload)
                elif event_type == "position_metrics":
                    stamp = datetime.fromisoformat(_iso(event.get("occurred_at")))
                    minute = stamp.minute - (stamp.minute % 5)
                    bucket = stamp.replace(
                        minute=minute, second=0, microsecond=0
                    ).isoformat()
                    event["event_key"] = (
                        f"{event.get('run_id')}:position-metrics:"
                        f"{str(event.get('symbol') or '').upper()}:{bucket}"
                    )
                key = str(event.get("event_key") or "").strip()
                if not key:
                    continue
                conn.execute(
                    """
                    insert or replace into events(
                        event_key,event_type,occurred_at,run_id,strategy_version_id,
                        symbol,correlation_id,source,payload_json,critical,created_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        key, event_type, _iso(event.get("occurred_at")),
                        event.get("run_id"), event.get("strategy_version_id"),
                        event.get("symbol"), event.get("correlation_id"),
                        event.get("source"), _json(payload), int(critical), now,
                    ),
                )
                inserted += 1
                cycle_key = payload.get("cycle_key")
                for candidate in candidate_rows:
                    row = self.compact_candidate(candidate)
                    ckey = str(row.get("candidate_key") or "").strip()
                    symbol = str(row.get("symbol") or "").strip()
                    if not ckey or not symbol:
                        continue
                    conn.execute(
                        """
                        insert or replace into candidates(
                            candidate_key,observed_at,cycle_key,run_id,strategy_version_id,
                            symbol,market_lane,action,qualified,final_decision,reason,
                            reference_price,stop_price,target_price,feature_json,created_at
                        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            ckey, _iso(row.get("observed_at") or event.get("occurred_at")),
                            cycle_key, event.get("run_id"),
                            row.get("strategy_version_id")
                            or event.get("strategy_version_id"),
                            symbol, row.get("market_lane"), row.get("action"),
                            int(bool(row.get("qualified"))), row.get("final_decision"),
                            row.get("reason"), row.get("reference_price"),
                            row.get("stop_price"), row.get("target_price"),
                            _json(row.get("features") or {}), now,
                        ),
                    )
                    candidates_inserted += 1
            conn.commit()
        return {
            "ok": True,
            "inserted": inserted,
            "candidate_rows": candidates_inserted,
            "shed": shed,
            "storage": self.storage_state(),
        }

    def prune(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        cuts = {
            "events": (now - timedelta(days=30)).isoformat(),
            "cycles": (now - timedelta(days=7)).isoformat(),
            "positions": (now - timedelta(days=14)).isoformat(),
            "candidates": (now - timedelta(days=7)).isoformat(),
            "nostra": (now - timedelta(days=30)).isoformat(),
        }
        deleted: dict[str, int] = {}
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                "delete from events where critical=0 and event_type='decision_cycle' and occurred_at < ?",
                (cuts["cycles"],),
            )
            deleted["decision_cycles"] = cur.rowcount
            cur = conn.execute(
                "delete from events where critical=0 and event_type='position_metrics' and occurred_at < ?",
                (cuts["positions"],),
            )
            deleted["position_metrics"] = cur.rowcount
            cur = conn.execute(
                """delete from events
                where critical=0
                  and event_type not in ('decision_cycle','position_metrics')
                  and occurred_at < ?""",
                (cuts["events"],),
            )
            deleted["other_events"] = cur.rowcount
            cur = conn.execute(
                "delete from candidates where observed_at < ?",
                (cuts["candidates"],),
            )
            deleted["candidates"] = cur.rowcount
            cur = conn.execute(
                "delete from nostra_records where observed_at < ?",
                (cuts["nostra"],),
            )
            deleted["nostra"] = cur.rowcount
            conn.commit()
            conn.execute("pragma wal_checkpoint(TRUNCATE)")
        return deleted

    def set_kv(self, namespace: str, key: str, value: Any) -> int:
        now = _iso()
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "select revision from kv_state where namespace=? and key=?",
                (namespace, key),
            ).fetchone()
            revision = int(row[0]) + 1 if row else 1
            conn.execute(
                """insert into kv_state(namespace,key,value_json,revision,updated_at)
                values(?,?,?,?,?)
                on conflict(namespace,key) do update
                set value_json=excluded.value_json,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at""",
                (namespace, key, _json(value), revision, now),
            )
            conn.commit()
        return revision

    def get_kv(
        self, namespace: str, key: str, default: Any = None
    ) -> tuple[Any, int]:
        with self.connect() as conn:
            row = conn.execute(
                "select value_json,revision from kv_state where namespace=? and key=?",
                (namespace, key),
            ).fetchone()
        if not row:
            return default, 0
        return _loads(row[0], default), int(row[1])

    def graen_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            problems = [
                self._problem(row)
                for row in conn.execute(
                    "select * from graen_problems order by priority desc, created_at limit 250"
                ).fetchall()
            ]
            runs = [
                self._run(row)
                for row in conn.execute(
                    "select * from graen_runs order by started_at desc limit 500"
                ).fetchall()
            ]
            artifacts = [
                self._artifact(row)
                for row in conn.execute(
                    "select * from graen_artifacts order by created_at desc limit 1000"
                ).fetchall()
            ]
        runtime, _ = self.get_kv("graen", "runtime", {})
        return {
            "ok": True,
            "problems": problems,
            "runs": runs,
            "artifacts": artifacts,
            "runtime_state": runtime or {},
        }

    @staticmethod
    def _problem(row: sqlite3.Row) -> dict[str, Any]:
        body = _loads(row["body_json"], {})
        return {
            **body,
            "problem_id": row["problem_id"],
            "problem_key": row["problem_key"],
            "status": row["status"],
            "priority": row["priority"],
            "domain": row["domain"],
            "metadata": _loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "problem_id": row["problem_id"],
            "worker_id": row["worker_id"],
            "runtime_version": row["runtime_version"],
            "methodology_version": row["methodology_version"],
            "status": row["status"],
            "source_commit": row["source_commit"],
            "deployment_id": row["deployment_id"],
            "result_summary": _loads(row["result_json"], {}),
            "model_usage": _loads(row["model_usage_json"], {}),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _artifact(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "artifact_id": row["artifact_id"],
            "artifact_key": row["artifact_key"],
            "problem_id": row["problem_id"],
            "run_id": row["run_id"],
            "artifact_type": row["artifact_type"],
            "methodology_version": row["methodology_version"],
            "source_commit": row["source_commit"],
            "content_hash": row["content_hash"],
            "content": _loads(row["content_json"], {}),
            "created_at": row["created_at"],
        }

    def graen_action(
        self, action: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if action == "create_problem":
            return self._graen_create_problem(body)
        if action in {"claim_problem", "claim_research_problem"}:
            return self._graen_claim(
                body, research=action == "claim_research_problem"
            )
        if action in {"heartbeat", "executor_heartbeat"}:
            runtime = {
                k: body.get(k)
                for k in (
                    "worker_id", "runtime_version", "methodology_version",
                    "source_commit", "deployment_id", "active_problem_id",
                    "last_error",
                )
            }
            runtime["heartbeat_at"] = _iso()
            self.set_kv("graen", "runtime", runtime)
            return {"ok": True, "runtime_state": runtime}
        if action == "queue_research_stage":
            return self._graen_queue_stage(body)
        if action == "record_artifact":
            return self._graen_record_artifact(body)
        if action in {
            "complete_problem",
            "complete_research_problem",
            "block_research_claim",
        }:
            return self._graen_complete(action, body)
        if action == "compiled_stage_evidence":
            return self._compiled_stage_evidence(body)
        if action == "shadow_checkpoint":
            return self._shadow_checkpoint(body)
        if action == "crypto_promotion_status":
            return self._crypto_promotion_status(body)
        if action in {"research_promotion_claim", "research_promotion_save"}:
            return {
                "ok": True,
                "claimed": False,
                "status": "MIGRATION_GATED",
                "reason": "research_code_promotion_paused_during_core_v3_cutover",
                "execution_authority": False,
                "live_execution_authorized": False,
            }
        raise ValueError("invalid_action")

    def _graen_create_problem(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = str(body.get("problem_id") or uuid4())
        material = {
            k: body.get(k)
            for k in (
                "title", "statement", "domain", "source", "requested_by",
                "linked_iren_job_id", "constraints", "success_criteria",
            )
        }
        problem_key = str(
            body.get("problem_key")
            or f"problem:{_hash(material)[:24]}"
        )
        now = _iso()
        metadata = dict(body.get("metadata") or {})
        with self._lock, self.connect() as conn:
            conn.execute(
                """insert or ignore into graen_problems(
                    problem_id,problem_key,status,priority,domain,body_json,
                    metadata_json,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?)""",
                (
                    problem_id, problem_key, "QUEUED",
                    int(body.get("priority") or 50),
                    str(body.get("domain") or "GENERAL_RESEARCH"),
                    _json(material), _json(metadata), now, now,
                ),
            )
            row = conn.execute(
                "select * from graen_problems where problem_key=?",
                (problem_key,),
            ).fetchone()
            conn.commit()
        return {"ok": True, "problem": self._problem(row)}

    def _graen_claim(
        self, body: dict[str, Any], *, research: bool
    ) -> dict[str, Any]:
        domain = str(body.get("domain") or "")
        worker = str(body.get("worker_id") or "graen")
        with self._lock, self.connect() as conn:
            clauses = ["status in ('QUEUED','WAITING')"]
            args: list[Any] = []
            if research and domain:
                clauses.append("domain=?")
                args.append(domain)
            row = conn.execute(
                f"""select * from graen_problems
                where {' and '.join(clauses)}
                order by priority desc, created_at limit 1""",
                tuple(args),
            ).fetchone()
            if row is None:
                return {"ok": True, "problem": None, "run": None}
            problem = self._problem(row)
            run_id = str(uuid4())
            now = _iso()
            conn.execute(
                """update graen_problems
                set status='RUNNING', started_at=coalesce(started_at,?),
                    updated_at=? where problem_id=?""",
                (now, now, row["problem_id"]),
            )
            conn.execute(
                """insert into graen_runs(
                    run_id,problem_id,worker_id,runtime_version,
                    methodology_version,status,source_commit,deployment_id,
                    started_at
                ) values(?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, row["problem_id"], worker,
                    body.get("runtime_version"),
                    body.get("methodology_version"), "RUNNING",
                    body.get("source_commit"), body.get("deployment_id"), now,
                ),
            )
            runrow = conn.execute(
                "select * from graen_runs where run_id=?",
                (run_id,),
            ).fetchone()
            conn.commit()
        problem["status"] = "RUNNING"
        return {"ok": True, "problem": problem, "run": self._run(runrow)}

    def _graen_queue_stage(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = str(body.get("problem_id") or "")
        stage = str(body.get("stage") or "")
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "select * from graen_problems where problem_id=?",
                (problem_id,),
            ).fetchone()
            if row is None:
                raise ValueError("problem_not_found")
            metadata = _loads(row["metadata_json"], {})
            metadata.update(dict(body.get("metadata") or {}))
            metadata["research_stage"] = stage
            conn.execute(
                """update graen_problems
                set status='WAITING', metadata_json=?, updated_at=?
                where problem_id=?""",
                (_json(metadata), _iso(), problem_id),
            )
            row = conn.execute(
                "select * from graen_problems where problem_id=?",
                (problem_id,),
            ).fetchone()
            conn.commit()
        return {"ok": True, "problem": self._problem(row)}

    def _graen_record_artifact(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        content = dict(body.get("content") or {})
        problem_id = str(body.get("problem_id") or "")
        artifact_type = str(body.get("artifact_type") or "ARTIFACT")
        artifact_key = str(
            body.get("artifact_key")
            or f"{problem_id}:{artifact_type}:{_hash(content)}"
        )
        artifact_id = str(uuid4())
        now = _iso()
        with self._lock, self.connect() as conn:
            conn.execute(
                """insert or ignore into graen_artifacts(
                    artifact_id,artifact_key,problem_id,run_id,artifact_type,
                    methodology_version,source_commit,content_hash,content_json,
                    created_at
                ) values(?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id, artifact_key, problem_id, body.get("run_id"),
                    artifact_type, body.get("methodology_version"),
                    body.get("source_commit"), _hash(content), _json(content),
                    now,
                ),
            )
            row = conn.execute(
                "select * from graen_artifacts where artifact_key=?",
                (artifact_key,),
            ).fetchone()
            conn.commit()
        return {"ok": True, "artifact": self._artifact(row)}

    def _graen_complete(
        self, action: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = str(body.get("problem_id") or "")
        run_id = str(body.get("run_id") or "")
        status = (
            "BLOCKED"
            if action == "block_research_claim"
            else str(body.get("status") or "WAITING")
        )
        now = _iso()
        result = dict(body.get("result_summary") or {})
        if action == "block_research_claim":
            result = {
                "error": body.get("error"),
                "worker_id": body.get("worker_id"),
            }
        with self._lock, self.connect() as conn:
            if run_id:
                conn.execute(
                    """update graen_runs
                    set status=?, result_json=?, model_usage_json=?,
                        completed_at=? where run_id=?""",
                    (
                        status, _json(result),
                        _json(body.get("model_usage") or {}),
                        now, run_id,
                    ),
                )
            completed_at = (
                now if status in {"SUCCEEDED", "FAILED", "CANCELLED"} else None
            )
            row = conn.execute(
                "select metadata_json from graen_problems where problem_id=?",
                (problem_id,),
            ).fetchone()
            metadata = _loads(row[0], {}) if row else {}
            metadata.pop("research_stage", None)
            conn.execute(
                """update graen_problems
                set status=?, completed_at=?, updated_at=?, metadata_json=?
                where problem_id=?""",
                (status, completed_at, now, _json(metadata), problem_id),
            )
            prow = conn.execute(
                "select * from graen_problems where problem_id=?",
                (problem_id,),
            ).fetchone()
            rrow = (
                conn.execute(
                    "select * from graen_runs where run_id=?",
                    (run_id,),
                ).fetchone()
                if run_id else None
            )
            conn.commit()
        return {
            "ok": True,
            "problem": self._problem(prow) if prow else None,
            "run": self._run(rrow) if rrow else None,
        }

    def _shadow_checkpoint(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        status = str(body.get("status") or "").upper()
        if status not in {
            "COLLECTING", "READY_FOR_HUMAN_REVIEW", "SHADOW_REJECTED"
        }:
            raise ValueError("invalid_shadow_checkpoint")
        problem_id = str(body.get("problem_id") or "")
        evidence = dict(body.get("evidence") or {})
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "select metadata_json from graen_problems where problem_id=?",
                (problem_id,),
            ).fetchone()
            if row is None:
                raise ValueError("graen_problem_not_found")
            metadata = _loads(row[0], {})
            metadata.pop("research_stage", None)
            metadata["forward_shadow"] = {
                "activation_id": body.get("activation_id"),
                "candidate_id": body.get("candidate_id"),
                "candidate_methodology": evidence.get(
                    "candidate_methodology"
                ),
                "evidence_phase": evidence.get(
                    "evidence_phase", "FORWARD_SHADOW"
                ),
                "status": status,
                "evidence": evidence,
                "synced_at": _iso(),
                "execution_authority": False,
                "broker_orders_possible": False,
                "promotion_authorized": False,
            }
            conn.execute(
                """update graen_problems
                set status='WAITING', metadata_json=?, updated_at=?
                where problem_id=?""",
                (_json(metadata), _iso(), problem_id),
            )
            conn.commit()
        return {
            "ok": True,
            "status": status,
            "protected_action_required": status == "READY_FOR_HUMAN_REVIEW",
            "execution_authority": False,
            "broker_orders_possible": False,
            "promotion_authorized": False,
        }

    def _compiled_stage_evidence(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = str(body.get("problem_id") or "")
        spec_hash = str(body.get("spec_hash") or "")
        stage = str(body.get("stage") or "")
        epoch = str(body.get("epoch") or "")
        predecessor = {
            "validation": "development",
            "holdout": "validation",
        }.get(stage)
        current = prior = None
        with self.connect() as conn:
            rows = conn.execute(
                """select * from graen_artifacts
                where problem_id=? and artifact_type='COMPILED_STAGE_RESULT'
                order by created_at asc""",
                (problem_id,),
            ).fetchall()
        for row in rows:
            artifact = self._artifact(row)
            payload = artifact["content"]
            if (
                str(payload.get("spec_hash") or "") != spec_hash
                or str(payload.get("epoch") or "") != epoch
            ):
                continue
            if payload.get("stage") == stage:
                current = {**payload, "artifact_id": artifact["artifact_id"]}
            elif payload.get("stage") == predecessor:
                prior = {**payload, "artifact_id": artifact["artifact_id"]}
        return {"ok": True, "current": current, "predecessor": prior}

    def _crypto_promotion_status(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        requested = dict(body.get("execution_contract") or {})
        keys = (
            "strategy_family", "strategy_version_id", "model_version",
            "calibration_version", "regime_version",
            "execution_adapter_version",
        )
        normalized = {k: str(requested.get(k) or "").strip() for k in keys}
        if any(not normalized[k] for k in keys):
            return {
                "ok": True,
                "status": "GATED",
                "promotion_ready": False,
                "reason": "incomplete_execution_contract",
                "execution_contract": normalized,
                "execution_authority": False,
                "live_execution_authorized": False,
            }
        with self.connect() as conn:
            rows = conn.execute(
                """select * from graen_artifacts
                where artifact_type like 'CRYPTO_%PROMOTION_READY%'
                order by created_at desc limit 100"""
            ).fetchall()
        for row in rows:
            artifact = self._artifact(row)
            payload = artifact["content"]
            contract = {
                k: str((payload.get("execution_contract") or {}).get(k) or "").strip()
                for k in keys
            }
            passed = bool(
                payload.get("statistical_promotion_ready") is True
                or payload.get("promotion_ready") is True
                or (
                    payload.get("passed") is True
                    and str(payload.get("stage") or "").upper() == "HOLDOUT"
                )
            )
            if contract == normalized and passed:
                return {
                    "ok": True,
                    "status": "PROMOTION_READY",
                    "promotion_ready": True,
                    "reason": "matching_protected_research_artifact",
                    "execution_contract": normalized,
                    "artifact": {
                        k: artifact.get(k)
                        for k in (
                            "artifact_id", "problem_id", "run_id",
                            "artifact_type", "methodology_version",
                            "created_at",
                        )
                    },
                    "execution_authority": False,
                    "live_execution_authorized": False,
                }
        return {
            "ok": True,
            "status": "GATED",
            "promotion_ready": False,
            "reason": "no_matching_promotion_artifact",
            "execution_contract": normalized,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    def scheduler_claim(
        self, job: dict[str, Any]
    ) -> dict[str, Any]:
        key = str(job.get("job_key") or "")
        if not key:
            raise ValueError("job_key_required")
        now = _iso()
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "select * from scheduler_runs where job_key=?",
                (key,),
            ).fetchone()
            if (
                existing
                and existing["status"] in {"RUNNING", "SUCCEEDED", "NOOP"}
            ):
                return {
                    "ok": True,
                    "claimed": False,
                    "run": dict(existing),
                }
            payload = dict(job)
            conn.execute(
                """insert into scheduler_runs(
                    job_key,status,payload_json,scheduled_at,started_at,updated_at
                ) values(?,?,?,?,?,?)
                on conflict(job_key) do update
                set status='RUNNING', payload_json=excluded.payload_json,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at""",
                (
                    key, "RUNNING", _json(payload), job.get("scheduled_at"),
                    now, now,
                ),
            )
            conn.commit()
        return {"ok": True, "claimed": True, "job_key": key}

    def scheduler_complete(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        key = str(body.get("job_key") or "")
        status = str(body.get("status") or "FAILED")
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "select payload_json from scheduler_runs where job_key=?",
                (key,),
            ).fetchone()
            payload = _loads(row[0], {}) if row else {}
            payload["completion"] = body
            conn.execute(
                """update scheduler_runs
                set status=?, payload_json=?, completed_at=?, updated_at=?
                where job_key=?""",
                (status, _json(payload), _iso(), _iso(), key),
            )
            conn.commit()
        return {"ok": True, "job_key": key, "status": status}

    def scheduler_recent(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """select * from scheduler_runs
                order by coalesce(scheduled_at,started_at) desc limit ?""",
                (max(1, min(250, int(limit))),),
            ).fetchall()
        return [
            {
                **_loads(r["payload_json"], {}),
                "job_key": r["job_key"],
                "status": r["status"],
                "scheduled_at": r["scheduled_at"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
            }
            for r in rows
        ]


    @staticmethod
    def _iren_work_defaults() -> dict[str, Any]:
        return {
            "settings": {
                "autopilot_enabled": False,
                "autopilot_max_jobs_per_day": 3,
            },
            "objectives": [],
            "jobs": [],
            "job_events": [],
            "commands": [],
        }

    def iren_work_snapshot(self) -> dict[str, Any]:
        value, _ = self.get_kv("iren", "work", self._iren_work_defaults())
        state = self._iren_work_defaults()
        if isinstance(value, dict):
            state.update(value)
        for key in ("objectives", "jobs", "job_events", "commands"):
            if not isinstance(state.get(key), list):
                state[key] = []
        if not isinstance(state.get("settings"), dict):
            state["settings"] = self._iren_work_defaults()["settings"]
        return state

    def iren_work_action(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        action = str(action or "").strip()
        with self._lock:
            state = self.iren_work_snapshot()
            now = _iso()

            if action == "iren_work_snapshot":
                return {"ok": True, **state}

            if action == "iren_command_create":
                row = {
                    "command_id": str(uuid4()),
                    "command_text": str(body.get("command_text") or "")[:4000],
                    "source": str(body.get("source") or "command")[:40],
                    "requested_by": str(body.get("requested_by") or "operator")[:160],
                    "context": dict(body.get("context") or {}),
                    "status": "QUEUED",
                    "result": {},
                    "response": {},
                    "linked_job_id": None,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": None,
                }
                if not row["command_text"].strip():
                    raise ValueError("command_required")
                state["commands"].insert(0, row)
                state["commands"] = state["commands"][:200]
                self.set_kv("iren", "work", state)
                return {"ok": True, "command": row}

            if action == "iren_commands_claim":
                limit = max(1, min(20, int(body.get("limit") or 5)))
                owner = str(body.get("owner") or "iren-work-engine")[:160]
                claimed: list[dict[str, Any]] = []
                for row in reversed(state["commands"]):
                    if len(claimed) >= limit:
                        break
                    if str(row.get("status") or "").upper() != "QUEUED":
                        continue
                    row["status"] = "PROCESSING"
                    row["claimed_by"] = owner
                    row["claimed_at"] = now
                    row["updated_at"] = now
                    claimed.append(dict(row))
                self.set_kv("iren", "work", state)
                return {"ok": True, "commands": claimed}

            if action == "iren_command_complete":
                command_id = str(body.get("command_id") or "")
                row = next(
                    (item for item in state["commands"] if str(item.get("command_id")) == command_id),
                    None,
                )
                if row is None:
                    return {"ok": True, "updated": False}
                row["status"] = str(body.get("status") or "SUCCEEDED").upper()
                result = body.get("response") if isinstance(body.get("response"), dict) else body.get("result")
                row["result"] = dict(result or {})
                row["response"] = dict(result or {})
                row["linked_job_id"] = body.get("linked_job_id")
                row["updated_at"] = now
                row["completed_at"] = now
                self.set_kv("iren", "work", state)
                return {"ok": True, "updated": True, "command": row}

            if action == "iren_job_create":
                incoming = dict(body.get("job") or {})
                row = {
                    **incoming,
                    "job_id": str(incoming.get("job_id") or uuid4()),
                    "status": str(incoming.get("status") or "QUEUED").upper(),
                    "created_at": str(incoming.get("created_at") or now),
                    "updated_at": now,
                    "result": dict(incoming.get("result") or {}),
                    "error": dict(incoming.get("error") or {}),
                }
                state["jobs"].insert(0, row)
                state["jobs"] = state["jobs"][:250]
                state["job_events"].insert(0, {
                    "event_id": str(uuid4()),
                    "job_id": row["job_id"],
                    "event": "created",
                    "status": row["status"],
                    "created_at": now,
                })
                state["job_events"] = state["job_events"][:500]
                self.set_kv("iren", "work", state)
                return {"ok": True, "job": row}

            if action == "iren_jobs_claim":
                limit = max(1, min(20, int(body.get("limit") or 3)))
                owner = str(body.get("owner") or "iren-work-engine")[:160]
                claimed: list[dict[str, Any]] = []
                for row in reversed(state["jobs"]):
                    if len(claimed) >= limit:
                        break
                    if str(row.get("status") or "").upper() != "QUEUED":
                        continue
                    row["status"] = "RUNNING"
                    row["claimed_by"] = owner
                    row["claimed_at"] = now
                    row["updated_at"] = now
                    claimed.append(dict(row))
                self.set_kv("iren", "work", state)
                return {"ok": True, "jobs": claimed}

            if action == "iren_job_update":
                job_id = str(body.get("job_id") or "")
                row = next(
                    (item for item in state["jobs"] if str(item.get("job_id")) == job_id),
                    None,
                )
                if row is None:
                    return {"ok": True, "updated": False}
                for key in ("status", "result", "error", "requires_human", "protected_action"):
                    if key in body:
                        value = body[key]
                        if key in {"result", "error"}:
                            value = dict(value or {})
                        elif key == "status":
                            value = str(value or "").upper()
                        row[key] = value
                row["updated_at"] = now
                if str(row.get("status") or "").upper() in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    row["completed_at"] = now
                state["job_events"].insert(0, {
                    "event_id": str(uuid4()),
                    "job_id": job_id,
                    "event": "updated",
                    "status": row.get("status"),
                    "created_at": now,
                })
                state["job_events"] = state["job_events"][:500]
                self.set_kv("iren", "work", state)
                return {"ok": True, "updated": True, "job": row}

            if action == "iren_objective_update":
                key = str(body.get("objective_key") or "")
                row = next(
                    (item for item in state["objectives"] if str(item.get("objective_key")) == key),
                    None,
                )
                if row is None:
                    return {"ok": True, "updated": False}
                for field in ("status", "title", "description", "priority", "metadata"):
                    if field in body:
                        row[field] = body[field]
                row["updated_at"] = now
                self.set_kv("iren", "work", state)
                return {"ok": True, "updated": True, "objective": row}

            if action == "iren_handoff_evidence":
                return {
                    "ok": True,
                    "health": {
                        "source": "rhen-core-sqlite",
                        "storage": self.storage_state(),
                    },
                    "command_contract": "rhen_native",
                }

            return {
                "ok": True,
                "status": "UNSUPPORTED",
                "action": action,
                "execution_authority": False,
            }


    def public_live_feed(
        self, now: datetime | None = None
    ) -> dict[str, Any]:
        """Return the aggregate-only public projection consumed by anevum.com."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff_10m = current - timedelta(minutes=10)
        cutoff_60m = current - timedelta(minutes=60)
        cutoff_2h = current - timedelta(hours=2)

        def stamp(value: Any) -> datetime | None:
            try:
                parsed = datetime.fromisoformat(
                    str(value or "").replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)

        recent = self._event_rows(limit=5000, newest_first=True)
        dated = [
            (event, stamp(event.get("occurred_at")))
            for event in recent
        ]
        recent_2h = [
            event for event, observed in dated
            if observed is not None and observed >= cutoff_2h
        ]
        recent_60m = [
            event for event, observed in dated
            if observed is not None and observed >= cutoff_60m
        ]
        recent_10m = [
            event for event, observed in dated
            if observed is not None and observed >= cutoff_10m
        ]

        latest_event = recent[0] if recent else None
        latest_event_at = stamp(
            latest_event.get("occurred_at") if latest_event else None
        )
        freshness = (
            max(0.0, (current - latest_event_at).total_seconds())
            if latest_event_at is not None
            else None
        )
        live = freshness is not None and freshness < 120.0

        scan_types = {"scan", "decision_cycle"}
        execution_types = {
            "execution", "broker_order", "broker_fill", "exit"
        }
        scan_events_10m = sum(
            event.get("event_type") in scan_types for event in recent_10m
        )
        execution_events_2h = sum(
            event.get("event_type") in execution_types
            for event in recent_2h
        )
        reconciliations_2h = sum(
            event.get("event_type") == "reconciliation"
            for event in recent_2h
        )
        errors_2h = sum(
            event.get("event_type") in {
                "runtime_error", "crypto_runtime_error"
            }
            for event in recent_2h
        )
        symbols_10m = len(
            {
                str(event.get("symbol") or "").upper()
                for event in recent_10m
                if event.get("symbol")
            }
        )

        event_labels = {
            "scan": ("observe", "Scanner cycle evaluated the market universe."),
            "decision_cycle": (
                "decide",
                "Decision engine evaluated the market universe.",
            ),
            "allocation": (
                "decide",
                "Allocation engine evaluated available capacity.",
            ),
            "signal": (
                "decide",
                "Decision engine evaluated a qualified setup.",
            ),
            "order_intent": (
                "risk",
                "Risk gate evaluated an execution intent.",
            ),
            "execution": (
                "execute",
                "Execution subsystem recorded market activity.",
            ),
            "broker_order": (
                "execute",
                "Broker interface recorded an order lifecycle event.",
            ),
            "broker_fill": (
                "execute",
                "Broker interface recorded a fill event.",
            ),
            "exit": (
                "execute",
                "Position lifecycle recorded an exit event.",
            ),
            "reconciliation": (
                "learn",
                "Broker state and canonical state were reconciled.",
            ),
            "runtime_start": ("system", "RHEN unified runtime started."),
            "runtime_stop": ("system", "RHEN unified runtime stopped."),
            "runtime_error": (
                "warning",
                "Runtime reported an operational exception.",
            ),
            "crypto_runtime_error": (
                "warning",
                "Crypto runtime reported an operational exception.",
            ),
        }
        public_types = set(event_labels)
        public_events = []
        for event in recent_2h:
            event_type = str(event.get("event_type") or "")
            if event_type not in public_types:
                continue
            kind, label = event_labels[event_type]
            public_events.append(
                {
                    "at": event.get("occurred_at"),
                    "type": event_type,
                    "kind": kind,
                    "label": label,
                }
            )
            if len(public_events) >= 28:
                break

        latest_scan = next(
            (
                event
                for event in recent
                if event.get("event_type") in scan_types
            ),
            None,
        )
        latest_scan_payload = (
            dict(latest_scan.get("payload") or {})
            if latest_scan else {}
        )
        operational_scan = (
            {
                "observed_at": latest_scan.get("occurred_at"),
                "market_session": latest_scan_payload.get("market_session"),
                "cycle_outcome": (
                    latest_scan_payload.get("cycle_outcome")
                    or latest_scan_payload.get("status")
                ),
                "data_status": latest_scan_payload.get("data_status"),
                "degraded": bool(
                    latest_scan_payload.get("degraded", False)
                ),
            }
            if latest_scan
            else None
        )

        buckets: dict[str, int] = {}
        for event in recent_60m:
            observed = stamp(event.get("occurred_at"))
            if observed is None:
                continue
            minute = observed.minute - (observed.minute % 10)
            bucket = observed.replace(
                minute=minute, second=0, microsecond=0
            ).isoformat()
            buckets[bucket] = buckets.get(bucket, 0) + 1
        activity = [
            {"at": key, "count": buckets[key]}
            for key in sorted(buckets)
        ]

        runtime_event = next(
            (
                event
                for event in recent
                if event.get("event_type") == "runtime_start"
            ),
            None,
        )
        runtime_payload = dict(
            (runtime_event or {}).get("payload") or {}
        )
        strategy_version = str(
            (runtime_event or {}).get("strategy_version_id")
            or os.getenv("STRATEGY_VERSION_ID", "")
            or "RHEN-CURRENT"
        )
        strategy_name = str(
            runtime_payload.get("strategy_name")
            or runtime_payload.get("strategy")
            or os.getenv("STRATEGY_NAME", "")
            or strategy_version
        )
        active_strategy = {
            "version_id": strategy_version,
            "strategy_name": strategy_name,
            "environment": (
                os.getenv("RAILWAY_ENVIRONMENT_NAME")
                or "production"
            ),
            "status": "RUNNING" if live else "STALE",
            "activated_at": (
                (runtime_event or {}).get("occurred_at")
                or current.isoformat()
            ),
        }

        with self.connect() as conn:
            account_rows = conn.execute(
                """select occurred_at,payload_json from events
                where event_type='account_snapshot'
                order by occurred_at asc limit 5000"""
            ).fetchall()
            problem_row = conn.execute(
                """select status,body_json,metadata_json,updated_at
                from graen_problems
                order by
                  case status
                    when 'RUNNING' then 0
                    when 'WAITING' then 1
                    when 'QUEUED' then 2
                    else 3
                  end,
                  updated_at desc
                limit 1"""
            ).fetchone()

        performance_points: list[tuple[str, float]] = []
        for row in account_rows:
            payload = _loads(row["payload_json"], {})
            try:
                equity = float(payload.get("equity"))
            except (TypeError, ValueError):
                continue
            if equity > 0:
                performance_points.append((row["occurred_at"], equity))

        if performance_points:
            baseline = performance_points[0][1]
            peak = baseline
            max_drawdown = 0.0
            curve = []
            for observed_at, equity in performance_points:
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = max(
                        max_drawdown,
                        (peak - equity) / peak * 100.0,
                    )
                curve.append(
                    {
                        "at": observed_at,
                        "return_pct": (
                            (equity / baseline - 1.0) * 100.0
                            if baseline > 0 else None
                        ),
                    }
                )
            if len(curve) > 72:
                step = max(1, len(curve) // 72)
                sampled = curve[::step]
                if sampled[-1] != curve[-1]:
                    sampled.append(curve[-1])
                curve = sampled
            latest_equity = performance_points[-1][1]
            performance = {
                "methodology_version": "PUBLIC-PERFORMANCE-CORE-v1",
                "basis": "canonical_account_snapshot_events",
                "status": "TRACKING",
                "sample_state": "EARLY_SAMPLE",
                "tracking_started_at": performance_points[0][0],
                "last_observed_at": performance_points[-1][0],
                "snapshot_count": len(performance_points),
                "closed_trades": None,
                "wins": None,
                "losses": None,
                "win_rate_pct": None,
                "account_return_pct": (
                    (latest_equity / baseline - 1.0) * 100.0
                    if baseline > 0 else None
                ),
                "realized_return_pct": None,
                "max_drawdown_pct": max_drawdown,
                "curve": curve,
                "limitations": [
                    "Only normalized account performance is public.",
                    "Dollar values, symbols, prices, quantities, orders, fills, and strategy thresholds are excluded.",
                ],
            }
        else:
            performance = {
                "methodology_version": "PUBLIC-PERFORMANCE-CORE-v1",
                "basis": "canonical_account_snapshot_events",
                "status": "AWAITING_CANONICAL_SAMPLE",
                "sample_state": "AWAITING_LIVE_SAMPLE",
                "tracking_started_at": None,
                "last_observed_at": None,
                "snapshot_count": 0,
                "closed_trades": None,
                "wins": None,
                "losses": None,
                "win_rate_pct": None,
                "account_return_pct": None,
                "realized_return_pct": None,
                "max_drawdown_pct": None,
                "curve": [],
                "limitations": [
                    "No normalized account sample is available yet.",
                    "Dollar values, symbols, prices, quantities, orders, fills, and strategy thresholds are excluded.",
                ],
            }

        research_status = None
        research_focus = None
        research_updated_at = None
        if problem_row:
            problem_body = _loads(problem_row["body_json"], {})
            problem_meta = _loads(problem_row["metadata_json"], {})
            research_status = str(problem_row["status"] or "UNKNOWN")
            research_focus = str(
                problem_body.get("title")
                or problem_body.get("statement")
                or problem_body.get("domain")
                or problem_meta.get("research_stage")
                or "Current RHEN research problem"
            )
            research_updated_at = problem_row["updated_at"]

        common_observed = (
            latest_event.get("occurred_at")
            if latest_event else current.isoformat()
        )
        health = "HEALTHY" if live else "STALE"
        systems = {
            "RHEN": {
                "runtime_state": (
                    "OBSERVING" if scan_events_10m else "READY"
                ),
                "health_state": health,
                "tracking_state": (
                    "LIVE_TELEMETRY" if live else "STALE"
                ),
                "observed_at": (
                    (latest_scan or {}).get("occurred_at")
                    or common_observed
                ),
                "independent_runtime": False,
                "activity": (
                    "Market-universe scanning is active."
                    if scan_events_10m
                    else "Unified runtime online; awaiting a fresh market scan."
                ),
            },
            "IREN": {
                "runtime_state": "READY" if live else "STALE",
                "health_state": health,
                "tracking_state": "CANONICAL_CONTROL_STATE",
                "observed_at": common_observed,
                "independent_runtime": False,
                "activity": "Supervising the unified RHEN runtime.",
            },
            "GRAEN": {
                "runtime_state": (
                    "RESEARCHING"
                    if research_status == "RUNNING"
                    else "READY"
                ),
                "health_state": health,
                "tracking_state": research_status or "READY",
                "observed_at": common_observed,
                "independent_runtime": False,
                "activity": (
                    research_focus
                    if research_status == "RUNNING" and research_focus
                    else "Research module ready; no active run exposed."
                ),
            },
            "VELUM": {
                "runtime_state": "READY" if live else "STALE",
                "health_state": health,
                "tracking_state": "READY",
                "observed_at": common_observed,
                "independent_runtime": False,
                "activity": "Replay module ready; no active replay exposed.",
            },
            "NOSTRA": {
                "runtime_state": "READY" if live else "STALE",
                "health_state": health,
                "tracking_state": "READY",
                "observed_at": common_observed,
                "independent_runtime": False,
                "activity": "Forecast module ready; no active forecast exposed.",
            },
        }

        return {
            "ok": True,
            "generated_at": current.isoformat(),
            "source": "rhen-core-sqlite",
            "live": live,
            "state": "LIVE" if live else "STALE",
            "freshness_seconds": freshness,
            "systems": systems,
            "active_strategy": active_strategy,
            "strategy_history": [],
            "telemetry": {
                "events_60m": len(recent_60m),
                "scan_events_10m": scan_events_10m,
                "symbols_10m": symbols_10m,
                "execution_events_2h": execution_events_2h,
                "reconciliations_2h": reconciliations_2h,
                "errors_2h": errors_2h,
            },
            "activity": activity,
            "events": public_events,
            "operational": {"latest_scan": operational_scan},
            "research": {
                "current_focus": research_focus,
                "current_status": research_status,
                "last_updated_at": research_updated_at,
                "next_direction": None,
                "completed_decisions": [],
                "active_questions": [],
                "latest_daily": None,
                "latest_weekly": None,
                "latest_weekly_summary": None,
                "evidence": {
                    "candidate_forward_outcomes": [],
                    "live_offline_comparison": [],
                    "analytics_only": True,
                },
                "limitations": [
                    "Public research projection is aggregate-only."
                ],
                "journal": [],
            },
            "performance": performance,
            "disclosure": {
                "level": "aggregate_only",
                "public_fields": [
                    "health",
                    "freshness",
                    "aggregate_event_counts",
                    "normalized_performance",
                    "sanitized_activity",
                ],
                "excluded_fields": [
                    "symbols",
                    "dollar_values",
                    "prices",
                    "quantities",
                    "orders",
                    "fills",
                    "strategy_thresholds",
                    "credentials",
                ],
            },
        }


    def strategy_pipeline_research(self) -> dict[str, Any]:
        """Return the durable Command research and strategy lifecycle projection."""
        with self.connect() as conn:
            problem_rows = conn.execute(
                """select * from graen_problems
                order by
                  case status
                    when 'RUNNING' then 0
                    when 'WAITING' then 1
                    when 'QUEUED' then 2
                    when 'BLOCKED' then 3
                    else 4
                  end,
                  updated_at desc
                limit 25"""
            ).fetchall()
            run_rows = conn.execute(
                """select * from graen_runs
                order by started_at desc limit 50"""
            ).fetchall()
            replay_rows = conn.execute(
                """select event_type,occurred_at,strategy_version_id,payload_json
                from events
                where event_type in (
                  'velum_graen_candidate_replay',
                  'velum_replay_result'
                )
                order by occurred_at desc limit 25"""
            ).fetchall()

        graen_runtime, _ = self.get_kv("graen", "runtime", {})

        problems: list[dict[str, Any]] = []
        for row in problem_rows:
            problem = self._problem(row)
            metadata = dict(problem.get("metadata") or {})
            problems.append(
                {
                    "problem_id": problem.get("problem_id"),
                    "title": problem.get("title") or problem.get("statement"),
                    "status": problem.get("status"),
                    "domain": problem.get("domain"),
                    "research_stage": metadata.get("research_stage"),
                    "candidate_id": (
                        metadata.get("candidate_id")
                        or problem.get("candidate_id")
                    ),
                    "hypothesis": metadata.get("hypothesis"),
                    "family": metadata.get("family"),
                    "mechanism": metadata.get("mechanism"),
                    "campaign_id": metadata.get("campaign_id"),
                    "target_lane": metadata.get("target_lane"),
                    "supersedes_strategy_version_id": metadata.get(
                        "supersedes_strategy_version_id"
                    ),
                    "release_requested": bool(
                        metadata.get("release_requested", False)
                    ),
                    "updated_at": problem.get("updated_at"),
                    "started_at": problem.get("started_at"),
                    "completed_at": problem.get("completed_at"),
                }
            )

        runs: list[dict[str, Any]] = []
        for row in run_rows:
            run = self._run(row)
            result = dict(run.get("result_summary") or {})
            runs.append(
                {
                    "run_id": run.get("run_id"),
                    "problem_id": run.get("problem_id"),
                    "status": run.get("status"),
                    "methodology_version": run.get("methodology_version"),
                    "result_state": (
                        result.get("result_state")
                        or result.get("state")
                        or result.get("status")
                    ),
                    "error": result.get("error"),
                    "started_at": run.get("started_at"),
                    "completed_at": run.get("completed_at"),
                    "created_at": run.get("started_at"),
                }
            )

        replay_projections: list[dict[str, Any]] = []
        for row in replay_rows:
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                payload = {}
            engineering_gate = (
                dict(payload.get("engineering_gate") or {})
                if isinstance(payload.get("engineering_gate"), dict)
                else {}
            )
            passed = engineering_gate.get("passed")
            status = (
                "PASSED"
                if passed is True
                else "FAILED"
                if passed is False
                else str(payload.get("status") or "COMPLETE").upper()
            )
            replay_range = (
                dict(payload.get("range") or {})
                if isinstance(payload.get("range"), dict)
                else {}
            )
            replay_projections.append(
                {
                    "owner": "VELUM",
                    "event_type": row["event_type"],
                    "candidate_id": payload.get("candidate_id"),
                    "problem_id": payload.get("problem_id"),
                    "strategy_version_id": (
                        row["strategy_version_id"]
                        or payload.get("strategy_version_id")
                    ),
                    "status": status,
                    "started_at": (
                        payload.get("replay_start")
                        or replay_range.get("start")
                    ),
                    "completed_at": row["occurred_at"],
                    "observed_at": row["occurred_at"],
                    "engineering_gate": engineering_gate or None,
                }
            )

        candidate = None
        active_candidate_states = {"RUNNING", "WAITING", "QUEUED", "BLOCKED"}
        selected_problem = next(
            (
                row
                for row in problems
                if str(row.get("status") or "").upper() in active_candidate_states
                or row.get("release_requested") is True
            ),
            None,
        )
        if selected_problem is not None:
            domain = str(selected_problem.get("domain") or "").upper()
            lane = str(selected_problem.get("target_lane") or "").lower()
            if lane not in {"crypto", "equities"}:
                lane = (
                    "crypto"
                    if "CRYPTO" in domain
                    else "equities"
                    if "EQUITY" in domain or "STOCK" in domain
                    else "unknown"
                )
            selected_run = next(
                (
                    row
                    for row in runs
                    if row.get("problem_id") == selected_problem.get("problem_id")
                ),
                None,
            )
            candidate = {
                "owner": "GRAEN",
                "problem_id": selected_problem.get("problem_id"),
                "candidate_id": selected_problem.get("candidate_id"),
                "title": selected_problem.get("title"),
                "lane": lane,
                "status": selected_problem.get("status"),
                "stage": selected_problem.get("research_stage"),
                "methodology_version": (
                    selected_run.get("methodology_version")
                    if selected_run else None
                ),
                "run_id": selected_run.get("run_id") if selected_run else None,
                "updated_at": selected_problem.get("updated_at"),
                "started_at": selected_problem.get("started_at"),
                "completed_at": selected_problem.get("completed_at"),
                "supersedes_strategy_version_id": (
                    selected_problem.get("supersedes_strategy_version_id")
                ),
                "release_requested": bool(
                    selected_problem.get("release_requested", False)
                ),
            }

        validation = None
        if candidate:
            validation = next(
                (
                    row
                    for row in replay_projections
                    if row.get("problem_id")
                    and row.get("problem_id") == candidate.get("problem_id")
                ),
                None,
            )
        if validation is None and replay_projections:
            validation = replay_projections[0]

        candidate_status = str((candidate or {}).get("status") or "").upper()
        validation_status = str((validation or {}).get("status") or "").upper()
        same_problem = bool(
            candidate
            and validation
            and validation.get("problem_id")
            and validation.get("problem_id") == candidate.get("problem_id")
        )
        release_status = "NO_CANDIDATE"
        reason = "No durable superseding research candidate is currently exposed."
        if candidate:
            release_status = "HOLD"
            reason = "Candidate has not completed validation."
            supersedes = candidate.get("supersedes_strategy_version_id")
            if not supersedes:
                reason = (
                    "Candidate has no explicit production supersession target."
                )
            elif candidate_status in {"FAILED", "CANCELLED", "BLOCKED"}:
                release_status = "REJECTED"
                reason = "Candidate research is not eligible for promotion."
            elif same_problem and validation_status == "FAILED":
                release_status = "REJECTED"
                reason = "Latest matching VELUM validation failed."
            elif (
                candidate_status in {"SUCCEEDED", "COMPLETE", "COMPLETED"}
                and same_problem
                and validation_status == "PASSED"
                and bool(candidate.get("release_requested"))
            ):
                release_status = "REVIEW"
                reason = "Research and validation are complete; operator review is required."

        return {
            "schema_version": "strategy_pipeline_research.v1",
            "candidate": candidate,
            "validation": validation,
            "release_gate": {
                "owner": "IREN",
                "status": release_status,
                "reason": reason,
                "target_lane": (
                    (candidate or {}).get("lane")
                    if (candidate or {}).get("supersedes_strategy_version_id")
                    else None
                ),
                "target_strategy_version_id": (
                    (candidate or {}).get("supersedes_strategy_version_id")
                ),
                "automatic_promotion": False,
                "production_authority_changed": False,
            },
            "research": {
                "graen_problems": problems,
                "graen_runs": runs,
                "velum_replays": replay_projections,
                "graen_runtime": graen_runtime or None,
            },
        }

    def canonical_evidence(self) -> dict[str, Any]:
        with self.connect() as conn:
            runtime_row = conn.execute(
                """select payload_json,occurred_at,run_id,strategy_version_id
                from events where event_type='runtime_start'
                order by occurred_at desc limit 1"""
            ).fetchone()
            daily_row = conn.execute(
                """select payload_json,occurred_at from events
                where event_type='research_daily_report'
                order by occurred_at desc limit 1"""
            ).fetchone()
            weekly_row = conn.execute(
                """select payload_json,occurred_at from events
                where event_type='research_weekly_report'
                order by occurred_at desc limit 1"""
            ).fetchone()
            agent_rows = conn.execute(
                """select payload_json,created_at from research_runs
                order by created_at desc limit 50"""
            ).fetchall()
            ledger_rows = conn.execute(
                """select payload_json,created_at from research_ledgers
                order by created_at desc limit 75"""
            ).fetchall()

        runtime = _loads(runtime_row["payload_json"], {}) if runtime_row else {}
        version = str(
            (runtime_row["strategy_version_id"] if runtime_row else None)
            or os.getenv("STRATEGY_VERSION_ID", "")
            or "RHEN-CURRENT"
        ).strip()
        name = str(
            runtime.get("strategy_name")
            or runtime.get("strategy")
            or os.getenv("STRATEGY_NAME", "")
            or version
        ).strip()
        current_strategy = {
            "version_id": version,
            "strategy_version_id": version,
            "strategy_name": name,
            "status": "running",
            "git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "activated_at": (
                runtime_row["occurred_at"] if runtime_row else _iso()
            ),
            "asset_class": runtime.get("asset_class") or "multi_asset",
            "mode": runtime.get("trading_mode") or os.getenv("TRADING_MODE", ""),
            "run_id": (
                runtime_row["run_id"]
                if runtime_row else os.getenv("TRADING_RUN_ID", "")
            ),
            "identity_fallback_to_version": name == version,
        }
        daily = _loads(daily_row["payload_json"], None) if daily_row else None
        weekly = _loads(weekly_row["payload_json"], None) if weekly_row else None
        agent_runs = [_loads(row["payload_json"], {}) for row in agent_rows]

        recent_hypotheses: list[dict[str, Any]] = []
        recent_events: list[dict[str, Any]] = []
        for row in ledger_rows:
            payload = _loads(row["payload_json"], {})
            recent_hypotheses.extend(
                item
                for item in payload.get("hypotheses", [])
                if isinstance(item, dict)
            )
            recent_events.extend(
                item
                for item in payload.get("events", [])
                if isinstance(item, dict)
            )
        search_ledger = {
            "exposure": {
                "ledger_version": "rhen-core-v3",
                "proposal_count": len(ledger_rows),
                "hypothesis_count": len(recent_hypotheses),
                "event_count": len(recent_events),
                "last_recorded_at": (
                    ledger_rows[0]["created_at"] if ledger_rows else None
                ),
                "production_authority": False,
                "protected_stage_authority": False,
            },
            "recent_hypotheses": recent_hypotheses[:75],
            "recent_events": recent_events[:100],
            "recent_multiplicity_plans": [],
            "recent_dependence_plans": [],
        }
        cutoffs = [
            row["occurred_at"]
            for row in (daily_row, weekly_row)
            if row is not None and row["occurred_at"]
        ]
        return {
            "current_strategy": current_strategy,
            "latest_daily_report": daily,
            "latest_weekly_report": weekly,
            "research_questions": [],
            "experiments": [],
            "research_decisions": [],
            "agent_runs": agent_runs,
            "search_ledger": search_ledger,
            "evidence_cutoff": max(cutoffs) if cutoffs else None,
        }

    def nostra_work(
        self, now: datetime | None = None
    ) -> dict[str, Any]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        forecast_start = (current - timedelta(minutes=10)).isoformat()
        score_start = (current - timedelta(hours=6)).isoformat()
        with self.connect() as conn:
            candidate_rows = conn.execute(
                """select * from candidates
                where observed_at >= ?
                  and lower(coalesce(market_lane,''))='crypto'
                order by observed_at asc limit 500""",
                (forecast_start,),
            ).fetchall()
            forecast_rows = conn.execute(
                """select payload_json,occurred_at from events
                where event_type='nostra_forecast' and occurred_at >= ?
                order by occurred_at asc limit 2000""",
                (score_start,),
            ).fetchall()
            outcome_rows = conn.execute(
                """select payload_json,occurred_at,symbol from events
                where event_type='candidate_forward_outcome'
                  and occurred_at >= ?
                order by occurred_at asc limit 4000""",
                (score_start,),
            ).fetchall()

        existing: set[str] = set()
        forecasts: list[dict[str, Any]] = []
        for row in forecast_rows:
            payload = _loads(row["payload_json"], {})
            identity = str(
                ((payload.get("provenance") or {}).get("candidate_identity"))
                or ((payload.get("source") or {}).get("candidate_identity"))
                or ""
            ).strip()
            if identity:
                existing.add(identity)
            expected = (payload.get("forecast_payload") or {}).get(
                "expected_return"
            )
            if identity and expected is not None:
                forecasts.append(
                    {
                        "forecast_id": payload.get("forecast_id"),
                        "candidate_identity": identity,
                        "symbol": payload.get("symbol"),
                        "model_id": payload.get("model_id"),
                        "model_version": payload.get("model_version"),
                        "expected_return": expected,
                        "generated_at": payload.get("generated_at")
                        or row["occurred_at"],
                    }
                )

        forecast_candidates: list[dict[str, Any]] = []
        for row in candidate_rows:
            identity = str(row["candidate_key"] or "").strip()
            if not identity or identity in existing:
                continue
            features = _loads(row["feature_json"], {})
            forecast_candidates.append(
                {
                    "candidate_identity": identity,
                    "candidate_id": identity,
                    "symbol": row["symbol"],
                    "observed_at": row["observed_at"],
                    "run_id": row["run_id"],
                    "strategy_version_id": row["strategy_version_id"],
                    "features": features,
                    "scan_cycle": {
                        "scan_cycle_id": row["cycle_key"],
                        "data_feed": None,
                        "bar_interval": None,
                        "data_status": "compact_core_v3",
                    },
                    "research_attribution": {},
                    "missing_model_ids": ["zero_return"],
                }
            )

        outcomes_by_identity: dict[str, dict[str, Any]] = {}
        for row in outcome_rows:
            payload = _loads(row["payload_json"], {})
            if str(payload.get("status") or "").lower() != "complete":
                continue
            if int(payload.get("horizon_minutes") or 0) != 10:
                continue
            identity = str(
                payload.get("candidate_id")
                or payload.get("candidate_key")
                or ""
            ).strip()
            if identity:
                outcomes_by_identity[identity] = {
                    "payload": payload,
                    "occurred_at": row["occurred_at"],
                    "symbol": row["symbol"],
                }

        score_outcomes: list[dict[str, Any]] = []
        for forecast in forecasts:
            outcome = outcomes_by_identity.get(
                forecast["candidate_identity"]
            )
            if not outcome or not forecast.get("forecast_id"):
                continue
            payload = outcome["payload"]
            score_outcomes.append(
                {
                    **forecast,
                    "observed_at": payload.get("observation_end_at")
                    or outcome["occurred_at"],
                    "realized_return": payload.get("forward_return"),
                    "max_favorable_return": payload.get(
                        "max_favorable_return"
                    ),
                    "max_adverse_return": payload.get(
                        "max_adverse_return"
                    ),
                    "outcome_methodology_version": payload.get(
                        "methodology_version"
                    ),
                }
            )

        return {
            "ok": True,
            "schema_version": "rhen-core-nostra-work-v1",
            "generated_at": current.isoformat(),
            "forecast_horizon_minutes": 10,
            "baseline_model_id": "zero_return",
            "research_only": True,
            "execution_authority": False,
            "forecast_candidates": forecast_candidates[:200],
            "score_outcomes": score_outcomes[:500],
            "baseline_evaluation": None,
            "model_evaluations": [],
            "drift_training_state": {
                "eligible": False,
                "independent_cycles": 0,
                "raw_outcome_count": 0,
                "training_cutoff": current.isoformat(),
                "mean_cycle_return": None,
                "reason": "core_v3_rebuild_baseline_only",
                "research_only": True,
                "execution_authority": False,
            },
            "counts": {
                "candidate_rows_scanned": len(candidate_rows),
                "forecast_candidates": len(forecast_candidates),
                "pending_score_outcomes": len(score_outcomes),
                "evaluation_models": 0,
                "drift_independent_cycles": 0,
            },
        }


    def _event_rows(
        self,
        *,
        event_types: set[str] | None = None,
        limit: int = 5000,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        order = "desc" if newest_first else "asc"
        with self.connect() as conn:
            if event_types:
                marks = ",".join("?" for _ in event_types)
                rows = conn.execute(
                    f"""select * from events
                    where event_type in ({marks})
                    order by occurred_at {order} limit ?""",
                    (*sorted(event_types), max(1, min(50000, int(limit)))),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""select * from events
                    order by occurred_at {order} limit ?""",
                    (max(1, min(50000, int(limit))),),
                ).fetchall()
        return [
            {
                "event_key": row["event_key"],
                "run_id": row["run_id"],
                "strategy_version_id": row["strategy_version_id"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "symbol": row["symbol"],
                "correlation_id": row["correlation_id"],
                "source": row["source"],
                "payload": _loads(row["payload_json"], {}),
                "ingested_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _session_date(value: str) -> str | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(NY).date().isoformat()

    def _latest_report_event(
        self,
        event_type: str,
        *,
        field: str,
        requested: str | None = None,
    ) -> dict[str, Any] | None:
        for event in self._event_rows(
            event_types={event_type},
            limit=1000,
            newest_first=True,
        ):
            payload = event.get("payload") or {}
            if requested is None or str(payload.get(field) or "") == requested:
                return event
        return None

    def _shadow_report(self, candidate_id: str | None) -> dict[str, Any]:
        wanted = {
            "graen_candidate_shadow_activation",
            "graen_candidate_shadow_state",
            "graen_candidate_shadow_opportunity",
            "graen_candidate_shadow_entry",
            "graen_candidate_shadow_exit",
            "graen_candidate_shadow_checkpoint",
        }
        events = self._event_rows(
            event_types=wanted,
            limit=1000,
            newest_first=True,
        )
        if candidate_id:
            events = [
                event
                for event in events
                if str((event.get("payload") or {}).get("candidate_id") or "")
                == candidate_id
            ]
        activation = next(
            (
                event
                for event in events
                if event["event_type"] == "graen_candidate_shadow_activation"
            ),
            None,
        )
        activation_id = str(
            ((activation or {}).get("payload") or {}).get("activation_id") or ""
        )
        if activation_id:
            events = [
                event
                for event in events
                if event is activation
                or str(
                    (event.get("payload") or {}).get("activation_id") or ""
                )
                == activation_id
            ]
        return {
            "ok": True,
            "shadow_methodology_version": "graen-forward-shadow-v1",
            "activation": activation,
            "state": next(
                (
                    event
                    for event in events
                    if event["event_type"] == "graen_candidate_shadow_state"
                ),
                None,
            ),
            "checkpoint": next(
                (
                    event
                    for event in events
                    if event["event_type"]
                    == "graen_candidate_shadow_checkpoint"
                ),
                None,
            ),
            "recent_events": [
                event
                for event in events
                if event["event_type"]
                in {
                    "graen_candidate_shadow_opportunity",
                    "graen_candidate_shadow_entry",
                    "graen_candidate_shadow_exit",
                    "graen_candidate_shadow_checkpoint",
                }
            ][:200],
        }

    def _promotion_evidence(self) -> dict[str, Any]:
        complete = []
        for event in self._event_rows(
            event_types={"candidate_forward_outcome"},
            limit=50000,
            newest_first=True,
        ):
            payload = event.get("payload") or {}
            if payload.get("status") != "complete":
                continue
            lane = str(payload.get("market_lane") or "").lower()
            version = str(
                payload.get("strategy_version_id")
                or event.get("strategy_version_id")
                or ""
            ).upper()
            if lane == "crypto" or version.startswith("CRYPTO-"):
                complete.append(event)

        ids: set[str] = set()
        hours: set[int] = set()
        weekdays: set[int] = set()
        pairs: set[str] = set()
        observed: list[datetime] = []
        mfes: list[float] = []
        maes: list[float] = []
        for event in complete:
            payload = event.get("payload") or {}
            identity = str(
                payload.get("candidate_id")
                or payload.get("candidate_key")
                or ""
            )
            if identity:
                ids.add(identity)
            raw_stamp = (
                (payload.get("details") or {}).get("reference_effective_at")
                or payload.get("candidate_observed_at")
                or event.get("occurred_at")
            )
            try:
                stamp = datetime.fromisoformat(
                    str(raw_stamp).replace("Z", "+00:00")
                )
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                observed.append(stamp)
                hours.add(stamp.hour)
                weekdays.add(stamp.weekday())
            except (TypeError, ValueError):
                pass
            symbol = str(
                event.get("symbol") or payload.get("symbol") or ""
            ).upper()
            if symbol:
                pairs.add(symbol)
            for key, target in (
                ("max_favorable_return", mfes),
                ("max_adverse_return", maes),
            ):
                try:
                    target.append(float(payload[key]))
                except (KeyError, TypeError, ValueError):
                    pass

        return {
            "methodology_version": "rhen-core-v3-crypto-promotion",
            "market_lane": "crypto",
            "resolved_candidate_predictions": len(ids),
            "paper_round_trips": 0,
            "paper_round_trip_source": "crypto_execution_disabled",
            "utc_hours_covered": sorted(hours),
            "weekdays_covered": sorted(weekdays),
            "volatility_regimes": [],
            "liquidity_regimes": [],
            "pairs_covered": sorted(pairs),
            "coverage_first_observed_at": (
                min(observed).isoformat() if observed else None
            ),
            "coverage_last_observed_at": (
                max(observed).isoformat() if observed else None
            ),
            "metrics": {
                "net_expectancy_after_costs": None,
                "brier_score": None,
                "log_loss": None,
                "calibration_intercept": None,
                "calibration_slope": None,
                "discrimination": None,
                "max_drawdown": None,
                "tail_loss": None,
                "mfe": sum(mfes) / len(mfes) if mfes else None,
                "mae": sum(maes) / len(maes) if maes else None,
                "slippage": None,
                "spread_sensitivity": None,
                "regime_stability": None,
                "time_of_week_stability": None,
            },
            "net_expectancy_positive_after_high_costs": False,
            "walk_forward_passed": False,
            "holdout_passed": False,
            "dependence_adjusted": False,
            "multiplicity_adjusted": False,
            "no_lookahead_verified": False,
            "source": "rhen-core:candidate_forward_outcome",
            "execution_authority": False,
        }

    def _candidate_report_rows(
        self,
        session: str,
        *,
        crypto: bool | None,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """select * from candidates
                order by observed_at desc limit 10000"""
            ).fetchall()
            outcome_rows = conn.execute(
                """select payload_json,occurred_at from events
                where event_type='candidate_forward_outcome'
                order by occurred_at asc limit 50000"""
            ).fetchall()
        outcomes: dict[str, dict[str, dict[str, Any]]] = {}
        for row in outcome_rows:
            payload = _loads(row["payload_json"], {})
            identity = str(
                payload.get("candidate_id")
                or payload.get("candidate_key")
                or ""
            )
            horizon = str(payload.get("horizon_minutes") or "")
            if not identity or not horizon:
                continue
            outcomes.setdefault(identity, {})[horizon] = {
                "status": payload.get("status"),
                "computed_at": payload.get("computed_at")
                or row["occurred_at"],
                "forward_return": payload.get("forward_return"),
                "max_favorable_return": payload.get(
                    "max_favorable_return"
                ),
                "max_adverse_return": payload.get(
                    "max_adverse_return"
                ),
                "methodology_version": payload.get(
                    "methodology_version"
                ),
            }

        result = []
        for row in rows:
            if self._session_date(row["observed_at"]) != session:
                continue
            lane = str(row["market_lane"] or "").lower()
            is_crypto = lane == "crypto" or str(
                row["strategy_version_id"] or ""
            ).upper().startswith("CRYPTO-")
            if crypto is True and not is_crypto:
                continue
            if crypto is False and is_crypto:
                continue
            identity = str(row["candidate_key"])
            features = _loads(row["feature_json"], {})
            result.append(
                {
                    "candidate_id": identity,
                    "candidate_key": identity,
                    "symbol": row["symbol"],
                    "observed_at": row["observed_at"],
                    "market_lane": row["market_lane"],
                    "strategy_version_id": row["strategy_version_id"],
                    "action": row["action"],
                    "qualified": bool(row["qualified"]),
                    "final_decision": row["final_decision"],
                    "reason": row["reason"],
                    "decision_reference_price": row["reference_price"],
                    "stop_price": row["stop_price"],
                    "target_price": row["target_price"],
                    "features": features,
                    "research_attribution": {},
                    "scan_cycle": {
                        "scan_cycle_id": row["cycle_key"],
                        "cycle_key": row["cycle_key"],
                        "run_id": row["run_id"],
                        "strategy_version_id": row["strategy_version_id"],
                        "observed_at": row["observed_at"],
                        "data_status": "compact_core_v3",
                    },
                    "forward_outcomes": outcomes.get(identity, {}),
                }
            )
        return result

    def _weekly_inputs(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("invalid_period") from exc
        if start > end:
            raise ValueError("invalid_period")

        recent = self._event_rows(limit=50000, newest_first=False)
        events = []
        for event in recent:
            session = self._session_date(str(event.get("occurred_at") or ""))
            if session is None:
                continue
            parsed = date.fromisoformat(session)
            if start <= parsed <= end:
                events.append(event)

        by_type: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            by_type.setdefault(event["event_type"], []).append(event)

        daily_reports = list(by_type.get("research_daily_report", []))
        daily_reports.sort(
            key=lambda row: str((row.get("payload") or {}).get("session") or "")
        )
        sessions = sorted(
            {
                str((row.get("payload") or {}).get("session"))
                for row in daily_reports
                if (row.get("payload") or {}).get("session")
            }
        )

        def counts(event_type: str, name: str) -> list[dict[str, Any]]:
            grouped: dict[str, int] = {}
            for event in by_type.get(event_type, []):
                session = self._session_date(
                    str(event.get("occurred_at") or "")
                ) or "unknown"
                grouped[session] = grouped.get(session, 0) + 1
            return [
                {"session": session, name: value}
                for session, value in sorted(grouped.items())
            ]

        order_intents = counts("order_intent", "order_intents")
        intent_entries: dict[str, int] = {}
        for event in by_type.get("order_intent", []):
            session = self._session_date(
                str(event.get("occurred_at") or "")
            ) or "unknown"
            intent = (event.get("payload") or {}).get("intent") or {}
            if str(intent.get("side") or "").lower() == "buy":
                intent_entries[session] = intent_entries.get(session, 0) + 1
        order_intents_by_session = [
            {
                **row,
                "entry_intents": intent_entries.get(
                    str(row.get("session") or ""),
                    0,
                ),
            }
            for row in order_intents
        ]

        with self.connect() as conn:
            candidate_rows = conn.execute(
                """select * from candidates
                order by observed_at asc limit 50000"""
            ).fetchall()
        candidate_grouped: dict[str, dict[str, int]] = {}
        rejection_grouped: dict[tuple[str, str], int] = {}
        for row in candidate_rows:
            session = self._session_date(str(row["observed_at"] or ""))
            if session is None:
                continue
            parsed = date.fromisoformat(session)
            if not (start <= parsed <= end):
                continue
            lane = str(row["market_lane"] or "").lower()
            if lane == "crypto":
                continue
            bucket = candidate_grouped.setdefault(
                session,
                {
                    "evaluated": 0,
                    "qualified": 0,
                    "rejected": 0,
                    "signals": 0,
                    "partial_backfill": 0,
                },
            )
            bucket["evaluated"] += 1
            if bool(row["qualified"]):
                bucket["qualified"] += 1
            else:
                bucket["rejected"] += 1
                reason = str(row["reason"] or "unspecified")
                key = (session, reason)
                rejection_grouped[key] = rejection_grouped.get(key, 0) + 1
            if str(row["action"] or "").lower() not in {"", "hold", "none"}:
                bucket["signals"] += 1

        candidate_by_session = [
            {"session": session, **values}
            for session, values in sorted(candidate_grouped.items())
        ]
        rejection_reasons = [
            {"session": session, "reason": reason, "count": count}
            for (session, reason), count in sorted(rejection_grouped.items())
        ]

        equity_grouped: dict[str, list[dict[str, Any]]] = {}
        drawdowns: list[float] = []
        for event in by_type.get("account_snapshot", []):
            session = self._session_date(
                str(event.get("occurred_at") or "")
            ) or "unknown"
            equity_grouped.setdefault(session, []).append(event)
            try:
                drawdowns.append(
                    float((event.get("payload") or {}).get("drawdown_pct"))
                )
            except (TypeError, ValueError):
                pass
        account_equity_by_session = []
        for session, rows in sorted(equity_grouped.items()):
            rows.sort(key=lambda row: str(row.get("occurred_at") or ""))
            account_equity_by_session.append(
                {
                    "session": session,
                    "starting_equity": (
                        rows[0].get("payload") or {}
                    ).get("equity"),
                    "ending_equity": (
                        rows[-1].get("payload") or {}
                    ).get("equity"),
                }
            )

        outcome_status: dict[str, int] = {}
        horizon_status: dict[int, dict[str, int]] = {}
        for event in by_type.get("candidate_forward_outcome", []):
            payload = event.get("payload") or {}
            status = str(payload.get("status") or "unknown")
            outcome_status[f"{status}_rows"] = (
                outcome_status.get(f"{status}_rows", 0) + 1
            )
            try:
                horizon = int(payload.get("horizon_minutes"))
            except (TypeError, ValueError):
                continue
            bucket = horizon_status.setdefault(
                horizon,
                {"complete": 0, "pending": 0, "failed": 0},
            )
            if status in bucket:
                bucket[status] += 1
        forward_outcomes_by_horizon = [
            {"horizon_minutes": horizon, **values}
            for horizon, values in sorted(horizon_status.items())
        ]

        live_offline: dict[tuple[str, str], int] = {}
        for event in by_type.get("live_offline_comparison", []):
            payload = event.get("payload") or {}
            session = str(
                payload.get("session")
                or self._session_date(str(event.get("occurred_at") or ""))
                or "unknown"
            )
            state = str(payload.get("match_state") or "UNKNOWN")
            key = (session, state)
            live_offline[key] = live_offline.get(key, 0) + 1

        runtime_instances = [
            {
                "run_id": event.get("run_id"),
                "strategy_version_id": event.get("strategy_version_id"),
                "started_at": event.get("occurred_at"),
                "metadata": event.get("payload") or {},
            }
            for event in by_type.get("runtime_start", [])[-10:]
        ]
        incidents = []
        for event in by_type.get("runtime_error", []):
            payload = event.get("payload") or {}
            incidents.append(
                {
                    "incident_type": "runtime_error",
                    "severity": "error",
                    "session": self._session_date(
                        str(event.get("occurred_at") or "")
                    ),
                    "message": payload.get("message")
                    or payload.get("error")
                    or "runtime_error",
                    "resolved_at": None,
                }
            )
        for event in by_type.get("reconciliation", []):
            payload = event.get("payload") or {}
            if payload.get("safe_to_enter") is False:
                incidents.append(
                    {
                        "incident_type": "reconciliation_mismatch",
                        "severity": "error",
                        "session": self._session_date(
                            str(event.get("occurred_at") or "")
                        ),
                        "message": payload.get("reason")
                        or "reconciliation_mismatch",
                        "resolved_at": None,
                    }
                )

        strategy_versions = sorted(
            {
                str(event.get("strategy_version_id"))
                for event in events
                if event.get("strategy_version_id")
            }
        )
        run_ids = sorted(
            {
                str(event.get("run_id"))
                for event in events
                if event.get("run_id")
            }
        )
        data_cutoff = max(
            (str(event.get("ingested_at") or "") for event in events),
            default=None,
        )
        return {
            "daily_reports": daily_reports,
            "earliest_daily_session": sessions[0] if sessions else None,
            "data_cutoff": data_cutoff,
            "strategy_versions": [
                {"version_id": value} for value in strategy_versions
            ],
            "runs": [{"run_id": value} for value in run_ids],
            "runtime_instances": runtime_instances,
            "account_equity_by_session": account_equity_by_session,
            "account_weekly_drawdown": {
                "max_drawdown_pct": max(drawdowns) if drawdowns else None
            },
            "orders_by_session": counts("broker_order", "orders"),
            "order_intents_by_session": order_intents_by_session,
            "fills_by_session": counts("broker_fill", "fills"),
            "candidate_by_session": candidate_by_session,
            "positions": [],
            "incidents": incidents,
            "forward_outcome_status": outcome_status,
            "forward_outcomes": [],
            "forward_outcomes_by_horizon": forward_outcomes_by_horizon,
            "rejection_reasons": rejection_reasons,
            "gate_rates": [],
            "operational_by_session": [],
            "canonical_period_summary": {},
            "live_offline": [],
            "live_offline_summary": [
                {
                    "session": session,
                    "match_state": state,
                    "count": count,
                }
                for (session, state), count in sorted(live_offline.items())
            ],
            "duplicate_checks": {},
            "warnings": [
                "RHEN Core v3 weekly inputs are derived from bounded SQLite "
                "events and normalized candidates; deep legacy warehouse "
                "projections are intentionally not recreated."
            ],
        }

    def report_read(self, params: dict[str, str]) -> dict[str, Any]:
        latest = params.get("latest")
        start_date = params.get("start")
        end_date = params.get("end")
        if start_date or end_date:
            if not start_date or not end_date:
                return {"ok": False, "error": "invalid_period"}
            try:
                inputs = self._weekly_inputs(
                    start_date=start_date,
                    end_date=end_date,
                )
            except ValueError:
                return {"ok": False, "error": "invalid_period"}
            return {
                "ok": True,
                "report_version": "rhen-weekly-v1.2",
                "inputs": inputs,
            }
        if latest == "graen_shadow":
            return self._shadow_report(params.get("shadow_candidate_id"))

        if str(params.get("crypto_promotion") or "") in {
            "1", "true", "True"
        }:
            return {"ok": True, "evidence": self._promotion_evidence()}

        crypto_session = params.get("crypto_evidence_session")
        if crypto_session:
            rows = self._candidate_report_rows(
                crypto_session,
                crypto=True,
            )
            rows = [
                row
                for row in rows
                if sum(
                    1
                    for outcome in row["forward_outcomes"].values()
                    if outcome.get("status") == "complete"
                )
                < 7
            ][:5000]
            return {
                "ok": True,
                "evidence_version": "rhen-crypto-forward-evidence-v2",
                "evidence_session": crypto_session,
                "candidates": rows,
            }

        evidence_session = params.get("evidence_session")
        if evidence_session:
            rows = self._candidate_report_rows(
                evidence_session,
                crypto=False,
            )
            equity_rows = []
            for row in rows[:5000]:
                copy = dict(row)
                by_horizon = copy.pop("forward_outcomes", {})
                copy["outcomes"] = [
                    {
                        "horizon_minutes": int(h)
                        if str(h).isdigit() else h,
                        **value,
                    }
                    for h, value in sorted(
                        by_horizon.items(),
                        key=lambda item: (
                            int(item[0])
                            if str(item[0]).isdigit()
                            else 999999
                        ),
                    )
                ]
                equity_rows.append(copy)
            daily = self._latest_report_event(
                "research_daily_report",
                field="session",
                requested=evidence_session,
            )
            return {
                "ok": True,
                "evidence_session": evidence_session,
                "candidates": equity_rows,
                "post_event": {
                    "source": "rhen-core",
                    "analytics_only": True,
                },
                "ads002": {},
                "ads002_v2": {},
                "latest_daily_report": daily,
            }

        post_session = params.get("post_event_evidence_session")
        if post_session:
            rows = self._candidate_report_rows(
                post_session,
                crypto=False,
            )
            return {
                "ok": True,
                "evidence_version": "rhen-post-event-candidates-v2",
                "evidence_session": post_session,
                "candidates": rows[:5000],
                "complete_horizons": {
                    row["candidate_id"]: sorted(
                        int(h)
                        for h, value in row["forward_outcomes"].items()
                        if str(h).isdigit()
                        and value.get("status") == "complete"
                    )
                    for row in rows
                },
                "post_event_complete": False,
                "post_event": {
                    "source": "rhen-core",
                    "analytics_only": True,
                    "forward_outcomes_loaded": False,
                    "forward_outcome_payloads_loaded": False,
                    "daily_report_loaded": False,
                    "resumable": True,
                },
            }

        if latest == "daily":
            session = params.get("session")
            row = self._latest_report_event(
                "research_daily_report",
                field="session",
                requested=session,
            )
            payload = row["payload"] if row else None
            return {
                "ok": True,
                "report_version": (payload or {}).get("report_version"),
                "report": payload,
            }

        if latest == "weekly":
            week_end = params.get("week_end")
            row = self._latest_report_event(
                "research_weekly_report",
                field="week_end",
                requested=week_end,
            )
            return {
                "ok": True,
                "report_version": "rhen-weekly-v1.2",
                "report": row["payload"] if row else None,
            }

        if latest == "command":
            daily = self._latest_report_event(
                "research_daily_report",
                field="session",
            )
            weekly = self._latest_report_event(
                "research_weekly_report",
                field="week_end",
            )
            recent = self._event_rows(limit=2000, newest_first=True)
            return {
                "ok": True,
                "evidence_version": "rhen-command-evidence-v3",
                "generated_at": _iso(),
                "latest_daily": daily["payload"] if daily else None,
                "latest_weekly": weekly["payload"] if weekly else None,
                "research_questions": [],
                "weekly_decisions": [],
                "research_decisions": [],
                "post_event_evidence": {
                    "analytics_only": True,
                    "source": "rhen-core",
                },
                "provenance": {
                    "runtime": next(
                        (
                            event for event in recent
                            if event["event_type"] == "runtime_start"
                        ),
                        None,
                    ),
                    "latest_scan_cycle": next(
                        (
                            event for event in recent
                            if event["event_type"] == "decision_cycle"
                        ),
                        None,
                    ),
                },
                "telemetry_health": {
                    "events_observed": len(recent),
                    "latest_event_at": (
                        recent[0]["occurred_at"] if recent else None
                    ),
                    "canonical_store": "rhen-core-sqlite",
                },
            }
        return {"ok": False, "error": "invalid_request"}

    @staticmethod
    def _reconciliation_result(
        *,
        unresolved_intents: list[dict[str, Any]],
        unknown_open_orders: list[dict[str, Any]],
        untracked_positions: list[dict[str, Any]],
        observed_at: datetime,
    ) -> dict[str, Any]:
        blockers = []
        if unresolved_intents:
            blockers.append(
                f"unresolved_intents:{len(unresolved_intents)}"
            )
        if unknown_open_orders:
            blockers.append(
                f"unknown_open_orders:{len(unknown_open_orders)}"
            )
        if untracked_positions:
            blockers.append(
                f"untracked_positions:{len(untracked_positions)}"
            )
        return {
            "safe_to_enter": not blockers,
            "reason": "reconciled" if not blockers else ",".join(blockers),
            "observed_at": observed_at.astimezone(UTC).isoformat(),
            "unresolved_intents": unresolved_intents,
            "unknown_open_orders": unknown_open_orders,
            "untracked_positions": untracked_positions,
        }

    def reconcile(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action") or "")
        if action == "resolve_intent":
            intent = dict(body.get("intent") or {})
            run_id = str(intent.get("run_id") or "")
            client_order_id = str(intent.get("client_order_id") or "")
            if (
                not run_id
                or not client_order_id
                or intent.get("state") != "broker_not_found"
            ):
                raise ValueError("invalid_intent_resolution")
            checked = _iso(intent.get("checked_at"))
            event = {
                "event_key": (
                    f"{run_id}:intent-reconcile:"
                    f"{client_order_id}:broker_not_found"
                ),
                "run_id": run_id,
                "event_type": "intent_reconciliation",
                "occurred_at": checked,
                "correlation_id": intent.get("correlation_id"),
                "source": "rhen-core",
                "payload": {
                    "client_order_id": client_order_id,
                    "state": "broker_not_found",
                },
            }
            result = self.ingest_events([event])
            return {
                "ok": True,
                "updated": int(result.get("inserted") or 0),
            }

        if action != "reconcile":
            raise ValueError("unsupported_action")
        data = dict(body.get("reconcile") or {})
        run_id = str(data.get("run_id") or "")
        strategy_version_id = str(
            data.get("strategy_version_id") or ""
        )
        managed_symbols = [
            str(value).upper()
            for value in data.get("managed_symbols") or []
            if str(value).strip()
        ]
        broker_positions = data.get("broker_positions") or []
        open_orders = data.get("open_orders") or []
        if (
            not run_id
            or not strategy_version_id
            or not isinstance(broker_positions, list)
            or not isinstance(open_orders, list)
        ):
            raise ValueError("invalid_reconciliation")
        try:
            observed_at = datetime.fromisoformat(
                str(data.get("observed_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("invalid_reconciliation") from exc
        if observed_at.tzinfo is None:
            raise ValueError("invalid_reconciliation")

        events = [
            event
            for event in self._event_rows(
                event_types={
                    "order_intent",
                    "broker_order",
                    "intent_reconciliation",
                },
                limit=50000,
                newest_first=False,
            )
            if str(event.get("run_id") or "") == run_id
        ]
        known_order_ids: set[str] = set()
        resolved_intents: set[str] = set()
        filled_buy_symbols: set[str] = set()
        order_intents: list[dict[str, Any]] = []
        for event in events:
            payload = event.get("payload") or {}
            if event["event_type"] == "broker_order":
                order = payload.get("order") or {}
                client_id = str(order.get("client_order_id") or "")
                if client_id:
                    known_order_ids.add(client_id)
                    resolved_intents.add(client_id)
                try:
                    filled = float(order.get("filled_qty") or 0)
                except (TypeError, ValueError):
                    filled = 0.0
                if (
                    str(order.get("side") or "").lower() == "buy"
                    and filled > 0
                ):
                    filled_buy_symbols.add(
                        str(order.get("symbol") or "").upper()
                    )
            elif event["event_type"] == "intent_reconciliation":
                if payload.get("state") == "broker_not_found":
                    resolved_intents.add(
                        str(payload.get("client_order_id") or "")
                    )
            elif event["event_type"] == "order_intent":
                intent = payload.get("intent") or {}
                client_id = str(
                    intent.get("idempotency_key")
                    or (intent.get("payload") or {}).get(
                        "client_order_id"
                    )
                    or ""
                )
                if client_id:
                    order_intents.append(
                        {
                            "intent_id": intent.get("intent_id"),
                            "client_order_id": client_id,
                            "symbol": intent.get("symbol"),
                            "side": intent.get("side"),
                            "intended_at": intent.get("intended_at")
                            or event.get("occurred_at"),
                        }
                    )

        unresolved = [
            row
            for row in order_intents
            if row["client_order_id"] not in resolved_intents
        ]
        managed = set(managed_symbols)
        positive_managed_symbols: set[str] = set()
        for position in broker_positions:
            symbol = str(position.get("symbol") or "").upper()
            try:
                qty = float(position.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0 and (not managed or symbol in managed):
                positive_managed_symbols.add(symbol)

        unknown_orders = []
        for order in open_orders:
            client_id = str(order.get("client_order_id") or "")
            if (
                not client_id.startswith("anevum-")
                or client_id in known_order_ids
            ):
                continue
            symbol = str(order.get("symbol") or "").upper()
            # Standing hard stops are broker-side protection, not new exposure.
            expected_protective_stop = (
                symbol in positive_managed_symbols
                and symbol in filled_buy_symbols
                and str(order.get("side") or "").lower() == "sell"
                and client_id.startswith(
                    f"anevum-{symbol.lower()}-hardstop-"
                )
            )
            if expected_protective_stop:
                continue
            unknown_orders.append(
                {
                    "id": order.get("id"),
                    "client_order_id": client_id,
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "status": order.get("status"),
                }
            )

        untracked = []
        for position in broker_positions:
            symbol = str(position.get("symbol") or "").upper()
            try:
                qty = float(position.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0 or (managed and symbol not in managed):
                continue
            if symbol not in filled_buy_symbols:
                untracked.append(
                    {
                        "symbol": symbol,
                        "qty": position.get("qty"),
                        "market_value": position.get("market_value"),
                    }
                )

        result = self._reconciliation_result(
            unresolved_intents=unresolved,
            unknown_open_orders=unknown_orders,
            untracked_positions=untracked,
            observed_at=observed_at,
        )
        self.ingest_events(
            [
                {
                    "event_key": (
                        f"{run_id}:reconciliation:"
                        f"{observed_at.astimezone(UTC).isoformat()}"
                    ),
                    "run_id": run_id,
                    "strategy_version_id": strategy_version_id,
                    "event_type": "reconciliation",
                    "occurred_at": observed_at.isoformat(),
                    "source": "rhen-core",
                    "payload": {
                        **result,
                        "managed_symbols": managed_symbols,
                    },
                }
            ]
        )
        return {"ok": True, "result": result}
