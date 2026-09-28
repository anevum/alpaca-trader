from pathlib import Path

from app.research_agent.theory import (
    FORBIDDEN_AUTHORITY,
    TheoryRegistryError,
    load_theory_registry,
    public_theory_projection,
    theory_status,
)


def test_theory_registry_loads_and_has_no_production_authority():
    registry = load_theory_registry()
    authority = registry["program"]["authority"]
    assert all(authority[key] is False for key in FORBIDDEN_AUTHORITY)
    assert registry["problems"][0]["problem_id"] == "ATP-001"


def test_public_projection_preserves_falsifiability_and_nonclaims():
    public = public_theory_projection()
    problem = public["problems"][0]
    assert problem["conjectures"]
    assert all(row["falsification"] for row in problem["conjectures"])
    assert problem["non_claims"]
    assert public["authority"]["theory_can_change_live_trading"] is False


def test_status_is_bounded_summary():
    status = theory_status()
    assert status["active_problem_count"] >= 1
    assert status["open_conjecture_count"] >= 1
    assert status["authority"]["production_promotion"] is False


def test_registry_rejects_authority_escalation(tmp_path: Path):
    source = load_theory_registry()
    clone = __import__("json").loads(__import__("json").dumps(source))
    clone["program"]["authority"]["production_promotion"] = True
    path = tmp_path / "registry.json"
    path.write_text(__import__("json").dumps(clone))
    try:
        load_theory_registry.cache_clear()
        try:
            load_theory_registry(path)
        except TheoryRegistryError:
            pass
        else:
            raise AssertionError("authority escalation should fail closed")
    finally:
        load_theory_registry.cache_clear()


def test_registered_result_is_application_not_original():
    registry = load_theory_registry()
    result = registry["problems"][0]["results"][0]
    assert result["result_id"] == "ATP-001-P1"
    assert result["claim_class"] == "APPLICATION"
    assert result["novelty_state"] == "APPLICATION"


def test_math001_is_registered_as_active_research_without_authority():
    registry = load_theory_registry()
    problem = next(
        row for row in registry["problems"]
        if row["problem_id"] == "MATH-001"
    )
    assert problem["status"] == "ACTIVE"
    assert problem["title"] == "Discovery Reliability"
    assert problem["conjectures"][0]["conjecture_id"] == "MATH-001-C1"
    assert {row["result_id"] for row in problem["results"]} >= {
        "MATH-001-P1",
        "MATH-001-P2",
        "MATH-001-B1",
    }
    assert all(
        row["novelty_state"] != "ORIGINAL_VERIFIED"
        for row in problem["results"]
    )
    assert any(
        "does not authorize DEVELOPMENT" in statement
        for statement in problem["non_claims"]
    )


def test_math001_public_projection_preserves_research_boundary():
    public = public_theory_projection()
    math001 = next(
        row for row in public["problems"]
        if row["problem_id"] == "MATH-001"
    )
    assert math001["visibility"] == "PUBLIC"
    assert math001["results"]
    assert public["authority"]["theory_can_change_live_trading"] is False
    assert public["authority"]["theory_can_open_protected_research_stages"] is False
