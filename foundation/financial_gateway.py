from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


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
                transaction_key = f"rhen-allocation:{subject}:{key}"
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
                    transaction_key = f"rhen-allocation-noop:{subject}:{key}"

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
                    "provider_accounts": [],
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
        "provider_accounts": provider_accounts,
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
