"""Real PostgreSQL tests for tenant-isolated RHEN paper execution."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from foundation.tenant_executor_gateway import (
    TenantPaperExecutor,
    record_strategy_signal,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="isolated PostgreSQL required",
)


class RollbackTest(Exception):
    pass


@pytest.fixture(name="conn")
def isolated_conn():
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as db:
        try:
            with db.transaction():
                yield db
                raise RollbackTest()
        except RollbackTest:
            pass


class FakeTenantBroker:
    def __init__(
        self,
        provider_account_id: str,
        *,
        equity: str = "100",
        crypto_capacity: str = "100",
        ambiguous_after_accept: bool = False,
        ambiguous_without_accept: bool = False,
    ):
        self.provider_account_id = provider_account_id
        self.equity = Decimal(equity)
        self.crypto_capacity = Decimal(crypto_capacity)
        self.ambiguous_after_accept = ambiguous_after_accept
        self.ambiguous_without_accept = ambiguous_without_accept
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self.submissions: list[tuple[str, str, str]] = []

    async def account_snapshot(self):
        return {
            "id": self.provider_account_id,
            "status": "ACTIVE",
            "crypto_status": "ACTIVE",
            "currency": "USD",
            "cash": str(self.crypto_capacity),
            "buying_power": str(self.crypto_capacity * Decimal("2")),
            "non_marginable_buying_power": str(self.crypto_capacity),
            "portfolio_value": str(self.equity),
            "equity": str(self.equity),
            "last_equity": str(self.equity),
            "long_market_value": "0",
            "short_market_value": "0",
            "trading_blocked": False,
            "transfers_blocked": False,
            "account_blocked": False,
        }

    async def positions_snapshot(self):
        return [dict(value) for value in self.positions.values()]

    async def open_orders_snapshot(self):
        return [
            dict(value)
            for value in self.orders.values()
            if str(value.get("status") or "").lower()
            not in {"filled", "canceled", "cancelled", "expired", "rejected"}
        ]

    async def recent_orders_snapshot(self, *, limit=100):
        return [dict(value) for value in list(self.orders.values())[-limit:]]

    async def order_by_client_order_id(self, client_order_id):
        row = self.orders.get(str(client_order_id))
        return dict(row) if row else None

    def _position(self, symbol: str) -> dict | None:
        return self.positions.get(symbol.upper())

    async def submit_crypto_market_buy(self, *, symbol, qty, client_order_id):
        self.submissions.append(("BUY", symbol, client_order_id))
        if self.ambiguous_without_accept:
            self.ambiguous_without_accept = False
            raise RuntimeError("simulated broker timeout before acceptance")

        quantity = Decimal(str(qty))
        order = {
            "id": f"order-{uuid4()}",
            "client_order_id": client_order_id,
            "symbol": symbol.upper(),
            "asset_class": "crypto",
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "qty": str(quantity),
            "filled_qty": str(quantity),
            "filled_avg_price": "100",
            "status": "filled",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        self.orders[client_order_id] = order
        prior = self._position(symbol)
        prior_qty = Decimal(str((prior or {}).get("qty") or "0"))
        total = prior_qty + quantity
        self.positions[symbol.upper()] = {
            "asset_id": f"asset-{symbol}",
            "symbol": symbol.upper(),
            "asset_class": "crypto",
            "side": "long",
            "qty": str(total),
            "avg_entry_price": "100",
            "market_value": str(total * Decimal("100")),
            "current_price": "100",
            "unrealized_pl": "0",
            "unrealized_plpc": "0",
        }
        if self.ambiguous_after_accept:
            self.ambiguous_after_accept = False
            raise RuntimeError("simulated timeout after broker acceptance")
        return dict(order)

    async def submit_crypto_market_sell(self, *, symbol, qty, client_order_id):
        self.submissions.append(("SELL", symbol, client_order_id))
        quantity = Decimal(str(qty))
        order = {
            "id": f"order-{uuid4()}",
            "client_order_id": client_order_id,
            "symbol": symbol.upper(),
            "asset_class": "crypto",
            "side": "sell",
            "type": "market",
            "time_in_force": "gtc",
            "qty": str(quantity),
            "filled_qty": str(quantity),
            "filled_avg_price": "101",
            "status": "filled",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        self.orders[client_order_id] = order
        prior = self._position(symbol)
        prior_qty = Decimal(str((prior or {}).get("qty") or "0"))
        remaining = max(prior_qty - quantity, Decimal("0"))
        if remaining > 0:
            self.positions[symbol.upper()] = {
                **(prior or {}),
                "qty": str(remaining),
                "market_value": str(remaining * Decimal("101")),
                "current_price": "101",
            }
        else:
            self.positions.pop(symbol.upper(), None)
        return dict(order)

    async def submit_crypto_stop_limit_sell(
        self,
        *,
        symbol,
        qty,
        stop_price,
        limit_price,
        client_order_id,
    ):
        self.submissions.append(("STOP", symbol, client_order_id))
        order = {
            "id": f"order-{uuid4()}",
            "client_order_id": client_order_id,
            "symbol": symbol.upper(),
            "asset_class": "crypto",
            "side": "sell",
            "type": "stop_limit",
            "time_in_force": "gtc",
            "qty": str(qty),
            "filled_qty": "0",
            "stop_price": str(stop_price),
            "limit_price": str(limit_price),
            "status": "accepted",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.orders[client_order_id] = order
        return dict(order)

    async def cancel_order(self, order_id):
        for order in self.orders.values():
            if str(order.get("id")) == str(order_id):
                order["status"] = "canceled"
                order["canceled_at"] = datetime.now(timezone.utc).isoformat()
                return


def _stable_release(conn):
    release_id = f"RHEN-TENANT-PAPER-{uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.strategy_releases(
                strategy_release_id,strategy_key,semantic_version,channel,
                lifecycle_state,source_commit,strategy_hash,configuration_hash,
                risk_policy_version,evidence,approved_by,approved_at
            )
            values(%s,'RHEN-BTC','1.0.0-test','STABLE','STABLE',
                   'test-commit','strategy-hash','config-hash','risk-v1',
                   '{}'::jsonb,'test-operator',now())
            """,
            (release_id,),
        )
        cur.execute(
            """
            insert into iren.system_state(system_key,health,state,observed_at)
            values('IREN','HEALTHY','{}'::jsonb,now())
            on conflict(system_key) do update
            set health='HEALTHY',state='{}'::jsonb,observed_at=now(),updated_at=now()
            """
        )
    return release_id


