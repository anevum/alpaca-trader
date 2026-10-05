"""Real PostgreSQL tests for Command Paper Beta v1."""
import base64
import os
from uuid import uuid4

import psycopg
import pytest

from foundation.command_platform import (
    DatabaseEnvelopeSecretResolver,
    PAPER_BETA_KEY_VERSION,
    _encrypt_token,
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
