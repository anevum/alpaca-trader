from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT
    / "database"
    / "20260928023123_sync_math001_a1_theory_artifacts.sql"
).read_text(encoding="utf-8")


def test_math001_a1_theory_sync_is_versioned_and_non_authoritative():
    assert "'MATH-001:foundation:v2'" in SQL
    assert "'MATH-001:A1:v1'" in SQL
    assert "'math001-search-ledger-v1'" in SQL
    assert "'production_authority', false" in SQL
    assert "'protected_stage_authority', false" in SQL
    assert "'final_multiplicity_policy_frozen', false" in SQL


def test_math001_a1_theory_sync_preserves_history():
    assert "set status = 'ARCHIVED'" in SQL
    assert "where artifact_key = 'MATH-001:foundation:v1'" in SQL
    assert "supersedes_artifact_id" in SQL
    assert "on conflict (artifact_key) do nothing" in SQL.lower()
