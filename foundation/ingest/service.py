from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from foundation.report_read import read_report
from foundation.iren_gateway import handle_action as handle_iren_action, recent_runs
from foundation.graen_gateway import handle_graen_action
from foundation.research_agent_gateway import read_evidence, record_run, record_search_ledger
from foundation.public_feed import read_public_feed
from foundation.nostra_gateway import read_nostra_work, read_nostra_forecasts
from foundation.cloudflare_access import (
    AccessAuthorizationError,
    AccessConfigurationError,
    verify_access_assertion,
)
from foundation.command_iren import (
    enqueue_command as enqueue_iren_command,
    project_command,
    read_command_snapshot,
)


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
        payload, sort_keys=True, separators=(",", ":"), default=str
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


def nostra_gateway_authorized(
    batch: EvidenceBatch,
    token: str | None,
) -> bool:
    expected = os.environ.get("NOSTRA_GATEWAY_TOKEN", "").strip()
    return bool(
        batch.events
        and all(event.source == "NOSTRA" for event in batch.events)
        and expected
        and token
        and hmac.compare_digest(token, expected)
    )


def require_nostra_gateway_token(token: str | None) -> None:
    expected = os.environ.get("NOSTRA_GATEWAY_TOKEN", "").strip()
    if not expected or token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def require_foundation_token(
    foundation_token: str | None = None,
    ingest_token: str | None = None,
) -> None:
    expected = os.environ.get("FOUNDATION_INGEST_TOKEN", "").strip()
    if not expected:
        return
    provided = foundation_token or ingest_token
    if provided is None or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def ensure_run(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str,
    strategy_version_id: str | None,
    observed_at: datetime,
    asset_class: str = "equity",
    mode: str = "live",
) -> None:
    cur.execute(
        """
        insert into rhen.strategy_runs (
            run_id, strategy_version_id, asset_class, mode,
            started_at, status, configuration
        )
        values (%s, %s, %s, %s, %s, 'running', '{}'::jsonb)
        on conflict (run_id) do update
        set strategy_version_id = case
                when rhen.strategy_runs.strategy_version_id = 'unknown'
                then excluded.strategy_version_id
                else rhen.strategy_runs.strategy_version_id
            end
        """,
        (
            run_id,
            strategy_version_id or "unknown",
            asset_class,
            mode,
            observed_at,
        ),
    )


def ensure_run_for_event(cur: psycopg.Cursor[Any], event: EvidenceEvent) -> None:
    if not event.run_id:
        return
    asset_class, mode = infer_run(event)
    ensure_run(
        cur,
        run_id=event.run_id,
        strategy_version_id=event.strategy_version_id,
        observed_at=event.occurred_at,
        asset_class=asset_class,
        mode=mode,
    )


