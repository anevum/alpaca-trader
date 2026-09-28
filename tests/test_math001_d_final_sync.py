from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "database" / "20260928131224_finalize_math001_d.sql"
).read_text(encoding="utf-8")


def test_math001_d_final_sync_versions_foundation_and_results():
    assert "'MATH-001:foundation:v4'" in SQL
    assert "'MATH-001:D:v1'" in SQL
    assert "'MATH-001:D-BENCHMARK:v1'" in SQL
    assert "where artifact_key = 'MATH-001:foundation:v3'" in SQL
    assert "supersedes_artifact_id" in SQL


def test_math001_d_final_sync_preserves_conditional_null_boundary():
    assert "'iid_required', false" in SQL
    assert "'conditional_null_required', true" in SQL
    assert "'raw_unbounded_b1_input_allowed', false" in SQL
    assert "'final_dependence_policy_frozen', false" in SQL


def test_math001_d_final_sync_keeps_zero_authority():
    assert "'production_authority', false" in SQL
    assert "'protected_stage_authority', false" in SQL
    assert "'theory_can_change_live_trading', false" in SQL
    assert "'theory_can_open_protected_research_stages', false" in SQL


def test_math001_d_benchmark_preserves_negative_assumption_violation_result():
    assert "'ar1_candidate_e_crossing_rate', 0.2707" in SQL
    assert "'ar1_familywise_e_crossing_rate', 0.902" in SQL
    assert "'overlap_ma1_candidate_e_crossing_rate', 0.0829" in SQL
    assert "'common_factor_mean_abs_cross_candidate_correlation', 0.941573327047043" in SQL
