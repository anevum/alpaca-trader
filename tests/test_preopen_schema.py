from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "20260927044500_rhen_preopen_state_v1.sql"


def test_preopen_schema_is_private_and_shadow_only():
    sql = MIGRATION.read_text().lower()
    assert "create table private.trading_preopen_snapshots" in sql
    assert "create table private.trading_preopen_outcomes" in sql
    assert "create table private.trading_preopen_model_artifacts" in sql
    assert "shadow_only = true" in sql
    assert "enable row level security" in sql
    assert "revoke all" in sql


def test_preopen_schema_does_not_modify_live_strategy_or_execution_tables():
    sql = MIGRATION.read_text().lower()
    forbidden = (
        "alter table private.trading_strategy_versions",
        "alter table private.trading_order_intents",
        "alter table private.trading_orders",
        "alter table private.trading_positions",
    )
    for statement in forbidden:
        assert statement not in sql
