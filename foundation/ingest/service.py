from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb


class EvidenceEvent(BaseModel):
    event_key: str = Field(min_length=1, max_length=512)
    run_id: str | None = None
    strategy_version_id: str | None = None
    event_type: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    symbol: str | None = None
    correlation_id: str | None = None
    source: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceBatch(BaseModel):
    events: list[EvidenceEvent] = Field(min_length=1, max_length=100)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def infer_run(event: EvidenceEvent) -> tuple[str, str]:
    payload = event.payload
    lane = str(
        payload.get("market_lane")
        or payload.get("asset_class")
        or payload.get("market")
        or "unknown"
    ).strip().lower()
    if "crypto" in lane:
        asset_class = "crypto"
    elif "equity" in lane or lane in {"stock", "stocks"}:
        asset_class = "equity"
    else:
        asset_class = "unknown"

    mode = str(
        payload.get("execution_mode")
        or payload.get("trading_mode")
        or payload.get("mode")
        or "shadow_migration"
    ).strip()
    return asset_class, mode or "shadow_migration"


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def ensure_run(cur: psycopg.Cursor[Any], event: EvidenceEvent) -> None:
    if not event.run_id:
        return
    asset_class, mode = infer_run(event)
    strategy_version_id = event.strategy_version_id or "unknown"
    cur.execute(
        """
        insert into rhen.strategy_runs (
            run_id,
            strategy_version_id,
            asset_class,
            mode,
            started_at,
            status,
            configuration
        )
        values (%s, %s, %s, %s, %s, 'shadow', '{}'::jsonb)
        on conflict (run_id) do update
        set strategy_version_id = case
                when rhen.strategy_runs.strategy_version_id = 'unknown'
                then excluded.strategy_version_id
                else rhen.strategy_runs.strategy_version_id
            end
        """,
        (
            event.run_id,
            strategy_version_id,
            asset_class,
            mode,
            event.occurred_at,
        ),
    )


app = FastAPI(title="ANEVUM Foundation Evidence Ingest", version="0.1.0")


@app.get("/live")
def live() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "foundation-ingest",
        "authority": "staging-shadow",
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return {"ok": True, "database": "ready"}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"database_not_ready:{type(exc).__name__}",
        ) from exc


@app.get("/status")
def status() -> dict[str, Any]:
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select count(*)::bigint, max(ingested_at)
                    from rhen.events
                    """
                )
                count, last_ingested_at = cur.fetchone()
        return {
            "ok": True,
            "event_count": int(count),
            "last_ingested_at": (
                last_ingested_at.isoformat() if last_ingested_at else None
            ),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"status_unavailable:{type(exc).__name__}",
        ) from exc


@app.post("/v1/events")
def ingest(batch: EvidenceBatch) -> dict[str, Any]:
    inserted = 0
    duplicates = 0
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                for event in batch.events:
                    ensure_run(cur, event)
                    cur.execute(
                        """
                        insert into rhen.events (
                            event_key,
                            run_id,
                            strategy_version_id,
                            event_type,
                            occurred_at,
                            symbol,
                            correlation_id,
                            source,
                            payload,
                            payload_hash
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (event_key) do nothing
                        returning event_id
                        """,
                        (
                            event.event_key,
                            event.run_id,
                            event.strategy_version_id,
                            event.event_type,
                            event.occurred_at,
                            event.symbol,
                            event.correlation_id,
                            event.source,
                            Jsonb(event.payload),
                            canonical_payload_hash(event.payload),
                        ),
                    )
                    if cur.fetchone():
                        inserted += 1
                    else:
                        duplicates += 1
            conn.commit()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"event_ingest_failed:{type(exc).__name__}",
        ) from exc

    return {
        "ok": True,
        "received": len(batch.events),
        "inserted": inserted,
        "duplicates": duplicates,
    }
