from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "foundation" / "ingest" / "service.py").read_text()
PLATFORM = (ROOT / "foundation" / "command_platform.py").read_text()
MIGRATION = (
    ROOT / "db" / "migrations" / "0027_command_paper_beta_oauth.sql"
).read_text().lower()


def test_customer_platform_routes_exist_without_exposing_iren_mutations():
    for route in (
        "/v1/command/platform/session",
        "/v1/command/platform/overview",
        "/v1/command/platform/allocation",
        "/v1/command/platform/risk",
        "/v1/command/platform/control/pause",
        "/v1/command/platform/control/resume",
        "/v1/command/platform/alpaca/oauth/start",
        "/v1/command/platform/alpaca/oauth/callback",
        "/v1/command/platform/broker/refresh",
    ):
        assert route in SERVICE
    assert "require_command_identity" in SERVICE
    assert "require_command_access" in SERVICE


def test_admin_tenant_provisioning_uses_admin_auth_boundary():
    route = '/v1/command/platform/admin/tenants'
    assert route in SERVICE
    segment = SERVICE[SERVICE.index(route):]
    assert "require_command_access" in segment[:1800]


def test_oauth_flow_is_fixed_to_alpaca_paper_and_registered_redirect():
    assert 'env": "paper"' in PLATFORM
    assert 'PAPER_SCOPE = "trading"' in PLATFORM
    assert "ALPACA_OAUTH_REDIRECT_URI" in PLATFORM
    assert "redirect != configured_redirect" in PLATFORM
    assert "client_secret" in PLATFORM
    assert "authorization_url" in PLATFORM


def test_plaintext_oauth_tokens_have_no_database_column():
    assert "ciphertext bytea not null" in MIGRATION
    assert "nonce bytea not null" in MIGRATION
    assert "access_token text" not in MIGRATION
    assert "refresh_token text" not in MIGRATION
    assert "paper-beta encrypted oauth token envelopes" in MIGRATION


def test_live_customer_authority_is_not_added_to_paper_beta_service():
    assert "live_customer_authority=true" not in PLATFORM.lower()
    assert '"live_customer_trading": false' in PLATFORM.lower()
    assert '"withdrawals": false' in PLATFORM.lower()



def test_operator_allowlist_is_required_for_admin_command():
    assert "command_operator_allowlist_not_configured" in SERVICE
    assert 'allowed_emails = os.environ.get("COMMAND_ACCESS_EMAILS", "").strip()' in SERVICE


def test_broker_crypto_enablement_is_derived_not_assumed():
    assert 'account.get("crypto_status")' in PLATFORM
    assert 'in {"ACTIVE", "APPROVED"}' in PLATFORM
    assert "values(%s,'ALPACA',%s,'PAPER',%s,true,%s,%s)" not in PLATFORM



def test_oauth_callback_reconciliation_reads_envelope_in_same_transaction():
    assert "connection=conn" in PLATFORM
    callback_start = PLATFORM.index("async def complete_paper_oauth")
    refresh_start = PLATFORM.index("async def refresh_paper_broker")
    callback = PLATFORM[callback_start:refresh_start]
    assert "DatabaseEnvelopeSecretResolver(" in callback
    assert "connection=conn" in callback


def test_strategy_assignment_is_operator_only():
    route = '/v1/command/platform/admin/strategy-assignment'
    assert route in SERVICE
    segment = SERVICE[SERVICE.index(route):]
    assert "require_command_access" in segment[:1800]
    assert "assign_paper_strategy_release" in segment[:2200]


def test_customer_projection_routes_use_tenant_identity_boundary():
    for route in (
        '/v1/command/account',
        '/v1/command/overview',
        '/v1/command/trading',
        '/v1/command/money',
        '/v1/command/activity',
    ):
        assert route in SERVICE
        segment = SERVICE[SERVICE.index(route):]
        assert "require_command_identity" in segment[:1800]
    assert "customer_account_projection" in SERVICE
    assert "customer_overview_projection" in SERVICE
    assert "customer_trading_projection" in SERVICE
    assert "customer_money_projection" in SERVICE
    assert "customer_activity_projection" in SERVICE