def project_event(cur: psycopg.Cursor[Any], event: EvidenceEvent) -> None:
    payload = event.payload

    if event.event_type == "nostra_snapshot":
        cur.execute(
            """
            insert into nostra.evidence_snapshots (
                snapshot_id, as_of_timestamp, symbol, market_lane,
                feature_set_version, payload
            )
            values (%s,%s,%s,%s,%s,%s)
            on conflict (snapshot_id) do nothing
            """,
            (
                str(payload.get("snapshot_id") or ""),
                payload.get("as_of_timestamp") or event.occurred_at,
                payload.get("symbol") or event.symbol,
                str(payload.get("market_lane") or "research"),
                str(payload.get("feature_set_version") or "unknown"),
                Jsonb(payload),
            ),
        )
        return

    if event.event_type == "nostra_forecast":
        cur.execute(
            """
            insert into nostra.evidence_forecasts (
                forecast_id, snapshot_id, generated_at, symbol, market_lane,
                horizon_minutes, target_kind, model_id, model_version, payload
            )
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (forecast_id) do nothing
            """,
            (
                str(payload.get("forecast_id") or ""),
                str(payload.get("snapshot_id") or ""),
                payload.get("generated_at") or event.occurred_at,
                payload.get("symbol") or event.symbol,
                str(payload.get("market_lane") or "research"),
                int(payload.get("horizon_minutes") or 0),
                str(payload.get("target_kind") or ""),
                str(payload.get("model_id") or ""),
                str(payload.get("model_version") or ""),
                Jsonb(payload),
            ),
        )
        return

    if event.event_type == "nostra_outcome":
        cur.execute(
            """
            insert into nostra.evidence_outcomes (
                outcome_id, forecast_id, observed_at, payload
            )
            values (%s,%s,%s,%s)
            on conflict (outcome_id) do nothing
            """,
            (
                str(payload.get("outcome_id") or ""),
                str(payload.get("forecast_id") or ""),
                payload.get("observed_at") or event.occurred_at,
                Jsonb(payload),
            ),
        )
        return

    if event.event_type == "nostra_score":
        cur.execute(
            """
            insert into nostra.evidence_scores (
                score_id, forecast_id, outcome_id, scoring_version, payload
            )
            values (%s,%s,%s,%s,%s)
            on conflict (score_id) do nothing
            """,
            (
                str(payload.get("score_id") or ""),
                str(payload.get("forecast_id") or ""),
                str(payload.get("outcome_id") or ""),
                str(payload.get("scoring_version") or ""),
                Jsonb(payload),
            ),
        )
        return

    if event.event_type == "nostra_evaluation":
        cur.execute(
            """
            insert into nostra.evidence_evaluations (
                evaluation_id, evaluated_at, model_id, model_version,
                horizon_minutes, target_kind, window_start, window_end,
                sample_count, through_score_id, payload
            )
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (evaluation_id) do nothing
            """,
            (
                str(payload.get("evaluation_id") or ""),
                payload.get("evaluated_at") or event.occurred_at,
                str(payload.get("model_id") or ""),
                str(payload.get("model_version") or ""),
                int(payload.get("horizon_minutes") or 0),
                str(payload.get("target_kind") or ""),
                payload.get("window_start"),
                payload.get("window_end"),
                int(payload.get("sample_count") or 0),
                str(payload.get("through_score_id") or ""),
                Jsonb(payload),
            ),
        )
        return

    if not event.run_id:
        return

    if event.event_type == "order_intent":
        intent = event.payload.get("intent") or {}
        client_order_id = str(intent.get("idempotency_key") or "").strip()
        intent_id = str(intent.get("intent_id") or "").strip()
        if client_order_id and intent_id:
            cur.execute(
                """
                insert into rhen.order_intents (
                    intent_id, run_id, strategy_version_id, client_order_id,
                    symbol, side, intended_at, state, correlation_id, payload
                )
                values (%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)
                on conflict (run_id, client_order_id) do update
                set strategy_version_id = excluded.strategy_version_id,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    payload = rhen.order_intents.payload || excluded.payload,
                    updated_at = now()
                """,
                (
                    intent_id,
                    event.run_id,
                    event.strategy_version_id,
                    client_order_id,
                    str(intent.get("symbol") or event.symbol or "").upper() or None,
                    intent.get("side"),
                    intent.get("intended_at") or event.occurred_at,
                    event.correlation_id,
                    Jsonb(intent),
                ),
            )

    elif event.event_type == "broker_order":
        order = event.payload.get("order") or {}
        broker_order_id = str(order.get("id") or "").strip()
        if broker_order_id:
            cur.execute(
                """
                insert into rhen.orders (
                    broker_order_id, run_id, client_order_id, symbol, side,
                    order_type, status, qty, filled_qty, limit_price, stop_price,
                    submitted_at, filled_at, canceled_at, broker_payload,
                    observed_at, updated_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                on conflict (broker_order_id) do update
                set run_id = excluded.run_id,
                    client_order_id = excluded.client_order_id,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    order_type = excluded.order_type,
                    status = excluded.status,
                    qty = excluded.qty,
                    filled_qty = excluded.filled_qty,
                    limit_price = excluded.limit_price,
                    stop_price = excluded.stop_price,
                    submitted_at = excluded.submitted_at,
                    filled_at = excluded.filled_at,
                    canceled_at = excluded.canceled_at,
                    broker_payload = excluded.broker_payload,
                    observed_at = excluded.observed_at,
                    updated_at = now()
                """,
                (
                    broker_order_id,
                    event.run_id,
                    order.get("client_order_id"),
                    str(order.get("symbol") or event.symbol or "").upper(),
                    order.get("side"),
                    order.get("type"),
                    str(order.get("status") or "unknown"),
                    order.get("qty"),
                    order.get("filled_qty"),
                    order.get("limit_price"),
                    order.get("stop_price"),
                    order.get("submitted_at"),
                    order.get("filled_at"),
                    order.get("canceled_at"),
                    Jsonb(order),
                    event.occurred_at,
                ),
            )

    elif event.event_type == "broker_fill":
        activity = event.payload.get("activity") or {}
        fill_id = str(activity.get("id") or "").strip()
        broker_order_id = str(activity.get("order_id") or "").strip()
        if fill_id and broker_order_id:
            cur.execute(
                "select 1 from rhen.orders where broker_order_id=%s",
                (broker_order_id,),
            )
            if cur.fetchone():
                cur.execute(
                    """
                    insert into rhen.fills (
                        fill_id, broker_order_id, run_id, symbol, side,
                        qty, price, filled_at, broker_payload, observed_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (fill_id) do update
                    set broker_payload=excluded.broker_payload,
                        observed_at=excluded.observed_at
                    """,
                    (
                        fill_id,
                        broker_order_id,
                        event.run_id,
                        str(activity.get("symbol") or event.symbol or "").upper(),
                        activity.get("side"),
                        activity.get("qty") or activity.get("quantity") or "0",
                        activity.get("price") or "0",
                        activity.get("transaction_time")
                        or activity.get("date")
                        or event.occurred_at,
                        Jsonb(activity),
                        event.occurred_at,
                    ),
                )


