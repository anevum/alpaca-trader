from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "db" / "migrations" / "0024_command_platform_core_v1.sql").read_text().lower()
RECONCILIATION = (
    ROOT / "db" / "migrations" / "0025_command_broker_reconciliation.sql"
).read_text().lower()
CONTROLS = (
    ROOT / "db" / "migrations" / "0026_command_tenant_trading_controls.sql"
).read_text().lower()


def test_only_one_active_broker_authorization_is_allowed():
    assert "anevum_broker_authorizations_active_uidx" in CORE
    assert "where status = 'active'" in CORE


def test_reconciliation_is_tenant_scoped_and_read_only_evidence():
    assert "create table if not exists anevum.broker_reconciliations" in RECONCILIATION
    assert (
        "foreign key (tenant_id, broker_account_id)"
        in RECONCILIATION
    )
    assert "references anevum.broker_accounts(tenant_id, broker_account_id)" in RECONCILIATION
    assert "read-only alpaca reconciliation evidence" in RECONCILIATION
    for forbidden in (
        "insert into rhen.orders",
        "post /v2/orders",
        "create transfer",
        "withdrawal",
    ):
        assert forbidden not in RECONCILIATION


def test_customer_bot_control_defaults_off_and_cannot_grant_live_authority():
    assert "create table if not exists anevum.tenant_trading_controls" in CONTROLS
    assert "bot_enabled boolean not null default false" in CONTROLS
    assert "customer_consent_version text" in CONTROLS
    assert "customer_consented_at timestamptz" in CONTROLS
    assert "never grants protected live-customer authority" in CONTROLS
    assert "live_customer_authority" not in CONTROLS
