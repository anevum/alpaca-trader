"""Real PostgreSQL tests for Command Paper Beta v1."""
import base64
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from foundation.command_platform import (
    DatabaseEnvelopeSecretResolver,
    PAPER_BETA_KEY_VERSION,
    _encrypt_token,
    _ensure_default_paper_strategy_assignment,
    assign_paper_strategy_release,
    customer_overview,
    provision_paper_beta_tenant,
    resolve_command_session,
    start_paper_oauth,
    update_allocation,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="isolated PostgreSQL required",
)


class RollbackTest(Exception):
    pass


@pytest.fixture(name="conn")
def isolated_conn(monkeypatch):
    monkeypatch.setenv("COMMAND_ACCESS_EMAILS", "owner@anevum.test")
    monkeypatch.setenv("ALPACA_OAUTH_CLIENT_ID", "paper-beta-client")
    monkeypatch.setenv(
        "ALPACA_OAUTH_REDIRECT_URI",
        "https://anevum.com/api/command/platform/alpaca/callback",
    )
    monkeypatch.setenv(
        "COMMAND_PAPER_ENVELOPE_KEY_B64",
        base64.b64encode(b"k" * 32).decode(),
    )
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as db:
        try:
            with db.transaction():
                yield db
                raise RollbackTest()
        except RollbackTest:
            pass


def test_invited_customer_resolves_to_customer_surface(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="customer@example.test",
        display_name="Customer Beta",
        created_by="owner@anevum.test",
    )
    session = resolve_command_session(conn, email="customer@example.test")

    assert session["command_admin"] is False
    assert session["surface"] == "customer"
    assert session["active_tenant_id"] == created["tenant_id"]
    assert session["tenants"][0]["role"] == "OWNER"


def test_owner_admin_remains_operator_even_without_tenant(conn):
    session = resolve_command_session(conn, email="owner@anevum.test")
    assert session["command_admin"] is True
    assert session["surface"] == "operator"
    assert session["tenants"] == []


def test_customer_cannot_mutate_a_different_tenant(conn):
    first = provision_paper_beta_tenant(
        conn,
        customer_email="one@example.test",
        display_name="One",
        created_by="owner@anevum.test",
    )
    second = provision_paper_beta_tenant(
        conn,
        customer_email="two@example.test",
        display_name="Two",
        created_by="owner@anevum.test",
    )
    with pytest.raises(PermissionError, match="membership"):
        update_allocation(
            conn,
            email="one@example.test",
            tenant_id=second["tenant_id"],
            allocation_fraction=0.5,
            absolute_cap=100,
        )
    assert first["tenant_id"] != second["tenant_id"]


def test_oauth_start_is_paper_only_and_contains_no_client_secret(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="oauth@example.test",
        display_name="OAuth Beta",
        created_by="owner@anevum.test",
    )
    result = start_paper_oauth(
        conn,
        email="oauth@example.test",
        tenant_id=created["tenant_id"],
        redirect_uri="https://anevum.com/api/command/platform/alpaca/callback",
    )
    assert "env=paper" in result["authorization_url"]
    assert "scope=trading" in result["authorization_url"]
    assert "client_id=paper-beta-client" in result["authorization_url"]
    assert "client_secret" not in result["authorization_url"]


def test_oauth_start_rejects_unregistered_redirect(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="redirect@example.test",
        display_name="Redirect Beta",
        created_by="owner@anevum.test",
    )
    with pytest.raises(ValueError, match="redirect_uri"):
        start_paper_oauth(
            conn,
            email="redirect@example.test",
            tenant_id=created["tenant_id"],
            redirect_uri="https://evil.example/callback",
        )


