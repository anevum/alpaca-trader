from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

UTC = timezone.utc

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
