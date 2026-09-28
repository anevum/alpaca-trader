from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "20260928125644_math001_d_dependence.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
GATEWAY = (
    ROOT
    / "supabase"
    / "functions"
    / "research-agent-gateway"
    / "index.ts"
).read_text(encoding="utf-8")


def test_math001_d_plan_table_is_private_and_append_only():
    lowered = SQL.lower()
    assert "create table private.trading_research_dependence_plans" in lowered
    assert "enable row level security" in lowered
    assert "revoke all on private.trading_research_dependence_plans" in lowered
    assert "trading_research_dependence_plans_append_only" in lowered
    assert "rhen_research_append_only_guard()" in lowered


def test_math001_d_cannot_add_iid_or_live_authority():
    assert "check (iid_required = false)" in SQL
    assert "check (production_authority = false)" in SQL
    assert "check (protected_stage_authority = false)" in SQL
    assert "'production_authority', false" in SQL
    assert "'protected_stage_authority', false" in SQL


def test_math001_d_plan_is_bound_to_proposal_identity():
    assert "proposal_hash text not null" in SQL
    assert "unique (proposal_id, proposal_revision)" in SQL
    assert "dependence plan is not bound to the search ledger proposal" in SQL
    assert "dependence plan identity collision" in SQL


def test_math001_d_state_fails_closed_on_empty_authority():
    assert "coalesce(bool_or(production_authority), false)" in SQL
    assert "coalesce(bool_or(protected_stage_authority), false)" in SQL
    assert "security_invoker = true" in SQL


def test_gateway_persists_and_exposes_dependence_plans():
    assert "rhen_research_record_dependence_plan" in GATEWAY
    assert "rhen_research_dependence_state_v1" in GATEWAY
    assert "recent_dependence_plans" in GATEWAY
    assert "dependence_plan_write_failed" in GATEWAY
