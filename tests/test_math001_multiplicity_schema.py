from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "20260928121527_math001_c_multiplicity.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
GATEWAY = (
    ROOT
    / "foundation"
    / "research_agent_gateway.py"
).read_text(encoding="utf-8")


def test_math001_c_multiplicity_plan_table_is_private_and_append_only():
    lowered = SQL.lower()
    assert "create table private.trading_research_multiplicity_plans" in lowered
    assert "enable row level security" in lowered
    assert "revoke all on private.trading_research_multiplicity_plans" in lowered
    assert "trading_research_multiplicity_plans_append_only" in lowered
    assert "rhen_research_append_only_guard()" in lowered


def test_math001_c_plan_cannot_gain_live_or_stage_authority():
    assert "production_authority boolean not null default false" in SQL
    assert "protected_stage_authority boolean not null default false" in SQL
    assert "check (production_authority = false)" in SQL
    assert "check (protected_stage_authority = false)" in SQL
    assert "'production_authority', false" in SQL
    assert "'protected_stage_authority', false" in SQL


def test_math001_c_plan_is_bound_to_proposal_identity():
    assert "proposal_hash text not null" in SQL
    assert "unique (proposal_id, proposal_revision)" in SQL
    assert "multiplicity plan is not bound to the search ledger proposal" in SQL
    assert "multiplicity plan identity collision" in SQL


def test_math001_c_uses_security_invoker_only():
    lowered = SQL.lower()
    assert "security invoker" in lowered
    assert "security definer" not in lowered


def test_gateway_persists_and_exposes_multiplicity_plans():
    assert "record_search_ledger" in GATEWAY
    assert "multiplicity_plan" in GATEWAY
    assert "recent_multiplicity_plans" in GATEWAY
    assert "production_authority" in GATEWAY
    assert "protected_stage_authority" in GATEWAY
