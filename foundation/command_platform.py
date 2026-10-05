from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.types.json import Jsonb

from app.platform_core.broker import TenantAlpacaReadClient, TenantBrokerAccount
from app.platform_core.lifecycle import (
    PaperCustomerFacts,
    derive_paper_customer_lifecycle,
    paper_funding_projection,
)
from app.platform_core.reconciliation import TenantBrokerReconciler
from foundation.platform_core_gateway import (
    latest_broker_reconciliation,
    record_broker_reconciliation,
    tenant_execution_eligibility,
)


UTC = timezone.utc
ALPACA_AUTHORIZE_URL = "https://app.alpaca.markets/oauth/authorize"
ALPACA_TOKEN_URL = "https://api.alpaca.markets/oauth/token"
ALPACA_PAPER_API = "https://paper-api.alpaca.markets"
PAPER_SCOPE = "trading"
PAPER_BETA_KEY_VERSION = "command-paper-beta-v1"


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _rows(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    columns = [column.name for column in cur.description]
    return [_serialize(dict(zip(columns, row))) for row in cur.fetchall()]


def _email(value: Any) -> str:
    return str(value or "").strip().lower()


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc


def _admin_emails() -> set[str]:
    return {
        value.strip().lower()
        for value in os.environ.get("COMMAND_ACCESS_EMAILS", "").split(",")
        if value.strip()
    }


def resolve_command_session(
    conn: psycopg.Connection[Any],
    *,
    email: str,
) -> dict[str, Any]:
    normalized = _email(email)
    if not normalized:
        raise ValueError("identity_email_required")
    is_admin = normalized in _admin_emails()

    with conn.cursor() as cur:
        cur.execute(
            """
            select p.principal_id,p.email,p.status,
                   m.tenant_id,m.role,m.status as membership_status,
                   t.tenant_key,t.display_name,t.status as tenant_status
            from anevum.principals p
            left join anevum.tenant_memberships m
              on m.principal_id=p.principal_id and m.status='ACTIVE'
            left join anevum.tenants t on t.tenant_id=m.tenant_id
            where lower(p.email)=lower(%s)
              and p.status='ACTIVE'
            order by
              case m.role when 'OWNER' then 0 when 'ADMIN' then 1 else 2 end,
              t.created_at asc
            """,
            (normalized,),
        )
        rows = _rows(cur)

    principal_id = rows[0]["principal_id"] if rows else None
    tenants = [
        {
            "tenant_id": row["tenant_id"],
            "tenant_key": row["tenant_key"],
            "display_name": row["display_name"],
            "tenant_status": row["tenant_status"],
            "role": row["role"],
        }
        for row in rows
        if row.get("tenant_id")
    ]
    if not is_admin and not tenants:
        raise PermissionError("command_tenant_membership_required")

    return {
        "authenticated": True,
        "email": normalized,
        "auth_source": "cloudflare_access",
        "command_admin": is_admin,
        "principal_id": principal_id,
        "tenants": tenants,
        "active_tenant_id": tenants[0]["tenant_id"] if tenants else None,
        "surface": "operator" if is_admin else "customer",
    }


def _require_tenant_membership(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
    minimum_roles: set[str] | None = None,
    require_active: bool = False,
) -> dict[str, Any]:
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.principal_id,p.email,m.role,t.tenant_key,t.display_name,t.status
            from anevum.principals p
            join anevum.tenant_memberships m on m.principal_id=p.principal_id
            join anevum.tenants t on t.tenant_id=m.tenant_id
            where lower(p.email)=lower(%s)
              and p.status='ACTIVE'
              and m.status='ACTIVE'
              and t.tenant_id=%s
            """,
            (_email(email), tenant_uuid),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("command_tenant_membership_required")
        columns = [column.name for column in cur.description]
    result = _serialize(dict(zip(columns, row)))
    if minimum_roles and str(result.get("role") or "") not in minimum_roles:
        raise PermissionError("command_tenant_role_insufficient")
    if require_active and str(result.get("status") or "") != "ACTIVE":
        raise PermissionError("command_tenant_not_active")
    return result


def _broker_account(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select broker_account_id,provider,provider_account_id,environment,
                   account_status,crypto_enabled,trading_blocked,withdrawals_blocked,
                   last_reconciled_at,created_at,updated_at
            from anevum.broker_accounts
            where tenant_id=%s
            order by case environment when 'PAPER' then 0 else 1 end, created_at
            limit 1
            """,
            (_uuid(tenant_id, "tenant_id"),),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [column.name for column in cur.description]
    return _serialize(dict(zip(columns, row)))


def _active_allocation(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select allocation_id,allocation_mode,allocation_fraction,absolute_cap,
                   status,effective_at
            from anevum.capital_allocations
            where tenant_id=%s and broker_account_id=%s and status='ACTIVE'
            order by effective_at desc
            limit 1
            """,
            (_uuid(tenant_id, "tenant_id"), _uuid(broker_account_id, "broker_account_id")),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [column.name for column in cur.description]
    return _serialize(dict(zip(columns, row)))


def _active_risk(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select risk_profile_id,max_position_fraction,max_gross_exposure_fraction,
                   max_daily_loss_fraction,max_drawdown_fraction,max_concurrent_positions,
                   status,effective_at
            from anevum.risk_profiles
            where tenant_id=%s and broker_account_id=%s and status='ACTIVE'
            order by effective_at desc
            limit 1
            """,
            (_uuid(tenant_id, "tenant_id"), _uuid(broker_account_id, "broker_account_id")),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [column.name for column in cur.description]
    return _serialize(dict(zip(columns, row)))


def _trading_control(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select bot_enabled,customer_consent_version,customer_consented_at,
                   updated_by,updated_at
            from anevum.tenant_trading_controls
            where tenant_id=%s and broker_account_id=%s
            """,
            (_uuid(tenant_id, "tenant_id"), _uuid(broker_account_id, "broker_account_id")),
        )
        row = cur.fetchone()
        if not row:
            return {
                "bot_enabled": False,
                "customer_consent_version": None,
                "customer_consented_at": None,
                "updated_by": None,
                "updated_at": None,
            }
        columns = [column.name for column in cur.description]
    return _serialize(dict(zip(columns, row)))


def _strategy_assignment(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.assignment_id,a.strategy_release_id,a.status,a.assigned_at,
                   r.strategy_key,r.semantic_version,r.channel,r.lifecycle_state
            from anevum.tenant_strategy_assignments a
            join anevum.strategy_releases r
              on r.strategy_release_id=a.strategy_release_id
            where a.tenant_id=%s and a.broker_account_id=%s and a.status='ACTIVE'
            order by a.assigned_at desc
            limit 1
            """,
            (_uuid(tenant_id, "tenant_id"), _uuid(broker_account_id, "broker_account_id")),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [column.name for column in cur.description]
    return _serialize(dict(zip(columns, row)))


def _ensure_default_paper_strategy_assignment(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str,
    assigned_by: str = "platform-paper-beta",
) -> dict[str, Any] | None:
    """Assign an explicitly configured or unambiguous paper-capable release.

    This never creates or promotes a strategy release. If more than one eligible
    release exists and no explicit default is configured, it fails closed and
    leaves assignment pending rather than guessing.
    """

    current = _strategy_assignment(
        conn,
        tenant_id=tenant_id,
        broker_account_id=broker_account_id,
    )
    if current:
        return current

    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker_account_id, "broker_account_id")
    configured_release = os.environ.get(
        "COMMAND_PAPER_DEFAULT_STRATEGY_RELEASE_ID", ""
    ).strip()

    with conn.transaction():
        with conn.cursor() as cur:
            if configured_release:
                cur.execute(
                    """
                    select strategy_release_id
                    from anevum.strategy_releases
                    where strategy_release_id=%s
                      and lifecycle_state in (
                          'PAPER_PASSED','APPROVED','CANARY','STABLE'
                      )
                    """,
                    (configured_release,),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(
                        "configured_paper_strategy_release_not_eligible"
                    )
                release_id = str(row[0])
            else:
                cur.execute(
                    """
                    select strategy_release_id
                    from anevum.strategy_releases
                    where lifecycle_state in (
                        'PAPER_PASSED','APPROVED','CANARY','STABLE'
                    )
                    order by created_at desc
                    limit 2
                    """
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                if len(rows) > 1:
                    return None
                release_id = str(rows[0][0])

            cur.execute(
                """
                insert into anevum.tenant_strategy_assignments(
                    tenant_id,broker_account_id,strategy_release_id,status,assigned_by
                )
                select %s,%s,%s,'ACTIVE',%s
                where not exists (
                    select 1
                    from anevum.tenant_strategy_assignments
                    where tenant_id=%s
                      and broker_account_id=%s
                      and status='ACTIVE'
                )
                returning assignment_id
                """,
                (
                    tenant_uuid,
                    broker_uuid,
                    release_id,
                    assigned_by,
                    tenant_uuid,
                    broker_uuid,
                ),
            )
            inserted = cur.fetchone()
            if inserted:
                _audit(
                    cur,
                    tenant_id=tenant_uuid,
                    email=assigned_by,
                    action="PAPER_STRATEGY_ASSIGNED",
                    object_type="strategy_release",
                    object_id=release_id,
                    payload={"broker_account_id": str(broker_uuid)},
                    actor_type="system",
                )

    return _strategy_assignment(
        conn,
        tenant_id=tenant_id,
        broker_account_id=broker_account_id,
    )



def assign_paper_strategy_release(
    conn: psycopg.Connection[Any],
    *,
    operator_email: str,
    tenant_id: str,
    strategy_release_id: str,
) -> dict[str, Any]:
    """Explicitly assign an existing paper-capable release to one tenant.

    This is an operator action for resolving ambiguous defaults. It cannot create,
    promote, or alter a strategy release and it grants no live authority.
    """

    tenant_uuid = _uuid(tenant_id, "tenant_id")
    release_id = str(strategy_release_id or "").strip()
    if not release_id:
        raise ValueError("strategy_release_id_required")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                select status
                from anevum.tenants
                where tenant_id=%s
                """,
                (tenant_uuid,),
            )
            tenant_row = cur.fetchone()
            if not tenant_row:
                raise ValueError("tenant_not_found")
            if str(tenant_row[0] or "").upper() != "ACTIVE":
                raise ValueError("tenant_not_active")

            cur.execute(
                """
                select broker_account_id
                from anevum.broker_accounts
                where tenant_id=%s and environment='PAPER'
                order by created_at
                limit 1
                """,
                (tenant_uuid,),
            )
            broker_row = cur.fetchone()
            if not broker_row:
                raise ValueError("paper_broker_account_required")
            broker_uuid = broker_row[0]

            cur.execute(
                """
                select strategy_key,semantic_version,channel,lifecycle_state
                from anevum.strategy_releases
                where strategy_release_id=%s
                  and lifecycle_state in (
                      'PAPER_PASSED','APPROVED','CANARY','STABLE'
                  )
                """,
                (release_id,),
            )
            release = cur.fetchone()
            if not release:
                raise ValueError("strategy_release_not_paper_capable")

            cur.execute(
                """
                update anevum.tenant_strategy_assignments
                set status='SUPERSEDED',unassigned_at=now()
                where tenant_id=%s
                  and broker_account_id=%s
                  and status='ACTIVE'
                """,
                (tenant_uuid, broker_uuid),
            )
            cur.execute(
                """
                insert into anevum.tenant_strategy_assignments(
                    tenant_id,broker_account_id,strategy_release_id,
                    status,assigned_by
                )
                values(%s,%s,%s,'ACTIVE',%s)
                returning assignment_id,assigned_at
                """,
                (tenant_uuid, broker_uuid, release_id, operator_email),
            )
            assignment_id, assigned_at = cur.fetchone()
            _audit(
                cur,
                tenant_id=tenant_uuid,
                email=operator_email,
                action="PAPER_STRATEGY_ASSIGNED",
                object_type="strategy_release",
                object_id=release_id,
                payload={
                    "broker_account_id": str(broker_uuid),
                    "assignment_id": str(assignment_id),
                },
                actor_type="operator",
            )

    return {
        "tenant_id": str(tenant_uuid),
        "broker_account_id": str(broker_uuid),
        "assignment_id": str(assignment_id),
        "strategy_release_id": release_id,
        "strategy_key": str(release[0]),
        "semantic_version": str(release[1]),
        "channel": str(release[2]),
        "lifecycle_state": str(release[3]),
        "assigned_at": _serialize(assigned_at),
        "live_customer_authority": False,
    }


def _activity(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    broker_account_id: str | None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select occurred_at,action,object_type,object_id,payload
            from anevum.protected_audit_events
            where tenant_id=%s
            order by occurred_at desc
            limit 30
            """,
            (_uuid(tenant_id, "tenant_id"),),
        )
        audit = _rows(cur)
        reconciliations: list[dict[str, Any]] = []
        if broker_account_id:
            cur.execute(
                """
                select observed_at,status,error_code
                from anevum.broker_reconciliations
                where tenant_id=%s and broker_account_id=%s
                order by observed_at desc
                limit 15
                """,
                (_uuid(tenant_id, "tenant_id"), _uuid(broker_account_id, "broker_account_id")),
            )
            reconciliations = [
                {
                    "occurred_at": row["observed_at"],
                    "action": "BROKER_RECONCILIATION",
                    "object_type": "broker_account",
                    "object_id": broker_account_id,
                    "payload": {
                        "status": row["status"],
                        "error_code": row["error_code"],
                    },
                }
                for row in _rows(cur)
            ]
    return sorted(
        [*audit, *reconciliations],
        key=lambda row: str(row.get("occurred_at") or ""),
        reverse=True,
    )[:40]


def customer_overview(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    member = _require_tenant_membership(conn, email=email, tenant_id=tenant_id)
    broker = _broker_account(conn, tenant_id=tenant_id)
    broker_id = str((broker or {}).get("broker_account_id") or "") or None
    allocation = _active_allocation(
        conn, tenant_id=tenant_id, broker_account_id=broker_id
    ) if broker_id else None
    risk = _active_risk(
        conn, tenant_id=tenant_id, broker_account_id=broker_id
    ) if broker_id else None
    control = _trading_control(
        conn, tenant_id=tenant_id, broker_account_id=broker_id
    ) if broker_id else {"bot_enabled": False}
    strategy = _strategy_assignment(
        conn, tenant_id=tenant_id, broker_account_id=broker_id
    ) if broker_id else None
    reconciliation = latest_broker_reconciliation(
        conn, tenant_id=tenant_id, broker_account_id=broker_id
    ) if broker_id else None

    eligibility = None
    gate_reasons: list[str] = []
    if broker_id:
        gate_input, gate_result = tenant_execution_eligibility(
            conn,
            tenant_id=tenant_id,
            broker_account_id=broker_id,
        )
        gate_reasons = list(gate_result.reasons)
        eligibility = {
            "eligible": gate_result.eligible,
            "reasons": gate_reasons,
            "environment": gate_input.environment,
            "live_customer_authority": gate_input.live_customer_authority,
        }

    account = None
    positions: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    if reconciliation and reconciliation.get("status") == "SUCCESS":
        with conn.cursor() as cur:
            cur.execute(
                """
                select account_snapshot,positions_snapshot,open_orders_snapshot,
                       recent_orders_snapshot
                from anevum.broker_reconciliations
                where tenant_id=%s and broker_account_id=%s
                order by observed_at desc,created_at desc
                limit 1
                """,
                (_uuid(tenant_id, "tenant_id"), _uuid(broker_id, "broker_account_id")),
            )
            row = cur.fetchone()
        if row:
            account = row[0]
            positions = list(row[1] or [])
            orders = [*(row[2] or []), *(row[3] or [])][:50]

    funding = paper_funding_projection(
        account,
        observed_at=(reconciliation or {}).get("observed_at"),
    )

    lifecycle = derive_paper_customer_lifecycle(
        PaperCustomerFacts(
            tenant_status=str(member.get("status") or "REGISTERED"),
            broker_connected=bool(
                broker is not None and str(broker.get("environment") or "") == "PAPER"
            ),
            broker_active=bool(
                broker is not None and str(broker.get("account_status") or "").upper() == "ACTIVE"
            ),
            reconciliation_success=bool(
                reconciliation and reconciliation.get("status") == "SUCCESS"
            ),
            funded=bool(funding.get("funded")),
            allocation_active=allocation is not None,
            risk_active=risk is not None,
            strategy_assigned=strategy is not None,
            customer_consented=bool(control.get("customer_consented_at")),
            bot_enabled=bool(control.get("bot_enabled")),
            execution_eligible=bool((eligibility or {}).get("eligible")),
            execution_reasons=tuple(gate_reasons),
        )
    )

    steps = [
        {"key": "membership", "complete": True, "label": "Command access"},
        {
            "key": "broker",
            "complete": broker is not None and str(broker.get("environment")) == "PAPER",
            "label": "Connect Alpaca Paper",
        },
        {
            "key": "reconciliation",
            "complete": bool(reconciliation and reconciliation.get("status") == "SUCCESS"),
            "label": "Verify broker state",
        },
        {
            "key": "funding",
            "complete": bool(funding.get("funded")),
            "label": "Confirm Alpaca Paper funds",
        },
        {"key": "allocation", "complete": allocation is not None, "label": "Choose RHEN allocation"},
        {"key": "risk", "complete": risk is not None, "label": "Set risk limits"},
        {
            "key": "consent",
            "complete": bool(control.get("customer_consented_at")),
            "label": "Accept paper automation consent",
        },
        {"key": "strategy", "complete": strategy is not None, "label": "Receive strategy release"},
    ]

    return {
        "schema_version": "command_customer.v2",
        "surface": "customer",
        "tenant": {
            "tenant_id": tenant_id,
            "tenant_key": member.get("tenant_key"),
            "display_name": member.get("display_name"),
            "status": member.get("status"),
            "role": member.get("role"),
        },
        "lifecycle": lifecycle,
        "onboarding": {
            "complete": bool(lifecycle.get("setup_ready")),
            "steps": steps,
        },
        "broker": broker,
        "funding": funding,
        "allocation": allocation,
        "risk": risk,
        "control": control,
        "strategy": strategy,
        "eligibility": eligibility,
        "reconciliation": reconciliation,
        "account": account,
        "positions": positions,
        "orders": orders,
        "activity": _activity(conn, tenant_id=tenant_id, broker_account_id=broker_id),
        "authority": {
            "paper_only": True,
            "live_customer_trading": False,
            "withdrawals": False,
            "funding_mutations": False,
        },
    }


def _recent_broker_transfers(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select transfer_intent_id,direction,currency,amount,status,
                   provider,provider_transfer_id,created_at,updated_at,settled_at,
                   failure_code
            from anevum.broker_transfer_intents
            where tenant_id=%s
            order by created_at desc
            limit 25
            """,
            (_uuid(tenant_id, "tenant_id"),),
        )
        return _rows(cur)


def customer_account_projection(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    overview = customer_overview(conn, email=email, tenant_id=tenant_id)
    return {
        "schema_version": "command_account.v1",
        "tenant": overview["tenant"],
        "lifecycle": overview["lifecycle"],
        "onboarding": overview["onboarding"],
        "broker": overview["broker"],
        "authority": overview["authority"],
    }


def customer_overview_projection(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    overview = customer_overview(conn, email=email, tenant_id=tenant_id)
    control = overview.get("control") or {}
    eligibility = overview.get("eligibility") or {}
    if eligibility.get("eligible"):
        automation_state = "ACTIVE"
    elif control.get("bot_enabled"):
        automation_state = "ENABLED_PENDING"
    else:
        automation_state = "PAUSED"
    return {
        "schema_version": "command_overview.v1",
        "tenant": overview["tenant"],
        "lifecycle": overview["lifecycle"],
        "mode": "PAPER",
        "automation_state": automation_state,
        "action_required": (
            None
            if overview["lifecycle"].get("state") == "ACTIVE"
            else overview["lifecycle"].get("next_action")
        ),
        "funding": overview["funding"],
        "allocation": overview["allocation"],
        "strategy": overview["strategy"],
        "risk": overview["risk"],
        "position_count": len(overview.get("positions") or []),
        "order_count": len(overview.get("orders") or []),
        "reconciliation": overview["reconciliation"],
        "authority": overview["authority"],
    }


def customer_trading_projection(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    overview = customer_overview(conn, email=email, tenant_id=tenant_id)
    return {
        "schema_version": "command_trading.v1",
        "tenant": overview["tenant"],
        "lifecycle": overview["lifecycle"],
        "mode": "PAPER",
        "control": overview["control"],
        "strategy": overview["strategy"],
        "eligibility": overview["eligibility"],
        "allocation": overview["allocation"],
        "risk": overview["risk"],
        "positions": overview["positions"],
        "orders": overview["orders"],
        "reconciliation": overview["reconciliation"],
        "authority": overview["authority"],
    }


def customer_money_projection(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    overview = customer_overview(conn, email=email, tenant_id=tenant_id)
    transfers = _recent_broker_transfers(conn, tenant_id=tenant_id)
    return {
        "schema_version": "command_money.v1",
        "tenant": overview["tenant"],
        "mode": "PAPER",
        "broker": overview["broker"],
        "funding": overview["funding"],
        "allocation": overview["allocation"],
        "recent_transfers": transfers,
        "authority": {
            **overview["authority"],
            "broker_is_source_of_truth": True,
        },
    }


def customer_activity_projection(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    overview = customer_overview(conn, email=email, tenant_id=tenant_id)
    transfers = _recent_broker_transfers(conn, tenant_id=tenant_id)
    transfer_events = [
        {
            "occurred_at": row.get("updated_at") or row.get("created_at"),
            "action": f"BROKER_TRANSFER_{str(row.get('status') or 'UNKNOWN').upper()}",
            "object_type": "broker_transfer",
            "object_id": row.get("transfer_intent_id"),
            "payload": {
                "direction": row.get("direction"),
                "currency": row.get("currency"),
                "amount": row.get("amount"),
                "provider": row.get("provider"),
                "failure_code": row.get("failure_code"),
            },
        }
        for row in transfers
    ]
    activity = sorted(
        [*(overview.get("activity") or []), *transfer_events],
        key=lambda row: str(row.get("occurred_at") or ""),
        reverse=True,
    )[:50]
    return {
        "schema_version": "command_activity.v1",
        "tenant": overview["tenant"],
        "activity": activity,
    }


def _audit(
    cur: psycopg.Cursor[Any],
    *,
    tenant_id: UUID,
    email: str,
    action: str,
    object_type: str,
    object_id: str,
    payload: dict[str, Any] | None = None,
    actor_type: str = "customer",
) -> None:
    cur.execute(
        """
        insert into anevum.protected_audit_events(
            event_key,tenant_id,actor_type,actor_id,action,
            object_type,object_id,payload
        )
        values(%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            sha256(
                f"{tenant_id}|{email}|{action}|{object_type}|{object_id}|{uuid4()}".encode()
            ).hexdigest(),
            tenant_id,
            actor_type,
            email,
            action,
            object_type,
            object_id,
            Jsonb(payload or {}),
        ),
    )


def update_allocation(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
    allocation_fraction: float,
    absolute_cap: float,
) -> dict[str, Any]:
    _require_tenant_membership(
        conn,
        email=email,
        tenant_id=tenant_id,
        minimum_roles={"OWNER", "ADMIN"},
        require_active=True,
    )
    fraction = float(allocation_fraction)
    cap = float(absolute_cap)
    if not 0 < fraction <= 1:
        raise ValueError("allocation_fraction_out_of_range")
    if not 0 < cap <= 1_000_000:
        raise ValueError("allocation_absolute_cap_out_of_range")
    broker = _broker_account(conn, tenant_id=tenant_id)
    if not broker or broker.get("environment") != "PAPER":
        raise ValueError("paper_broker_account_required")
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker["broker_account_id"], "broker_account_id")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update anevum.capital_allocations
                set status='SUPERSEDED',superseded_at=now()
                where tenant_id=%s and broker_account_id=%s and status='ACTIVE'
                """,
                (tenant_uuid, broker_uuid),
            )
            cur.execute(
                """
                insert into anevum.capital_allocations(
                    tenant_id,broker_account_id,allocation_fraction,
                    absolute_cap,status
                )
                values(%s,%s,%s,%s,'ACTIVE')
                returning allocation_id
                """,
                (tenant_uuid, broker_uuid, fraction, cap),
            )
            allocation_id = str(cur.fetchone()[0])
            _audit(
                cur,
                tenant_id=tenant_uuid,
                email=email,
                action="ALLOCATION_UPDATED",
                object_type="capital_allocation",
                object_id=allocation_id,
                payload={"allocation_fraction": fraction, "absolute_cap": cap},
            )
    return customer_overview(conn, email=email, tenant_id=tenant_id)


def update_risk(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
    max_position_fraction: float,
    max_gross_exposure_fraction: float,
    max_daily_loss_fraction: float,
    max_drawdown_fraction: float,
    max_concurrent_positions: int,
) -> dict[str, Any]:
    _require_tenant_membership(
        conn,
        email=email,
        tenant_id=tenant_id,
        minimum_roles={"OWNER", "ADMIN"},
        require_active=True,
    )
    position = float(max_position_fraction)
    gross = float(max_gross_exposure_fraction)
    daily = float(max_daily_loss_fraction)
    drawdown = float(max_drawdown_fraction)
    concurrent = int(max_concurrent_positions)
    if not 0 < position <= 1 or not 0 < gross <= 1:
        raise ValueError("risk_exposure_out_of_range")
    if not 0 < daily <= 0.25 or not 0 < drawdown <= 0.50:
        raise ValueError("risk_loss_limit_out_of_range")
    if not 1 <= concurrent <= 20:
        raise ValueError("risk_position_count_out_of_range")
    broker = _broker_account(conn, tenant_id=tenant_id)
    if not broker or broker.get("environment") != "PAPER":
        raise ValueError("paper_broker_account_required")
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker["broker_account_id"], "broker_account_id")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update anevum.risk_profiles
                set status='SUPERSEDED',superseded_at=now()
                where tenant_id=%s and broker_account_id=%s and status='ACTIVE'
                """,
                (tenant_uuid, broker_uuid),
            )
            cur.execute(
                """
                insert into anevum.risk_profiles(
                    tenant_id,broker_account_id,max_position_fraction,
                    max_gross_exposure_fraction,max_daily_loss_fraction,
                    max_drawdown_fraction,max_concurrent_positions,status
                )
                values(%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                returning risk_profile_id
                """,
                (tenant_uuid, broker_uuid, position, gross, daily, drawdown, concurrent),
            )
            risk_id = str(cur.fetchone()[0])
            _audit(
                cur,
                tenant_id=tenant_uuid,
                email=email,
                action="RISK_PROFILE_UPDATED",
                object_type="risk_profile",
                object_id=risk_id,
                payload={
                    "max_position_fraction": position,
                    "max_gross_exposure_fraction": gross,
                    "max_daily_loss_fraction": daily,
                    "max_drawdown_fraction": drawdown,
                    "max_concurrent_positions": concurrent,
                },
            )
    return customer_overview(conn, email=email, tenant_id=tenant_id)


def set_paper_control(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
    enabled: bool,
    consent_version: str | None = None,
) -> dict[str, Any]:
    _require_tenant_membership(
        conn,
        email=email,
        tenant_id=tenant_id,
        minimum_roles={"OWNER", "ADMIN"},
        require_active=True,
    )
    broker = _broker_account(conn, tenant_id=tenant_id)
    if not broker or broker.get("environment") != "PAPER":
        raise ValueError("paper_broker_account_required")
    tenant_uuid = _uuid(tenant_id, "tenant_id")
    broker_uuid = _uuid(broker["broker_account_id"], "broker_account_id")

    with conn.transaction():
        with conn.cursor() as cur:
            if enabled:
                cur.execute(
                    """
                    select customer_consent_version,customer_consented_at
                    from anevum.tenant_trading_controls
                    where tenant_id=%s and broker_account_id=%s
                    """,
                    (tenant_uuid, broker_uuid),
                )
                existing = cur.fetchone()
                version = str(consent_version or (existing[0] if existing else "") or "").strip()
                consented_at = existing[1] if existing else None
                if not version:
                    raise ValueError("paper_trading_consent_required")
                if consented_at is None:
                    consented_at = datetime.now(UTC)
                cur.execute(
                    """
                    insert into anevum.tenant_trading_controls(
                        tenant_id,broker_account_id,bot_enabled,
                        customer_consent_version,customer_consented_at,updated_by
                    )
                    values(%s,%s,true,%s,%s,%s)
                    on conflict(tenant_id,broker_account_id) do update
                    set bot_enabled=true,
                        customer_consent_version=excluded.customer_consent_version,
                        customer_consented_at=coalesce(
                            anevum.tenant_trading_controls.customer_consented_at,
                            excluded.customer_consented_at
                        ),
                        updated_by=excluded.updated_by,
                        updated_at=now()
                    """,
                    (tenant_uuid, broker_uuid, version, consented_at, email),
                )
                action = "PAPER_AUTOMATION_RESUMED"
            else:
                cur.execute(
                    """
                    insert into anevum.tenant_trading_controls(
                        tenant_id,broker_account_id,bot_enabled,updated_by
                    )
                    values(%s,%s,false,%s)
                    on conflict(tenant_id,broker_account_id) do update
                    set bot_enabled=false,updated_by=excluded.updated_by,updated_at=now()
                    """,
                    (tenant_uuid, broker_uuid, email),
                )
                action = "PAPER_AUTOMATION_PAUSED"
            _audit(
                cur,
                tenant_id=tenant_uuid,
                email=email,
                action=action,
                object_type="broker_account",
                object_id=str(broker_uuid),
            )
    return customer_overview(conn, email=email, tenant_id=tenant_id)


def _paper_key() -> bytes:
    raw = os.environ.get("COMMAND_PAPER_ENVELOPE_KEY_B64", "").strip()
    if not raw:
        raise RuntimeError("COMMAND_PAPER_ENVELOPE_KEY_B64 is not configured")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise RuntimeError("COMMAND_PAPER_ENVELOPE_KEY_B64 is invalid") from exc
    if len(key) != 32:
        raise RuntimeError("COMMAND_PAPER_ENVELOPE_KEY_B64 must decode to 32 bytes")
    return key


def _encrypt_token(*, tenant_id: str, token: str) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    aad = f"ANEVUM|{tenant_id}|ALPACA_PAPER_OAUTH|{PAPER_BETA_KEY_VERSION}".encode()
    ciphertext = AESGCM(_paper_key()).encrypt(nonce, token.encode(), aad)
    return nonce, ciphertext


@dataclass
class DatabaseEnvelopeSecretResolver:
    database_url: str
    tenant_id: str
    connection: psycopg.Connection[Any] | None = None

    def resolve(self, secret_reference: str) -> str:
        def read_row(conn: psycopg.Connection[Any]):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select key_version,nonce,ciphertext
                    from anevum.secret_envelopes
                    where secret_reference=%s and tenant_id=%s and revoked_at is null
                    """,
                    (secret_reference, _uuid(self.tenant_id, "tenant_id")),
                )
                return cur.fetchone()

        if self.connection is not None:
            row = read_row(self.connection)
        else:
            with psycopg.connect(self.database_url, connect_timeout=5) as conn:
                row = read_row(conn)
        if not row:
            raise RuntimeError("paper OAuth secret envelope unavailable")
        version, nonce, ciphertext = row
        if version != PAPER_BETA_KEY_VERSION:
            raise RuntimeError("paper OAuth secret key version unsupported")
        aad = f"ANEVUM|{self.tenant_id}|ALPACA_PAPER_OAUTH|{version}".encode()
        try:
            plaintext = AESGCM(_paper_key()).decrypt(bytes(nonce), bytes(ciphertext), aad)
        except Exception:
            raise RuntimeError("paper OAuth secret envelope decrypt failed") from None
        return plaintext.decode()


def start_paper_oauth(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    tenant_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    member = _require_tenant_membership(
        conn,
        email=email,
        tenant_id=tenant_id,
        minimum_roles={"OWNER", "ADMIN"},
        require_active=True,
    )
    client_id = os.environ.get("ALPACA_OAUTH_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("ALPACA_OAUTH_CLIENT_ID is not configured")
    redirect = str(redirect_uri or "").strip()
    configured_redirect = os.environ.get("ALPACA_OAUTH_REDIRECT_URI", "").strip()
    if not configured_redirect:
        raise RuntimeError("ALPACA_OAUTH_REDIRECT_URI is not configured")
    if redirect != configured_redirect or not redirect.startswith("https://"):
        raise ValueError("oauth_redirect_uri_invalid")

    raw_state = secrets.token_urlsafe(32)
    state_hash = sha256(raw_state.encode()).hexdigest()
    expires = datetime.now(UTC) + timedelta(minutes=10)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into anevum.oauth_states(
                    state_hash,tenant_id,principal_id,provider,environment,
                    redirect_uri,expires_at
                )
                values(%s,%s,%s,'ALPACA','PAPER',%s,%s)
                """,
                (
                    state_hash,
                    _uuid(tenant_id, "tenant_id"),
                    _uuid(member["principal_id"], "principal_id"),
                    redirect,
                    expires,
                ),
            )
            _audit(
                cur,
                tenant_id=_uuid(tenant_id, "tenant_id"),
                email=email,
                action="ALPACA_PAPER_OAUTH_STARTED",
                object_type="tenant",
                object_id=tenant_id,
            )

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": raw_state,
            "scope": PAPER_SCOPE,
            "env": "paper",
        }
    )
    return {
        "authorization_url": f"{ALPACA_AUTHORIZE_URL}?{query}",
        "expires_at": expires.isoformat(),
        "environment": "PAPER",
        "scope": PAPER_SCOPE,
    }


