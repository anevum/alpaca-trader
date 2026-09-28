from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "20260928021901_math001_a1_search_ledger.sql"
SQL = MIGRATION.read_text(encoding="utf-8")


def test_math001_a1_creates_private_append_only_ledger():
    assert "create table private.trading_research_hypothesis_families" in SQL
    assert "create table private.trading_research_hypotheses" in SQL
    assert "create table private.trading_research_search_events" in SQL
    assert "create function private.rhen_research_append_only_guard()" in SQL
    assert "before update or delete on private.trading_research_hypothesis_families" in SQL
    assert "before update or delete on private.trading_research_hypotheses" in SQL
    assert "before update or delete on private.trading_research_search_events" in SQL


def test_math001_a1_stays_private_and_security_invoker_only():
    lowered = SQL.lower()
    assert "enable row level security" in lowered
    assert "revoke all on private.trading_research_hypothesis_families from anon, authenticated" in lowered
    assert "revoke all on private.trading_research_hypotheses from anon, authenticated" in lowered
    assert "revoke all on private.trading_research_search_events from anon, authenticated" in lowered
    assert "security invoker" in lowered
    assert "security definer" not in lowered


def test_math001_a1_backfills_existing_search_exposure_without_rewriting_history():
    assert "edge-corpus-v1" in SQL
    assert "controlled continuation" not in SQL  # derived from canonical result rows
    assert "edge-discovery-v2-residual-downshock-rebound-v2.1" in SQL
    assert "'REJECTED'" in SQL
    assert "'ARCHIVED'" in SQL
    assert "corpus_quality_failure_not_performance_rejection" in SQL
    assert "on conflict do nothing" in SQL.lower()


def test_math001_a1_exposes_bounded_search_accounting_views():
    assert "create view private.rhen_research_search_exposure_v1" in SQL
    assert "create view private.rhen_research_family_search_exposure_v1" in SQL
    assert "prior_hypotheses_examined" in SQL
    assert "data_contaminating" in SQL
    assert "global_filtration_id" in SQL


def test_math001_a1_recorder_has_no_stage_or_production_authority():
    recorder = SQL.split(
        "create function private.rhen_research_record_search_ledger", 1
    )[1]
    assert "production_authority', false" in recorder
    assert "protected_stage_authority', false" in recorder
    assert "trading_strategy_versions" not in recorder
    assert "trading_orders" not in recorder
    assert "trading_positions" not in recorder