def test_database_envelope_round_trip_never_stores_plaintext(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="secret@example.test",
        display_name="Secret Beta",
        created_by="owner@anevum.test",
    )
    token = "paper-token-that-must-not-appear-in-db"
    nonce, ciphertext = _encrypt_token(tenant_id=created["tenant_id"], token=token)
    secret_reference = f"db-envelope://{uuid4()}"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.secret_envelopes(
                secret_reference,tenant_id,purpose,key_version,nonce,ciphertext
            )
            values(%s,%s,'ALPACA_PAPER_OAUTH',%s,%s,%s)
            """,
            (
                secret_reference,
                created["tenant_id"],
                PAPER_BETA_KEY_VERSION,
                nonce,
                ciphertext,
            ),
        )
        cur.execute(
            """
            select encode(ciphertext,'escape')
            from anevum.secret_envelopes
            where secret_reference=%s
            """,
            (secret_reference,),
        )
        stored = cur.fetchone()[0]

    assert token not in stored
    resolver = DatabaseEnvelopeSecretResolver(
        os.environ["TEST_DATABASE_URL"],
        created["tenant_id"],
        connection=conn,
    )
    assert resolver.resolve(secret_reference) == token



def test_existing_tenant_key_cannot_be_claimed_by_another_customer(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="original@example.test",
        display_name="Original Beta",
        tenant_key="shared-beta-key",
        created_by="owner@anevum.test",
    )
    with pytest.raises(ValueError, match="tenant_key_already_exists"):
        provision_paper_beta_tenant(
            conn,
            customer_email="intruder@example.test",
            display_name="Intruder Beta",
            tenant_key="shared-beta-key",
            created_by="owner@anevum.test",
        )
    assert created["tenant_key"] == "shared-beta-key"


def test_inactive_tenant_remains_readable_but_cannot_start_oauth(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="inactive@example.test",
        display_name="Inactive Beta",
        created_by="owner@anevum.test",
    )
    with conn.cursor() as cur:
        cur.execute(
            "update anevum.tenants set status='RESTRICTED' where tenant_id=%s",
            (created["tenant_id"],),
        )

    session = resolve_command_session(conn, email="inactive@example.test")
    assert session["tenants"][0]["tenant_status"] == "RESTRICTED"

    with pytest.raises(PermissionError, match="not_active"):
        start_paper_oauth(
            conn,
            email="inactive@example.test",
            tenant_id=created["tenant_id"],
            redirect_uri="https://anevum.com/api/command/platform/alpaca/callback",
        )



def test_default_paper_release_assignment_requires_no_database_hand_edit(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="release@example.test",
        display_name="Release Beta",
        created_by="owner@anevum.test",
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_accounts(
                tenant_id,provider,provider_account_id,environment,
                account_status,crypto_enabled,trading_blocked,withdrawals_blocked
            )
            values(%s,'ALPACA',%s,'PAPER','ACTIVE',true,false,false)
            returning broker_account_id
            """,
            (created["tenant_id"], f"paper-{uuid4()}"),
        )
        broker_id = str(cur.fetchone()[0])
        release_id = f"paper-release-{uuid4()}"
        cur.execute(
            """
            insert into anevum.strategy_releases(
                strategy_release_id,strategy_key,semantic_version,channel,
                lifecycle_state,source_commit,strategy_hash,configuration_hash,
                risk_policy_version,evidence
            )
            values(%s,'RHEN-BTC','0.0.1-paper','INTERNAL','PAPER_PASSED',
                   'test-commit','strategy-hash','config-hash','risk-v1','{}'::jsonb)
            """,
            (release_id,),
        )

    assigned = _ensure_default_paper_strategy_assignment(
        conn,
        tenant_id=created["tenant_id"],
        broker_account_id=broker_id,
    )

    assert assigned is not None
    assert assigned["strategy_release_id"] == release_id
    assert assigned["lifecycle_state"] == "PAPER_PASSED"


