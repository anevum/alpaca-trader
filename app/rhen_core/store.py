from __future__ import annotations

import base64
import zlib
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")

CRITICAL_EVENT_TYPES = {
    "order_intent", "broker_order", "broker_fill", "order_update",
    "position_opened", "position_closed", "reconciliation",
    "runtime_error", "strategy_promotion",
    # The scheduler depends on these durable reports. Storage pressure may
    # shed high-volume observations, but must not silently discard its inputs.
    "research_daily_report", "research_weekly_report", "extended_research_snapshot",
}


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


MAX_PACKED_SCAN_BYTES = 2 * 1024 * 1024


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        result = json.loads(value)
        if isinstance(result, dict) and result.get("_rhen_payload_codec") == "zlib-json-v1":
            size = int(result["raw_bytes"])
            if not 0 <= size <= MAX_PACKED_SCAN_BYTES:
                return default
            decoder = zlib.decompressobj()
            raw = decoder.decompress(base64.b64decode(result["data"], validate=True), MAX_PACKED_SCAN_BYTES + 1)
            if not decoder.eof or decoder.unused_data or len(raw) != size:
                return default
            return json.loads(raw)
        return result
    except Exception:
        return default


def _pack_scan_json(raw: str) -> str:
    data = raw.encode("utf-8")
    if not 1024 <= len(data) <= MAX_PACKED_SCAN_BYTES:
        return raw
    packed = _json({"_rhen_payload_codec": "zlib-json-v1", "raw_bytes": len(data),
                    "data": base64.b64encode(zlib.compress(data, level=1)).decode("ascii")})
    return packed if len(packed) < len(raw) else raw


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

def _retired_research_domain(value: Any) -> bool:
    normalized = str(value or "").strip().upper()
    return "CRYPTO" in normalized or "BTC" in normalized