async def complete_paper_oauth(
    conn: psycopg.Connection[Any],
    *,
    database_url: str,
    email: str,
    code: str,
    state: str,
) -> dict[str, Any]:
    state_hash = sha256(str(state or "").encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            select s.tenant_id,s.principal_id,s.redirect_uri,s.expires_at,s.consumed_at,
                   p.email
            from anevum.oauth_states s
            join anevum.principals p on p.principal_id=s.principal_id
            where s.state_hash=%s and s.provider='ALPACA' and s.environment='PAPER'
            for update
            """,
            (state_hash,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError("oauth_state_invalid")
    tenant_id, principal_id, redirect_uri, expires_at, consumed_at, expected_email = row
    if consumed_at is not None:
        raise ValueError("oauth_state_already_used")
    if expires_at <= datetime.now(UTC):
        raise ValueError("oauth_state_expired")
    if _email(expected_email) != _email(email):
        raise PermissionError("oauth_identity_mismatch")
    _require_tenant_membership(
        conn,
        email=email,
        tenant_id=str(tenant_id),
        minimum_roles={"OWNER", "ADMIN"},
        require_active=True,
    )

    client_id = os.environ.get("ALPACA_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ALPACA_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Alpaca OAuth client credentials are not configured")

    async with httpx.AsyncClient(timeout=15.0) as http:
        token_response = await http.post(
            ALPACA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": str(code or "").strip(),
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if not token_response.is_success:
        raise RuntimeError(f"Alpaca OAuth token exchange failed ({token_response.status_code})")
    token_payload = token_response.json()
    token = str(token_payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Alpaca OAuth token exchange returned no access token")

    async with httpx.AsyncClient(timeout=15.0) as http:
        account_response = await http.get(
            ALPACA_PAPER_API + "/v2/account",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if not account_response.is_success:
        raise RuntimeError(f"Alpaca paper account verification failed ({account_response.status_code})")
    account = account_response.json()
    provider_account_id = str(account.get("id") or "").strip()
    if not provider_account_id:
        raise RuntimeError("Alpaca paper account identity missing")

    secret_reference = f"db-envelope://{uuid4()}"
    nonce, ciphertext = _encrypt_token(tenant_id=str(tenant_id), token=token)
    broker_account_id: UUID
    scope = str(token_payload.get("scope") or PAPER_SCOPE).strip()
    scopes = [item for item in scope.split() if item]

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                update anevum.oauth_states
                set consumed_at=now()
                where state_hash=%s and consumed_at is null
                """,
                (state_hash,),
            )
            if cur.rowcount != 1:
                raise ValueError("oauth_state_already_used")
            cur.execute(
                """
                insert into anevum.broker_accounts(
                    tenant_id,provider,provider_account_id,environment,
                    account_status,crypto_enabled,trading_blocked,withdrawals_blocked
                )
                values(%s,'ALPACA',%s,'PAPER',%s,%s,%s,%s)
                on conflict(provider,environment,provider_account_id) do update
                set account_status=excluded.account_status,
                    crypto_enabled=excluded.crypto_enabled,
                    trading_blocked=excluded.trading_blocked,
                    withdrawals_blocked=excluded.withdrawals_blocked,
                    updated_at=now()
                returning broker_account_id
                """,
                (
                    tenant_id,
                    provider_account_id,
                    str(account.get("status") or "UNKNOWN").upper(),
                    str(account.get("crypto_status") or "").upper() in {"ACTIVE", "APPROVED"},
                    bool(account.get("trading_blocked")),
                    bool(account.get("transfers_blocked")),
                ),
            )
            broker_account_id = cur.fetchone()[0]
            cur.execute(
                """
                select tenant_id from anevum.broker_accounts
                where broker_account_id=%s
                """,
                (broker_account_id,),
            )
            owner_tenant = cur.fetchone()[0]
            if owner_tenant != tenant_id:
                raise PermissionError("broker_account_already_owned_by_another_tenant")

            cur.execute(
                """
                update anevum.secret_envelopes se
                set revoked_at=now()
                from anevum.broker_authorizations ba
                where ba.broker_account_id=%s
                  and ba.status='ACTIVE'
                  and se.secret_reference=ba.secret_reference
                  and se.revoked_at is null
                """,
                (broker_account_id,),
            )
            cur.execute(
                """
                update anevum.broker_authorizations
                set status='REVOKED',updated_at=now()
                where broker_account_id=%s and status='ACTIVE'
                """,
                (broker_account_id,),
            )
            cur.execute(
                """
                insert into anevum.secret_envelopes(
                    secret_reference,tenant_id,purpose,key_version,nonce,ciphertext
                )
                values(%s,%s,'ALPACA_PAPER_OAUTH',%s,%s,%s)
                """,
                (secret_reference, tenant_id, PAPER_BETA_KEY_VERSION, nonce, ciphertext),
            )
            cur.execute(
                """
                insert into anevum.broker_authorizations(
                    broker_account_id,authorization_kind,secret_reference,
                    scopes,status,issued_at,last_validated_at
                )
                values(%s,'OAUTH',%s,%s,'ACTIVE',now(),now())
                """,
                (broker_account_id, secret_reference, Jsonb(scopes)),
            )
            _audit(
                cur,
                tenant_id=tenant_id,
                email=email,
                action="ALPACA_PAPER_CONNECTED",
                object_type="broker_account",
                object_id=str(broker_account_id),
                payload={"provider_account_id": provider_account_id, "scopes": scopes},
            )

    broker = TenantBrokerAccount(
        tenant_id=str(tenant_id),
        broker_account_id=str(broker_account_id),
        provider_account_id=provider_account_id,
        environment="PAPER",
        authorization_kind="OAUTH",
        secret_reference=secret_reference,
    )
    resolver = DatabaseEnvelopeSecretResolver(
        database_url,
        str(tenant_id),
        connection=conn,
    )
    result = await TenantBrokerReconciler(
        broker,
        TenantAlpacaReadClient(broker, resolver),
    ).reconcile()
    record_broker_reconciliation(conn, result)
    return customer_overview(conn, email=email, tenant_id=str(tenant_id))