def test_customer_overview_derives_funding_and_lifecycle_from_alpaca_snapshot(conn):
    created = provision_paper_beta_tenant(
        conn,
        customer_email="funding@example.test",
        display_name="Funding Beta",
        created_by="owner@anevum.test",
    )
    provider_account_id = f"paper-{uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_accounts(
                tenant_id,provider,provider_account_id,environment,
                account_status,crypto_enabled,trading_blocked,withdrawals_blocked
            )
            values(%s,'ALPACA',%s,'PAPER','ACTIVE',true,false,false)
            returning broker_account_id
            """,
            (created["tenant_id"], provider_account_id),
        )
        broker_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into anevum.broker_authorizations(
                broker_account_id,authorization_kind,secret_reference,
                scopes,status,issued_at,last_validated_at
            )
            values(%s,'OAUTH',%s,'["trading"]'::jsonb,'ACTIVE',now(),now())
            """,
            (broker_id, f"db-envelope://{uuid4()}"),
        )
        cur.execute(
            """
            insert into anevum.broker_reconciliations(
                tenant_id,broker_account_id,status,environment,provider,
                provider_account_id_expected,provider_account_id_observed,
                observed_at,account_snapshot,positions_snapshot,
                open_orders_snapshot,recent_orders_snapshot,snapshot_hash
            )
            values(%s,%s,'SUCCESS','PAPER','ALPACA',%s,%s,now(),
                   %s,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,%s)
            """,
            (
                created["tenant_id"],
                broker_id,
                provider_account_id,
                provider_account_id,
                Jsonb({
                    "equity": "100000",
                    "cash": "100000",
                    "buying_power": "200000",
                }),
                f"snapshot-{uuid4()}",
            ),
        )

    overview = customer_overview(
        conn,
        email="funding@example.test",
        tenant_id=created["tenant_id"],
    )

    assert overview["schema_version"] == "command_customer.v2"
    assert overview["funding"]["source"] == "ALPACA"
    assert overview["funding"]["funded"] is True
    assert overview["funding"]["equity"] == "100000"
    assert overview["lifecycle"]["state"] == "TRADING_CONFIGURATION_REQUIRED"
    funding_step = next(
        step for step in overview["onboarding"]["steps"]
        if step["key"] == "funding"
    )
    assert funding_step["complete"] is True



def test_ambiguous_default_release_requires_explicit_operator_assignment(conn, monkeypatch):
    monkeypatch.delenv("COMMAND_PAPER_DEFAULT_STRATEGY_RELEASE_ID", raising=False)
    created = provision_paper_beta_tenant(
        conn,
        customer_email="ambiguous@example.test",
        display_name="Ambiguous Beta",
        created_by="owner@anevum.test",
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into anevum.broker_accounts(
                tenant_id,provider,provider_account_id,environment,
                account_status,crypto_enabled,trading_blocked,withdrawals_blocked
            )
            values(%s,'ALPACA',%s,'PAPER','ACTIVE',true,false,false)
            returning broker_account_id
            """,
            (created["tenant_id"], f"paper-{uuid4()}"),
        )
        broker_id = str(cur.fetchone()[0])
        release_ids = [f"paper-release-{uuid4()}", f"paper-release-{uuid4()}"]
        for index, release_id in enumerate(release_ids):
            cur.execute(
                """
                insert into anevum.strategy_releases(
                    strategy_release_id,strategy_key,semantic_version,channel,
                    lifecycle_state,source_commit,strategy_hash,configuration_hash,
                    risk_policy_version,evidence
                )
                values(%s,%s,%s,'INTERNAL','PAPER_PASSED',
                       %s,%s,%s,'risk-v1','{}'::jsonb)
                """,
                (
                    release_id,
                    f"RHEN-BTC-{index}",
                    f"0.0.{index + 1}-paper",
                    f"test-commit-{index}",
                    f"strategy-hash-{index}",
                    f"config-hash-{index}",
                ),
            )

    automatic = _ensure_default_paper_strategy_assignment(
        conn,
        tenant_id=created["tenant_id"],
        broker_account_id=broker_id,
    )
    assert automatic is None

    explicit = assign_paper_strategy_release(
        conn,
        operator_email="owner@anevum.test",
        tenant_id=created["tenant_id"],
        strategy_release_id=release_ids[1],
    )
    assert explicit["strategy_release_id"] == release_ids[1]
    assert explicit["live_customer_authority"] is False

    with conn.cursor() as cur:
        cur.execute(
            """
            select actor_type,action,object_id
            from anevum.protected_audit_events
            where tenant_id=%s
              and action='PAPER_STRATEGY_ASSIGNED'
            order by occurred_at desc
            limit 1
            """,
            (created["tenant_id"],),
        )
        actor_type, action, object_id = cur.fetchone()
    assert actor_type == "operator"
    assert action == "PAPER_STRATEGY_ASSIGNED"
    assert object_id == release_ids[1]
