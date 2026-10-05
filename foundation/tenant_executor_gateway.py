from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from app.platform_core.contracts import deterministic_order_identity
from app.platform_core.reconciliation import TenantBrokerReconciler
from app.platform_core.tenant_execution import (
    TenantEntryRiskInput,
    TenantPaperBroker,
    TenantPaperSignal,
    canonical_crypto_symbol,
    decimal_value,
    evaluate_tenant_entry_risk,
    position_for_symbol,
)
from foundation.platform_core_gateway import (
    load_tenant_broker_account,
    record_broker_reconciliation,
    record_tenant_executor_heartbeat,
    tenant_execution_eligibility,
)


BrokerClientFactory = Callable[[Any], TenantPaperBroker]


def _rows(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    columns = [column.name for column in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def record_strategy_signal(
    conn: psycopg.Connection[Any],
    *,
    signal_id: str,
    strategy_release_id: str,
    action: str,
    symbol: str,
    reference_price: Any,
    target_allocation_fraction: Any = None,
    stop_price: Any = None,
    stop_limit_price: Any = None,
    take_profit_price: Any = None,
    observed_at: datetime,
    expires_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("expires_at must be timezone-aware")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into rhen.strategy_signals(
                    signal_id,strategy_release_id,action,symbol,reference_price,
                    target_allocation_fraction,stop_price,stop_limit_price,
                    take_profit_price,observed_at,expires_at,metadata
                )
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict(signal_id) do nothing
                returning *
                """,
                (
                    str(signal_id),
                    str(strategy_release_id),
                    str(action).upper(),
                    canonical_crypto_symbol(symbol),
                    reference_price,
                    target_allocation_fraction,
                    stop_price,
                    stop_limit_price,
                    take_profit_price,
                    observed_at.astimezone(timezone.utc),
                    expires_at.astimezone(timezone.utc),
                    Jsonb(metadata or {}),
                ),
            )
            row = cur.fetchone()
            if row:
                columns = [column.name for column in cur.description]
                return dict(zip(columns, row))

            cur.execute(
                "select * from rhen.strategy_signals where signal_id=%s",
                (str(signal_id),),
            )
            existing = cur.fetchone()
            if not existing:
                raise RuntimeError("strategy_signal_insert_failed")
            columns = [column.name for column in cur.description]
            result = dict(zip(columns, existing))

    immutable_fields = {
        "strategy_release_id": str(strategy_release_id),
        "action": str(action).upper(),
        "symbol": canonical_crypto_symbol(symbol),
        "reference_price": Decimal(str(reference_price)),
        "target_allocation_fraction": (
            Decimal(str(target_allocation_fraction))
            if target_allocation_fraction is not None
            else None
        ),
        "stop_price": Decimal(str(stop_price)) if stop_price is not None else None,
        "stop_limit_price": (
            Decimal(str(stop_limit_price)) if stop_limit_price is not None else None
        ),
        "take_profit_price": (
            Decimal(str(take_profit_price)) if take_profit_price is not None else None
        ),
    }
    for field, expected in immutable_fields.items():
        actual = result.get(field)
        if isinstance(expected, Decimal):
            actual = Decimal(str(actual))
        if actual != expected:
            raise ValueError("strategy_signal_idempotency_conflict")
    return result


def load_strategy_signal(
    conn: psycopg.Connection[Any],
    signal_id: str,
) -> tuple[TenantPaperSignal, datetime, datetime]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select signal_id,strategy_release_id,action,symbol,reference_price,
                   target_allocation_fraction,stop_price,stop_limit_price,
                   take_profit_price,observed_at,expires_at,metadata
            from rhen.strategy_signals
            where signal_id=%s
            """,
            (str(signal_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("strategy_signal_not_found")
        columns = [column.name for column in cur.description]
        record = dict(zip(columns, row))
    return (
        TenantPaperSignal.from_record(record),
        record["observed_at"],
        record["expires_at"],
    )


class TenantPaperExecutor:
    """Fan one immutable RHEN signal into independent PAPER account decisions."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        client_factory: BrokerClientFactory,
        runtime_id: str,
        source_commit: str,
        deployment_id: str | None = None,
    ):
        self.conn = conn
        self.client_factory = client_factory
        self.runtime_id = str(runtime_id)
        self.source_commit = str(source_commit)
        self.deployment_id = deployment_id
        if not self.runtime_id:
            raise ValueError("runtime_id_required")
        if not self.source_commit:
            raise ValueError("source_commit_required")

    def heartbeat(
        self,
        *,
        status: str = "READY",
        at: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = at or datetime.now(timezone.utc)
        return record_tenant_executor_heartbeat(
            self.conn,
            runtime_id=self.runtime_id,
            source_commit=self.source_commit,
            deployment_id=self.deployment_id,
            status=status,
            heartbeat_at=stamp,
            capabilities={
                "tenant_isolation": True,
                "crypto_spot": True,
                "paper_only": True,
                "intent_before_order": True,
                "ambiguous_submission_reconciliation": True,
                "protective_stop": True,
            },
        )

    def _candidate_accounts(self, release_id: str) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select tsa.tenant_id,tsa.broker_account_id,t.status as tenant_status
                from anevum.tenant_strategy_assignments tsa
                join anevum.broker_accounts b
                  on b.tenant_id=tsa.tenant_id
                 and b.broker_account_id=tsa.broker_account_id
                join anevum.tenants t on t.tenant_id=tsa.tenant_id
                where tsa.strategy_release_id=%s
                  and tsa.status='ACTIVE'
                  and b.environment='PAPER'
                  and b.provider='ALPACA'
                order by tsa.tenant_id,tsa.broker_account_id
                """,
                (release_id,),
            )
            return _rows(cur)

    def _risk_input(
        self,
        tenant_id: str,
        broker_account_id: str,
    ) -> TenantEntryRiskInput:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select a.allocation_fraction,a.absolute_cap,
                       rp.max_position_fraction,
                       rp.max_gross_exposure_fraction,
                       rp.max_daily_loss_fraction,
                       rp.max_drawdown_fraction,
                       rp.max_concurrent_positions,
                       s.high_water_equity
                from anevum.capital_allocations a
                join anevum.risk_profiles rp
                  on rp.tenant_id=a.tenant_id
                 and rp.broker_account_id=a.broker_account_id
                 and rp.status='ACTIVE'
                left join anevum.tenant_execution_account_state s
                  on s.tenant_id=a.tenant_id
                 and s.broker_account_id=a.broker_account_id
                where a.tenant_id=%s
                  and a.broker_account_id=%s
                  and a.status='ACTIVE'
                order by a.effective_at desc,rp.effective_at desc
                limit 1
                """,
                (tenant_id, broker_account_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("tenant_allocation_or_risk_profile_unavailable")
        return TenantEntryRiskInput(
            allocation_fraction=Decimal(str(row[0])),
            absolute_cap=Decimal(str(row[1])),
            max_position_fraction=Decimal(str(row[2])),
            max_gross_exposure_fraction=Decimal(str(row[3])),
            max_daily_loss_fraction=Decimal(str(row[4])),
            max_drawdown_fraction=Decimal(str(row[5])),
            max_concurrent_positions=int(row[6]),
            high_water_equity=(
                Decimal(str(row[7])) if row[7] is not None else None
            ),
        )

    def _record_account_risk_state(
        self,
        *,
        tenant_id: str,
        broker_account_id: str,
        equity: Decimal,
        high_water: Decimal,
        signal_id: str,
        observed_at: datetime,
    ) -> None:
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    insert into anevum.tenant_execution_account_state(
                        tenant_id,broker_account_id,high_water_equity,last_equity,
                        last_observed_at,last_signal_id,updated_at
                    )
                    values(%s,%s,%s,%s,%s,%s,now())
                    on conflict(tenant_id,broker_account_id) do update
                    set high_water_equity=greatest(
                            coalesce(anevum.tenant_execution_account_state.high_water_equity,0),
                            excluded.high_water_equity
                        ),
                        last_equity=excluded.last_equity,
                        last_observed_at=excluded.last_observed_at,
                        last_signal_id=excluded.last_signal_id,
                        updated_at=now()
                    """,
                    (
                        tenant_id,
                        broker_account_id,
                        high_water,
                        max(equity, Decimal("0")),
                        observed_at,
                        signal_id,
                    ),
                )

    def _intent(
        self,
        *,
        tenant_id: str,
        broker_account_id: str,
        signal: TenantPaperSignal,
        signal_id: str,
        side: str,
        intent_kind: str,
        status: str,
        risk_decision: dict[str, Any],
        qty: Decimal | None = None,
        notional: Decimal | None = None,
    ) -> dict[str, Any]:
        order_intent_id, client_order_id = deterministic_order_identity(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            strategy_release_id=signal.strategy_release_id,
            signal_id=signal_id,
            symbol=signal.symbol,
            side=side,
        )
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    insert into rhen.account_order_intents(
                        order_intent_id,tenant_id,broker_account_id,
                        strategy_release_id,signal_id,symbol,side,status,
                        requested_qty,requested_notional,risk_decision,
                        client_order_id,correlation_id,intent_kind
                    )
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict(order_intent_id) do nothing
                    """,
                    (
                        order_intent_id,
                        tenant_id,
                        broker_account_id,
                        signal.strategy_release_id,
                        signal_id,
                        signal.symbol,
                        side,
                        status,
                        qty,
                        notional,
                        Jsonb(risk_decision),
                        client_order_id,
                        signal.signal_id,
                        intent_kind,
                    ),
                )
                cur.execute(
                    """
                    select order_intent_id,status,requested_qty,requested_notional,
                           risk_decision,client_order_id,broker_order_id,
                           submitted_at,resolved_at,intent_kind
                    from rhen.account_order_intents
                    where order_intent_id=%s
                    """,
                    (order_intent_id,),
                )
                row = cur.fetchone()
                columns = [column.name for column in cur.description]
        return dict(zip(columns, row))

    def _existing_intent(
        self,
        *,
        tenant_id: str,
        broker_account_id: str,
        signal: TenantPaperSignal,
        signal_id: str,
        side: str,
    ) -> dict[str, Any] | None:
        order_intent_id, _client_order_id = deterministic_order_identity(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            strategy_release_id=signal.strategy_release_id,
            signal_id=signal_id,
            symbol=signal.symbol,
            side=side,
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select order_intent_id,status,requested_qty,requested_notional,
                       risk_decision,client_order_id,broker_order_id,
                       submitted_at,resolved_at,intent_kind
                from rhen.account_order_intents
                where order_intent_id=%s
                """,
                (order_intent_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            columns = [column.name for column in cur.description]
        return dict(zip(columns, row))

    def _update_intent(
        self,
        order_intent_id: str,
        *,
        status: str,
        broker_order_id: str | None = None,
        submitted: bool = False,
        resolved: bool = False,
        risk_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sets = ["status=%s"]
        values: list[Any] = [status]
        if broker_order_id is not None:
            sets.append("broker_order_id=%s")
            values.append(str(broker_order_id))
        if submitted:
            sets.append("submitted_at=coalesce(submitted_at,now())")
        if resolved:
            sets.append("resolved_at=now()")
        if risk_decision is not None:
            sets.append("risk_decision=%s")
            values.append(Jsonb(risk_decision))
        values.append(order_intent_id)
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""
                    update rhen.account_order_intents
                    set {", ".join(sets)}
                    where order_intent_id=%s
                    returning order_intent_id,status,requested_qty,requested_notional,
                              risk_decision,client_order_id,broker_order_id,
                              submitted_at,resolved_at,intent_kind
                    """,
                    tuple(values),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("order_intent_not_found")
                columns = [column.name for column in cur.description]
        return dict(zip(columns, row))

    async def _recover_or_submit(
        self,
        *,
        client: TenantPaperBroker,
        intent: dict[str, Any],
        submit,
    ) -> tuple[str, dict[str, Any] | None]:
        client_order_id = str(intent["client_order_id"])
        existing = await client.order_by_client_order_id(client_order_id)
        if existing is not None:
            broker_status = str(existing.get("status") or "").lower()
            if broker_status in {"canceled", "cancelled", "expired", "rejected"}:
                self._update_intent(
                    str(intent["order_intent_id"]),
                    status="FAILED",
                    broker_order_id=str(existing.get("id") or "") or None,
                    submitted=True,
                    resolved=True,
                )
                return "broker_terminal_failure", existing
            self._update_intent(
                str(intent["order_intent_id"]),
                status="ACKNOWLEDGED",
                broker_order_id=str(existing.get("id") or "") or None,
                submitted=True,
            )
            return "reconciled", existing

        current_status = str(intent.get("status") or "")
        if current_status in {"ACKNOWLEDGED", "RECONCILED"}:
            return "acknowledged", None
        if current_status in {"AMBIGUOUS", "SUBMITTING"}:
            return "ambiguous_unresolved", None
        if current_status == "RISK_REJECTED":
            return "risk_rejected", None

        self._update_intent(
            str(intent["order_intent_id"]),
            status="SUBMITTING",
            submitted=True,
        )
        try:
            order = await submit()
        except Exception:
            recovered = await client.order_by_client_order_id(client_order_id)
            if recovered is not None:
                self._update_intent(
                    str(intent["order_intent_id"]),
                    status="ACKNOWLEDGED",
                    broker_order_id=str(recovered.get("id") or "") or None,
                    submitted=True,
                )
                return "reconciled_after_error", recovered
            self._update_intent(
                str(intent["order_intent_id"]),
                status="AMBIGUOUS",
                submitted=True,
            )
            return "ambiguous", None

        broker_status = str(order.get("status") or "").lower()
        if broker_status in {"canceled", "cancelled", "expired", "rejected"}:
            self._update_intent(
                str(intent["order_intent_id"]),
                status="FAILED",
                broker_order_id=str(order.get("id") or "") or None,
                submitted=True,
                resolved=True,
            )
            return "broker_terminal_failure", order
        self._update_intent(
            str(intent["order_intent_id"]),
            status="ACKNOWLEDGED",
            broker_order_id=str(order.get("id") or "") or None,
            submitted=True,
        )
        return "submitted", order

    async def _ensure_protection(
        self,
        *,
        client: TenantPaperBroker,
        tenant_id: str,
        broker_account_id: str,
        signal: TenantPaperSignal,
        buy_intent: dict[str, Any],
        buy_order: dict[str, Any] | None,
    ) -> dict[str, Any]:
        order = buy_order
        if order is None:
            order = await client.order_by_client_order_id(
                str(buy_intent["client_order_id"])
            )
        if not order:
            return {"state": "PENDING", "reason": "entry_order_not_observed"}

        status = str(order.get("status") or "").lower()
        filled_qty = decimal_value(order.get("filled_qty"))
        if status != "filled" or filled_qty <= 0:
            return {"state": "PENDING", "reason": "entry_not_fully_filled"}

        protective_signal_id = signal.signal_id + ":hardstop"
        protective = self._intent(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            signal=signal,
            signal_id=protective_signal_id,
            side="SELL",
            intent_kind="PROTECTIVE_STOP",
            status="READY",
            risk_decision={
                "allowed": True,
                "risk_reducing": True,
                "reason": "protective_stop_for_rhen_entry",
                "entry_order_intent_id": buy_intent["order_intent_id"],
            },
            qty=filled_qty,
        )
        state, stop_order = await self._recover_or_submit(
            client=client,
            intent=protective,
            submit=lambda: client.submit_crypto_stop_limit_sell(
                symbol=signal.symbol,
                qty=str(filled_qty),
                stop_price=str(signal.stop_price),
                limit_price=str(signal.stop_limit_price),
                client_order_id=str(protective["client_order_id"]),
            ),
        )
        stop_status = str((stop_order or {}).get("status") or "").lower()
        protected = (
            state in {"submitted", "reconciled", "reconciled_after_error", "acknowledged"}
            and stop_status not in {"canceled", "cancelled", "expired", "rejected"}
        )
        return {
            "state": "PROTECTED" if protected else "BLOCKED",
            "submission_state": state,
            "order": stop_order,
        }

    async def _owned_qty(
        self,
        *,
        client: TenantPaperBroker,
        tenant_id: str,
        broker_account_id: str,
        symbol: str,
    ) -> Decimal:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select side,client_order_id,intent_kind
                from rhen.account_order_intents
                where tenant_id=%s
                  and broker_account_id=%s
                  and symbol=%s
                  and status in ('ACKNOWLEDGED','RECONCILED')
                  and intent_kind in ('ENTRY','EXIT','PROTECTIVE_STOP')
                order by created_at
                """,
                (tenant_id, broker_account_id, symbol),
            )
            rows = cur.fetchall()

        owned = Decimal("0")
        for side, client_order_id, _kind in rows:
            order = await client.order_by_client_order_id(str(client_order_id))
            if not order:
                continue
            filled = decimal_value(order.get("filled_qty"))
            if filled <= 0:
                continue
            if str(side).upper() == "BUY":
                owned += filled
            else:
                owned -= filled
        return max(owned, Decimal("0"))

    async def _cancel_open_protection(
        self,
        *,
        client: TenantPaperBroker,
        tenant_id: str,
        broker_account_id: str,
        symbol: str,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select client_order_id
                from rhen.account_order_intents
                where tenant_id=%s
                  and broker_account_id=%s
                  and symbol=%s
                  and intent_kind='PROTECTIVE_STOP'
                  and status in ('ACKNOWLEDGED','RECONCILED')
                order by created_at desc
                """,
                (tenant_id, broker_account_id, symbol),
            )
            rows = cur.fetchall()
        for (client_order_id,) in rows:
            order = await client.order_by_client_order_id(str(client_order_id))
            if not order:
                continue
            if str(order.get("status") or "").lower() in {
                "new", "accepted", "pending_new", "partially_filled"
            } and order.get("id"):
                await client.cancel_order(str(order["id"]))

    async def _execute_exit(
        self,
        *,
        client: TenantPaperBroker,
        tenant_id: str,
        broker_account_id: str,
        signal: TenantPaperSignal,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if bool(account.get("trading_blocked")):
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "blocked",
                "reason": "broker_trading_blocked",
            }

        position = position_for_symbol(positions, signal.symbol)
        if position is None:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "hold",
                "reason": "no_position",
            }

        owned = await self._owned_qty(
            client=client,
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            symbol=signal.symbol,
        )
        broker_qty = abs(decimal_value(position.get("qty")))
        exit_qty = min(owned, broker_qty)
        if exit_qty <= 0:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "blocked",
                "reason": "no_verified_rhen_owned_quantity",
            }

        await self._cancel_open_protection(
            client=client,
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            symbol=signal.symbol,
        )

        intent = self._intent(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            signal=signal,
            signal_id=signal.signal_id,
            side="SELL",
            intent_kind="EXIT",
            status="READY",
            risk_decision={
                "allowed": True,
                "risk_reducing": True,
                "verified_rhen_owned_qty": str(owned),
                "broker_position_qty": str(broker_qty),
                "approved_exit_qty": str(exit_qty),
            },
            qty=exit_qty,
        )
        state, order = await self._recover_or_submit(
            client=client,
            intent=intent,
            submit=lambda: client.submit_crypto_market_sell(
                symbol=signal.symbol,
                qty=str(exit_qty),
                client_order_id=str(intent["client_order_id"]),
            ),
        )
        return {
            "tenant_id": tenant_id,
            "broker_account_id": broker_account_id,
            "action": "submitted" if order is not None else "blocked",
            "submission_state": state,
            "order": order,
            "order_intent_id": intent["order_intent_id"],
        }

    async def _execute_account(
        self,
        *,
        signal: TenantPaperSignal,
        tenant_id: str,
        broker_account_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        try:
            broker_account = load_tenant_broker_account(
                self.conn,
                tenant_id=tenant_id,
                broker_account_id=broker_account_id,
            )
            if broker_account.environment.upper() != "PAPER":
                raise ValueError("tenant_executor_paper_account_required")
            client = self.client_factory(broker_account)
        except Exception as exc:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "blocked",
                "reason": f"broker_client_unavailable:{type(exc).__name__}",
            }

        reconciliation = await TenantBrokerReconciler(
            broker_account,
            client,  # type: ignore[arg-type]
        ).reconcile()
        record_broker_reconciliation(self.conn, reconciliation)
        if not reconciliation.ready:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "blocked",
                "reason": reconciliation.error_code or "broker_reconciliation_failed",
            }

        account = dict(reconciliation.account_snapshot or {})
        positions = list(reconciliation.positions_snapshot or [])
        open_orders = list(reconciliation.open_orders_snapshot or [])

        if signal.action == "EXIT_LONG":
            return await self._execute_exit(
                client=client,
                tenant_id=tenant_id,
                broker_account_id=broker_account_id,
                signal=signal,
                account=account,
                positions=positions,
            )

        existing_entry = self._existing_intent(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            signal=signal,
            signal_id=signal.signal_id,
            side="BUY",
        )
        if existing_entry is not None:
            existing_status = str(existing_entry.get("status") or "")
            if existing_status == "RISK_REJECTED":
                return {
                    "tenant_id": tenant_id,
                    "broker_account_id": broker_account_id,
                    "action": "rejected",
                    "reasons": list(
                        dict(existing_entry.get("risk_decision") or {}).get("reasons") or []
                    ),
                    "order_intent_id": existing_entry["order_intent_id"],
                    "replayed": True,
                }
            stored_qty = decimal_value(existing_entry.get("requested_qty"))
            state, order = await self._recover_or_submit(
                client=client,
                intent=existing_entry,
                submit=lambda: client.submit_crypto_market_buy(
                    symbol=signal.symbol,
                    qty=str(stored_qty),
                    client_order_id=str(existing_entry["client_order_id"]),
                ),
            )
            if state in {"ambiguous", "ambiguous_unresolved", "broker_terminal_failure"}:
                return {
                    "tenant_id": tenant_id,
                    "broker_account_id": broker_account_id,
                    "action": "blocked",
                    "reason": "ambiguous_submission_unresolved",
                    "submission_state": state,
                    "order_intent_id": existing_entry["order_intent_id"],
                    "replayed": True,
                }
            protection = await self._ensure_protection(
                client=client,
                tenant_id=tenant_id,
                broker_account_id=broker_account_id,
                signal=signal,
                buy_intent=existing_entry,
                buy_order=order,
            )
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "reconciled",
                "submission_state": state,
                "order": order,
                "protection": protection,
                "order_intent_id": existing_entry["order_intent_id"],
                "replayed": True,
            }

        gate_now = max(
            now.astimezone(timezone.utc),
            reconciliation.observed_at.astimezone(timezone.utc),
        )
        gate_input, gate = tenant_execution_eligibility(
            self.conn,
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            now=gate_now,
        )
        if not gate.eligible:
            intent = self._intent(
                tenant_id=tenant_id,
                broker_account_id=broker_account_id,
                signal=signal,
                signal_id=signal.signal_id,
                side="BUY",
                intent_kind="ENTRY",
                status="RISK_REJECTED",
                risk_decision={
                    "allowed": False,
                    "layer": "platform_eligibility",
                    "reasons": list(gate.reasons),
                },
            )
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "rejected",
                "reasons": list(gate.reasons),
                "order_intent_id": intent["order_intent_id"],
                "environment": gate_input.environment,
            }

        risk_input = self._risk_input(tenant_id, broker_account_id)
        decision = evaluate_tenant_entry_risk(
            signal,
            risk_input,
            account,
            positions,
            open_orders,
        )
        observed_at = reconciliation.observed_at
        self._record_account_risk_state(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            equity=decimal_value(account.get("equity")),
            high_water=decision.next_high_water_equity,
            signal_id=signal.signal_id,
            observed_at=observed_at,
        )
        intent = self._intent(
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            signal=signal,
            signal_id=signal.signal_id,
            side="BUY",
            intent_kind="ENTRY",
            status="READY" if decision.allowed else "RISK_REJECTED",
            risk_decision=decision.payload(),
            qty=decision.approved_qty if decision.approved_qty > 0 else None,
            notional=(
                decision.approved_notional
                if decision.approved_notional > 0
                else None
            ),
        )
        if not decision.allowed:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "rejected",
                "reasons": list(decision.reasons),
                "order_intent_id": intent["order_intent_id"],
            }

        state, order = await self._recover_or_submit(
            client=client,
            intent=intent,
            submit=lambda: client.submit_crypto_market_buy(
                symbol=signal.symbol,
                qty=str(decision.approved_qty),
                client_order_id=str(intent["client_order_id"]),
            ),
        )
        if state in {"ambiguous", "ambiguous_unresolved", "broker_terminal_failure"}:
            return {
                "tenant_id": tenant_id,
                "broker_account_id": broker_account_id,
                "action": "blocked",
                "reason": "ambiguous_submission_unresolved",
                "submission_state": state,
                "order_intent_id": intent["order_intent_id"],
            }

        protection = await self._ensure_protection(
            client=client,
            tenant_id=tenant_id,
            broker_account_id=broker_account_id,
            signal=signal,
            buy_intent=intent,
            buy_order=order,
        )
        return {
            "tenant_id": tenant_id,
            "broker_account_id": broker_account_id,
            "action": "submitted" if order is not None else "reconciled",
            "submission_state": state,
            "order": order,
            "protection": protection,
            "order_intent_id": intent["order_intent_id"],
        }

    async def execute_signal(
        self,
        signal_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        stamp = stamp.astimezone(timezone.utc)
        self.heartbeat(at=stamp)

        signal, observed_at, expires_at = load_strategy_signal(
            self.conn,
            signal_id,
        )
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if stamp < observed_at.astimezone(timezone.utc):
            raise ValueError("strategy_signal_not_yet_active")
        if stamp > expires_at.astimezone(timezone.utc):
            return {
                "signal_id": signal.signal_id,
                "strategy_release_id": signal.strategy_release_id,
                "state": "EXPIRED",
                "results": [],
            }

        candidates = self._candidate_accounts(signal.strategy_release_id)
        results: list[dict[str, Any]] = []
        for row in candidates:
            results.append(
                await self._execute_account(
                    signal=signal,
                    tenant_id=str(row["tenant_id"]),
                    broker_account_id=str(row["broker_account_id"]),
                    now=stamp,
                )
            )

        return {
            "signal_id": signal.signal_id,
            "strategy_release_id": signal.strategy_release_id,
            "state": "COMPLETE",
            "candidate_accounts": len(candidates),
            "results": results,
        }

    async def run_pending(
        self,
        *,
        limit: int = 25,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        stamp = now or datetime.now(timezone.utc)
        self.heartbeat(at=stamp)
        bounded = max(1, min(int(limit), 100))
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select signal_id
                from rhen.strategy_signals
                where observed_at <= %s
                  and expires_at >= %s
                order by observed_at,signal_id
                limit %s
                """,
                (stamp, stamp, bounded),
            )
            signal_ids = [str(row[0]) for row in cur.fetchall()]
        results: list[dict[str, Any]] = []
        for signal_id in signal_ids:
            results.append(await self.execute_signal(signal_id, now=stamp))
        return results