def reconciliation_result(
    *,
    unresolved_intents: list[dict[str, Any]],
    unknown_open_orders: list[dict[str, Any]],
    untracked_positions: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[str, Any]:
    safe = not (
        unresolved_intents or unknown_open_orders or untracked_positions
    )
    blockers: list[str] = []
    if unresolved_intents:
        blockers.append(f"unresolved_intents:{len(unresolved_intents)}")
    if unknown_open_orders:
        blockers.append(f"unknown_open_orders:{len(unknown_open_orders)}")
    if untracked_positions:
        blockers.append(f"untracked_positions:{len(untracked_positions)}")
    return {
        "safe_to_enter": safe,
        "reason": "reconciled" if safe else ",".join(blockers),
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "unresolved_intents": unresolved_intents,
        "unknown_open_orders": unknown_open_orders,
        "untracked_positions": untracked_positions,
    }


app = FastAPI(title="ANEVUM RHEN Canonical Persistence", version="0.2.0")


@app.get("/live")
def live() -> dict[str, Any]:
    return {"ok": True, "service": "foundation-ingest", "authority": "rhen-canonical-ready"}


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
def status(
    x_anevum_foundation_token: str | None = Header(default=None, alias="x-anevum-foundation-token"),
    x_anevum_ingest_token: str | None = Header(default=None, alias="x-anevum-ingest-token"),
) -> dict[str, Any]:
    require_foundation_token(x_anevum_foundation_token, x_anevum_ingest_token)
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*)::bigint, max(ingested_at) from rhen.events")
                count, last_ingested_at = cur.fetchone()
                cur.execute("select count(*)::bigint from rhen.reconciliations")
                reconciliations = int(cur.fetchone()[0])
        return {
            "ok": True,
            "event_count": int(count),
            "reconciliation_count": reconciliations,
            "last_ingested_at": last_ingested_at.isoformat() if last_ingested_at else None,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"status_unavailable:{type(exc).__name__}",
        ) from exc