async def refresh_paper_broker(
    conn: psycopg.Connection[Any],
    *,
    database_url: str,
    email: str,
    tenant_id: str,
) -> dict[str, Any]:
    _require_tenant_membership(
        conn,
        email=email,
        tenant_id=tenant_id,
        require_active=True,
    )
    broker_row = _broker_account(conn, tenant_id=tenant_id)
    if not broker_row or broker_row.get("environment") != "PAPER":
        raise ValueError("paper_broker_account_required")
    with conn.cursor() as cur:
        cur.execute(
            """
            select authorization_kind,secret_reference
            from anevum.broker_authorizations
            where broker_account_id=%s and status='ACTIVE'
            """,
            (_uuid(broker_row["broker_account_id"], "broker_account_id"),),
        )
        auth = cur.fetchone()
    if not auth:
        raise ValueError("active_broker_authorization_required")

    broker = TenantBrokerAccount(
        tenant_id=tenant_id,
        broker_account_id=broker_row["broker_account_id"],
        provider_account_id=broker_row["provider_account_id"],
        environment="PAPER",
        authorization_kind=str(auth[0]),
        secret_reference=str(auth[1]),
    )
    resolver = DatabaseEnvelopeSecretResolver(database_url, tenant_id)
    result = await TenantBrokerReconciler(
        broker,
        TenantAlpacaReadClient(broker, resolver),
    ).reconcile()
    record_broker_reconciliation(conn, result)
    if result.status == "SUCCESS":
        _ensure_default_paper_strategy_assignment(
            conn,
            tenant_id=tenant_id,
            broker_account_id=str(broker_row["broker_account_id"]),
        )
    return customer_overview(conn, email=email, tenant_id=tenant_id)


