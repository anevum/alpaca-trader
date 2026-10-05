from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.platform_core.broker import TenantBrokerAccount
from app.platform_core.reconciliation import BrokerReconciliationResult


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid_{field}") from exc


def load_tenant_broker_account(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> TenantBrokerAccount:
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker_account_id, "broker_account_id")
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                b.tenant_id,
                b.broker_account_id,
                b.provider_account_id,
                b.environment,
                a.authorization_kind,
                a.secret_reference
            from anevum.broker_accounts b
            join anevum.broker_authorizations a
              on a.broker_account_id=b.broker_account_id
             and a.status='ACTIVE'
            where b.tenant_id=%s
              and b.broker_account_id=%s
              and b.provider='ALPACA'
            """,
            (tenant_uuid, broker_uuid),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError("tenant_broker_account_not_found_or_not_authorized")
    if len(rows) != 1:
        raise ValueError("ambiguous_active_broker_authorization")
    row = rows[0]
    return TenantBrokerAccount(
        tenant_id=str(row[0]),
        broker_account_id=str(row[1]),
        provider_account_id=str(row[2]),
        environment=str(row[3]),
        authorization_kind=str(row[4]),
        secret_reference=str(row[5]),
    )


def record_broker_reconciliation(
    conn: psycopg.Connection[Any],
    result: BrokerReconciliationResult,
) -> dict[str, Any]:
    tenant_uuid = _uuid(result.tenant_id, "tenant_id")
    broker_uuid = _uuid(result.broker_account_id, "broker_account_id")
    record = result.to_record()

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into anevum.broker_reconciliations (
                    tenant_id,
                    broker_account_id,
                    status,
                    environment,
                    provider,
                    provider_account_id_expected,
                    provider_account_id_observed,
                    observed_at,
                    account_snapshot,
                    positions_snapshot,
                    open_orders_snapshot,
                    recent_orders_snapshot,
                    snapshot_hash,
                    error_code,
                    error_detail
                )
                values (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                on conflict (
                    tenant_id,
                    broker_account_id,
                    snapshot_hash
                ) where status='SUCCESS'
                do update set completed_at=now()
                returning reconciliation_id,status,observed_at,completed_at
                """,
                (
                    tenant_uuid,
                    broker_uuid,
                    record["status"],
                    record["environment"],
                    record["provider"],
                    record["provider_account_id_expected"],
                    record["provider_account_id_observed"],
                    record["observed_at"],
                    Jsonb(record["account_snapshot"]) if record["account_snapshot"] is not None else None,
                    Jsonb(record["positions_snapshot"]) if record["positions_snapshot"] is not None else None,
                    Jsonb(record["open_orders_snapshot"]) if record["open_orders_snapshot"] is not None else None,
                    Jsonb(record["recent_orders_snapshot"]) if record["recent_orders_snapshot"] is not None else None,
                    record["snapshot_hash"],
                    record["error_code"],
                    record["error_detail"],
                ),
            )
            row = cur.fetchone()
            columns = [column.name for column in cur.description]

            if result.ready:
                cur.execute(
                    """
                    update anevum.broker_accounts
                    set last_reconciled_at=%s,
                        account_status=coalesce(%s, account_status),
                        trading_blocked=coalesce(%s, trading_blocked),
                        updated_at=now()
                    where tenant_id=%s
                      and broker_account_id=%s
                    """,
                    (
                        result.observed_at,
                        str((result.account_snapshot or {}).get("status") or "").upper() or None,
                        (
                            bool((result.account_snapshot or {}).get("trading_blocked"))
                            if "trading_blocked" in (result.account_snapshot or {})
                            else None
                        ),
                        tenant_uuid,
                        broker_uuid,
                    ),
                )

    return dict(zip(columns, row))


def latest_broker_reconciliation(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> dict[str, Any] | None:
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker_account_id, "broker_account_id")
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                reconciliation_id,status,environment,provider,
                provider_account_id_expected,provider_account_id_observed,
                observed_at,completed_at,snapshot_hash,error_code,error_detail
            from anevum.broker_reconciliations
            where tenant_id=%s and broker_account_id=%s
            order by observed_at desc, created_at desc
            limit 1
            """,
            (tenant_uuid, broker_uuid),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [column.name for column in cur.description]
    return dict(zip(columns, row))
