from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from foundation.alpaca_broker_sandbox import (
    AlpacaBrokerSandboxProvider,
    sandbox_provider_status,
)


CURRENCY = "USD"
SCHEMA_VERSION = "anevum-finance.v1"
EXTERNAL_MONEY_MOVEMENT_ENABLED = False
LIVE_EXECUTION_AUTHORIZED = False
ACCOUNT_KINDS = ("AVAILABLE_CASH", "RHEN_ALLOCATION", "RESERVE")
EXECUTION_MODES = {"SANDBOX", "PAPER"}


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def money(value: Any, *, allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid_amount") from exc
    if not amount.is_finite():
        raise ValueError("invalid_amount")
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("amount_must_be_positive")
    return amount.quantize(Decimal("0.000000000001"))


def _safe_key(value: Any, *, field: str, max_length: int = 200) -> str:
    key = str(value or "").strip()
    if not key or len(key) > max_length:
        raise ValueError(f"invalid_{field}")
    return key


def _customer_row(cur: psycopg.Cursor[Any], command_subject: str) -> tuple[Any, ...] | None:
    cur.execute(
        """
        select customer_id, command_subject, status, display_label, metadata, created_at, updated_at
        from anevum.financial_customers
        where command_subject=%s
        """,
        (command_subject,),
    )
    return cur.fetchone()


def _serialize_customer(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "customer_id": str(row[0]),
        "command_subject": row[1],
        "status": row[2],
        "display_label": row[3],
        "metadata": row[4] or {},
        "created_at": row[5],
        "updated_at": row[6],
    }


def _ensure_account_set(
    cur: psycopg.Cursor[Any],
    *,
    customer_id: Any,
) -> None:
    for kind in ACCOUNT_KINDS:
        cur.execute(
            """
            insert into anevum.financial_accounts (
                customer_id, account_kind, currency, status, metadata
            )
            values (%s,%s,%s,'ACTIVE','{}'::jsonb)
            on conflict (customer_id, account_kind, currency) do nothing
            """,
            (customer_id, kind, CURRENCY),
        )
        cur.execute(
            """
            select financial_account_id
            from anevum.financial_accounts
            where customer_id=%s and account_kind=%s and currency=%s
            """,
            (customer_id, kind, CURRENCY),
        )
        account_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into anevum.financial_ledger_accounts (
                account_code, owner_type, customer_id, financial_account_id,
                classification, normal_side, currency, status, metadata
            )
            values (%s,'CUSTOMER',%s,%s,'LIABILITY','CREDIT',%s,'ACTIVE','{}'::jsonb)
            on conflict (financial_account_id) do nothing
            """,
            (
                f"CUSTOMER:{customer_id}:{kind}:{CURRENCY}",
                customer_id,
                account_id,
                CURRENCY,
            ),
        )


def initialize_customer(
    database_url: str,
    *,
    command_subject: str,
    display_label: str | None = None,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    label = str(display_label or "").strip()[:200] or None
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.financial_customers (
                        command_subject, display_label, status, metadata
                    )
                    values (%s,%s,'ACTIVE','{}'::jsonb)
                    on conflict (command_subject) do update
                    set display_label=coalesce(
                        excluded.display_label,
                        anevum.financial_customers.display_label
                    ),
                    updated_at=now()
                    returning customer_id
                    """,
                    (subject, label),
                )
                customer_id = cur.fetchone()[0]
                _ensure_account_set(cur, customer_id=customer_id)
                cur.execute(
                    """
                    insert into anevum.audit_log (
                        actor_type, actor_id, system_key, action, object_type, object_id, details
                    )
                    values ('COMMAND_USER',%s,'FINANCE','finance.initialize',
                            'financial_customer',%s,'{}'::jsonb)
                    """,
                    (subject, str(customer_id)),
                )
    return read_snapshot(database_url, command_subject=subject)