def provision_paper_beta_tenant(
    conn: psycopg.Connection[Any],
    *,
    customer_email: str,
    display_name: str,
    tenant_key: str | None = None,
    created_by: str,
) -> dict[str, Any]:
    normalized = _email(customer_email)
    name = str(display_name or "").strip()
    if not normalized or "@" not in normalized:
        raise ValueError("customer_email_invalid")
    if not name:
        raise ValueError("tenant_display_name_required")
    key = str(tenant_key or "").strip().lower()
    if not key:
        digest = sha256(normalized.encode()).hexdigest()[:10]
        stem = "".join(ch if ch.isalnum() else "-" for ch in normalized.split("@", 1)[0])
        key = f"beta-{stem.strip('-') or 'customer'}-{digest}"
    if len(key) > 100 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in key):
        raise ValueError("tenant_key_invalid")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select principal_id from anevum.principals where lower(email)=lower(%s)",
                (normalized,),
            )
            principal_row = cur.fetchone()
            if principal_row:
                principal_id = principal_row[0]
                cur.execute(
                    """
                    update anevum.principals
                    set status='ACTIVE',updated_at=now()
                    where principal_id=%s
                    """,
                    (principal_id,),
                )
            else:
                principal_id = uuid4()
                cur.execute(
                    """
                    insert into anevum.principals(
                        principal_id,external_subject,email,status
                    )
                    values(%s,%s,%s,'ACTIVE')
                    """,
                    (principal_id, f"invite:{uuid4()}", normalized),
                )

            cur.execute(
                "select tenant_id from anevum.tenants where tenant_key=%s",
                (key,),
            )
            tenant_row = cur.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cur.execute(
                    """
                    select 1
                    from anevum.tenant_memberships
                    where tenant_id=%s and principal_id=%s
                    """,
                    (tenant_id, principal_id),
                )
                if not cur.fetchone():
                    raise ValueError("tenant_key_already_exists")
            else:
                tenant_id = uuid4()
                cur.execute(
                    """
                    insert into anevum.tenants(
                        tenant_id,tenant_key,display_name,status
                    )
                    values(%s,%s,%s,'ACTIVE')
                    """,
                    (tenant_id, key, name),
                )

            cur.execute(
                """
                insert into anevum.tenant_memberships(
                    tenant_id,principal_id,role,status
                )
                values(%s,%s,'OWNER','ACTIVE')
                on conflict(tenant_id,principal_id) do update
                set role='OWNER',status='ACTIVE',updated_at=now()
                """,
                (tenant_id, principal_id),
            )
            cur.execute(
                """
                select entitlement_id
                from anevum.entitlements
                where tenant_id=%s
                  and product_key='COMMAND'
                  and status in ('ACTIVE','TRIAL')
                  and (expires_at is null or expires_at > now())
                limit 1
                """,
                (tenant_id,),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    insert into anevum.entitlements(
                        tenant_id,product_key,status,source,effective_at,metadata
                    )
                    values(%s,'COMMAND','TRIAL','paper-beta',now(),%s)
                    """,
                    (
                        tenant_id,
                        Jsonb({
                            "paper_only": True,
                            "live_customer_trading": False,
                            "created_by": created_by,
                        }),
                    ),
                )
            _audit(
                cur,
                tenant_id=tenant_id,
                email=created_by,
                action="PAPER_BETA_TENANT_PROVISIONED",
                object_type="tenant",
                object_id=str(tenant_id),
                payload={"customer_email": normalized, "tenant_key": key},
            )

    return {
        "tenant_id": str(tenant_id),
        "tenant_key": key,
        "display_name": name,
        "customer_email": normalized,
        "role": "OWNER",
        "entitlement": "TRIAL",
        "paper_only": True,
        "live_customer_trading": False,
    }
