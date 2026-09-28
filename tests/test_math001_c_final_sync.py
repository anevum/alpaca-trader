from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "database" / "20260928122355_finalize_math001_c.sql"
).read_text(encoding="utf-8")


def test_math001_c_final_sync_versions_foundation_and_results():
    assert "'MATH-001:foundation:v3'" in SQL
    assert "'MATH-001:C:v1'" in SQL
    assert "'MATH-001:C-BENCHMARK:v1'" in SQL
    assert "where artifact_key = 'MATH-001:foundation:v2'" in SQL
    assert "supersedes_artifact_id" in SQL


def test_math001_c_final_sync_keeps_policy_unfrozen():
    assert "'final_multiplicity_policy_frozen', false" in SQL
    assert "'final_alpha_or_fdr_target_frozen', false" in SQL
    assert "'final_policy_frozen', false" in SQL
    assert "'production_authority', false" in SQL
    assert "'protected_stage_authority', false" in SQL


def test_empty_multiplicity_state_fails_closed_to_false_authority():
    assert "coalesce(bool_or(production_authority), false)" in SQL
    assert "coalesce(bool_or(protected_stage_authority), false)" in SQL
    assert "security_invoker = true" in SQL
