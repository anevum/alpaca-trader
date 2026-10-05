from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "db" / "migrations" / "0029_command_tenant_paper_executor.sql"
).read_text().lower()
BROKER = (ROOT / "app" / "platform_core" / "broker.py").read_text().lower()
GATEWAY = (ROOT / "foundation" / "tenant_executor_gateway.py").read_text().lower()


def test_signal_queue_is_immutable_crypto_only_and_release_scoped():
    assert "create table if not exists rhen.strategy_signals" in MIGRATION
    assert "strategy_release_id text not null" in MIGRATION
    assert "strategy_signals_are_immutable" in MIGRATION
    assert "check (position('/' in symbol) > 0)" in MIGRATION
    assert "enter_long" in MIGRATION
    assert "exit_long" in MIGRATION


def test_tenant_executor_runtime_is_paper_only_and_capability_gated():
    assert "create table if not exists anevum.tenant_execution_runtimes" in MIGRATION
    assert "check (environment = 'paper')" in MIGRATION
    assert "tenant_isolation" in GATEWAY
    assert "crypto_spot" in GATEWAY
    assert "paper_only" in GATEWAY


def test_account_order_intents_distinguish_entry_exit_and_protection():
    assert "intent_kind text not null default 'entry'" in MIGRATION
    assert "'entry','exit','protective_stop'" in MIGRATION


def test_execution_client_refuses_live_accounts():
    assert "class tenantalpacapaperexecutionclient" in BROKER
    assert 'if account.environment.upper() != "paper"' in BROKER
    assert "tenant execution client is paper-only" in BROKER


def test_executor_reconciles_ambiguity_and_does_not_blind_retry():
    assert "order_by_client_order_id" in GATEWAY
    assert "ambiguous_submission_unresolved" in GATEWAY
    assert 'current_status in {"ambiguous", "submitting"}' in GATEWAY
    assert "broker_terminal_failure" in GATEWAY


def test_executor_exit_uses_verified_rhen_owned_quantity():
    assert "verified_rhen_owned_qty" in GATEWAY
    assert "no_verified_rhen_owned_quantity" in GATEWAY
    assert "min(owned, broker_qty)" in GATEWAY
