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
) -> dict[str, Any]:
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