@app.post("/v1/events")
def ingest(
    batch: EvidenceBatch,
    x_anevum_foundation_token: str | None = Header(default=None, alias="x-anevum-foundation-token"),
    x_anevum_ingest_token: str | None = Header(default=None, alias="x-anevum-ingest-token"),
    x_nostra_gateway_token: str | None = Header(default=None, alias="x-nostra-gateway-token"),
) -> dict[str, Any]:
    if not nostra_gateway_authorized(batch, x_nostra_gateway_token):
        require_foundation_token(x_anevum_foundation_token, x_anevum_ingest_token)
    inserted = 0
    duplicates = 0
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                for event in batch.events:
                    ensure_run_for_event(cur, event)
                    cur.execute(
                        """
                        insert into rhen.events (
                            event_key, run_id, strategy_version_id, event_type,
                            occurred_at, symbol, correlation_id, source,
                            payload, payload_hash
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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

                    # Projection is independently idempotent and must run for
                    # duplicate event rows too. During migration the durable
                    # outbox and canonical sender can race on the same key;
                    # whichever arrives second still has to materialize the
                    # intent/order/fill projection required by reconciliation.
                    project_event(cur, event)
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


@app.get("/v1/scheduler-gateway")
def scheduler_gateway_get(
    limit: int = 100,
    x_anevum_foundation_token: str | None = Header(
        default=None,
        alias="x-anevum-foundation-token",
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(
        x_anevum_foundation_token,
        x_anevum_ingest_token,
    )
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            runs = recent_runs(conn, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"scheduler_gateway_read_failed:{type(exc).__name__}",
        ) from exc
    return {
        "ok": True,
        "service": "anevum-iren-scheduler-gateway",
        "runs": runs,
    }


@app.post("/v1/scheduler-gateway")
def scheduler_gateway_post(
    body: dict[str, Any],
    x_anevum_foundation_token: str | None = Header(
        default=None,
        alias="x-anevum-foundation-token",
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(
        x_anevum_foundation_token,
        x_anevum_ingest_token,
    )
    action = str(body.get("action") or "")
    try:
        result = handle_iren_action(database_url(), action, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"scheduler_gateway_failed:{type(exc).__name__}",
        ) from exc
    return {
        "ok": True,
        "service": "anevum-iren-scheduler-gateway",
        **result,
    }


@app.get("/v1/crypto-promotion-status")
def crypto_promotion_status(
    strategy_family: str,
    strategy_version_id: str,
    model_version: str,
    calibration_version: str,
    regime_version: str,
    execution_adapter_version: str,
    x_anevum_foundation_token: str | None = Header(
        default=None,
        alias="x-anevum-foundation-token",
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(
        x_anevum_foundation_token,
        x_anevum_ingest_token,
    )
    contract = {
        "strategy_family": strategy_family,
        "strategy_version_id": strategy_version_id,
        "model_version": model_version,
        "calibration_version": calibration_version,
        "regime_version": regime_version,
        "execution_adapter_version": execution_adapter_version,
    }
    try:
        result = handle_graen_action(
            database_url(),
            "crypto_promotion_status",
            {"execution_contract": contract},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"crypto_promotion_status_failed:{type(exc).__name__}",
        ) from exc
    return {
        "ok": True,
        **result,
        "execution_authority": False,
        "live_execution_authorized": False,
    }


@app.get("/v1/graen-gateway")
def graen_gateway_get(
    x_graen_gateway_token: str | None = Header(
        default=None,
        alias="x-graen-gateway-token",
    ),
) -> dict[str, Any]:
    expected = os.environ.get("GRAEN_GATEWAY_TOKEN", "").strip()
    if not expected:
        expected = os.environ.get("FOUNDATION_INGEST_TOKEN", "").strip()
    if not expected or x_graen_gateway_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = handle_graen_action(database_url(), None, method="GET")
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"graen_gateway_failed:{type(exc).__name__}",
        ) from exc
    return {"ok": True, **payload}


@app.post("/v1/graen-gateway")
def graen_gateway_post(
    body: dict[str, Any],
    x_graen_gateway_token: str | None = Header(
        default=None,
        alias="x-graen-gateway-token",
    ),
) -> dict[str, Any]:
    expected = os.environ.get("GRAEN_GATEWAY_TOKEN", "").strip()
    if not expected:
        expected = os.environ.get("FOUNDATION_INGEST_TOKEN", "").strip()
    if not expected or x_graen_gateway_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    action = str(body.get("action") or "")
    try:
        payload = handle_graen_action(database_url(), action, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"graen_gateway_failed:{type(exc).__name__}",
        ) from exc
    return {"ok": True, **payload}


async def require_command_access(
    cf_access_jwt_assertion: str | None,
) -> dict[str, Any]:
    try:
        return await verify_access_assertion(
            cf_access_jwt_assertion,
            team_domain=os.environ.get("CF_ACCESS_TEAM_DOMAIN", ""),
            audience=os.environ.get("CF_ACCESS_AUD", ""),
            allowed_emails=os.environ.get("COMMAND_ACCESS_EMAILS", ""),
        )
    except AccessConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AccessAuthorizationError as exc:
        status = 403 if "not_allowed" in str(exc) else 401
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/v1/command/iren")
async def command_iren_get(
    cf_access_jwt_assertion: str | None = Header(
        default=None,
        alias="Cf-Access-Jwt-Assertion",
    ),
) -> dict[str, Any]:
    await require_command_access(cf_access_jwt_assertion)
    try:
        return project_command(read_command_snapshot(database_url()))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"iren_command_failed:{type(exc).__name__}",
        ) from exc


@app.post("/v1/command/iren", status_code=202)
async def command_iren_post(
    request: Request,
    cf_access_jwt_assertion: str | None = Header(
        default=None,
        alias="Cf-Access-Jwt-Assertion",
    ),
) -> dict[str, Any]:
    identity = await require_command_access(cf_access_jwt_assertion)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_json")
    command = str(body.get("command") or "").strip()[:4000]
    if not command:
        raise HTTPException(status_code=400, detail="command_required")
    try:
        created = enqueue_iren_command(
            database_url(),
            command=command,
            requested_by=str(identity.get("email") or "command-admin"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"iren_command_failed:{type(exc).__name__}",
        ) from exc
    return {
        "schema_version": "iren_command.v2",
        "accepted": True,
        "command": created.get("command"),
    }


@app.get("/v1/trading-public-feed")
def trading_public_feed() -> dict[str, Any]:
    try:
        return read_public_feed(database_url())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"public_feed_failed:{type(exc).__name__}",
        ) from exc


@app.get("/v1/nostra-gateway")
def nostra_gateway_get(
    x_nostra_gateway_token: str | None = Header(
        default=None,
        alias="x-nostra-gateway-token",
    ),
) -> dict[str, Any]:
    require_nostra_gateway_token(x_nostra_gateway_token)
    try:
        return read_nostra_work(database_url())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"nostra_gateway_failed:{type(exc).__name__}",
        ) from exc


@app.get("/v1/nostra-forecasts")
def nostra_forecasts_get(
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(ingest_token=x_anevum_ingest_token)
    try:
        return read_nostra_forecasts(database_url())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"nostra_forecast_read_failed:{type(exc).__name__}",
        ) from exc


@app.get("/v1/research-agent-gateway")
def research_agent_gateway_get(
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(ingest_token=x_anevum_ingest_token)
    try:
        evidence = read_evidence(database_url())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"research_agent_gateway_failed:{type(exc).__name__}",
        ) from exc
    return {"ok": True, "evidence": evidence}


@app.post("/v1/research-agent-gateway")
async def research_agent_gateway_post(
    request: Request,
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(ingest_token=x_anevum_ingest_token)
    content_length = int(request.headers.get("content-length") or "0")
    if content_length > 512_000:
        raise HTTPException(status_code=413, detail="payload_too_large")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_json")
    action = str(body.get("action") or "")
    if action not in {"record_run", "record_run_and_search_ledger"}:
        raise HTTPException(status_code=400, detail="invalid_action")
    run = body.get("run")
    if not isinstance(run, dict):
        raise HTTPException(status_code=400, detail="invalid_run_record")
    try:
        result = record_run(database_url(), run)
        if action == "record_run":
            if result.get("duplicate"):
                raise HTTPException(status_code=409, detail="duplicate_run_key")
            return {"ok": True, **result}
        ledger = body.get("search_ledger")
        if not isinstance(ledger, dict):
            raise HTTPException(status_code=400, detail="invalid_search_ledger")
        ledger_result = record_search_ledger(database_url(), ledger)
        return {
            "ok": True,
            **result,
            "search_ledger_recorded": True,
            "search_ledger": ledger_result,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"research_agent_gateway_failed:{type(exc).__name__}",
        ) from exc


@app.get("/v1/trading-report-read")
def trading_report_read(
    request: Request,
    x_anevum_foundation_token: str | None = Header(
        default=None,
        alias="x-anevum-foundation-token",
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    require_foundation_token(
        x_anevum_foundation_token,
        x_anevum_ingest_token,
    )
    params = {key: value for key, value in request.query_params.items()}
    try:
        payload = read_report(database_url(), params)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"report_read_failed:{type(exc).__name__}",
        ) from exc
    if payload.get("ok") is False:
        error = str(payload.get("error") or "invalid_request")
        status_code = 422 if error.startswith("invalid_") else 400
        raise HTTPException(status_code=status_code, detail=error)
    return payload


@app.post("/v1/trading-reconcile")
def trading_reconcile(
    body: dict[str, Any],
    x_anevum_foundation_token: str | None = Header(default=None, alias="x-anevum-foundation-token"),
    x_anevum_ingest_token: str | None = Header(default=None, alias="x-anevum-ingest-token"),
) -> dict[str, Any]:
    require_foundation_token(x_anevum_foundation_token, x_anevum_ingest_token)
    action = str(body.get("action") or "")

    if action == "resolve_intent":
        intent = body.get("intent") or {}
        run_id = str(intent.get("run_id") or "")
        client_order_id = str(intent.get("client_order_id") or "")
        checked_raw = str(intent.get("checked_at") or "")
        if not run_id or not client_order_id or intent.get("state") != "broker_not_found":
            raise HTTPException(status_code=400, detail="invalid_intent_resolution")
        try:
            checked_at = datetime.fromisoformat(checked_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_intent_resolution") from exc

        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update rhen.order_intents
                    set state='broker_not_found',
                        reconciliation_checked_at=%s,
                        payload=payload || %s,
                        updated_at=now()
                    where run_id=%s and client_order_id=%s
                    returning intent_id, strategy_version_id
                    """,
                    (
                        checked_at,
                        Jsonb({"reconciliation_state": "broker_not_found"}),
                        run_id,
                        client_order_id,
                    ),
                )
                row = cur.fetchone()
                if row:
                    event_key = f"{run_id}:intent-reconcile:{client_order_id}:broker_not_found"
                    cur.execute(
                        """
                        insert into rhen.events (
                            event_key, run_id, strategy_version_id, event_type,
                            occurred_at, correlation_id, source, payload
                        )
                        values (%s,%s,%s,'intent_reconciliation',%s,%s,'alpaca-trader',%s)
                        on conflict (event_key) do nothing
                        """,
                        (
                            event_key,
                            run_id,
                            row[1],
                            checked_at,
                            intent.get("correlation_id"),
                            Jsonb({
                                "client_order_id": client_order_id,
                                "state": "broker_not_found",
                            }),
                        ),
                    )
                conn.commit()
        return {"ok": True, "updated": 1 if row else 0}

    if action != "reconcile":
        raise HTTPException(status_code=400, detail="unsupported_action")

    input_data = body.get("reconcile") or {}
    run_id = str(input_data.get("run_id") or "")
    strategy_version_id = str(input_data.get("strategy_version_id") or "")
    observed_raw = str(input_data.get("observed_at") or "")
    managed_symbols = [
        str(x).upper()
        for x in (input_data.get("managed_symbols") or [])
        if str(x).strip()
    ]
    broker_positions = input_data.get("broker_positions") or []
    open_orders = input_data.get("open_orders") or []
    if not run_id or not strategy_version_id or not isinstance(broker_positions, list) or not isinstance(open_orders, list):
        raise HTTPException(status_code=400, detail="invalid_reconciliation")
    try:
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_reconciliation") from exc

    with psycopg.connect(database_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            ensure_run(
                cur,
                run_id=run_id,
                strategy_version_id=strategy_version_id,
                observed_at=observed_at,
            )
            cur.execute(
                """
                select intent_id, client_order_id, symbol, side, intended_at
                from rhen.order_intents i
                where i.run_id=%s
                  and i.state='pending'
                  and not exists (
                    select 1 from rhen.orders o
                    where o.run_id=i.run_id
                      and o.client_order_id=i.client_order_id
                  )
                order by intended_at
                """,
                (run_id,),
            )
            unresolved_intents = [
                {
                    "intent_id": row[0],
                    "client_order_id": row[1],
                    "symbol": row[2],
                    "side": row[3],
                    "intended_at": row[4].isoformat(),
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                "select client_order_id from rhen.orders where run_id=%s and client_order_id is not null",
                (run_id,),
            )
            known_order_ids = {str(row[0]) for row in cur.fetchall()}

            unknown_open_orders = []
            for order in open_orders:
                client_order_id = str(order.get("client_order_id") or "")
                if not client_order_id.startswith("anevum-"):
                    continue
                if client_order_id not in known_order_ids:
                    unknown_open_orders.append({
                        "id": order.get("id"),
                        "client_order_id": client_order_id,
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "status": order.get("status"),
                    })

            cur.execute(
                """
                select distinct symbol
                from rhen.orders
                where run_id=%s
                  and lower(coalesce(side,''))='buy'
                  and coalesce(filled_qty,0) > 0
                """,
                (run_id,),
            )
            symbols_with_filled_buys = {str(row[0]).upper() for row in cur.fetchall()}

            untracked_positions = []
            managed = set(managed_symbols)
            for position in broker_positions:
                symbol = str(position.get("symbol") or "").upper()
                try:
                    qty = float(position.get("qty") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 0 or (managed and symbol not in managed):
                    continue
                if symbol not in symbols_with_filled_buys:
                    untracked_positions.append({
                        "symbol": symbol,
                        "qty": position.get("qty"),
                        "market_value": position.get("market_value"),
                    })

            result = reconciliation_result(
                unresolved_intents=unresolved_intents,
                unknown_open_orders=unknown_open_orders,
                untracked_positions=untracked_positions,
                observed_at=observed_at,
            )
            cur.execute(
                """
                insert into rhen.reconciliations (
                    run_id, strategy_version_id, observed_at, managed_symbols,
                    broker_positions, open_orders, unresolved_intents,
                    unknown_open_orders, untracked_positions,
                    safe_to_enter, reason
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    run_id,
                    strategy_version_id,
                    observed_at,
                    managed_symbols,
                    Jsonb(broker_positions),
                    Jsonb(open_orders),
                    Jsonb(unresolved_intents),
                    Jsonb(unknown_open_orders),
                    Jsonb(untracked_positions),
                    result["safe_to_enter"],
                    result["reason"],
                ),
            )
            conn.commit()

    return {"ok": True, "result": result}