def _tenant(conn, release_id: str, *, name: str, cap: str):
    tenant_id = uuid4()
    broker_id = uuid4()
    provider_id = f"paper-{name}-{uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.tenants(
                tenant_id,tenant_key,display_name,status
            )
            values(%s,%s,%s,'ACTIVE')
            """,
            (tenant_id, f"{name}-{uuid4()}", name),
        )
        cur.execute(
            """
            insert into anevum.entitlements(
                tenant_id,product_key,status,source,effective_at
            )
            values(%s,'COMMAND','ACTIVE','test',now())
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            insert into anevum.broker_accounts(
                broker_account_id,tenant_id,provider,provider_account_id,
                environment,account_status,crypto_enabled,trading_blocked,
                withdrawals_blocked
            )
            values(%s,%s,'ALPACA',%s,'PAPER','ACTIVE',true,false,false)
            """,
            (broker_id, tenant_id, provider_id),
        )
        cur.execute(
            """
            insert into anevum.broker_authorizations(
                broker_account_id,authorization_kind,secret_reference,
                scopes,status,issued_at,last_validated_at
            )
            values(%s,'OAUTH',%s,%s,'ACTIVE',now(),now())
            """,
            (
                broker_id,
                f"test-secret://{uuid4()}",
                Jsonb(["trading"]),
            ),
        )
        cur.execute(
            """
            insert into anevum.capital_allocations(
                tenant_id,broker_account_id,allocation_fraction,
                absolute_cap,status
            )
            values(%s,%s,1,%s,'ACTIVE')
            """,
            (tenant_id, broker_id, cap),
        )
        cur.execute(
            """
            insert into anevum.risk_profiles(
                tenant_id,broker_account_id,max_position_fraction,
                max_gross_exposure_fraction,max_daily_loss_fraction,
                max_drawdown_fraction,max_concurrent_positions,status
            )
            values(%s,%s,0.50,1,0.10,0.50,5,'ACTIVE')
            """,
            (tenant_id, broker_id),
        )
        cur.execute(
            """
            insert into anevum.tenant_strategy_assignments(
                tenant_id,broker_account_id,strategy_release_id,
                status,assigned_by
            )
            values(%s,%s,%s,'ACTIVE','test')
            """,
            (tenant_id, broker_id, release_id),
        )
        cur.execute(
            """
            insert into anevum.tenant_trading_controls(
                tenant_id,broker_account_id,bot_enabled,
                customer_consent_version,customer_consented_at,updated_by
            )
            values(%s,%s,true,'paper-v1',now(),'test')
            """,
            (tenant_id, broker_id),
        )
    return str(tenant_id), str(broker_id), provider_id


def _entry_signal(conn, release_id: str):
    now = datetime.now(timezone.utc)
    signal_id = f"signal-{uuid4()}"
    record_strategy_signal(
        conn,
        signal_id=signal_id,
        strategy_release_id=release_id,
        action="ENTER_LONG",
        symbol="BTC/USD",
        reference_price="100",
        target_allocation_fraction="0.50",
        stop_price="95",
        stop_limit_price="94",
        take_profit_price="110",
        observed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        metadata={"source": "test"},
    )
    return signal_id, now


def _exit_signal(conn, release_id: str):
    now = datetime.now(timezone.utc)
    signal_id = f"signal-{uuid4()}"
    record_strategy_signal(
        conn,
        signal_id=signal_id,
        strategy_release_id=release_id,
        action="EXIT_LONG",
        symbol="BTC/USD",
        reference_price="101",
        observed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        metadata={"source": "test-exit"},
    )
    return signal_id, now


def test_one_signal_fans_out_to_two_isolated_tenants(conn):
    release_id = _stable_release(conn)
    tenant_a, broker_a, provider_a = _tenant(
        conn, release_id, name="tenant-a", cap="100"
    )
    tenant_b, broker_b, provider_b = _tenant(
        conn, release_id, name="tenant-b", cap="40"
    )
    clients = {
        provider_a: FakeTenantBroker(provider_a, equity="200", crypto_capacity="200"),
        provider_b: FakeTenantBroker(provider_b, equity="200", crypto_capacity="200"),
    }
    signal_id, now = _entry_signal(conn, release_id)

    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda account: clients[account.provider_account_id],
        runtime_id="tenant-paper-test",
        source_commit="test-sha",
        deployment_id="test-deployment",
    )
    result = asyncio.run(executor.execute_signal(signal_id, now=now))

    assert result["state"] == "COMPLETE"
    assert result["candidate_accounts"] == 2
    assert {row["tenant_id"] for row in result["results"]} == {tenant_a, tenant_b}
    assert all(row["action"] == "submitted" for row in result["results"])
    assert all(row["protection"]["state"] == "PROTECTED" for row in result["results"])

    buys_a = [row for row in clients[provider_a].submissions if row[0] == "BUY"]
    buys_b = [row for row in clients[provider_b].submissions if row[0] == "BUY"]
    assert len(buys_a) == 1
    assert len(buys_b) == 1
    assert buys_a[0][2] != buys_b[0][2]

    with conn.cursor() as cur:
        cur.execute(
            """
            select tenant_id,broker_account_id,intent_kind,requested_notional,
                   client_order_id,status
            from rhen.account_order_intents
            where signal_id in (%s,%s)
            order by tenant_id,intent_kind
            """,
            (signal_id, signal_id + ":hardstop"),
        )
        rows = cur.fetchall()
    assert len(rows) == 4
    assert {str(row[0]) for row in rows} == {tenant_a, tenant_b}
    for row in rows:
        if str(row[0]) == tenant_a:
            assert str(row[1]) == broker_a
        if str(row[0]) == tenant_b:
            assert str(row[1]) == broker_b

    entries = [row for row in rows if row[2] == "ENTRY"]
    notional_by_tenant = {str(row[0]): Decimal(str(row[3])) for row in entries}
    assert notional_by_tenant[tenant_a] == Decimal("50.0000000000")
    assert notional_by_tenant[tenant_b] == Decimal("20.0000000000")


def test_replaying_signal_reconciles_without_duplicate_orders(conn):
    release_id = _stable_release(conn)
    tenant_id, _broker_id, provider_id = _tenant(
        conn, release_id, name="replay", cap="100"
    )
    client = FakeTenantBroker(provider_id, equity="100", crypto_capacity="100")
    signal_id, now = _entry_signal(conn, release_id)
    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda _account: client,
        runtime_id="tenant-paper-replay",
        source_commit="test-sha",
    )

    first = asyncio.run(executor.execute_signal(signal_id, now=now))
    first_submissions = list(client.submissions)
    second = asyncio.run(
        executor.execute_signal(signal_id, now=now + timedelta(seconds=1))
    )

    assert first["results"][0]["action"] == "submitted"
    assert second["results"][0]["action"] == "reconciled"
    assert second["results"][0]["replayed"] is True
    assert client.submissions == first_submissions
    assert second["results"][0]["tenant_id"] == tenant_id


def test_ambiguous_accepted_order_is_reconciled_not_resubmitted(conn):
    release_id = _stable_release(conn)
    _tenant_id, _broker_id, provider_id = _tenant(
        conn, release_id, name="ambiguous-accepted", cap="100"
    )
    client = FakeTenantBroker(
        provider_id,
        ambiguous_after_accept=True,
    )
    signal_id, now = _entry_signal(conn, release_id)
    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda _account: client,
        runtime_id="tenant-paper-ambiguous-accepted",
        source_commit="test-sha",
    )

    result = asyncio.run(executor.execute_signal(signal_id, now=now))
    row = result["results"][0]
    assert row["action"] == "submitted"
    assert row["submission_state"] == "reconciled_after_error"
    assert len([item for item in client.submissions if item[0] == "BUY"]) == 1


def test_unresolved_ambiguous_submission_never_blind_retries(conn):
    release_id = _stable_release(conn)
    _tenant_id, _broker_id, provider_id = _tenant(
        conn, release_id, name="ambiguous-missing", cap="100"
    )
    client = FakeTenantBroker(
        provider_id,
        ambiguous_without_accept=True,
    )
    signal_id, now = _entry_signal(conn, release_id)
    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda _account: client,
        runtime_id="tenant-paper-ambiguous-missing",
        source_commit="test-sha",
    )

    first = asyncio.run(executor.execute_signal(signal_id, now=now))
    second = asyncio.run(
        executor.execute_signal(signal_id, now=now + timedelta(seconds=1))
    )

    assert first["results"][0]["reason"] == "ambiguous_submission_unresolved"
    assert second["results"][0]["reason"] == "ambiguous_submission_unresolved"
    assert len([item for item in client.submissions if item[0] == "BUY"]) == 1


def test_exit_closes_only_verified_rhen_owned_quantity(conn):
    release_id = _stable_release(conn)
    _tenant_id, _broker_id, provider_id = _tenant(
        conn, release_id, name="owned-exit", cap="100"
    )
    client = FakeTenantBroker(provider_id, equity="100", crypto_capacity="100")
    entry_id, entry_now = _entry_signal(conn, release_id)
    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda _account: client,
        runtime_id="tenant-paper-owned-exit",
        source_commit="test-sha",
    )
    entry = asyncio.run(executor.execute_signal(entry_id, now=entry_now))
    assert entry["results"][0]["action"] == "submitted"

    # Simulate a customer manually adding 0.20 BTC after RHEN's 0.50 BTC fill.
    position = client.positions["BTC/USD"]
    position["qty"] = "0.70"
    position["market_value"] = "70"

    exit_id, exit_now = _exit_signal(conn, release_id)
    exit_result = asyncio.run(executor.execute_signal(exit_id, now=exit_now))
    row = exit_result["results"][0]

    assert row["action"] == "submitted"
    sells = [item for item in client.submissions if item[0] == "SELL"]
    assert len(sells) == 1
    remaining = Decimal(client.positions["BTC/USD"]["qty"])
    assert remaining == Decimal("0.20")


def test_one_tenant_risk_rejection_does_not_block_other_tenant(conn):
    release_id = _stable_release(conn)
    tenant_a, _broker_a, provider_a = _tenant(
        conn, release_id, name="risk-a", cap="100"
    )
    tenant_b, _broker_b, provider_b = _tenant(
        conn, release_id, name="risk-b", cap="100"
    )
    clients = {
        provider_a: FakeTenantBroker(provider_a, crypto_capacity="0"),
        provider_b: FakeTenantBroker(provider_b, crypto_capacity="100"),
    }
    signal_id, now = _entry_signal(conn, release_id)
    executor = TenantPaperExecutor(
        conn,
        client_factory=lambda account: clients[account.provider_account_id],
        runtime_id="tenant-paper-risk-isolation",
        source_commit="test-sha",
    )

    result = asyncio.run(executor.execute_signal(signal_id, now=now))
    by_tenant = {row["tenant_id"]: row for row in result["results"]}

    assert by_tenant[tenant_a]["action"] == "rejected"
    assert "crypto_capacity_unavailable" in by_tenant[tenant_a]["reasons"]
    assert by_tenant[tenant_b]["action"] == "submitted"
    assert not [row for row in clients[provider_a].submissions if row[0] == "BUY"]
    assert len([row for row in clients[provider_b].submissions if row[0] == "BUY"]) == 1