def _ledger_balance(
    cur: psycopg.Cursor[Any],
    *,
    ledger_account_id: Any,
) -> Decimal:
    cur.execute(
        """
        select
            la.normal_side,
            coalesce(sum(
                case
                    when e.side = la.normal_side then e.amount
                    else -e.amount
                end
            ),0)
        from anevum.financial_ledger_accounts la
        left join anevum.financial_ledger_entries e
          on e.ledger_account_id=la.ledger_account_id
        where la.ledger_account_id=%s
        group by la.normal_side
        """,
        (ledger_account_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("ledger_account_not_found")
    return Decimal(str(row[1]))


def _account_ledger(
    cur: psycopg.Cursor[Any],
    *,
    customer_id: Any,
    account_kind: str,
) -> tuple[Any, Any]:
    cur.execute(
        """
        select fa.financial_account_id, la.ledger_account_id
        from anevum.financial_accounts fa
        join anevum.financial_ledger_accounts la
          on la.financial_account_id=fa.financial_account_id
        where fa.customer_id=%s
          and fa.account_kind=%s
          and fa.currency=%s
          and fa.status='ACTIVE'
          and la.status='ACTIVE'
        """,
        (customer_id, account_kind, CURRENCY),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("financial_account_not_initialized")
    return row[0], row[1]


def _insert_transaction(
    cur: psycopg.Cursor[Any],
    *,
    transaction_key: str,
    transaction_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    provider: str | None = None,
    provider_reference: str | None = None,
) -> tuple[Any, bool]:
    payload_hash = canonical_payload_hash(payload)
    cur.execute(
        """
        insert into anevum.financial_ledger_transactions (
            transaction_key, transaction_type, currency, payload_hash,
            provider, provider_reference, correlation_id, metadata
        )
        values (%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (transaction_key) do nothing
        returning transaction_id
        """,
        (
            transaction_key,
            transaction_type,
            CURRENCY,
            payload_hash,
            provider,
            provider_reference,
            correlation_id,
            Jsonb(payload),
        ),
    )
    inserted = cur.fetchone()
    if inserted:
        return inserted[0], False

    cur.execute(
        """
        select transaction_id, payload_hash
        from anevum.financial_ledger_transactions
        where transaction_key=%s
        """,
        (transaction_key,),
    )
    existing = cur.fetchone()
    if not existing or existing[1] != payload_hash:
        raise ValueError("idempotency_conflict")
    return existing[0], True


def sandbox_deposit(
    database_url: str,
    *,
    command_subject: str,
    amount: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    key = _safe_key(idempotency_key, field="idempotency_key")
    deposit = money(amount)
    transaction_key = f"sandbox-deposit:{subject}:{key}"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "sandbox_deposit",
        "command_subject": subject,
        "amount": str(deposit),
        "currency": CURRENCY,
        "sandbox_only": True,
        "external_money_movement_enabled": False,
    }

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                customer = _customer_row(cur, subject)
                if not customer:
                    raise ValueError("financial_customer_not_initialized")
                customer_id = customer[0]
                available_account_id, available_ledger_id = _account_ledger(
                    cur,
                    customer_id=customer_id,
                    account_kind="AVAILABLE_CASH",
                )
                cur.execute(
                    """
                    select ledger_account_id
                    from anevum.financial_ledger_accounts
                    where account_code='SYSTEM:SANDBOX:CUSTODIAN_CASH:USD'
                      and status='ACTIVE'
                    """
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("sandbox_custodian_ledger_missing")
                system_ledger_id = row[0]

                transaction_id, duplicate = _insert_transaction(
                    cur,
                    transaction_key=transaction_key,
                    transaction_type="SANDBOX_DEPOSIT",
                    payload=payload,
                    correlation_id=key,
                    provider="ANEVUM_SANDBOX",
                    provider_reference=key,
                )
                if not duplicate:
                    cur.executemany(
                        """
                        insert into anevum.financial_ledger_entries (
                            transaction_id, ledger_account_id, sequence, side, amount, memo
                        )
                        values (%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                transaction_id,
                                system_ledger_id,
                                1,
                                "DEBIT",
                                deposit,
                                "sandbox custodian cash",
                            ),
                            (
                                transaction_id,
                                available_ledger_id,
                                2,
                                "CREDIT",
                                deposit,
                                "customer available cash",
                            ),
                        ],
                    )
                    cur.execute(
                        """
                        insert into anevum.financial_transfers (
                            idempotency_key, customer_id, financial_account_id,
                            direction, provider, provider_environment, currency,
                            amount, status, provider_reference, ledger_transaction_id,
                            metadata, settled_at
                        )
                        values (%s,%s,%s,'DEPOSIT','ANEVUM_SANDBOX','SANDBOX',%s,
                                %s,'SETTLED',%s,%s,%s,now())
                        on conflict (idempotency_key) do nothing
                        """,
                        (
                            transaction_key,
                            customer_id,
                            available_account_id,
                            CURRENCY,
                            deposit,
                            key,
                            transaction_id,
                            Jsonb({"sandbox_only": True}),
                        ),
                    )
                    cur.execute(
                        """
                        select transfer_id from anevum.financial_transfers
                        where idempotency_key=%s
                        """,
                        (transaction_key,),
                    )
                    transfer_id = cur.fetchone()[0]
                    cur.execute(
                        """
                        insert into anevum.financial_transfer_events (
                            event_key, transfer_id, event_type, status,
                            provider_reference, payload
                        )
                        values (%s,%s,'sandbox_deposit_settled','SETTLED',%s,%s)
                        on conflict (event_key) do nothing
                        """,
                        (
                            f"{transaction_key}:settled",
                            transfer_id,
                            key,
                            Jsonb(payload),
                        ),
                    )
                    cur.execute(
                        """
                        insert into anevum.audit_log (
                            actor_type, actor_id, system_key, action,
                            object_type, object_id, correlation_id, details
                        )
                        values ('COMMAND_USER',%s,'FINANCE','finance.sandbox_deposit',
                                'ledger_transaction',%s,%s,%s)
                        """,
                        (
                            subject,
                            str(transaction_id),
                            key,
                            Jsonb({"amount": str(deposit), "currency": CURRENCY}),
                        ),
                    )

    result = read_snapshot(database_url, command_subject=subject)
    result["idempotent"] = duplicate
    result["transaction_key"] = transaction_key
    return result


def set_rhen_allocation(
    database_url: str,
    *,
    command_subject: str,
    target_amount: Any,
    idempotency_key: str,
    execution_mode: str = "PAPER",
    max_position_fraction: Any = "0.10",
    max_daily_loss_fraction: Any = "0.05",
    strategy_version_id: str | None = None,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    key = _safe_key(idempotency_key, field="idempotency_key")
    target = money(target_amount, allow_zero=True)
    mode = str(execution_mode or "").strip().upper()
    if mode not in EXECUTION_MODES:
        raise ValueError("invalid_execution_mode")
    try:
        position_fraction = Decimal(str(max_position_fraction))
        daily_loss_fraction = Decimal(str(max_daily_loss_fraction))
    except InvalidOperation as exc:
        raise ValueError("invalid_risk_fraction") from exc
    if not (Decimal("0") < position_fraction <= Decimal("1")):
        raise ValueError("invalid_risk_fraction")
    if not (Decimal("0") < daily_loss_fraction <= Decimal("1")):
        raise ValueError("invalid_risk_fraction")
    strategy = str(strategy_version_id or "").strip()[:200] or None

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                customer = _customer_row(cur, subject)
                if not customer:
                    raise ValueError("financial_customer_not_initialized")
                customer_id = customer[0]

                available_account_id, available_ledger_id = _account_ledger(
                    cur,
                    customer_id=customer_id,
                    account_kind="AVAILABLE_CASH",
                )
                rhen_account_id, rhen_ledger_id = _account_ledger(
                    cur,
                    customer_id=customer_id,
                    account_kind="RHEN_ALLOCATION",
                )
                cur.execute(
                    """
                    select ledger_account_id
                    from anevum.financial_ledger_accounts
                    where ledger_account_id in (%s,%s)
                    order by ledger_account_id
                    for update
                    """,
                    (available_ledger_id, rhen_ledger_id),
                )
                cur.fetchall()

                request_transaction_key = f"rhen-allocation:{subject}:{key}"
                cur.execute(
                    """
                    select details
                    from anevum.audit_log
                    where actor_type='COMMAND_USER'
                      and actor_id=%s
                      and system_key='FINANCE'
                      and action='finance.set_rhen_allocation_noop'
                      and correlation_id=%s
                    order by audit_id desc
                    limit 1
                    """,
                    (subject, key),
                )
                prior_noop = cur.fetchone()
                if prior_noop:
                    prior_meta = prior_noop[0] or {}
                    if (
                        str(prior_meta.get("target_amount")) != str(target)
                        or str(prior_meta.get("execution_mode")) != mode
                        or (prior_meta.get("strategy_version_id") or None) != strategy
                    ):
                        raise ValueError("idempotency_conflict")
                    result = read_snapshot(database_url, command_subject=subject)
                    result["idempotent"] = True
                    result["transaction_key"] = request_transaction_key
                    return result

                cur.execute(
                    """
                    select metadata
                    from anevum.financial_ledger_transactions
                    where transaction_key=%s
                    """,
                    (request_transaction_key,),
                )
                prior_request = cur.fetchone()
                if prior_request:
                    prior_meta = prior_request[0] or {}
                    if (
                        str(prior_meta.get("target_amount")) != str(target)
                        or str(prior_meta.get("execution_mode")) != mode
                        or (prior_meta.get("strategy_version_id") or None) != strategy
                    ):
                        raise ValueError("idempotency_conflict")
                    result = read_snapshot(database_url, command_subject=subject)
                    result["idempotent"] = True
                    result["transaction_key"] = request_transaction_key
                    return result

                available = _ledger_balance(cur, ledger_account_id=available_ledger_id)
                current = _ledger_balance(cur, ledger_account_id=rhen_ledger_id)
                delta = target - current
                if delta > 0 and available < delta:
                    raise ValueError("insufficient_available_cash")

                payload = {
                    "schema_version": SCHEMA_VERSION,
                    "action": "set_rhen_allocation",
                    "command_subject": subject,
                    "previous_amount": str(current),
                    "target_amount": str(target),
                    "delta": str(delta),
                    "currency": CURRENCY,
                    "execution_mode": mode,
                    "strategy_version_id": strategy,
                    "external_execution_enabled": False,
                    "live_execution_authorized": False,
                }
                transaction_key = request_transaction_key
                duplicate = False
                if delta != 0:
                    transaction_id, duplicate = _insert_transaction(
                        cur,
                        transaction_key=transaction_key,
                        transaction_type="RHEN_ALLOCATION",
                        payload=payload,
                        correlation_id=key,
                    )
                    if not duplicate:
                        if delta > 0:
                            entries = [
                                (
                                    transaction_id,
                                    available_ledger_id,
                                    1,
                                    "DEBIT",
                                    delta,
                                    "reduce available cash for RHEN allocation",
                                ),
                                (
                                    transaction_id,
                                    rhen_ledger_id,
                                    2,
                                    "CREDIT",
                                    delta,
                                    "increase RHEN allocation",
                                ),
                            ]
                        else:
                            release = -delta
                            entries = [
                                (
                                    transaction_id,
                                    rhen_ledger_id,
                                    1,
                                    "DEBIT",
                                    release,
                                    "reduce RHEN allocation",
                                ),
                                (
                                    transaction_id,
                                    available_ledger_id,
                                    2,
                                    "CREDIT",
                                    release,
                                    "return allocation to available cash",
                                ),
                            ]
                        cur.executemany(
                            """
                            insert into anevum.financial_ledger_entries (
                                transaction_id, ledger_account_id, sequence,
                                side, amount, memo
                            )
                            values (%s,%s,%s,%s,%s,%s)
                            """,
                            entries,
                        )
                else:
                    # A no-op target still needs an idempotency marker. Store it as
                    # an audit-only request, not as a ledger transaction with no entries.
                    cur.execute(
                        """
                        insert into anevum.audit_log (
                            actor_type, actor_id, system_key, action, object_type,
                            object_id, correlation_id, details
                        )
                        values ('COMMAND_USER',%s,'FINANCE','finance.set_rhen_allocation_noop',
                                'rhen_allocation_request',%s,%s,%s)
                        """,
                        (
                            subject,
                            str(rhen_account_id),
                            key,
                            Jsonb(payload),
                        ),
                    )

                cur.execute(
                    """
                    insert into anevum.rhen_allocations (
                        customer_id, financial_account_id, execution_mode, status,
                        currency, max_allocation, max_position_fraction,
                        max_daily_loss_fraction, strategy_version_id,
                        external_execution_enabled, metadata
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,%s)
                    on conflict (customer_id, financial_account_id) do update
                    set execution_mode=excluded.execution_mode,
                        status=excluded.status,
                        max_allocation=excluded.max_allocation,
                        max_position_fraction=excluded.max_position_fraction,
                        max_daily_loss_fraction=excluded.max_daily_loss_fraction,
                        strategy_version_id=excluded.strategy_version_id,
                        external_execution_enabled=false,
                        metadata=excluded.metadata,
                        updated_at=now()
                    """,
                    (
                        customer_id,
                        rhen_account_id,
                        mode,
                        "ACTIVE" if target > 0 else "PAUSED",
                        CURRENCY,
                        target,
                        position_fraction,
                        daily_loss_fraction,
                        strategy,
                        Jsonb({
                            "source": "command_financial_gateway_v1",
                            "available_account_id": str(available_account_id),
                        }),
                    ),
                )
                cur.execute(
                    """
                    insert into anevum.audit_log (
                        actor_type, actor_id, system_key, action, object_type,
                        object_id, correlation_id, details
                    )
                    values ('COMMAND_USER',%s,'FINANCE','finance.set_rhen_allocation',
                            'rhen_allocation',%s,%s,%s)
                    """,
                    (
                        subject,
                        str(rhen_account_id),
                        key,
                        Jsonb({
                            "target_amount": str(target),
                            "execution_mode": mode,
                            "external_execution_enabled": False,
                        }),
                    ),
                )

    result = read_snapshot(database_url, command_subject=subject)
    result["idempotent"] = duplicate
    result["transaction_key"] = transaction_key
    return result



def _provider_account_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status == "ACTIVE":
        return "ACTIVE"
    if status in {"DISABLED", "CLOSED", "REJECTED"}:
        return "CLOSED" if status == "CLOSED" else "RESTRICTED"
    return "PENDING"


def _safe_provider_metadata(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_status": str(account.get("status") or "").strip() or None,
        "account_type": str(account.get("account_type") or "").strip() or None,
        "currency": str(account.get("currency") or "").strip() or None,
        "crypto_status": str(account.get("crypto_status") or "").strip() or None,
    }


def _provider_account_for_customer(
    cur: psycopg.Cursor[Any],
    *,
    customer_id: Any,
) -> tuple[Any, str] | None:
    cur.execute(
        """
        select provider_account_id, provider_account_ref
        from anevum.financial_provider_accounts
        where customer_id=%s
          and provider='ALPACA_BROKER'
          and provider_environment='SANDBOX'
          and status <> 'CLOSED'
        order by created_at asc
        limit 1
        """,
        (customer_id,),
    )
    row = cur.fetchone()
    return (row[0], str(row[1])) if row else None


def create_sandbox_provider_account(
    database_url: str,
    *,
    command_subject: str,
    application: dict[str, Any],
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    if not isinstance(application, dict) or not application:
        raise ValueError("invalid_account_application")

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                raise ValueError("financial_customer_not_initialized")
            customer_id = customer[0]
            existing = _provider_account_for_customer(cur, customer_id=customer_id)
            if existing:
                result = read_snapshot(database_url, command_subject=subject)
                result["provider_account_existing"] = True
                return result

    provider = AlpacaBrokerSandboxProvider.from_env()
    try:
        account = provider.create_customer_account(application)
    finally:
        provider.close()

    account_ref = _safe_key(account.get("id"), field="provider_account_ref", max_length=100)
    status = _provider_account_status(account.get("status"))
    metadata = _safe_provider_metadata(account)

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                customer = _customer_row(cur, subject)
                if not customer:
                    raise ValueError("financial_customer_not_initialized")
                customer_id = customer[0]
                cur.execute(
                    """
                    insert into anevum.financial_provider_accounts (
                        customer_id, provider, provider_account_ref,
                        provider_environment, status, metadata
                    )
                    values (%s,'ALPACA_BROKER',%s,'SANDBOX',%s,%s)
                    on conflict (provider, provider_account_ref) do update
                    set status=excluded.status,
                        metadata=excluded.metadata,
                        updated_at=now()
                    """,
                    (customer_id, account_ref, status, Jsonb(metadata)),
                )
                cur.execute(
                    """
                    insert into anevum.audit_log (
                        actor_type, actor_id, system_key, action, object_type,
                        object_id, details
                    )
                    values ('COMMAND_USER',%s,'FINANCE',
                            'finance.sandbox_provider_account_create',
                            'financial_provider_account',%s,%s)
                    """,
                    (
                        subject,
                        account_ref,
                        Jsonb({
                            "provider": "ALPACA_BROKER",
                            "environment": "SANDBOX",
                            "status": status,
                        }),
                    ),
                )
    result = read_snapshot(database_url, command_subject=subject)
    result["provider_account_created"] = True
    return result


def refresh_sandbox_provider_account(
    database_url: str,
    *,
    command_subject: str,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                raise ValueError("financial_customer_not_initialized")
            provider_row = _provider_account_for_customer(cur, customer_id=customer[0])
            if not provider_row:
                raise ValueError("sandbox_provider_account_not_found")
            provider_account_id, provider_account_ref = provider_row

    provider = AlpacaBrokerSandboxProvider.from_env()
    try:
        account = provider.get_account(provider_account_ref)
    finally:
        provider.close()

    status = _provider_account_status(account.get("status"))
    metadata = _safe_provider_metadata(account)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update anevum.financial_provider_accounts
                    set status=%s, metadata=%s, updated_at=now()
                    where provider_account_id=%s
                    """,
                    (status, Jsonb(metadata), provider_account_id),
                )
    return read_snapshot(database_url, command_subject=subject)


def create_sandbox_bank_link(
    database_url: str,
    *,
    command_subject: str,
    processor_token: str,
    bank_account_type: str = "CHECKING",
    nickname: str | None = None,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    token = _safe_key(processor_token, field="processor_token", max_length=1000)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                raise ValueError("financial_customer_not_initialized")
            customer_id = customer[0]
            provider_row = _provider_account_for_customer(cur, customer_id=customer_id)
            if not provider_row:
                raise ValueError("sandbox_provider_account_not_found")
            provider_account_id, provider_account_ref = provider_row

    provider = AlpacaBrokerSandboxProvider.from_env()
    try:
        link = provider.create_bank_link(
            provider_account_ref,
            {
                "processor_token": token,
                "bank_account_type": bank_account_type,
                "nickname": nickname,
            },
        )
    finally:
        provider.close()

    relationship_ref = _safe_key(
        link.get("id"),
        field="provider_relationship_ref",
        max_length=100,
    )
    raw_status = str(link.get("status") or "").strip().upper()
    status = "ACTIVE" if raw_status in {"APPROVED", "ACTIVE"} else "PENDING"
    account_type = str(link.get("account_type") or bank_account_type or "").strip().upper()[:20] or None
    display_name = str(nickname or "Linked bank").strip()[:120]

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.financial_bank_links (
                        customer_id, provider_account_id, provider,
                        provider_relationship_ref, bank_account_type,
                        display_name, status, metadata
                    )
                    values (%s,%s,'ALPACA_BROKER',%s,%s,%s,%s,%s)
                    on conflict (provider, provider_relationship_ref) do update
                    set bank_account_type=excluded.bank_account_type,
                        display_name=excluded.display_name,
                        status=excluded.status,
                        metadata=excluded.metadata,
                        updated_at=now()
                    """,
                    (
                        customer_id,
                        provider_account_id,
                        relationship_ref,
                        account_type,
                        display_name,
                        status,
                        Jsonb({"provider_status": raw_status or None}),
                    ),
                )
                cur.execute(
                    """
                    insert into anevum.audit_log (
                        actor_type, actor_id, system_key, action,
                        object_type, object_id, details
                    )
                    values ('COMMAND_USER',%s,'FINANCE','finance.sandbox_bank_link',
                            'financial_bank_link',%s,%s)
                    """,
                    (
                        subject,
                        relationship_ref,
                        Jsonb({
                            "provider": "ALPACA_BROKER",
                            "environment": "SANDBOX",
                            "status": status,
                        }),
                    ),
                )
    return read_snapshot(database_url, command_subject=subject)


def create_sandbox_provider_transfer(
    database_url: str,
    *,
    command_subject: str,
    direction: str,
    amount: Any,
    bank_link_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    key = _safe_key(idempotency_key, field="idempotency_key")
    transfer_amount = money(amount)
    transfer_direction = str(direction or "").strip().upper()
    if transfer_direction not in {"DEPOSIT", "WITHDRAWAL"}:
        raise ValueError("invalid_transfer_direction")

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                raise ValueError("financial_customer_not_initialized")
            customer_id = customer[0]
            provider_row = _provider_account_for_customer(cur, customer_id=customer_id)
            if not provider_row:
                raise ValueError("sandbox_provider_account_not_found")
            provider_account_id, provider_account_ref = provider_row
            cur.execute(
                """
                select provider_relationship_ref
                from anevum.financial_bank_links
                where bank_link_id::text=%s
                  and customer_id=%s
                  and provider_account_id=%s
                  and provider='ALPACA_BROKER'
                  and status in ('PENDING','ACTIVE')
                """,
                (bank_link_id, customer_id, provider_account_id),
            )
            link_row = cur.fetchone()
            if not link_row:
                raise ValueError("sandbox_bank_link_not_found")
            relationship_ref = str(link_row[0])
            available_account_id, available_ledger_id = _account_ledger(
                cur,
                customer_id=customer_id,
                account_kind="AVAILABLE_CASH",
            )
            if transfer_direction == "WITHDRAWAL":
                available = _ledger_balance(cur, ledger_account_id=available_ledger_id)
                if available < transfer_amount:
                    raise ValueError("insufficient_available_cash")
            transfer_key = f"alpaca-sandbox-transfer:{subject}:{key}"
            cur.execute(
                """
                select provider_reference, amount, direction
                from anevum.financial_transfers
                where idempotency_key=%s
                """,
                (transfer_key,),
            )
            existing = cur.fetchone()
            if existing:
                if Decimal(str(existing[1])) != transfer_amount or str(existing[2]) != transfer_direction:
                    raise ValueError("idempotency_conflict")
                result = read_snapshot(database_url, command_subject=subject)
                result["idempotent"] = True
                return result

    provider = AlpacaBrokerSandboxProvider.from_env()
    try:
        transfer = provider.create_transfer(
            provider_account_ref,
            {
                "relationship_id": relationship_ref,
                "amount": str(transfer_amount),
                "direction": "INCOMING" if transfer_direction == "DEPOSIT" else "OUTGOING",
            },
        )
    finally:
        provider.close()

    transfer_ref = _safe_key(transfer.get("id"), field="provider_transfer_ref", max_length=100)
    provider_status = str(transfer.get("status") or "").strip().upper()
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.financial_transfers (
                        idempotency_key, customer_id, financial_account_id,
                        provider_account_id, direction, provider,
                        provider_environment, currency, amount, status,
                        provider_reference, metadata
                    )
                    values (%s,%s,%s,%s,%s,'ALPACA_BROKER','SANDBOX',
                            %s,%s,'PENDING',%s,%s)
                    on conflict (idempotency_key) do nothing
                    """,
                    (
                        transfer_key,
                        customer_id,
                        available_account_id,
                        provider_account_id,
                        transfer_direction,
                        CURRENCY,
                        transfer_amount,
                        transfer_ref,
                        Jsonb({"provider_status": provider_status or None}),
                    ),
                )
                cur.execute(
                    """
                    select transfer_id
                    from anevum.financial_transfers
                    where idempotency_key=%s
                    """,
                    (transfer_key,),
                )
                transfer_id = cur.fetchone()[0]
                cur.execute(
                    """
                    insert into anevum.financial_transfer_events (
                        event_key, transfer_id, event_type, status,
                        provider_reference, payload
                    )
                    values (%s,%s,'provider_transfer_requested','PENDING',%s,%s)
                    on conflict (event_key) do nothing
                    """,
                    (
                        f"{transfer_ref}:requested",
                        transfer_id,
                        transfer_ref,
                        Jsonb({
                            "provider": "ALPACA_BROKER",
                            "environment": "SANDBOX",
                            "provider_status": provider_status or None,
                        }),
                    ),
                )
    result = read_snapshot(database_url, command_subject=subject)
    result["provider_transfer_created"] = True
    return result


def _settle_provider_transfer(
    cur: psycopg.Cursor[Any],
    *,
    transfer_id: Any,
    transfer_direction: str,
    amount: Decimal,
    provider_reference: str,
    available_ledger_id: Any,
) -> Any:
    cur.execute(
        """
        select ledger_account_id
        from anevum.financial_ledger_accounts
        where account_code='SYSTEM:SANDBOX:CUSTODIAN_CASH:USD'
          and status='ACTIVE'
        """
    )
    system_row = cur.fetchone()
    if not system_row:
        raise ValueError("sandbox_custodian_ledger_missing")
    system_ledger_id = system_row[0]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "provider_transfer_settlement",
        "provider": "ALPACA_BROKER",
        "environment": "SANDBOX",
        "provider_reference": provider_reference,
        "direction": transfer_direction,
        "amount": str(amount),
        "currency": CURRENCY,
    }
    transaction_id, duplicate = _insert_transaction(
        cur,
        transaction_key=f"alpaca-sandbox-settle:{provider_reference}",
        transaction_type="SANDBOX_PROVIDER_TRANSFER",
        payload=payload,
        provider="ALPACA_BROKER",
        provider_reference=provider_reference,
        correlation_id=provider_reference,
    )
    if not duplicate:
        if transfer_direction == "DEPOSIT":
            entries = [
                (transaction_id, system_ledger_id, 1, "DEBIT", amount, "sandbox provider cash"),
                (transaction_id, available_ledger_id, 2, "CREDIT", amount, "customer available cash"),
            ]
        else:
            available = _ledger_balance(cur, ledger_account_id=available_ledger_id)
            if available < amount:
                raise ValueError("insufficient_available_cash_at_settlement")
            entries = [
                (transaction_id, available_ledger_id, 1, "DEBIT", amount, "customer withdrawal"),
                (transaction_id, system_ledger_id, 2, "CREDIT", amount, "sandbox provider cash release"),
            ]
        cur.executemany(
            """
            insert into anevum.financial_ledger_entries (
                transaction_id, ledger_account_id, sequence, side, amount, memo
            )
            values (%s,%s,%s,%s,%s,%s)
            """,
            entries,
        )
    cur.execute(
        """
        update anevum.financial_transfers
        set status='SETTLED',
            ledger_transaction_id=%s,
            settled_at=coalesce(settled_at,now()),
            updated_at=now()
        where transfer_id=%s
        """,
        (transaction_id, transfer_id),
    )
    return transaction_id


def sync_sandbox_provider_transfers(
    database_url: str,
    *,
    command_subject: str,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                raise ValueError("financial_customer_not_initialized")
            customer_id = customer[0]
            provider_row = _provider_account_for_customer(cur, customer_id=customer_id)
            if not provider_row:
                raise ValueError("sandbox_provider_account_not_found")
            _, provider_account_ref = provider_row
            cur.execute(
                """
                select transfer_id, direction, amount, provider_reference
                from anevum.financial_transfers
                where customer_id=%s
                  and provider='ALPACA_BROKER'
                  and provider_environment='SANDBOX'
                  and status='PENDING'
                  and provider_reference is not null
                order by created_at asc
                limit 25
                """,
                (customer_id,),
            )
            pending = list(cur.fetchall())

    provider = AlpacaBrokerSandboxProvider.from_env()
    observations: list[tuple[Any, str, Decimal, str, str]] = []
    try:
        for transfer_id, direction, amount, provider_reference in pending:
            remote = provider.get_transfer(provider_account_ref, str(provider_reference))
            remote_status = str(remote.get("status") or "").strip().upper()
            observations.append(
                (
                    transfer_id,
                    str(direction),
                    Decimal(str(amount)),
                    str(provider_reference),
                    remote_status,
                )
            )
    finally:
        provider.close()

    settled = 0
    failed = 0
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                _, available_ledger_id = _account_ledger(
                    cur,
                    customer_id=customer_id,
                    account_kind="AVAILABLE_CASH",
                )
                for transfer_id, direction, amount, provider_reference, remote_status in observations:
                    if remote_status in {"COMPLETE", "COMPLETED", "SETTLED"}:
                        _settle_provider_transfer(
                            cur,
                            transfer_id=transfer_id,
                            transfer_direction=direction,
                            amount=amount,
                            provider_reference=provider_reference,
                            available_ledger_id=available_ledger_id,
                        )
                        local_status = "SETTLED"
                        settled += 1
                    elif remote_status in {"CANCELED", "CANCELLED", "REJECTED", "FAILED", "RETURNED"}:
                        local_status = "CANCELED" if remote_status in {"CANCELED", "CANCELLED"} else "FAILED"
                        cur.execute(
                            """
                            update anevum.financial_transfers
                            set status=%s, failure_code=%s, updated_at=now()
                            where transfer_id=%s
                            """,
                            (local_status, remote_status, transfer_id),
                        )
                        failed += 1
                    else:
                        local_status = "PENDING"
                        cur.execute(
                            """
                            update anevum.financial_transfers
                            set metadata=metadata || %s, updated_at=now()
                            where transfer_id=%s
                            """,
                            (Jsonb({"provider_status": remote_status or None}), transfer_id),
                        )
                    cur.execute(
                        """
                        insert into anevum.financial_transfer_events (
                            event_key, transfer_id, event_type, status,
                            provider_reference, payload
                        )
                        values (%s,%s,'provider_transfer_observed',%s,%s,%s)
                        on conflict (event_key) do nothing
                        """,
                        (
                            f"{provider_reference}:observed:{remote_status or 'UNKNOWN'}",
                            transfer_id,
                            local_status,
                            provider_reference,
                            Jsonb({"provider_status": remote_status or None}),
                        ),
                    )
    result = read_snapshot(database_url, command_subject=subject)
    result["sync"] = {
        "observed": len(observations),
        "settled": settled,
        "failed": failed,
    }
    return result


def read_snapshot(
    database_url: str,
    *,
    command_subject: str,
) -> dict[str, Any]:
    subject = _safe_key(command_subject, field="command_subject", max_length=320)
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            customer = _customer_row(cur, subject)
            if not customer:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "initialized": False,
                    "currency": CURRENCY,
                    "balances": {
                        "available_cash": "0",
                        "rhen_allocation": "0",
                        "reserve": "0",
                        "total": "0",
                    },
                    "allocation": None,
                    "provider": sandbox_provider_status(),
                    "provider_accounts": [],
                    "bank_links": [],
                    "recent_transfers": [],
                    "external_money_movement_enabled": False,
                    "live_execution_authorized": False,
                }
            customer_id = customer[0]
            cur.execute(
                """
                select
                    fa.account_kind,
                    la.ledger_account_id
                from anevum.financial_accounts fa
                join anevum.financial_ledger_accounts la
                  on la.financial_account_id=fa.financial_account_id
                where fa.customer_id=%s
                  and fa.currency=%s
                order by fa.account_kind
                """,
                (customer_id, CURRENCY),
            )
            balances: dict[str, Decimal] = {
                "AVAILABLE_CASH": Decimal("0"),
                "RHEN_ALLOCATION": Decimal("0"),
                "RESERVE": Decimal("0"),
            }
            for kind, ledger_account_id in cur.fetchall():
                balances[str(kind)] = _ledger_balance(
                    cur,
                    ledger_account_id=ledger_account_id,
                )

            cur.execute(
                """
                select execution_mode,status,max_allocation,max_position_fraction,
                       max_daily_loss_fraction,strategy_version_id,
                       external_execution_enabled,updated_at
                from anevum.rhen_allocations
                where customer_id=%s
                order by updated_at desc
                limit 1
                """,
                (customer_id,),
            )
            allocation_row = cur.fetchone()
            allocation = None
            if allocation_row:
                allocation = {
                    "execution_mode": allocation_row[0],
                    "status": allocation_row[1],
                    "max_allocation": str(allocation_row[2]),
                    "max_position_fraction": str(allocation_row[3]),
                    "max_daily_loss_fraction": str(allocation_row[4]),
                    "strategy_version_id": allocation_row[5],
                    "external_execution_enabled": bool(allocation_row[6]),
                    "updated_at": allocation_row[7],
                }

            cur.execute(
                """
                select provider,provider_account_ref,provider_environment,status,metadata,updated_at
                from anevum.financial_provider_accounts
                where customer_id=%s
                order by created_at asc
                """,
                (customer_id,),
            )
            provider_accounts = [
                {
                    "provider": row[0],
                    "provider_account_ref": row[1],
                    "provider_environment": row[2],
                    "status": row[3],
                    "metadata": row[4] or {},
                    "updated_at": row[5],
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                select bank_link_id,provider,provider_relationship_ref,
                       bank_account_type,display_name,status,metadata,updated_at
                from anevum.financial_bank_links
                where customer_id=%s
                order by created_at asc
                """,
                (customer_id,),
            )
            bank_links = [
                {
                    "bank_link_id": str(row[0]),
                    "provider": row[1],
                    "provider_relationship_ref": row[2],
                    "bank_account_type": row[3],
                    "display_name": row[4],
                    "status": row[5],
                    "metadata": row[6] or {},
                    "updated_at": row[7],
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                select transfer_id,direction,provider,provider_environment,currency,
                       amount,status,provider_reference,created_at,settled_at
                from anevum.financial_transfers
                where customer_id=%s
                order by created_at desc
                limit 25
                """,
                (customer_id,),
            )
            transfers = [
                {
                    "transfer_id": str(row[0]),
                    "direction": row[1],
                    "provider": row[2],
                    "provider_environment": row[3],
                    "currency": row[4],
                    "amount": str(row[5]),
                    "status": row[6],
                    "provider_reference": row[7],
                    "created_at": row[8],
                    "settled_at": row[9],
                }
                for row in cur.fetchall()
            ]

    total = sum(balances.values(), Decimal("0"))
    return {
        "schema_version": SCHEMA_VERSION,
        "initialized": True,
        "customer": _serialize_customer(customer),
        "currency": CURRENCY,
        "balances": {
            "available_cash": str(balances["AVAILABLE_CASH"]),
            "rhen_allocation": str(balances["RHEN_ALLOCATION"]),
            "reserve": str(balances["RESERVE"]),
            "total": str(total),
        },
        "allocation": allocation,
        "provider": sandbox_provider_status(),
        "provider_accounts": provider_accounts,
        "bank_links": bank_links,
        "recent_transfers": transfers,
        "external_money_movement_enabled": EXTERNAL_MONEY_MOVEMENT_ENABLED,
        "live_execution_authorized": LIVE_EXECUTION_AUTHORIZED,
    }


def handle_financial_action(
    database_url: str,
    *,
    command_subject: str,
    action: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    if action == "initialize":
        return initialize_customer(
            database_url,
            command_subject=command_subject,
            display_label=str(body.get("display_label") or "").strip() or None,
        )
    if action == "sandbox_deposit":
        return sandbox_deposit(
            database_url,
            command_subject=command_subject,
            amount=body.get("amount"),
            idempotency_key=body.get("idempotency_key"),
        )
    if action == "sandbox_create_provider_account":
        application = body.get("application")
        if not isinstance(application, dict):
            raise ValueError("invalid_account_application")
        return create_sandbox_provider_account(
            database_url,
            command_subject=command_subject,
            application=application,
        )
    if action == "sandbox_refresh_provider_account":
        return refresh_sandbox_provider_account(
            database_url,
            command_subject=command_subject,
        )
    if action == "sandbox_link_bank":
        return create_sandbox_bank_link(
            database_url,
            command_subject=command_subject,
            processor_token=body.get("processor_token"),
            bank_account_type=str(body.get("bank_account_type") or "CHECKING"),
            nickname=str(body.get("nickname") or "").strip() or None,
        )
    if action == "sandbox_provider_transfer":
        return create_sandbox_provider_transfer(
            database_url,
            command_subject=command_subject,
            direction=str(body.get("direction") or ""),
            amount=body.get("amount"),
            bank_link_id=str(body.get("bank_link_id") or ""),
            idempotency_key=body.get("idempotency_key"),
        )
    if action == "sandbox_sync_provider":
        refreshed = refresh_sandbox_provider_account(
            database_url,
            command_subject=command_subject,
        )
        if refreshed.get("provider_accounts"):
            return sync_sandbox_provider_transfers(
                database_url,
                command_subject=command_subject,
            )
        return refreshed
    if action == "set_rhen_allocation":
        return set_rhen_allocation(
            database_url,
            command_subject=command_subject,
            target_amount=body.get("target_amount"),
            idempotency_key=body.get("idempotency_key"),
            execution_mode=str(body.get("execution_mode") or "PAPER"),
            max_position_fraction=body.get("max_position_fraction", "0.10"),
            max_daily_loss_fraction=body.get("max_daily_loss_fraction", "0.05"),
            strategy_version_id=body.get("strategy_version_id"),
        )
    raise ValueError("invalid_financial_action")
