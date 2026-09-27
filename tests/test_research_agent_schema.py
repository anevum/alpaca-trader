from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "20260927040654_rhen_research_agent_v1_foundation.sql"
SQL = MIGRATION.read_text()
INDEX_FIX = (
    ROOT / "database" / "20260927042124_index_rhen_research_lease_takeover.sql"
).read_text()


def test_schema_is_additive_and_preserves_legacy_nullability():
    assert "create table private.trading_research_agent_runs" in SQL
    assert "create table private.trading_research_leases" in SQL
    assert "alter column source_weekly_report_id drop not null" in SQL
    assert "add column workflow_state text" in SQL
    assert "workflow_state is null or workflow_state in" in SQL
    assert "update private.trading_research_questions" not in SQL.lower()
    assert "update private.trading_experiments" not in SQL.lower()
    assert "delete from private.trading_research_questions" not in SQL.lower()


def test_new_constraints_are_backward_compatible_and_bounded():
    assert "category is null or category in" in SQL
    assert "priority_score is null or priority_score between 0 and 100" in SQL
    assert "source_weekly_report_id is null or source_agent_run_id is null" in SQL
    assert "status in ('RUNNING','COMPLETED','NOOP','BLOCKED','FAILED')" in SQL


def test_private_security_and_atomic_lease_primitives_are_present():
    assert "security invoker" in SQL.lower()
    assert "security definer" not in SQL.lower()
    assert "on conflict (lease_key) do update" in SQL.lower()
    assert "where lease.expires_at <= v_now" in SQL.lower()
    assert "revoke execute on function" in SQL.lower()
    assert "from public, anon, authenticated" in SQL.lower()


def test_optional_takeover_foreign_key_has_a_covering_index():
    assert "trading_research_leases(takeover_from_run_id)" in INDEX_FIX