class RhenCoreStore:
    """Single-writer bounded RHEN Core state store."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = str(path or os.getenv("RHEN_CORE_DB_PATH", "/data/rhen-core.db"))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.warning_bytes = int(os.getenv("RHEN_CORE_STORAGE_WARNING_MB", "500")) * 1024 * 1024
        self.shed_bytes = int(os.getenv("RHEN_CORE_STORAGE_SHED_MB", "750")) * 1024 * 1024
        self.maintenance_interval_seconds = max(
            900,
            int(os.getenv("RHEN_CORE_MAINTENANCE_SECONDS", "21600")),
        )
        self._last_maintenance_monotonic = 0.0
        self._maintenance_error: str | None = None
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma synchronous=NORMAL")
        conn.execute("pragma foreign_keys=ON")
        conn.execute("pragma busy_timeout=15000")
        conn.execute("pragma wal_autocheckpoint=1000")
        conn.execute("pragma journal_size_limit=16777216")
        return conn

    def _initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("pragma auto_vacuum=INCREMENTAL")
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
                create index if not exists events_time on events(occurred_at desc);
                create index if not exists events_run_type_time on events(run_id, event_type, occurred_at desc);

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
        self._startup_maintenance()

    def file_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def storage_state(self) -> dict[str, Any]:
        size = self.file_size_bytes()
        page_count = freelist_count = page_size = auto_vacuum = 0
        try:
            with self.connect() as conn:
                page_count = int(conn.execute("pragma page_count").fetchone()[0] or 0)
                freelist_count = int(
                    conn.execute("pragma freelist_count").fetchone()[0] or 0
                )
                page_size = int(conn.execute("pragma page_size").fetchone()[0] or 0)
                auto_vacuum = int(
                    conn.execute("pragma auto_vacuum").fetchone()[0] or 0
                )
        except sqlite3.DatabaseError:
            pass
        allocated_db_bytes = page_count * page_size
        reclaimable_db_bytes = freelist_count * page_size
        fragmentation_pct = (
            round((reclaimable_db_bytes / allocated_db_bytes) * 100.0, 3)
            if allocated_db_bytes > 0
            else 0.0
        )
        # SQLite freelist pages are physically allocated but immediately
        # reusable by future writes. Base telemetry shedding on effective live
        # bytes so reusable pages do not trigger a false storage emergency.
        reusable_bytes = min(max(reclaimable_db_bytes, 0), max(size, 0))
        effective_bytes = max(size - reusable_bytes, 0)
        return {
            "bytes": size,
            "mb": round(size / 1024 / 1024, 3),
            "effective_bytes": effective_bytes,
            "effective_mb": round(effective_bytes / 1024 / 1024, 3),
            "reusable_bytes": reusable_bytes,
            "reusable_mb": round(reusable_bytes / 1024 / 1024, 3),
            "warning": effective_bytes >= self.warning_bytes,
            "physical_warning": size >= self.warning_bytes,
            "analytics_shedding": effective_bytes >= self.shed_bytes,
            "shed_basis": "effective_used_bytes",
            "warning_mb": self.warning_bytes // 1024 // 1024,
            "shed_mb": self.shed_bytes // 1024 // 1024,
            "allocated_db_bytes": allocated_db_bytes,
            "reclaimable_db_bytes": reclaimable_db_bytes,
            "fragmentation_pct": fragmentation_pct,
            "auto_vacuum_mode": auto_vacuum,
            "maintenance_error": self._maintenance_error,
        }

    @staticmethod
    def compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        features = candidate.get("features") or {}
        quality = features.get("market_quality") or {}
        compact_features = {
            k: features.get(k)
            for k in (
                "market", "momentum_pct", "vwap_edge_pct", "relative_volume_ratio",
                "trend_persistence", "quality_score", "current_close", "bar_time",
                "evidence_reference_only", "warmup_bar_count", "required_bar_count",
                "confirmation_passes", "regime_passes",
                "opportunity_score", "estimated_net_edge_pct",
                "expected_gross_move_pct", "return_5m", "return_15m",
                "return_60m", "range_60m_pct",
            )
            if features.get(k) is not None
        }
        if quality:
            compact_features["market_quality"] = {
                k: quality.get(k)
                for k in ("spread_pct", "bar_age_seconds", "quote_age_seconds")
                if quality.get(k) is not None
            }
        shadow = candidate.get("shadow_economics")
        if isinstance(shadow, dict):
            estimate = shadow.get("estimate")
            estimate = estimate if isinstance(estimate, dict) else {}
            admission = shadow.get("shadow_admission")
            admission = admission if isinstance(admission, dict) else {}
            compact_features["shadow_economics"] = {
                "methodology_version": shadow.get("methodology_version"),
                "research_only": shadow.get("research_only") is True,
                "execution_authority": shadow.get("execution_authority") is True,
                "expected_gross_bps": estimate.get("expected_gross_bps"),
                "expected_net_bps": estimate.get("expected_net_bps"),
                "gross_to_cost_ratio": estimate.get("gross_to_cost_ratio"),
                "confidence": estimate.get("confidence"),
                "would_admit": admission.get("would_admit"),
                "reason": admission.get("reason"),
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
        def numeric(value: Any) -> float | None:
            if value in (None, "") or isinstance(value, bool):
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if number == number else None

        opportunity_scores: list[float] = []
        net_edges: list[float] = []
        expected_moves: list[float] = []
        shadow_net_edges: list[float] = []
        shadow_admit_count = 0
        shadow_candidate_count = 0
        shadow_methodology_version: str | None = None
        forward_measurement_ready_count = 0
        forward_missing_reference_price_count = 0
        forward_missing_bar_time_count = 0
        evidence_reference_only_count = 0
        for candidate in candidates:
            features = candidate.get("features")
            features = features if isinstance(features, dict) else {}
            reference_price = numeric(candidate.get("decision_reference_price"))
            has_reference_price = (
                reference_price is not None and reference_price > 0
            )
            has_bar_time = bool(str(features.get("bar_time") or "").strip())
            if not has_reference_price:
                forward_missing_reference_price_count += 1
            if not has_bar_time:
                forward_missing_bar_time_count += 1
            if has_reference_price and has_bar_time:
                forward_measurement_ready_count += 1
            if features.get("evidence_reference_only") is True:
                evidence_reference_only_count += 1

            score = numeric(features.get("opportunity_score"))
            if score is not None:
                opportunity_scores.append(score)
            edge = numeric(features.get("estimated_net_edge_pct"))
            if edge is None:
                cost_model = features.get("cost_model")
                if isinstance(cost_model, dict):
                    edge = numeric(cost_model.get("estimated_net_edge_pct"))
            if edge is not None:
                net_edges.append(edge)
            expected = numeric(features.get("expected_gross_move_pct"))
            if expected is not None:
                expected_moves.append(expected)

            shadow = candidate.get("shadow_economics")
            if isinstance(shadow, dict):
                shadow_candidate_count += 1
                shadow_methodology_version = (
                    str(shadow.get("methodology_version") or "")
                    or shadow_methodology_version
                )
                estimate = shadow.get("estimate")
                estimate = estimate if isinstance(estimate, dict) else {}
                net_bps = numeric(estimate.get("expected_net_bps"))
                if net_bps is not None:
                    shadow_net_edges.append(net_bps)
                admission = shadow.get("shadow_admission")
                admission = admission if isinstance(admission, dict) else {}
                if admission.get("would_admit") is True:
                    shadow_admit_count += 1
        if candidates:
            summary["forward_measurement_ready_count"] = (
                forward_measurement_ready_count
            )
            summary["forward_measurement_ready_rate_pct"] = round(
                forward_measurement_ready_count / len(candidates) * 100.0,
                4,
            )
            summary["forward_missing_reference_price_count"] = (
                forward_missing_reference_price_count
            )
            summary["forward_missing_bar_time_count"] = (
                forward_missing_bar_time_count
            )
            summary["evidence_reference_only_count"] = (
                evidence_reference_only_count
            )

        if opportunity_scores:
            summary["top_opportunity_score"] = max(opportunity_scores)
        if net_edges:
            summary["best_estimated_net_edge_pct"] = max(net_edges)
        if expected_moves:
            summary["best_expected_gross_move_pct"] = max(expected_moves)
        if shadow_candidate_count:
            summary["shadow_economics_candidate_count"] = shadow_candidate_count
            summary["shadow_economics_admit_count"] = shadow_admit_count
            summary["shadow_economics_admit_rate_pct"] = round(
                shadow_admit_count / shadow_candidate_count * 100.0,
                4,
            )
            if shadow_net_edges:
                summary["shadow_economics_mean_net_bps"] = round(
                    sum(shadow_net_edges) / len(shadow_net_edges),
                    6,
                )
                summary["shadow_economics_best_net_bps"] = round(
                    max(shadow_net_edges),
                    6,
                )
            if shadow_methodology_version:
                summary["shadow_economics_methodology_version"] = (
                    shadow_methodology_version
                )

        runtime = payload.get("runtime") or {}
        summary["runtime"] = {
            k: runtime.get(k)
            for k in ("runtime_instance_id", "deployment_id", "git_commit", "market")
            if runtime.get(k) is not None
        }
        return summary, sampled

    def ingest_events(self, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
        self._prune_if_due()
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
                        event.get("source"),
                        _pack_scan_json(_json(payload)) if event_type == "scan" else _json(payload),
                        int(critical), now,
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
        storage = self.storage_state()
        pressure = bool(storage.get("warning"))
        cuts = {
            "events": (
                now - timedelta(days=10 if pressure else 14)
            ).isoformat(),
            "cycles": (
                now - timedelta(days=3 if pressure else 7)
            ).isoformat(),
            "positions": (
                now - timedelta(days=3 if pressure else 7)
            ).isoformat(),
            "candidates": (
                now - timedelta(days=3 if pressure else 7)
            ).isoformat(),
            "nostra": (
                now - timedelta(days=10 if pressure else 14)
            ).isoformat(),
            "scheduler": (now - timedelta(days=30)).isoformat(),
        }
        deleted: dict[str, int] = {}
        with self._lock, self.connect() as conn:
            # Bounded derived checkpoints, distinct from permanent execution truth.
            cur = conn.execute(
                "delete from events where event_type='extended_research_snapshot' and occurred_at < ?",
                ((now - timedelta(days=35)).isoformat(),),
            )
            deleted["extended_research_snapshots"] = cur.rowcount
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
            cur = conn.execute(
                """delete from events
                where critical=0
                  and event_type='candidate_forward_outcome'
                  and payload_json not like '%"status":"complete"%'"""
            )
            deleted["incomplete_forward_outcomes"] = cur.rowcount
            cur = conn.execute(
                """delete from events
                where critical=0
                  and event_type='live_offline_comparison'
                  and occurred_at < ?""",
                (cuts["candidates"],),
            )
            deleted["live_offline_comparisons"] = cur.rowcount
            cur = conn.execute(
                """delete from scheduler_runs
                where status <> 'RUNNING'
                  and coalesce(completed_at, scheduled_at, updated_at) < ?""",
                (cuts["scheduler"],),
            )
            deleted["scheduler_runs"] = cur.rowcount
            cur = conn.execute(
                """delete from research_runs
                where rowid not in (
                    select rowid from research_runs
                    order by created_at desc
                    limit 500
                )"""
            )
            deleted["research_runs"] = cur.rowcount
            cur = conn.execute(
                """delete from research_ledgers
                where rowid not in (
                    select rowid from research_ledgers
                    order by created_at desc
                    limit 1000
                )"""
            )
            deleted["research_ledgers"] = cur.rowcount
            cur = conn.execute(
                """delete from graen_runs
                where status not in ('RUNNING','QUEUED')
                  and rowid not in (
                      select rowid from graen_runs
                      order by started_at desc
                      limit 1000
                  )"""
            )
            deleted["graen_runs"] = cur.rowcount
            cur = conn.execute(
                """delete from graen_artifacts
                where rowid not in (
                    select rowid from graen_artifacts
                    order by created_at desc
                    limit 2000
                )"""
            )
            deleted["graen_artifacts"] = cur.rowcount
            conn.commit()
            conn.execute("pragma wal_checkpoint(TRUNCATE)")
            conn.execute("pragma incremental_vacuum(4096)")
        return deleted

    def _prune_if_due(self) -> None:
        current = time.monotonic()
        if (
            self._last_maintenance_monotonic
            and current - self._last_maintenance_monotonic
            < self.maintenance_interval_seconds
        ):
            return
        self._last_maintenance_monotonic = current
        try:
            self.prune()
            self._maintenance_error = None

        except Exception as exc:
            self._maintenance_error = f"{type(exc).__name__}: {exc}"

    def compact_storage(self, *, force: bool = False) -> dict[str, Any]:
        before = self.storage_state()
        allocated = int(before.get("allocated_db_bytes") or 0)
        reclaimable = int(before.get("reclaimable_db_bytes") or 0)
        page_size = 0
        if allocated > 0:
            try:
                with self.connect() as conn:
                    page_size = int(
                        conn.execute("pragma page_size").fetchone()[0] or 0
                    )
            except sqlite3.DatabaseError:
                page_size = 0
        fragmentation = (
            reclaimable / allocated
            if allocated > 0
            else 0.0
        )
        auto_vacuum_mode = int(before.get("auto_vacuum_mode") or 0)

        # In incremental auto-vacuum mode, reclaim free pages in place before
        # considering a full VACUUM. This avoids requiring a second database-
        # sized temporary file when the persistent volume is already tight.
        incremental_threshold = 8 * 1024 * 1024
        if (
            auto_vacuum_mode == 2
            and reclaimable > 0
            and (force or reclaimable >= incremental_threshold)
        ):
            free_pages = (
                max(1, reclaimable // page_size)
                if page_size > 0
                else 4096
            )
            page_budget = int(
                free_pages if force else min(free_pages, 8192)
            )
            with self._lock, self.connect() as conn:
                conn.execute("pragma wal_checkpoint(TRUNCATE)")
                conn.execute(f"pragma incremental_vacuum({page_budget})")
            after = self.storage_state()
            return {
                "compacted": (
                    int(after.get("bytes") or 0)
                    < int(before.get("bytes") or 0)
                ),
                "reason": "incremental_vacuum_completed",
                "page_budget": page_budget,
                "before": before,
                "after": after,
            }

        should_full_vacuum = force or auto_vacuum_mode != 2 or (
            reclaimable >= 64 * 1024 * 1024
            and fragmentation >= 0.15
        )
        if not should_full_vacuum:
            return {
                "compacted": False,
                "reason": "fragmentation_below_threshold",
                "before": before,
                "after": before,
            }

        with self._lock, self.connect() as conn:
            conn.execute("pragma wal_checkpoint(TRUNCATE)")
            conn.execute("pragma auto_vacuum=INCREMENTAL")
            conn.execute("vacuum")

        after = self.storage_state()
        return {
            "compacted": True,
            "reason": "vacuum_completed",
            "before": before,
            "after": after,
        }

    def compact_scan_payloads(self, limit: int = 40000) -> dict[str, int]:
        """Lossless, bounded migration of raw scan diagnostics only."""
        changed = saved = 0
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "select event_key,payload_json from events where event_type='scan' "
                "and length(payload_json)>=1024 and payload_json not like '{\"_rhen_payload_codec\":%' "
                "order by occurred_at desc limit ?", (max(1, min(limit, 40000)),)
            ).fetchall()
            for row in rows:
                raw = row["payload_json"]
                try:
                    json.loads(raw)
                except ValueError:
                    continue
                packed = _pack_scan_json(raw)
                if packed != raw and _loads(packed, None) == json.loads(raw):
                    conn.execute("update events set payload_json=? where event_key=?", (packed, row["event_key"]))
                    changed += 1
                    saved += len(raw.encode()) - len(packed.encode())
            conn.commit()
        return {"changed": changed, "payload_bytes_saved": saved}

    def _startup_maintenance(self) -> None:
        try:
            self.prune()
            if self.storage_state()["warning"]:
                print(json.dumps({"event": "core_scan_payload_compacted", **self.compact_scan_payloads()}, sort_keys=True), flush=True)
            # Shrinking row payloads leaves free space inside live B-tree pages,
            # which the freelist does not measure. Repack once after codec migration.
            repacked, _ = self.get_kv("maintenance", "scan_codec_repacked_v1", False)
            with self.connect() as conn:
                packed = conn.execute(
                    "select 1 from events where event_type='scan' "
                    "and payload_json like '{\"_rhen_payload_codec\":%' limit 1"
                ).fetchone()
            needs_repack = not repacked and packed is not None
            compacted = self.compact_storage(force=needs_repack)
            if needs_repack and compacted.get("reason") == "vacuum_completed":
                self.set_kv("maintenance", "scan_codec_repacked_v1", {"completed_at": _iso()})
            self._maintenance_error = None
            # Read-only allocation evidence; no payloads or trading records logged.
            with self.connect() as conn:
                rows = conn.execute(
                    "select name, sum(pgsize) as bytes from dbstat "
                    "group by name order by bytes desc limit 12"
                ).fetchall()
                event_sizes = conn.execute(
                    "select event_type, count(*), sum(length(payload_json)) from events "
                    "group by event_type order by sum(length(payload_json)) desc limit 12"
                ).fetchall()
            print(json.dumps({"event": "core_storage_allocation",
                "objects": [{"name": row[0], "bytes": row[1]} for row in rows],
                "event_types": [{"type": row[0], "count": row[1], "payload_bytes": row[2]} for row in event_sizes]},
                sort_keys=True), flush=True)
        except Exception as exc:
            self._maintenance_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._last_maintenance_monotonic = time.monotonic()

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

    def accept_iren_configuration(
        self,
        *,
        expected_revision: int,
        fingerprint: str,
        reviewed_by: str,
    ) -> dict[str, Any]:
        """Accept the exact current protected configuration as IREN's baseline.

        This is a protected operator action. It is revision-fenced and
        fingerprint-bound so a stale Command view cannot silently approve a
        different runtime configuration. The existing incident remains open
        until subsequent IREN observations verify the accepted fingerprint.
        """
        if expected_revision < 0:
            raise ValueError("invalid_expected_revision")
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint.startswith("sha256:") or len(fingerprint) != 71:
            raise ValueError("invalid_configuration_fingerprint")
        reviewed_by = str(reviewed_by or "").strip()[:200] or "operator"

        with self._lock, self.connect() as conn:
            row = conn.execute(
                """select value_json,revision
                   from kv_state
                   where namespace='iren' and key='state'"""
            ).fetchone()
            if not row:
                raise ValueError("iren_state_unavailable")

            state = _loads(row["value_json"], {})
            current_revision = int(row["revision"] or 0)
            if current_revision != expected_revision:
                return {
                    "ok": True,
                    "accepted": False,
                    "conflict": True,
                    "revision": current_revision,
                }
            if not isinstance(state, dict):
                raise ValueError("iren_state_invalid")

            review = state.get("configuration_review")
            current = state.get("configuration_current")
            if not isinstance(review, dict) or str(
                review.get("status") or ""
            ) != "CONFIGURATION_REVIEW_REQUIRED":
                raise ValueError("configuration_review_not_required")
            if not isinstance(current, dict):
                raise ValueError("configuration_current_unavailable")
            if current.get("schema_version") != "rhen_protected_configuration.v2":
                raise ValueError("configuration_snapshot_not_v2")

            current_fingerprint = str(current.get("fingerprint") or "")
            review_fingerprint = str(review.get("current_fingerprint") or "")
            if fingerprint != current_fingerprint or fingerprint != review_fingerprint:
                raise ValueError("configuration_fingerprint_mismatch")

            accepted_at = _iso()
            updated = dict(state)
            updated["configuration_baseline"] = {
                "fingerprint": fingerprint,
                "snapshot": current,
                "observed_at": state.get("observed_at"),
                "basis": "explicit_operator_acceptance",
                "accepted_at": accepted_at,
                "accepted_by": reviewed_by,
            }
            updated["configuration_drift"] = None
            updated["configuration_review"] = {
                **review,
                "status": "ACCEPTED_PENDING_REOBSERVATION",
                "accepted_at": accepted_at,
                "accepted_by": reviewed_by,
            }
            updated["configuration_acceptance"] = {
                "fingerprint": fingerprint,
                "accepted_at": accepted_at,
                "accepted_by": reviewed_by,
            }

            revision = current_revision + 1
            conn.execute(
                """update kv_state
                   set value_json=?, revision=?, updated_at=?
                   where namespace='iren' and key='state' and revision=?""",
                (_json(updated), revision, accepted_at, current_revision),
            )
            if conn.total_changes != 1:
                conn.rollback()
                latest = conn.execute(
                    """select revision from kv_state
                       where namespace='iren' and key='state'"""
                ).fetchone()
                return {
                    "ok": True,
                    "accepted": False,
                    "conflict": True,
                    "revision": int(latest["revision"] if latest else 0),
                }
            conn.commit()
            return {
                "ok": True,
                "accepted": True,
                "conflict": False,
                "revision": revision,
                "fingerprint": fingerprint,
                "accepted_at": accepted_at,
                "state": updated,
            }


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

    def _graen_recover_orphaned_runs(
        self, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Fail closed on stale research claims no worker heartbeat owns."""
        active_problem_id = str(body.get("active_problem_id") or "").strip()
        current_deployment_id = str(body.get("deployment_id") or "").strip()
        stale_seconds = max(
            900,
            min(
                86400,
                int(os.getenv("GRAEN_STALE_RUN_SECONDS", "14400")),
            ),
        )
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        cutoff = now_dt - timedelta(seconds=stale_seconds)
        recovered: list[dict[str, Any]] = []

        def started_at(row: sqlite3.Row) -> datetime:
            try:
                value = datetime.fromisoformat(
                    str(row["started_at"] or "").replace("Z", "+00:00")
                )
            except ValueError:
                value = now_dt
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """select r.run_id,r.problem_id,r.worker_id,r.deployment_id,
                          r.started_at,r.result_json,p.metadata_json
                   from graen_runs r
                   join graen_problems p on p.problem_id=r.problem_id
                   where r.status='RUNNING' and p.status='RUNNING'
                   order by r.started_at asc"""
            ).fetchall()
            protected = {
                str(row["problem_id"])
                for row in rows
                if str(row["problem_id"]) == active_problem_id
                or started_at(row) > cutoff
            }
            stale_by_problem: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                problem_id = str(row["problem_id"])
                if problem_id in protected:
                    continue
                stale_by_problem.setdefault(problem_id, []).append(row)

            for problem_id, stale_rows in stale_by_problem.items():
                run_ids = [str(row["run_id"]) for row in stale_rows]
                previous_deployments = sorted(
                    {
                        str(row["deployment_id"])
                        for row in stale_rows
                        if row["deployment_id"]
                    }
                )
                recovery = {
                    "reason": "stale_executor_run_without_active_heartbeat",
                    "recovered_at": now,
                    "run_ids": run_ids,
                    "previous_deployment_ids": previous_deployments,
                    "current_deployment_id": current_deployment_id or None,
                    "stale_after_seconds": stale_seconds,
                    "execution_authority": False,
                }
                for row in stale_rows:
                    result = _loads(row["result_json"], {})
                    result.update(
                        {
                            "state": "ORPHANED_RUNTIME_RECOVERED",
                            "recovery": recovery,
                        }
                    )
                    conn.execute(
                        """update graen_runs
                           set status='CANCELLED',result_json=?,completed_at=?
                           where run_id=? and status='RUNNING'""",
                        (_json(result), now, row["run_id"]),
                    )

                problem_row = conn.execute(
                    """select metadata_json from graen_problems
                       where problem_id=? and status='RUNNING'""",
                    (problem_id,),
                ).fetchone()
                if problem_row is None:
                    continue
                metadata = _loads(problem_row["metadata_json"], {})
                metadata["stale_run_recovery"] = recovery
                conn.execute(
                    """update graen_problems
                       set status='BLOCKED',metadata_json=?,updated_at=?,
                           completed_at=null
                       where problem_id=? and status='RUNNING'""",
                    (_json(metadata), now, problem_id),
                )
                recovered.append(
                    {
                        "problem_id": problem_id,
                        "run_ids": run_ids,
                        "status": "BLOCKED",
                        "reason": recovery["reason"],
                    }
                )
            conn.commit()
        return recovered

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
            recovered = self._graen_recover_orphaned_runs(body)
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
            return {
                "ok": True,
                "runtime_state": runtime,
                "recovered_stale_runs": recovered,
            }
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
        if action == "research_promotion_claim":
            return self._research_promotion_claim(body)
        if action == "research_promotion_save":
            return self._research_promotion_save(body)
        if action == "claim_adaptive_research_problem":
            return self._graen_claim_adaptive(body)
        raise ValueError("invalid_action")

    @staticmethod
    def _valid_uuid(value: Any, error: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(error) from exc

    def _research_promotion_claim(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = self._valid_uuid(
            body.get("problem_id"), "invalid_promotion_identity"
        )
        owner = self._valid_uuid(
            body.get("owner"), "invalid_promotion_identity"
        )
        now = datetime.now(UTC)
        with self._lock, self.connect() as conn:
            row = conn.execute(
                """select status,metadata_json from graen_problems
                where problem_id=?""",
                (problem_id,),
            ).fetchone()
            if row is None or row["status"] not in {"WAITING", "BLOCKED"}:
                return {"ok": True, "claimed": False}

            metadata = _loads(row["metadata_json"], {})
            state = dict(metadata.get("code_promotion") or {})
            lease = dict(metadata.get("code_promotion_lease") or {})
            if state.get("phase") == "COMPLETE":
                return {"ok": True, "claimed": False}

            until = lease.get("until")
            if until:
                try:
                    lease_until = datetime.fromisoformat(
                        str(until).replace("Z", "+00:00")
                    )
                    if lease_until.tzinfo is None:
                        lease_until = lease_until.replace(tzinfo=UTC)
                    if lease_until.astimezone(UTC) > now:
                        return {"ok": True, "claimed": False}
                except ValueError:
                    pass

            revision = int(metadata.get("code_promotion_revision") or 0)
            prespec = dict(
                state.get("prespec")
                or metadata.get("research_implementation_spec")
                or {}
            )
            exposure: dict[str, Any] = {}
            exposure_id = str(prespec.get("exposure_artifact_id") or "")
            if exposure_id:
                artifact = conn.execute(
                    """select * from graen_artifacts
                    where artifact_id=? and problem_id=?
                      and artifact_type='RESEARCH_CORPUS_EXPOSURE_LEDGER'""",
                    (exposure_id, problem_id),
                ).fetchone()
                if artifact is not None:
                    exposure = {
                        **_loads(artifact["content_json"], {}),
                        "artifact_id": artifact["artifact_id"],
                    }

            runtime_row = conn.execute(
                """select value_json from kv_state
                where namespace='graen' and key='runtime'"""
            ).fetchone()
            executor = (
                _loads(runtime_row["value_json"], {})
                if runtime_row is not None
                else {}
            )
            metadata["code_promotion_lease"] = {
                "owner": owner,
                "until": (now + timedelta(minutes=5)).isoformat(),
            }
            conn.execute(
                """update graen_problems
                set metadata_json=?,updated_at=? where problem_id=?""",
                (_json(metadata), now.isoformat(), problem_id),
            )
            conn.commit()

        return {
            "ok": True,
            "claimed": True,
            "revision": revision,
            "state": state,
            "prespec": prespec,
            "exposure": exposure,
            "prespec_artifact_id": metadata.get("code_prespec_artifact_id"),
            "executor_heartbeat": executor,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    def _research_promotion_save(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        problem_id = self._valid_uuid(
            body.get("problem_id"), "invalid_promotion_state"
        )
        owner = self._valid_uuid(body.get("owner"), "invalid_promotion_state")
        try:
            expected = int(body.get("expected_revision"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_promotion_state") from exc
        state = dict(body.get("state") or {})
        if len(_json(state)) > 100000:
            raise ValueError("invalid_promotion_state")

        now = datetime.now(UTC)
        with self._lock, self.connect() as conn:
            row = conn.execute(
                """select status,metadata_json from graen_problems
                where problem_id=?""",
                (problem_id,),
            ).fetchone()
            if row is None or row["status"] not in {"WAITING", "BLOCKED"}:
                raise ValueError("promotion_problem_not_idle")

            metadata = _loads(row["metadata_json"], {})
            previous = dict(metadata.get("code_promotion") or {})
            lease = dict(metadata.get("code_promotion_lease") or {})
            try:
                lease_until = datetime.fromisoformat(
                    str(lease.get("until") or "").replace("Z", "+00:00")
                )
                if lease_until.tzinfo is None:
                    lease_until = lease_until.replace(tzinfo=UTC)
            except ValueError as exc:
                raise ValueError(
                    "promotion_lease_or_revision_conflict"
                ) from exc

            if (
                str(lease.get("owner")) != owner
                or int(metadata.get("code_promotion_revision") or 0) != expected
                or lease_until.astimezone(UTC) <= now
            ):
                raise ValueError("promotion_lease_or_revision_conflict")

            if previous.get("spec_hash") and (
                previous.get("spec_hash") != state.get("spec_hash")
                or previous.get("prespec") != state.get("prespec")
            ):
                raise ValueError("immutable_prespec_changed")

            prespec_id = metadata.get("code_prespec_artifact_id")
            if state.get("prespec") and not prespec_id:
                spec_hash = str(state.get("spec_hash") or "")
                if state.get("phase") != "BRANCH" or len(spec_hash) != 64:
                    raise ValueError("prespec_must_be_frozen_before_branch")
                content = {
                    "specification": state["prespec"],
                    "specification_hash": spec_hash,
                }
                artifact_key = (
                    f"{problem_id}:research-code-prespec:{spec_hash}"
                )
                existing = conn.execute(
                    """select artifact_id from graen_artifacts
                    where artifact_key=?""",
                    (artifact_key,),
                ).fetchone()
                if existing is None:
                    prespec_id = str(uuid4())
                    conn.execute(
                        """insert into graen_artifacts(
                            artifact_id,artifact_key,problem_id,run_id,
                            artifact_type,methodology_version,source_commit,
                            content_hash,content_json,created_at
                        ) values(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            prespec_id,
                            artifact_key,
                            problem_id,
                            None,
                            "RESEARCH_CODE_FROZEN_PRESPEC",
                            "graen.research-code-promotion.v1",
                            None,
                            _hash(content),
                            _json(content),
                            now.isoformat(),
                        ),
                    )
                else:
                    prespec_id = existing["artifact_id"]

            if state.get("phase") == "COMPLETE":
                if (
                    previous.get("phase") != "RESUME"
                    or state.get("resume_stage")
                    != "STRATEGY_COMPILED_DEVELOPMENT"
                    or not state.get("merge_sha")
                    or not state.get("deployment_id")
                    or not state.get("executor_heartbeat_at")
                    or not isinstance(state.get("ci"), list)
                    or not state.get("ci")
                    or not prespec_id
                ):
                    raise ValueError(
                        "verified_deployment_required_before_resume"
                    )
                active = conn.execute(
                    """select 1 from graen_runs
                    where problem_id=? and status='RUNNING' limit 1""",
                    (problem_id,),
                ).fetchone()
                if active is not None:
                    raise ValueError(
                        "active_research_run_prevents_resume"
                    )

            if previous == state:
                metadata["code_promotion_lease"] = {}
                conn.execute(
                    """update graen_problems
                    set metadata_json=?,updated_at=? where problem_id=?""",
                    (_json(metadata), now.isoformat(), problem_id),
                )
                conn.commit()
                return {
                    "ok": True,
                    "revision": expected,
                    "prespec_artifact_id": prespec_id,
                }

            revision = expected + 1
            metadata.update(
                {
                    "code_promotion": state,
                    "code_promotion_revision": revision,
                    "code_prespec_artifact_id": prespec_id,
                    "code_promotion_lease": {},
                }
            )
            if state.get("phase") == "COMPLETE":
                metadata["research_stage"] = (
                    "STRATEGY_COMPILED_DEVELOPMENT"
                )
                metadata["compiled_specification_hash"] = state.get(
                    "spec_hash"
                )

            event = {
                "revision": revision,
                **state,
                "prespec_artifact_id": prespec_id,
            }
            artifact_key = (
                f"{problem_id}:research-code-event:{revision}"
            )
            conn.execute(
                """insert or ignore into graen_artifacts(
                    artifact_id,artifact_key,problem_id,run_id,
                    artifact_type,methodology_version,source_commit,
                    content_hash,content_json,created_at
                ) values(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    artifact_key,
                    problem_id,
                    None,
                    "RESEARCH_CODE_PROMOTION_EVENT",
                    "graen.research-code-promotion.v1",
                    None,
                    _hash(event),
                    _json(event),
                    now.isoformat(),
                ),
            )
            conn.execute(
                """update graen_problems
                set metadata_json=?,updated_at=? where problem_id=?""",
                (_json(metadata), now.isoformat(), problem_id),
            )
            conn.commit()

        return {
            "ok": True,
            "revision": revision,
            "prespec_artifact_id": prespec_id,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    def _graen_claim_adaptive(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        domain = str(body.get("domain") or "")
        worker = str(body.get("worker_id") or "graen-adaptive")
        allowed = {
            "STRATEGY_COMPILED_DEVELOPMENT",
            "STRATEGY_COMPILED_VALIDATION",
            "STRATEGY_COMPILED_HOLDOUT",
            "STRATEGY_COMPILED_VELUM",
        }
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """select * from graen_problems
                where status in ('QUEUED','WAITING') and domain=?
                order by priority desc,created_at""",
                (domain,),
            ).fetchall()
            selected = None
            waiting_until: str | None = None
            for candidate in rows:
                metadata = _loads(candidate["metadata_json"], {})
                promotion = dict(metadata.get("code_promotion") or {})
                stage = str(metadata.get("research_stage") or "")
                if (
                    stage not in allowed
                    or promotion.get("phase") not in {
                        "COMPLETE", "RUNTIME_COMPILED"
                    }
                ):
                    continue

                # Confirmatory data is time-sealed. A generated program may be
                # deployed now, but VALIDATION/HOLDOUT cannot be claimed until
                # the entire frozen window has elapsed. VELUM is a post-holdout
                # independent replay and has no additional maturity window.
                if stage != "STRATEGY_COMPILED_VELUM":
                    spec = dict(
                        promotion.get("prespec")
                        or metadata.get("research_implementation_spec")
                        or {}
                    )
                    stage_name = stage.removeprefix(
                        "STRATEGY_COMPILED_"
                    ).lower()
                    try:
                        end_raw = spec["corpus"][stage_name][1]
                        stage_end = datetime.fromisoformat(
                            str(end_raw).replace("Z", "+00:00")
                        )
                        if stage_end.tzinfo is None:
                            stage_end = stage_end.replace(tzinfo=UTC)
                        stage_end = stage_end.astimezone(UTC)
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if datetime.now(UTC) < stage_end:
                        if (
                            waiting_until is None
                            or stage_end.isoformat() < waiting_until
                        ):
                            waiting_until = stage_end.isoformat()
                        continue

                selected = candidate
                break
            if selected is None:
                return {
                    "ok": True,
                    "problem": None,
                    "run": None,
                    "waiting_until": waiting_until,
                    "execution_authority": False,
                }

            now_text = _iso()
            conn.execute(
                """update graen_problems
                set status='RUNNING',
                    started_at=coalesce(started_at,?),
                    updated_at=? where problem_id=?""",
                (now_text, now_text, selected["problem_id"]),
            )
            run_id = str(uuid4())
            conn.execute(
                """insert into graen_runs(
                    run_id,problem_id,worker_id,runtime_version,
                    methodology_version,status,source_commit,deployment_id,
                    started_at
                ) values(?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    selected["problem_id"],
                    worker,
                    body.get("runtime_version"),
                    body.get("methodology_version"),
                    "RUNNING",
                    body.get("source_commit"),
                    body.get("deployment_id"),
                    now_text,
                ),
            )
            runrow = conn.execute(
                "select * from graen_runs where run_id=?",
                (run_id,),
            ).fetchone()
            conn.commit()

        problem = self._problem(selected)
        problem["status"] = "RUNNING"
        return {
            "ok": True,
            "problem": problem,
            "run": self._run(runrow),
            "execution_authority": False,
        }

    def _graen_create_problem(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        domain = str(body.get("domain") or "GENERAL_RESEARCH")
        if _retired_research_domain(domain):
            raise ValueError("retired_asset_class_domain")
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
                    domain,
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
        if domain and _retired_research_domain(domain):
            return {"ok": True, "problem": None, "run": None}
        with self._lock, self.connect() as conn:
            clauses = [
                "status in ('QUEUED','WAITING')",
                "upper(domain) not like '%CRYPTO%'",
                "upper(domain) not like '%BTC%'",
            ]
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

    def scheduler_claim(
        self, job: dict[str, Any]
    ) -> dict[str, Any]:
        key = str(job.get("job_key") or "").strip()
        if not key:
            raise ValueError("job_key_required")

        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        max_attempts = max(1, min(10, int(job.get("max_attempts") or 1)))
        allow_retry = bool(job.get("allow_retry", False))
        retry_delay_seconds = max(
            0,
            min(3600, int(job.get("retry_delay_seconds") or 0)),
        )
        lease_seconds = max(
            60,
            min(3600, int(job.get("lease_seconds") or 1800)),
        )
        retryable = {
            "transient_infrastructure",
            "dependency_unavailable",
            "evidence_unavailable",
        }

        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "select * from scheduler_runs where job_key=?",
                (key,),
            ).fetchone()

            if existing is None:
                payload = {
                    **dict(job),
                    "_scheduler_attempt": 1,
                    "_scheduler_claimed_at": now,
                }
                conn.execute(
                    """insert into scheduler_runs(
                        job_key,status,payload_json,scheduled_at,started_at,updated_at
                    ) values(?,?,?,?,?,?)""",
                    (
                        key,
                        "RUNNING",
                        _json(payload),
                        job.get("scheduled_at"),
                        now,
                        now,
                    ),
                )
                conn.commit()
                return {
                    "ok": True,
                    "claimed": True,
                    "job_key": key,
                    "attempt": 1,
                    "max_attempts": max_attempts,
                }

            status = str(existing["status"] or "")
            payload = _loads(existing["payload_json"], {})
            attempt = max(1, int(payload.get("_scheduler_attempt") or 1))
            existing_max = max(
                1,
                int(payload.get("max_attempts") or max_attempts),
            )
            effective_max = max(existing_max, max_attempts)

            if status in {"SUCCEEDED", "NOOP", "MISSED", "SKIPPED", "STALE"}:
                return {
                    "ok": True,
                    "claimed": False,
                    "duplicate": True,
                    "status": status,
                    "attempt": attempt,
                    "max_attempts": effective_max,
                }

            if status == "RUNNING":
                started_at = existing["started_at"]
                try:
                    started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    started = now_dt
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                lease_expired = (now_dt - started.astimezone(UTC)).total_seconds() >= lease_seconds
                if not lease_expired or attempt >= effective_max:
                    return {
                        "ok": True,
                        "claimed": False,
                        "busy": not lease_expired,
                        "exhausted": lease_expired and attempt >= effective_max,
                        "status": status,
                        "attempt": attempt,
                        "max_attempts": effective_max,
                    }

            if status == "FAILED":
                completion = payload.get("completion")
                completion = completion if isinstance(completion, dict) else {}
                classification = str(
                    completion.get("error_classification") or ""
                )
                if (
                    not allow_retry
                    or classification not in retryable
                    or attempt >= effective_max
                ):
                    return {
                        "ok": True,
                        "claimed": False,
                        "exhausted": attempt >= effective_max,
                        "retryable": classification in retryable,
                        "status": status,
                        "attempt": attempt,
                        "max_attempts": effective_max,
                    }
                updated_at = existing["updated_at"]
                try:
                    updated = datetime.fromisoformat(
                        str(updated_at).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    updated = now_dt
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                delay = min(
                    3600,
                    retry_delay_seconds * (2 ** max(0, attempt - 1)),
                )
                retry_at = updated.astimezone(UTC) + timedelta(seconds=delay)
                if delay > 0 and now_dt < retry_at:
                    return {
                        "ok": True,
                        "claimed": False,
                        "retry_waiting": True,
                        "retry_at": retry_at.isoformat(),
                        "status": status,
                        "attempt": attempt,
                        "max_attempts": effective_max,
                    }

            next_attempt = attempt + 1
            payload.update(
                {
                    **dict(job),
                    "_scheduler_attempt": next_attempt,
                    "_scheduler_claimed_at": now,
                }
            )
            conn.execute(
                """update scheduler_runs
                set status='RUNNING',
                    payload_json=?,
                    started_at=?,
                    completed_at=null,
                    updated_at=?
                where job_key=?""",
                (_json(payload), now, now, key),
            )
            conn.commit()
            return {
                "ok": True,
                "claimed": True,
                "job_key": key,
                "attempt": next_attempt,
                "max_attempts": effective_max,
                "retry": status == "FAILED",
                "recovered": status == "RUNNING",
            }

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
                # Match the pre-cutover IREN policy: bounded deterministic
                # maintenance is on unless an explicit durable/environment
                # setting disables it.
                "autopilot_enabled": True,
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

        # Public polling must stay bounded to the freshness window. Historical
        # lookups needed for identity are fetched separately below.
        recent = self._event_rows(
            since=cutoff_2h.isoformat(),
            limit=5000,
            newest_first=True,
        )
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

        latest_event = recent[0] if recent else next(
            iter(self._event_rows(limit=1, newest_first=True)),
            None,
        )
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
        reconciliation_history = [
            event for event in recent_2h
            if event.get("event_type") == "reconciliation"
        ]
        latest_reconciliation = (
            reconciliation_history[0] if reconciliation_history else
            next(iter(self._event_rows(
                event_types={"reconciliation"}, limit=1, newest_first=True,
            )), None)
        )
        last_reconciliation_at = stamp(
            latest_reconciliation.get("occurred_at") if latest_reconciliation else None
        )
        last_reconciliation_safe = (
            (latest_reconciliation.get("payload") or {}).get("safe_to_enter")
            if latest_reconciliation else None
        )
        reconciliation_state = (
            "STALE"
            if last_reconciliation_at is not None and
            (current - last_reconciliation_at).total_seconds() > 600
            else "SAFE" if last_reconciliation_safe is True
            else "BLOCKED" if last_reconciliation_safe is False
            else "UNKNOWN"
        )
        # Public activity should report changes in broker safety, not every
        # normal reconciliation heartbeat. The underlying evidence is retained.
        reconciliation_transitions: dict[str, bool] = {}
        previous_safe: bool | None = None
        for event in reversed(reconciliation_history):
            safe = (event.get("payload") or {}).get("safe_to_enter")
            if type(safe) is not bool:
                continue
            if (previous_safe is None and not safe) or (
                previous_safe is not None and safe != previous_safe
            ):
                reconciliation_transitions[str(event.get("event_key") or "")] = safe
            previous_safe = safe
        errors_2h = sum(
            event.get("event_type") in {
                "runtime_error"
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
            "runtime_start": ("system", "RHEN unified runtime started."),
            "runtime_stop": ("system", "RHEN unified runtime stopped."),
            "runtime_error": (
                "warning",
                "Runtime reported an operational exception.",
            ),
        }
        public_types = set(event_labels) | {"reconciliation"}
        public_events = []
        for event in recent_2h:
            event_type = str(event.get("event_type") or "")
            if event_type not in public_types:
                continue
            if event_type == "reconciliation":
                safe = reconciliation_transitions.get(str(event.get("event_key") or ""))
                if safe is None:
                    continue
                kind, label = (
                    ("system", "Broker reconciliation recovered; safety check passed.")
                    if safe else
                    ("warning", "Broker reconciliation found a discrepancy; new entries blocked.")
                )
            else:
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
        if latest_scan is None:
            latest_scan = next(
                iter(
                    self._event_rows(
                        event_types=scan_types,
                        limit=1,
                        newest_first=True,
                    )
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
        if runtime_event is None:
            runtime_event = next(
                iter(
                    self._event_rows(
                        event_types={"runtime_start"},
                        limit=1,
                        newest_first=True,
                    )
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
                order by occurred_at desc limit 5000"""
            ).fetchall()
            account_rows = list(reversed(account_rows))
            problem_row = conn.execute(
                """select status,body_json,metadata_json,updated_at
                from graen_problems
                where upper(domain) not like '%CRYPTO%'
                  and upper(domain) not like '%BTC%'
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

        iren_state, _iren_revision = self.get_kv("iren", "state", {})
        iren_state = iren_state if isinstance(iren_state, dict) else {}
        iren_observed_at = stamp(iren_state.get("observed_at"))
        iren_fresh = (
            iren_observed_at is not None
            and max(0.0, (current - iren_observed_at).total_seconds()) < 180.0
        )
        iren_control_state = str(iren_state.get("state") or "").upper()
        iren_health = (
            iren_control_state
            if iren_fresh and iren_control_state
            else "STALE"
        )
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
                "runtime_state": "READY" if iren_fresh else "STALE",
                "health_state": iren_health,
                "tracking_state": "CANONICAL_CONTROL_STATE",
                "observed_at": (
                    iren_state.get("observed_at")
                    or common_observed
                ),
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
            "broker_reconciliation": {
                "state": reconciliation_state,
                "last_checked_at": (
                    last_reconciliation_at.isoformat()
                    if last_reconciliation_at is not None else None
                ),
                "checks_2h": reconciliations_2h,
            },
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


    @staticmethod
    def _research_number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number

    @classmethod
    def _research_metrics(cls, value: Any, *, depth: int = 0) -> dict[str, float]:
        if depth > 4:
            return {}
        aliases = {
            "trade_count": "trades",
            "trades": "trades",
            "closed_trades": "trades",
            "independent_days": "independent_days",
            "independent_day_blocks": "independent_days",
            "expectancy_per_trade": "expectancy",
            "expectancy_per_trade_pct": "expectancy_pct",
            "net_expectancy": "expectancy",
            "profit_factor": "profit_factor",
            "max_drawdown": "max_drawdown",
            "max_drawdown_pct": "max_drawdown_pct",
            "net_return": "net_return",
            "return_pct": "return_pct",
            "win_rate": "win_rate",
            "win_rate_pct": "win_rate_pct",
            "sample_count": "sample_count",
            "candidate_count": "candidate_count",
            "opportunity_count": "opportunities",
            "trades_per_day": "trades_per_day",
            "median_trade_return": "median_trade_return",
            "development_trade_count": "development_trades",
            "validation_trade_count": "validation_trades",
        }
        output: dict[str, float] = {}
        if isinstance(value, dict):
            for key, raw in value.items():
                alias = aliases.get(str(key))
                if alias and alias not in output:
                    number = cls._research_number(raw)
                    if number is not None:
                        output[alias] = number
                if isinstance(raw, (dict, list)):
                    for nested_key, nested_value in cls._research_metrics(
                        raw, depth=depth + 1
                    ).items():
                        output.setdefault(nested_key, nested_value)
        elif isinstance(value, list):
            for raw in value[:32]:
                for nested_key, nested_value in cls._research_metrics(
                    raw, depth=depth + 1
                ).items():
                    output.setdefault(nested_key, nested_value)
        return output

    @classmethod
    def _find_research_list(
        cls, value: Any, key: str, *, depth: int = 0
    ) -> list[Any] | None:
        if depth > 5:
            return None
        if isinstance(value, dict):
            direct = value.get(key)
            if isinstance(direct, list):
                return direct
            for child_key in (
                "baseline", "result", "development", "validation", "holdout",
                "primary", "high", "base", "low", "scenarios", "stage_results",
            ):
                found = cls._find_research_list(
                    value.get(child_key), key, depth=depth + 1
                )
                if found is not None:
                    return found
            for child in value.values():
                if isinstance(child, (dict, list)):
                    found = cls._find_research_list(
                        child, key, depth=depth + 1
                    )
                    if found is not None:
                        return found
        elif isinstance(value, list):
            for child in value[:16]:
                found = cls._find_research_list(
                    child, key, depth=depth + 1
                )
                if found is not None:
                    return found
        return None

    @classmethod
    def _research_series(cls, value: Any) -> list[dict[str, Any]]:
        curve = cls._find_research_list(value, "equity_curve")
        if curve:
            rows: list[tuple[str, float]] = []
            for index, point in enumerate(curve):
                if not isinstance(point, dict):
                    continue
                equity = cls._research_number(point.get("equity"))
                if equity is None:
                    continue
                rows.append((str(point.get("at") or index), equity))
            if len(rows) > 1 and rows[0][1] != 0:
                step = max(1, len(rows) // 120)
                sampled = rows[::step]
                if sampled[-1] != rows[-1]:
                    sampled.append(rows[-1])
                base = rows[0][1]
                return [{
                    "key": "normalized_return",
                    "label": "Normalized return",
                    "unit": "%",
                    "points": [
                        {
                            "at": stamp,
                            "value": round(((equity / base) - 1.0) * 100.0, 6),
                        }
                        for stamp, equity in sampled
                    ],
                }]

        trades = cls._find_research_list(value, "trades")
        if trades:
            compounded = 1.0
            points: list[dict[str, Any]] = [{"at": "start", "value": 0.0}]
            for index, trade in enumerate(trades[:500]):
                if not isinstance(trade, dict):
                    continue
                trade_return = cls._research_number(trade.get("net_return"))
                if trade_return is None:
                    trade_return = cls._research_number(trade.get("return_pct"))
                if trade_return is None:
                    continue
                compounded *= 1.0 + trade_return
                points.append({
                    "at": str(
                        trade.get("exit_at")
                        or trade.get("entry_at")
                        or index + 1
                    ),
                    "value": round((compounded - 1.0) * 100.0, 6),
                })
            if len(points) > 1:
                step = max(1, len(points) // 120)
                sampled = points[::step]
                if sampled[-1] != points[-1]:
                    sampled.append(points[-1])
                return [{
                    "key": "trade_return",
                    "label": "Cumulative net return",
                    "unit": "%",
                    "points": sampled,
                }]
        return []

    @staticmethod
    def _research_progress(status: Any, stage: Any) -> int:
        state = str(status or "UNKNOWN").upper()
        phase = str(stage or "").upper()
        if state in {"SUCCEEDED", "COMPLETED", "COMPLETE", "FAILED", "CANCELLED"}:
            return 100
        if state == "QUEUED":
            return 8
        if state == "BLOCKED":
            return 70
        if state == "WAITING":
            return 85
        if "COMPLETE" in phase:
            return 100
        if "VELUM" in phase or "REPLAY" in phase:
            return 78
        if "HOLDOUT" in phase:
            return 70
        if "VALIDATION" in phase:
            return 55
        if "DEVELOPMENT" in phase:
            return 38
        if "IMPLEMENT" in phase or "HYPOTHESIS" in phase:
            return 20
        return 25 if state == "RUNNING" else 0

    @staticmethod
    def _research_control_projection(
        problems: list[dict[str, Any]],
        runs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Describe what the research runtime may do without operator/model reasoning.

        Repetitive evidence collection, frozen-stage evaluation, and replay are
        automation. Generating a new hypothesis family, patching strategy code,
        or promoting production strategy remains an explicit Work/Codex review.
        """
        active_states = {"RUNNING", "WAITING", "QUEUED", "BLOCKED"}
        active_problem = next(
            (
                row for row in problems
                if str(row.get("status") or "").upper() in active_states
            ),
            problems[0] if problems else None,
        )
        problem_id = str((active_problem or {}).get("problem_id") or "")
        stage = str((active_problem or {}).get("research_stage") or "").upper()
        latest_run = next(
            (
                row for row in runs
                if not problem_id or str(row.get("problem_id") or "") == problem_id
            ),
            runs[0] if runs else None,
        )
        next_action = str((latest_run or {}).get("next_action") or "").upper()
        decision = str((latest_run or {}).get("decision") or "").upper()
        rejected_generations = sum(
            1
            for row in runs
            if str(row.get("problem_id") or "") == problem_id
            and "REJECTED" in str(row.get("result_state") or "").upper()
        )

        mode = "IDLE"
        reason = "No active frozen research chain is exposed."
        review_required = False
        review_kind = None

        if stage == "ADAPTIVE_PROGRAM_EXHAUSTED":
            mode = "RESEARCH_REVIEW_REQUIRED"
            reason = (
                "The bounded hypothesis family is exhausted. A new hypothesis "
                "family requires an operator-directed Work/Codex research pass."
            )
            review_required = True
            review_kind = "NEW_HYPOTHESIS_FAMILY"
        elif stage == "CANDIDATE_READY_FOR_STRATEGY_REVIEW" or next_action == "PROTECTED_STRATEGY_UPDATE_REVIEW":
            mode = "RELEASE_REVIEW_REQUIRED"
            reason = (
                "Research evidence reached the protected strategy boundary. "
                "Strategy patching or promotion requires explicit review."
            )
            review_required = True
            review_kind = "STRATEGY_PATCH_OR_RELEASE"
        elif any(
            str(row.get("status") or "").upper() == "RUNNING"
            for row in runs
        ):
            mode = "AUTOMATED_TEST"
            reason = "A frozen experiment is running; autonomous execution is limited to its defined methodology."
        elif stage.startswith("STRATEGY_COMPILED_") or stage == "RESEARCH_IMPLEMENTATION_REQUIRED":
            mode = "AUTOMATED_TEST"
            reason = "A frozen research chain may advance through development, validation, holdout, and replay."
        elif active_problem is not None:
            mode = "OBSERVING"
            reason = "Research state is durable, but no bounded experiment is currently executing."

        return {
            "schema_version": "research_control.v1",
            "mode": mode,
            "reason": reason,
            "review_required": review_required,
            "review_kind": review_kind,
            "work_credit_recommended": review_required,
            "problem_id": problem_id or None,
            "stage": stage or None,
            "decision": decision or None,
            "next_action": next_action or None,
            "rejected_generations": rejected_generations,
            "latest_run_id": (latest_run or {}).get("run_id"),
            "autonomy": {
                "collect_market_evidence": True,
                "execute_frozen_hypotheses": True,
                "run_replay_validation": True,
                "generate_new_hypothesis_family": False,
                "patch_strategy_code": False,
                "promote_live_strategy": False,
                "change_risk_or_capital": False,
            },
        }

    def command_research_tracking(self) -> dict[str, Any]:
        """Canonical read-only Command tracking from the consolidated RHEN Core."""
        with self.connect() as conn:
            problem_rows = conn.execute(
                """select * from graen_problems
                where upper(domain) not like '%CRYPTO%'
                  and upper(domain) not like '%BTC%'
                order by
                  case status
                    when 'RUNNING' then 0
                    when 'WAITING' then 1
                    when 'QUEUED' then 2
                    when 'BLOCKED' then 3
                    else 4
                  end,
                  updated_at desc
                limit 40"""
            ).fetchall()
            run_rows = conn.execute(
                """select * from graen_runs
                order by started_at desc limit 64"""
            ).fetchall()
            artifact_rows = conn.execute(
                """select * from graen_artifacts
                order by created_at desc limit 96"""
            ).fetchall()
            event_rows = conn.execute(
                """select event_key,event_type,occurred_at,run_id,
                          strategy_version_id,source,payload_json
                   from events
                   where event_type like 'velum_%'
                      or event_type like 'nostra_%'
                      or event_type like 'graen_%'
                   order by occurred_at desc
                   limit 360"""
            ).fetchall()
            shadow_cycle_rows = conn.execute(
                """select event_key,event_type,occurred_at,run_id,
                          strategy_version_id,source,payload_json
                   from events
                   where event_type = 'decision_cycle'
                     and payload_json like '%shadow_economics_candidate_count%'
                   order by occurred_at desc
                   limit 120"""
            ).fetchall()
            shadow_allocation_rows = conn.execute(
                """select event_key,event_type,occurred_at,run_id,
                          strategy_version_id,source,payload_json
                   from events
                   where event_type = 'order_intent'
                     and payload_json like '%shadow_allocation%'
                   order by occurred_at desc
                   limit 120"""
            ).fetchall()

        graen_runtime, _ = self.get_kv("graen", "runtime", {})
        problems = []
        for row in problem_rows:
            problem = self._problem(row)
            metadata = dict(problem.get("metadata") or {})
            problems.append({
                "problem_id": problem.get("problem_id"),
                "title": problem.get("title") or problem.get("statement"),
                "status": problem.get("status"),
                "domain": problem.get("domain"),
                "research_stage": metadata.get("research_stage"),
                "candidate_id": metadata.get("candidate_id") or problem.get("candidate_id"),
                "hypothesis": metadata.get("hypothesis"),
                "family": metadata.get("family"),
                "mechanism": metadata.get("mechanism"),
                "campaign_id": metadata.get("campaign_id"),
                "target_lane": metadata.get("target_lane"),
                "supersedes_strategy_version_id": metadata.get(
                    "supersedes_strategy_version_id"
                ),
                "release_requested": bool(metadata.get("release_requested", False)),
                "updated_at": problem.get("updated_at"),
                "started_at": problem.get("started_at"),
                "completed_at": problem.get("completed_at"),
            })

        raw_runs = [self._run(row) for row in run_rows]
        raw_artifacts = [self._artifact(row) for row in artifact_rows]
        artifacts_by_run: dict[str, list[dict[str, Any]]] = {}
        for artifact in reversed(raw_artifacts):
            run_id = str(artifact.get("run_id") or "")
            if run_id:
                artifacts_by_run.setdefault(run_id, []).append(artifact)

        problems_by_id = {
            str(row.get("problem_id")): row for row in problems
            if row.get("problem_id")
        }
        runs: list[dict[str, Any]] = []
        compact_runs: list[dict[str, Any]] = []
        represented_problems: set[str] = set()
        for run in raw_runs:
            run_id = str(run.get("run_id") or "")
            problem_id = str(run.get("problem_id") or "")
            represented_problems.add(problem_id)
            problem = problems_by_id.get(problem_id, {})
            result = dict(run.get("result_summary") or {})
            artifacts = artifacts_by_run.get(run_id, [])
            richest: Any = result
            richest_score = (
                len(self._research_metrics(result))
                + (5 if self._research_series(result) else 0)
            )
            for artifact in reversed(artifacts):
                content = artifact.get("content")
                if not isinstance(content, dict):
                    continue
                score = (
                    len(self._research_metrics(content))
                    + (5 if self._research_series(content) else 0)
                )
                if score >= richest_score:
                    richest = content
                    richest_score = score
            stage = (
                problem.get("research_stage")
                or result.get("state")
                or result.get("status")
            )
            status = str(run.get("status") or "UNKNOWN").upper()
            compact_runs.append({
                "run_id": run_id,
                "problem_id": problem_id or None,
                "status": status,
                "methodology_version": run.get("methodology_version"),
                "result_state": result.get("state") or result.get("status"),
                "decision": result.get("decision"),
                "next_action": result.get("next_action"),
                "candidate_id": result.get("candidate_id") or problem.get("candidate_id"),
                "error": result.get("error"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
                "created_at": run.get("started_at"),
            })
            runs.append({
                "run_id": run_id,
                "system": "GRAEN",
                "kind": "RESEARCH",
                "title": (
                    problem.get("title")
                    or result.get("research_batch_id")
                    or problem.get("candidate_id")
                    or "GRAEN research run"
                ),
                "status": status,
                "stage": stage,
                "progress_pct": self._research_progress(status, stage),
                "problem_id": problem_id or None,
                "candidate_id": problem.get("candidate_id") or result.get("candidate_id"),
                "methodology_version": run.get("methodology_version"),
                "strategy_version_id": result.get("strategy_version_id"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
                "updated_at": run.get("completed_at") or run.get("started_at"),
                "metrics": self._research_metrics(richest),
                "series": self._research_series(richest),
                "artifact_count": len(artifacts),
                "detail": {
                    "decision": result.get("decision"),
                    "next_action": result.get("next_action"),
                    "hypothesis": problem.get("hypothesis"),
                    "family": problem.get("family"),
                    "mechanism": problem.get("mechanism"),
                    "campaign_id": problem.get("campaign_id"),
                    "progress_basis": "durable_stage_status",
                },
            })

        # A durable queued/running problem is still real research activity even
        # before a run record has been claimed.
        for problem in problems:
            problem_id = str(problem.get("problem_id") or "")
            status = str(problem.get("status") or "UNKNOWN").upper()
            if not problem_id or problem_id in represented_problems:
                continue
            if status not in {"RUNNING", "QUEUED", "WAITING", "BLOCKED"}:
                continue
            stage = problem.get("research_stage")
            runs.append({
                "run_id": "problem:" + problem_id,
                "system": "GRAEN",
                "kind": "RESEARCH_QUEUE",
                "title": problem.get("title") or "GRAEN research problem",
                "status": status,
                "stage": stage,
                "progress_pct": self._research_progress(status, stage),
                "problem_id": problem_id,
                "candidate_id": problem.get("candidate_id"),
                "methodology_version": None,
                "strategy_version_id": None,
                "started_at": problem.get("started_at"),
                "completed_at": problem.get("completed_at"),
                "updated_at": problem.get("updated_at"),
                "metrics": {},
                "series": [],
                "artifact_count": 0,
                "detail": {
                    "hypothesis": problem.get("hypothesis"),
                    "family": problem.get("family"),
                    "mechanism": problem.get("mechanism"),
                    "campaign_id": problem.get("campaign_id"),
                    "progress_basis": "durable_problem_status",
                },
            })

        # GRAEN stage evaluators persist aggregate gate evidence rather than
        # synthetic equity curves. Build an evidence-indexed progression across
        # generations of the same durable problem so Command can show real
        # research movement without inventing intra-run price paths.
        graen_by_problem: dict[str, list[dict[str, Any]]] = {}
        for item in runs:
            if (
                item.get("system") == "GRAEN"
                and item.get("kind") == "RESEARCH"
                and item.get("problem_id")
            ):
                graen_by_problem.setdefault(
                    str(item["problem_id"]), []
                ).append(item)

        progression_metrics = (
            ("expectancy", "Expectancy / trade", "%", 100.0),
            ("win_rate", "Win rate", "%", 100.0),
            ("max_drawdown", "Max drawdown", "%", 100.0),
            ("trades", "Trades", "", 1.0),
            ("opportunities", "Opportunities", "", 1.0),
            ("profit_factor", "Profit factor", "", 1.0),
        )
        for grouped in graen_by_problem.values():
            grouped.sort(
                key=lambda row: str(
                    row.get("completed_at")
                    or row.get("updated_at")
                    or row.get("started_at")
                    or ""
                )
            )
            for index, item in enumerate(grouped):
                if item.get("series"):
                    continue
                history = grouped[: index + 1]
                generated_series: list[dict[str, Any]] = []
                for metric_key, label, unit, scale in progression_metrics:
                    points: list[dict[str, Any]] = []
                    for generation in history:
                        metrics = (
                            generation.get("metrics")
                            if isinstance(generation.get("metrics"), dict)
                            else {}
                        )
                        value = self._research_number(metrics.get(metric_key))
                        if value is None:
                            continue
                        points.append({
                            "at": (
                                generation.get("completed_at")
                                or generation.get("updated_at")
                                or generation.get("started_at")
                                or generation.get("candidate_id")
                                or generation.get("run_id")
                            ),
                            "value": round(value * scale, 6),
                        })
                    if len(points) > 1:
                        generated_series.append({
                            "key": "graen_" + metric_key,
                            "label": label,
                            "unit": unit,
                            "points": points[-120:],
                        })
                if generated_series:
                    item["series"] = generated_series
                    detail = (
                        item.get("detail")
                        if isinstance(item.get("detail"), dict)
                        else {}
                    )
                    item["detail"] = {
                        **detail,
                        "chart_basis": "graen_generation_gate_evidence",
                        "inactive_time_drawn": False,
                    }

        replay_projections: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        velum_runs: dict[str, dict[str, Any]] = {}
        nostra_runs: dict[str, dict[str, Any]] = {}
        shadow_net_points: list[dict[str, Any]] = []
        shadow_best_points: list[dict[str, Any]] = []
        shadow_admit_points: list[dict[str, Any]] = []
        shadow_latest_metrics: dict[str, float] = {}
        shadow_methodology: str | None = None
        shadow_started_at: str | None = None
        shadow_updated_at: str | None = None
        allocation_ratio_points: list[dict[str, Any]] = []
        allocation_net_points: list[dict[str, Any]] = []
        allocation_velocity_points: list[dict[str, Any]] = []
        allocation_methodology: str | None = None
        allocation_started_at: str | None = None
        allocation_updated_at: str | None = None
        allocation_observed = 0
        allocation_would_allocate = 0

        for row in reversed(shadow_cycle_rows):
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                continue
            occurred_at = row["occurred_at"]
            candidate_count = self._research_number(
                payload.get("shadow_economics_candidate_count")
            )
            if candidate_count is None or candidate_count <= 0:
                continue
            shadow_started_at = shadow_started_at or occurred_at
            shadow_updated_at = occurred_at
            shadow_methodology = (
                str(
                    payload.get("shadow_economics_methodology_version")
                    or ""
                )
                or shadow_methodology
            )
            mean_net = self._research_number(
                payload.get("shadow_economics_mean_net_bps")
            )
            best_net = self._research_number(
                payload.get("shadow_economics_best_net_bps")
            )
            admit_rate = self._research_number(
                payload.get("shadow_economics_admit_rate_pct")
            )
            if mean_net is not None:
                shadow_net_points.append({"at": occurred_at, "value": mean_net})
                shadow_latest_metrics["mean_expected_net_bps"] = mean_net
            if best_net is not None:
                shadow_best_points.append({"at": occurred_at, "value": best_net})
                shadow_latest_metrics["best_expected_net_bps"] = best_net
            if admit_rate is not None:
                shadow_admit_points.append({"at": occurred_at, "value": admit_rate})
                shadow_latest_metrics["shadow_admission_rate_pct"] = admit_rate
            shadow_latest_metrics["candidate_count"] = candidate_count
            events.append({
                "event_id": str(row["event_key"]),
                "at": occurred_at,
                "system": "RHEN",
                "run_id": "rhen-shadow-economics",
                "event_type": "shadow_economics_cycle",
                "stage": "EVIDENCE_COLLECTION",
                "status": "OBSERVED",
                "progress_pct": 50,
                "title": "Opportunity economics shadow",
                "detail": (
                    f"{int(candidate_count)} candidates · "
                    f"{admit_rate:.1f}% shadow-admit"
                    if admit_rate is not None
                    else f"{int(candidate_count)} candidates"
                ),
            })

        for row in reversed(shadow_allocation_rows):
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                continue
            intent = payload.get("intent")
            intent = intent if isinstance(intent, dict) else {}
            intent_payload = intent.get("payload")
            intent_payload = (
                intent_payload if isinstance(intent_payload, dict) else {}
            )
            candidate_snapshot = intent_payload.get("candidate_snapshot")
            candidate_snapshot = (
                candidate_snapshot
                if isinstance(candidate_snapshot, dict)
                else {}
            )
            allocation = candidate_snapshot.get("shadow_allocation")
            if not isinstance(allocation, dict):
                continue
            if allocation.get("research_only") is not True:
                continue
            if allocation.get("execution_authority") is True:
                continue

            occurred_at = row["occurred_at"]
            live_safe = self._research_number(
                allocation.get("live_safe_notional")
            )
            shadow_notional = self._research_number(
                allocation.get("shadow_notional")
            )
            expected_net_bps = self._research_number(
                allocation.get("expected_net_bps")
            )
            velocity = self._research_number(
                allocation.get("capital_velocity_per_minute")
            )
            ratio_pct = None
            if (
                live_safe is not None
                and live_safe > 0
                and shadow_notional is not None
                and shadow_notional >= 0
            ):
                ratio_pct = shadow_notional / live_safe * 100.0

            allocation_observed += 1
            if allocation.get("would_allocate") is True:
                allocation_would_allocate += 1
            allocation_started_at = allocation_started_at or occurred_at
            allocation_updated_at = occurred_at
            allocation_methodology = (
                str(allocation.get("methodology_version") or "")
                or allocation_methodology
            )
            if ratio_pct is not None:
                allocation_ratio_points.append({
                    "at": occurred_at,
                    "value": round(ratio_pct, 6),
                })
            if expected_net_bps is not None:
                allocation_net_points.append({
                    "at": occurred_at,
                    "value": expected_net_bps,
                })
            if velocity is not None:
                allocation_velocity_points.append({
                    "at": occurred_at,
                    "value": velocity,
                })

            events.append({
                "event_id": str(row["event_key"]),
                "at": occurred_at,
                "system": "RHEN",
                "run_id": "rhen-shadow-allocation",
                "event_type": "shadow_allocation_observed",
                "stage": "EVIDENCE_COLLECTION",
                "status": "OBSERVED",
                "progress_pct": 50,
                "title": "Capital allocation shadow",
                "detail": (
                    (
                        f"{ratio_pct:.1f}% of live-safe notional · "
                        f"{'allocate' if allocation.get('would_allocate') is True else 'skip'}"
                    )
                    if ratio_pct is not None
                    else str(allocation.get("reason") or "observed")
                ),
            })

        for row in reversed(event_rows):
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                payload = {}
            event_type = str(row["event_type"] or "")
            occurred_at = row["occurred_at"]

            if event_type.startswith("velum_"):
                engineering_gate = (
                    dict(payload.get("engineering_gate") or {})
                    if isinstance(payload.get("engineering_gate"), dict)
                    else {}
                )
                passed = engineering_gate.get("passed")
                replay_status = (
                    "PASSED" if passed is True else
                    "FAILED" if passed is False else
                    str(payload.get("status") or "RUNNING").upper()
                )
                problem_id = payload.get("problem_id")
                run_key = str(
                    payload.get("graen_run_id")
                    or problem_id
                    or row["run_id"]
                    or row["event_key"]
                )
                progress = int(
                    self._research_number(payload.get("progress_pct"))
                    or (100 if event_type != "velum_replay_progress" else 55)
                )
                phase = payload.get("phase") or (
                    "COMPLETE"
                    if event_type != "velum_replay_progress"
                    else "REPLAYING"
                )
                item = velum_runs.setdefault(run_key, {
                    "run_id": run_key,
                    "system": "VELUM",
                    "kind": "CANDIDATE_REPLAY",
                    "title": payload.get("candidate_id") or "VELUM replay",
                    "status": replay_status,
                    "stage": phase,
                    "progress_pct": progress,
                    "problem_id": problem_id,
                    "candidate_id": payload.get("candidate_id"),
                    "methodology_version": payload.get("candidate_methodology"),
                    "strategy_version_id": row["strategy_version_id"],
                    "started_at": payload.get("replay_start") or occurred_at,
                    "completed_at": None,
                    "updated_at": occurred_at,
                    "metrics": {},
                    "series": [],
                    "detail": {},
                })
                item.update({
                    "status": replay_status,
                    "stage": phase,
                    "progress_pct": max(int(item.get("progress_pct") or 0), progress),
                    "updated_at": occurred_at,
                })
                if event_type != "velum_replay_progress":
                    item["completed_at"] = occurred_at
                    item["metrics"] = self._research_metrics(payload)
                    item["series"] = self._research_series(payload)
                    item["detail"] = {
                        "engineering_gate": engineering_gate or None,
                        "evidence_role": payload.get("evidence_role"),
                        "bar_coverage": payload.get("bar_coverage"),
                    }
                    replay_range = (
                        dict(payload.get("range") or {})
                        if isinstance(payload.get("range"), dict)
                        else {}
                    )
                    replay_projections.append({
                        "owner": "VELUM",
                        "event_type": event_type,
                        "candidate_id": payload.get("candidate_id"),
                        "problem_id": problem_id,
                        "strategy_version_id": (
                            row["strategy_version_id"]
                            or payload.get("strategy_version_id")
                        ),
                        "status": replay_status,
                        "started_at": (
                            payload.get("replay_start")
                            or replay_range.get("start")
                        ),
                        "completed_at": occurred_at,
                        "observed_at": occurred_at,
                        "engineering_gate": engineering_gate or None,
                    })
                events.append({
                    "event_id": str(row["event_key"]),
                    "at": occurred_at,
                    "system": "VELUM",
                    "run_id": run_key,
                    "event_type": event_type,
                    "stage": phase,
                    "status": replay_status,
                    "progress_pct": progress,
                    "title": payload.get("candidate_id") or "VELUM replay",
                    "detail": payload.get("message") or payload.get("reason"),
                })
                continue

            if event_type.startswith("nostra_"):
                run_key = str(
                    payload.get("forecast_id")
                    or payload.get("calibration_id")
                    or row["event_key"]
                )
                kind = "CALIBRATION" if "calibr" in event_type else "FORECAST"
                status = str(payload.get("status") or "OBSERVED").upper()
                item = nostra_runs.setdefault(run_key, {
                    "run_id": run_key,
                    "system": "NOSTRA",
                    "kind": kind,
                    "title": (
                        payload.get("subject")
                        or payload.get("model_version")
                        or "NOSTRA " + kind.lower()
                    ),
                    "status": status,
                    "stage": event_type.upper(),
                    "progress_pct": 100 if "outcome" in event_type or "scored" in event_type else 50,
                    "problem_id": None,
                    "candidate_id": payload.get("candidate_id"),
                    "methodology_version": payload.get("methodology_version"),
                    "strategy_version_id": row["strategy_version_id"],
                    "started_at": occurred_at,
                    "completed_at": occurred_at if "outcome" in event_type or "scored" in event_type else None,
                    "updated_at": occurred_at,
                    "metrics": self._research_metrics(payload),
                    "series": self._research_series(payload),
                    "detail": {
                        "model_version": payload.get("model_version"),
                        "source_event": event_type,
                    },
                })
                item["updated_at"] = occurred_at
                events.append({
                    "event_id": str(row["event_key"]),
                    "at": occurred_at,
                    "system": "NOSTRA",
                    "run_id": run_key,
                    "event_type": event_type,
                    "stage": item["stage"],
                    "status": status,
                    "progress_pct": item["progress_pct"],
                    "title": item["title"],
                    "detail": None,
                })

        runs.extend(velum_runs.values())
        runs.extend(nostra_runs.values())
        if shadow_updated_at is not None:
            series = []
            if len(shadow_net_points) > 1:
                series.append({
                    "key": "shadow_mean_net_bps",
                    "label": "Mean expected net edge",
                    "unit": " bps",
                    "points": shadow_net_points[-120:],
                })
            if len(shadow_best_points) > 1:
                series.append({
                    "key": "shadow_best_net_bps",
                    "label": "Best expected net edge",
                    "unit": " bps",
                    "points": shadow_best_points[-120:],
                })
            if len(shadow_admit_points) > 1:
                series.append({
                    "key": "shadow_admission_rate",
                    "label": "Shadow admission rate",
                    "unit": "%",
                    "points": shadow_admit_points[-120:],
                })
            runs.append({
                "run_id": "rhen-shadow-economics",
                "system": "RHEN",
                "kind": "SHADOW_ECONOMICS",
                "title": "Opportunity economics shadow",
                "status": "OBSERVING",
                "stage": "EVIDENCE_COLLECTION",
                "progress_pct": 50,
                "problem_id": None,
                "candidate_id": None,
                "methodology_version": shadow_methodology,
                "strategy_version_id": None,
                "started_at": shadow_started_at,
                "completed_at": None,
                "updated_at": shadow_updated_at,
                "metrics": shadow_latest_metrics,
                "series": series,
                "artifact_count": 0,
                "detail": {
                    "research_only": True,
                    "execution_authority": False,
                    "changes_live_decision": False,
                    "chart_basis": "decision_cycle_shadow_economics",
                    "inactive_time_drawn": False,
                },
            })

        if allocation_updated_at is not None:
            allocation_metrics: dict[str, float] = {
                "selected_entry_count": float(allocation_observed),
                "would_allocate_count": float(allocation_would_allocate),
                "shadow_allocation_rate_pct": round(
                    allocation_would_allocate
                    / max(allocation_observed, 1)
                    * 100.0,
                    4,
                ),
            }
            if allocation_ratio_points:
                allocation_metrics["mean_shadow_to_live_pct"] = round(
                    sum(point["value"] for point in allocation_ratio_points)
                    / len(allocation_ratio_points),
                    6,
                )
            if allocation_net_points:
                allocation_metrics["mean_expected_net_bps"] = round(
                    sum(point["value"] for point in allocation_net_points)
                    / len(allocation_net_points),
                    6,
                )
            if allocation_velocity_points:
                allocation_metrics[
                    "mean_capital_velocity_per_minute"
                ] = round(
                    sum(
                        point["value"]
                        for point in allocation_velocity_points
                    )
                    / len(allocation_velocity_points),
                    10,
                )

            allocation_series: list[dict[str, Any]] = []
            if len(allocation_ratio_points) > 1:
                allocation_series.append({
                    "key": "shadow_to_live_pct",
                    "label": "Shadow / live-safe notional",
                    "unit": "%",
                    "points": allocation_ratio_points[-120:],
                })
            if len(allocation_net_points) > 1:
                allocation_series.append({
                    "key": "allocation_expected_net_bps",
                    "label": "Expected net edge",
                    "unit": " bps",
                    "points": allocation_net_points[-120:],
                })
            if len(allocation_velocity_points) > 1:
                allocation_series.append({
                    "key": "capital_velocity_per_minute",
                    "label": "Capital velocity",
                    "unit": "",
                    "points": allocation_velocity_points[-120:],
                })

            runs.append({
                "run_id": "rhen-shadow-allocation",
                "system": "RHEN",
                "kind": "SHADOW_ALLOCATION",
                "title": "Capital allocation shadow",
                "status": "OBSERVING",
                "stage": "EVIDENCE_COLLECTION",
                "progress_pct": 50,
                "problem_id": None,
                "candidate_id": None,
                "methodology_version": allocation_methodology,
                "strategy_version_id": None,
                "started_at": allocation_started_at,
                "completed_at": None,
                "updated_at": allocation_updated_at,
                "metrics": allocation_metrics,
                "series": allocation_series,
                "artifact_count": 0,
                "detail": {
                    "research_only": True,
                    "execution_authority": False,
                    "changes_live_decision": False,
                    "bounded_by_live_safe_notional": True,
                    "chart_basis": "selected_entry_shadow_allocation",
                    "inactive_time_drawn": False,
                },
            })

        # The run/artifact records themselves are durable events. Include them
        # so a selected GRAEN run always has a trace even without a replay.
        for run in runs:
            if run.get("system") != "GRAEN":
                continue
            events.append({
                "event_id": "graen-run:" + str(run.get("run_id")),
                "at": run.get("updated_at") or run.get("started_at"),
                "system": "GRAEN",
                "run_id": run.get("run_id"),
                "event_type": "graen_run",
                "stage": run.get("stage"),
                "status": run.get("status"),
                "progress_pct": run.get("progress_pct"),
                "title": run.get("title"),
                "detail": (run.get("detail") or {}).get("next_action"),
            })
        for artifact in raw_artifacts:
            run_id = str(artifact.get("run_id") or "")
            if not run_id:
                continue
            events.append({
                "event_id": "graen-artifact:" + str(artifact.get("artifact_id")),
                "at": artifact.get("created_at"),
                "system": "GRAEN",
                "run_id": run_id,
                "event_type": "artifact_persisted",
                "stage": artifact.get("artifact_type"),
                "status": "PERSISTED",
                "title": artifact.get("artifact_type"),
                "detail": artifact.get("methodology_version"),
            })

        def stamp(value: Any) -> str:
            return str(value or "")

        runs.sort(
            key=lambda row: stamp(row.get("updated_at") or row.get("started_at")),
            reverse=True,
        )
        events.sort(key=lambda row: stamp(row.get("at")), reverse=True)
        replay_projections.sort(
            key=lambda row: stamp(row.get("observed_at")), reverse=True
        )
        control = self._research_control_projection(problems, compact_runs)
        return {
            "control": control,
            "graen_problems": problems,
            "graen_runs": compact_runs,
            "velum_replays": replay_projections[:40],
            "graen_runtime": graen_runtime or None,
            "observability": {
                "schema_version": "research_observability.v3",
                "updated_at": _iso(),
                "poll_seconds": 3,
                "runs": runs[:64],
                "events": events[:120],
                "authority": {
                    "read_only": True,
                    "research_only": True,
                    "live_trading_performance_mixed": False,
                    "source": "rhen_core_sqlite",
                },
            },
        }

    def candidate_evidence_readiness(self) -> dict[str, Any]:
        """Summarize recent decision-time evidence required by forward analytics."""
        with self.connect() as conn:
            rows = conn.execute(
                """select payload_json,occurred_at
                   from events
                   where event_type='decision_cycle'
                     and payload_json like '%forward_measurement_ready_count%'
                   order by occurred_at desc
                   limit 20"""
            ).fetchall()

        total = ready = missing_price = missing_bar_time = reference_only = 0
        latest_at = None
        cycles = 0
        for row in rows:
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                continue
            candidate_count = int(payload.get("candidate_count") or 0)
            ready_count = int(
                payload.get("forward_measurement_ready_count") or 0
            )
            total += candidate_count
            ready += ready_count
            missing_price += int(
                payload.get("forward_missing_reference_price_count") or 0
            )
            missing_bar_time += int(
                payload.get("forward_missing_bar_time_count") or 0
            )
            reference_only += int(
                payload.get("evidence_reference_only_count") or 0
            )
            latest_at = latest_at or row["occurred_at"]
            cycles += 1

        rate = round(ready / total * 100.0, 4) if total > 0 else None
        if cycles == 0 or total == 0:
            state = "AWAITING_MEASURABLE_COHORT"
            next_action = (
                "Collect the first post-fix live decision cycle before "
                "evaluating forward-outcome coverage."
            )
        elif ready == total:
            state = "READY"
            next_action = (
                "Continue collecting mature forward outcomes from the "
                "measurement-ready cohort."
            )
        elif ready > 0:
            state = "PARTIAL"
            next_action = (
                "Keep measurable candidates in validation and investigate "
                "the remaining missing decision-time references."
            )
        else:
            state = "DEGRADED"
            next_action = (
                "Forward validation is blocked for the sampled cohort; "
                "repair decision-time price/time evidence before using it."
            )
        return {
            "schema_version": "candidate_evidence_readiness.v2",
            "state": state,
            "research_only": True,
            "execution_authority": False,
            "changes_live_decision": False,
            "measurement_contract": "exact_decision_price_plus_completed_bar_time",
            "sampled_cycles": cycles,
            "candidate_count": total,
            "measurement_ready_count": ready,
            "measurement_ready_rate_pct": rate,
            "missing_reference_price_count": missing_price,
            "missing_bar_time_count": missing_bar_time,
            "evidence_reference_only_count": reference_only,
            "latest_observed_at": latest_at,
            "next_action": next_action,
        }

    def strategy_pipeline_research(self) -> dict[str, Any]:
        """Return the durable Command research and strategy lifecycle projection."""
        tracking = self.command_research_tracking()
        problems = list(tracking.get("graen_problems") or [])
        runs = list(tracking.get("graen_runs") or [])
        replay_projections = list(tracking.get("velum_replays") or [])
        graen_runtime = tracking.get("graen_runtime")

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
            lane = "equities"
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
            "evidence_readiness": self.candidate_evidence_readiness(),
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
            "research": tracking,
        }

    def record_research_audit(
        self,
        run: dict[str, Any],
        *,
        search_ledger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist deterministic research audit state inside canonical Core."""

        payload = dict(run or {})
        run_key = str(
            payload.get("run_key")
            or payload.get("run_id")
            or ("run:" + _hash(payload))
        ).strip()
        if not run_key:
            raise ValueError("research run key is required")
        now = _iso()
        ledger_hash: str | None = None
        ledger_payload: dict[str, Any] | None = None
        if search_ledger is not None:
            ledger_payload = dict(search_ledger)
            ledger_hash = str(
                ledger_payload.get("ledger_hash")
                or ("ledger:" + _hash(ledger_payload))
            ).strip()
            if not ledger_hash:
                raise ValueError("research ledger hash is required")

        with self._lock, self.connect() as conn:
            conn.execute(
                """insert or replace into research_runs(
                    run_key,payload_json,created_at
                ) values(?,?,?)""",
                (run_key, _json(payload), now),
            )
            if ledger_payload is not None and ledger_hash is not None:
                conn.execute(
                    """insert or replace into research_ledgers(
                        ledger_hash,payload_json,created_at
                    ) values(?,?,?)""",
                    (ledger_hash, _json(ledger_payload), now),
                )
            conn.commit()

        return {
            "ok": True,
            "run_key": run_key,
            "ledger_hash": ledger_hash,
            "search_ledger_recorded": ledger_hash is not None,
            "execution_authority": False,
            "broker_orders_possible": False,
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

    def nostra_forecasts(
        self, now: datetime | None = None, *, limit: int = 200
    ) -> dict[str, Any]:
        """Bounded projection of still-live canonical NOSTRA forecast events."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        bounded = max(1, min(int(limit), 200))
        since = (current - timedelta(hours=24)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """select payload_json,occurred_at from events
                   where event_type='nostra_forecast'
                     and occurred_at >= ?
                   order by occurred_at desc, rowid desc
                   limit ?""",
                (since, bounded),
            ).fetchall()

        forecasts: list[dict[str, Any]] = []
        rejected = 0
        for row in rows:
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                rejected += 1
                continue
            try:
                generated = datetime.fromisoformat(
                    str(payload.get("generated_at") or row["occurred_at"]).replace("Z","+00:00")
                )
                as_of = datetime.fromisoformat(
                    str(payload.get("as_of_timestamp") or "").replace("Z","+00:00")
                )
                if generated.tzinfo is None or as_of.tzinfo is None:
                    raise ValueError("naive timestamp")
                generated = generated.astimezone(UTC)
                as_of = as_of.astimezone(UTC)
                horizon = int(payload.get("horizon_minutes") or 0)
                if (
                    payload.get("research_only") is not True
                    or payload.get("execution_authority") is not False
                    or payload.get("target_kind") != "return"
                    or payload.get("authority_state") not in {"NORMAL","LOW_SUPPORT"}
                    or not str(payload.get("forecast_id") or "").strip()
                    or not str(payload.get("snapshot_id") or "").strip()
                    or not str(payload.get("symbol") or "").strip()
                    or as_of > generated
                    or generated > current
                    or not 1 <= horizon <= 1440
                    or generated + timedelta(minutes=horizon) <= current
                ):
                    raise ValueError("invalid forecast")
                forecasts.append(payload)
            except (TypeError, ValueError):
                rejected += 1

        return {
            "ok": True,
            "schema_version": "nostra-canonical-forecast-read-v1",
            "observed_at": current.isoformat(),
            "forecasts": forecasts,
            "returned_count": len(forecasts),
            "rejected_count": rejected,
            "truncated": len(rows) >= bounded,
            "research_only": True,
            "execution_authority": False,
            "broker_write_authority": False,
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
                  and lower(coalesce(market_lane,'')) in ('us_equity','us_equity_extended','equities','')
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
                    "market_lane": row["market_lane"] or "us_equity",
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
        run_id: str | None = None,
        since: str | None = None,
        limit: int = 5000,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        order = "desc" if newest_first else "asc"
        clauses: list[str] = []
        params: list[Any] = []
        if event_types:
            marks = ",".join("?" for _ in event_types)
            clauses.append(f"event_type in ({marks})")
            params.extend(sorted(event_types))
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(str(run_id))
        if since is not None:
            clauses.append("occurred_at>=?")
            params.append(_iso(since))
        where = " where " + " and ".join(clauses) if clauses else ""
        bounded_limit = max(1, min(50000, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                f"""select * from events{where}
                order by occurred_at {order} limit ?""",
                (*params, bounded_limit),
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

    def _candidate_report_rows(
        self,
        session: str,
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
            allocation_rows = conn.execute(
                """select payload_json,occurred_at from events
                where event_type='order_intent'
                  and payload_json like '%shadow_allocation%'
                order by occurred_at asc limit 10000"""
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

        allocations: dict[str, dict[str, Any]] = {}
        for row in allocation_rows:
            payload = _loads(row["payload_json"], {})
            if not isinstance(payload, dict):
                continue
            intent = payload.get("intent")
            intent = intent if isinstance(intent, dict) else {}
            intent_payload = intent.get("payload")
            intent_payload = (
                intent_payload if isinstance(intent_payload, dict) else {}
            )
            snapshot = intent_payload.get("candidate_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            allocation = snapshot.get("shadow_allocation")
            if not isinstance(allocation, dict):
                continue
            if allocation.get("research_only") is not True:
                continue
            if allocation.get("execution_authority") is True:
                continue
            identity = str(
                snapshot.get("candidate_key")
                or intent_payload.get("candidate_key")
                or ""
            ).strip()
            if not identity:
                continue
            live_safe = self._research_number(
                allocation.get("live_safe_notional")
            )
            shadow_notional = self._research_number(
                allocation.get("shadow_notional")
            )
            ratio_pct = None
            if (
                live_safe is not None
                and live_safe > 0
                and shadow_notional is not None
                and shadow_notional >= 0
            ):
                ratio_pct = round(
                    shadow_notional / live_safe * 100.0,
                    6,
                )
            allocations[identity] = {
                "methodology_version": allocation.get(
                    "methodology_version"
                ),
                "research_only": True,
                "execution_authority": False,
                "changes_live_decision": (
                    allocation.get("changes_live_decision") is True
                ),
                "bounded_by_live_safe_notional": (
                    allocation.get("bounded_by_live_safe_notional") is True
                ),
                "would_allocate": allocation.get("would_allocate"),
                "reason": allocation.get("reason"),
                "shadow_to_live_pct": ratio_pct,
                "expected_net_bps": allocation.get("expected_net_bps"),
                "confidence": allocation.get("confidence"),
                "expected_holding_minutes": allocation.get(
                    "expected_holding_minutes"
                ),
                "capital_velocity_per_minute": allocation.get(
                    "capital_velocity_per_minute"
                ),
                "observed_at": row["occurred_at"],
            }

        result = []
        for row in rows:
            if self._session_date(row["observed_at"]) != session:
                continue
            lane = str(row["market_lane"] or "").lower()
            if lane not in {"", "us_equity", "us_equity_extended", "equities"}:
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
                    "shadow_allocation": allocations.get(identity),
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
            if lane not in {"", "us_equity", "us_equity_extended", "equities"}:
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

        if latest == "ledger":
            run_id = str(params.get("run_id") or "").strip()
            if not run_id:
                return {"ok": False, "error": "invalid_run_id"}
            try:
                limit = int(params.get("limit") or 500)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_limit"}
            if limit < 1 or limit > 5000:
                return {"ok": False, "error": "invalid_limit"}

            rows = self._event_rows(
                event_types={
                    "order_intent",
                    "broker_order",
                    "broker_fill",
                    "intent_reconciliation",
                },
                run_id=run_id,
                limit=limit,
                newest_first=True,
            )
            events = []
            for row in rows:
                payload = row.get("payload") or {}
                event = {
                    "event_key": row.get("event_key"),
                    "event_type": row.get("event_type"),
                    "occurred_at": row.get("occurred_at"),
                    "symbol": row.get("symbol"),
                }
                if row.get("event_type") == "broker_order":
                    order = payload.get("order") or {}
                    event["order"] = {
                        key: order.get(key)
                        for key in (
                            "id",
                            "client_order_id",
                            "symbol",
                            "side",
                            "type",
                            "status",
                            "qty",
                            "filled_qty",
                            "filled_avg_price",
                            "submitted_at",
                            "updated_at",
                            "filled_at",
                        )
                        if order.get(key) is not None
                    }
                elif row.get("event_type") == "broker_fill":
                    activity = payload.get("activity") or {}
                    event["fill"] = {
                        key: activity.get(key)
                        for key in (
                            "id",
                            "order_id",
                            "symbol",
                            "side",
                            "qty",
                            "price",
                            "transaction_time",
                            "date",
                        )
                        if activity.get(key) is not None
                    }
                elif row.get("event_type") == "order_intent":
                    intent = payload.get("intent") or {}
                    event["intent"] = {
                        key: intent.get(key)
                        for key in (
                            "intent_id",
                            "idempotency_key",
                            "symbol",
                            "side",
                            "intended_at",
                        )
                        if intent.get(key) is not None
                    }
                elif row.get("event_type") == "intent_reconciliation":
                    event["intent_reconciliation"] = {
                        key: payload.get(key)
                        for key in ("client_order_id", "state")
                        if payload.get(key) is not None
                    }
                events.append(event)
            return {
                "ok": True,
                "ledger_version": "rhen-canonical-ledger-read-v1",
                "run_id": run_id,
                "generated_at": _iso(),
                "event_count": len(events),
                "truncated": len(rows) >= limit,
                "events": events,
                "execution_authority": False,
                "broker_orders_possible": False,
            }

        evidence_session = params.get("evidence_session")
        if evidence_session:
            rows = self._candidate_report_rows(evidence_session)
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
                "evidence_readiness": self.candidate_evidence_readiness(),
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
            rows = self._candidate_report_rows(post_session)
            return {
                "ok": True,
                "evidence_version": "rhen-post-event-candidates-v2",
                "evidence_session": post_session,
                "candidates": rows[:5000],
                "evidence_readiness": self.candidate_evidence_readiness(),
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
                "evidence_readiness": self.candidate_evidence_readiness(),
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

        # Reconciliation is on the live broker-write path. Restrict the SQL read
        # to the active run before payload deserialization instead of loading up
        # to 50k events across every historical run and filtering in Python.
        events = self._event_rows(
            event_types={
                "order_intent",
                "broker_order",
                "intent_reconciliation",
            },
            run_id=run_id,
            limit=50000,
            newest_first=False,
        )
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
