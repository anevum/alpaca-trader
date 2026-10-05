from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.platform_core.broker import TenantBrokerAccount
from app.platform_core.contracts import (
    ExecutionEligibility,
    TradingEligibilityInput,
    evaluate_execution_eligibility,
)
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



def tenant_execution_eligibility(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
    max_reconciliation_age_seconds: int = 120,
    now: datetime | None = None,
) -> tuple[TradingEligibilityInput, ExecutionEligibility]:
    """Resolve canonical database facts into the fail-closed execution gate.

    Protected customer live authority is deliberately hard-coded false in
    Platform Core v1. A later separately protected authority mechanism must be
    designed and reviewed before customer live execution can become eligible.
    """

    if max_reconciliation_age_seconds <= 0:
        raise ValueError("max_reconciliation_age_seconds must be positive")

    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker_account_id, "broker_account_id")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    reference = reference.astimezone(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            select
                t.status as tenant_status,
                b.environment,
                b.account_status as broker_account_status,
                b.crypto_enabled,
                b.trading_blocked,
                coalesce((
                    select e.status
                    from anevum.entitlements e
                    where e.tenant_id=t.tenant_id
                      and e.product_key='COMMAND'
                      and e.effective_at <= %s
                      and (e.expires_at is null or e.expires_at > %s)
                    order by
                        case e.status when 'ACTIVE' then 0 when 'TRIAL' then 1 else 2 end,
                        e.effective_at desc
                    limit 1
                ), 'MISSING') as entitlement_status,
                exists (
                    select 1
                    from anevum.capital_allocations a
                    where a.tenant_id=t.tenant_id
                      and a.broker_account_id=b.broker_account_id
                      and a.status='ACTIVE'
                ) as allocation_active,
                exists (
                    select 1
                    from anevum.capital_allocations a
                    where a.tenant_id=t.tenant_id
                      and a.broker_account_id=b.broker_account_id
                      and a.status='ACTIVE'
                      and a.allocation_fraction > 0
                      and a.absolute_cap > 0
                ) as allocation_positive,
                exists (
                    select 1
                    from anevum.risk_profiles rp
                    where rp.tenant_id=t.tenant_id
                      and rp.broker_account_id=b.broker_account_id
                      and rp.status='ACTIVE'
                ) as risk_profile_active,
                tsa.assignment_id is not null as strategy_assignment_active,
                coalesce(sr.lifecycle_state, 'MISSING') as strategy_release_state,
                coalesce(tc.bot_enabled, false) as customer_bot_enabled,
                (
                    tc.customer_consent_version is not null
                    and tc.customer_consented_at is not null
                ) as customer_trading_consent,
                coalesce((
                    select upper(s.health)='HEALTHY'
                    from iren.system_state s
                    where s.system_key='IREN'
                ), false) as iren_fleet_healthy
            from anevum.tenants t
            join anevum.broker_accounts b
              on b.tenant_id=t.tenant_id
            left join anevum.tenant_strategy_assignments tsa
              on tsa.tenant_id=t.tenant_id
             and tsa.broker_account_id=b.broker_account_id
             and tsa.status='ACTIVE'
            left join anevum.strategy_releases sr
              on sr.strategy_release_id=tsa.strategy_release_id
            left join anevum.tenant_trading_controls tc
              on tc.tenant_id=t.tenant_id
             and tc.broker_account_id=b.broker_account_id
            where t.tenant_id=%s
              and b.broker_account_id=%s
            """,
            (reference, reference, tenant_uuid, broker_uuid),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("tenant_broker_account_not_found")
        columns = [column.name for column in cur.description]
        facts = dict(zip(columns, row))

    reconciliation = latest_broker_reconciliation(
        conn,
        tenant_id=tenant_id,
        broker_account_id=broker_account_id,
    )
    broker_reconciled = False
    if reconciliation and reconciliation.get("status") == "SUCCESS":
        observed_at = reconciliation.get("observed_at")
        if isinstance(observed_at, datetime):
            observed = observed_at
            if observed.tzinfo is None or observed.utcoffset() is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = reference - observed.astimezone(timezone.utc)
            broker_reconciled = (
                timedelta(0)
                <= age
                <= timedelta(seconds=max_reconciliation_age_seconds)
            )

    gate_input = TradingEligibilityInput(
        environment=str(facts["environment"]),
        tenant_status=str(facts["tenant_status"]),
        entitlement_status=str(facts["entitlement_status"]),
        broker_account_status=str(facts["broker_account_status"]),
        broker_crypto_enabled=bool(facts["crypto_enabled"]),
        broker_trading_blocked=bool(facts["trading_blocked"]),
        broker_reconciled=broker_reconciled,
        allocation_active=bool(facts["allocation_active"]),
        allocation_positive=bool(facts["allocation_positive"]),
        risk_profile_active=bool(facts["risk_profile_active"]),
        strategy_assignment_active=bool(facts["strategy_assignment_active"]),
        strategy_release_state=str(facts["strategy_release_state"]),
        customer_trading_consent=bool(facts["customer_trading_consent"]),
        customer_bot_enabled=bool(facts["customer_bot_enabled"]),
        iren_fleet_healthy=bool(facts["iren_fleet_healthy"]),
        live_customer_authority=False,
    )
    return gate_input, evaluate_execution_eligibility(gate_input)
