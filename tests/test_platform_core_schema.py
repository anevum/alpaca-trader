from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "0024_command_platform_core_v1.sql"
SQL = MIGRATION.read_text()
LOWER = SQL.lower()


def test_platform_core_schema_is_additive_and_tenant_scoped():
    required = (
        "create table if not exists anevum.tenants",
        "create table if not exists anevum.tenant_memberships",
        "create table if not exists anevum.entitlements",
        "create table if not exists anevum.broker_accounts",
        "create table if not exists anevum.broker_authorizations",
        "create table if not exists anevum.capital_allocations",
        "create table if not exists anevum.risk_profiles",
        "create table if not exists anevum.strategy_releases",
        "create table if not exists anevum.tenant_strategy_assignments",
        "create table if not exists rhen.account_order_intents",
        "create table if not exists anevum.protected_audit_events",
    )
    for statement in required:
        assert statement in LOWER

    assert "tenant_id uuid not null references anevum.tenants(tenant_id)" in LOWER


def test_broker_authorization_stores_only_secret_reference_metadata():
    assert "secret_reference text not null" in LOWER
    assert "alpaca_api_secret" not in LOWER
    assert "api_secret text" not in LOWER
    assert "access_token text" not in LOWER
    assert "refresh_token text" not in LOWER


def test_platform_core_does_not_enable_live_execution_or_withdrawals():
    forbidden = (
        "live_trading=true",
        "execution_enabled=true",
        "bot_armed=true",
        "create transfer",
        "create withdrawal",
        "submit withdrawal",
    )
    for statement in forbidden:
        assert statement not in LOWER


def test_strategy_release_requires_human_approval_before_executable_states():
    assert "lifecycle_state not in ('approved','canary','stable')" in LOWER
    assert "approved_by is not null and approved_at is not null" in LOWER


def test_order_intent_identity_is_unique_per_tenant_account_signal():
    assert "rhen_account_order_intents_identity_uidx" in LOWER
    assert "tenant_id," in LOWER
    assert "broker_account_id," in LOWER
    assert "strategy_release_id," in LOWER
    assert "signal_id," in LOWER


def test_protected_audit_log_is_append_only():
    assert "anevum.reject_protected_audit_mutation" in LOWER
    assert "before update or delete on anevum.protected_audit_events" in LOWER
    assert "protected audit events are append-only" in LOWER
