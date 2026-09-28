from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

REGISTRY_SCHEMA = "anevum-theory-registry-v1"
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "research" / "theory_registry.json"
FORBIDDEN_AUTHORITY = (
    "live_strategy_mutation",
    "live_risk_or_sizing_mutation",
    "broker_calls",
    "stage_opening",
    "methodology_freeze",
    "quarantine_access",
    "production_promotion",
)


class TheoryRegistryError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def registry_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TheoryRegistryError(f"{field} is required")
    return text


def _validate_problem(problem: Mapping[str, Any]) -> None:
    problem_id = _require_text(problem.get("problem_id"), "problem_id")
    _require_text(problem.get("title"), f"{problem_id}.title")
    _require_text(problem.get("question"), f"{problem_id}.question")
    if problem.get("status") not in {"ACTIVE", "PAUSED", "SOLVED", "REJECTED", "ARCHIVED"}:
        raise TheoryRegistryError(f"{problem_id}.status is invalid")
    if problem.get("visibility") not in {"PUBLIC", "PRIVATE"}:
        raise TheoryRegistryError(f"{problem_id}.visibility is invalid")
    formalization = problem.get("formalization")
    if not isinstance(formalization, Mapping):
        raise TheoryRegistryError(f"{problem_id}.formalization must be an object")
    for key in ("objective_latex", "variation_latex", "dynamic_regret_latex", "target_bound"):
        _require_text(formalization.get(key), f"{problem_id}.formalization.{key}")
    conjectures = problem.get("conjectures")
    if not isinstance(conjectures, list):
        raise TheoryRegistryError(f"{problem_id}.conjectures must be an array")
    seen: set[str] = set()
    for conjecture in conjectures:
        if not isinstance(conjecture, Mapping):
            raise TheoryRegistryError(f"{problem_id}.conjecture must be an object")
        conjecture_id = _require_text(conjecture.get("conjecture_id"), "conjecture_id")
        if conjecture_id in seen:
            raise TheoryRegistryError(f"duplicate conjecture_id: {conjecture_id}")
        seen.add(conjecture_id)
        if conjecture.get("novelty_state") not in {
            "UNASSESSED", "KNOWN", "APPLICATION", "POTENTIALLY_NOVEL",
            "ORIGINAL_VERIFIED", "REJECTED"
        }:
            raise TheoryRegistryError(f"{conjecture_id}.novelty_state is invalid")
        _require_text(conjecture.get("statement"), f"{conjecture_id}.statement")
        _require_text(conjecture.get("falsification"), f"{conjecture_id}.falsification")

    results = problem.get("results") or []
    if not isinstance(results, list):
        raise TheoryRegistryError(f"{problem_id}.results must be an array")
    result_ids: set[str] = set()
    for result in results:
        if not isinstance(result, Mapping):
            raise TheoryRegistryError(f"{problem_id}.result must be an object")
        result_id = _require_text(result.get("result_id"), "result_id")
        if result_id in result_ids:
            raise TheoryRegistryError(f"duplicate result_id: {result_id}")
        result_ids.add(result_id)
        if result.get("claim_class") not in {
            "KNOWN", "APPLICATION", "POTENTIALLY_NOVEL", "ORIGINAL_VERIFIED"
        }:
            raise TheoryRegistryError(f"{result_id}.claim_class is invalid")
        if result.get("novelty_state") not in {
            "KNOWN", "APPLICATION", "POTENTIALLY_NOVEL", "ORIGINAL_VERIFIED"
        }:
            raise TheoryRegistryError(f"{result_id}.novelty_state is invalid")
        _require_text(result.get("statement"), f"{result_id}.statement")
        _require_text(result.get("scope"), f"{result_id}.scope")


@lru_cache(maxsize=1)
def load_theory_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TheoryRegistryError("theory registry is unavailable") from exc
    if payload.get("schema_version") != REGISTRY_SCHEMA:
        raise TheoryRegistryError("theory registry schema mismatch")
    program = payload.get("program")
    if not isinstance(program, Mapping):
        raise TheoryRegistryError("program is required")
    _require_text(program.get("program_id"), "program.program_id")
    _require_text(program.get("name"), "program.name")
    authority = program.get("authority")
    if not isinstance(authority, Mapping):
        raise TheoryRegistryError("program.authority is required")
    enabled = [key for key in FORBIDDEN_AUTHORITY if authority.get(key) is not False]
    if enabled:
        raise TheoryRegistryError("theory registry may not grant production authority: " + ", ".join(enabled))
    problems = payload.get("problems")
    if not isinstance(problems, list) or not problems:
        raise TheoryRegistryError("at least one theory problem is required")
    seen: set[str] = set()
    for problem in problems:
        if not isinstance(problem, Mapping):
            raise TheoryRegistryError("theory problem must be an object")
        _validate_problem(problem)
        problem_id = str(problem["problem_id"])
        if problem_id in seen:
            raise TheoryRegistryError(f"duplicate problem_id: {problem_id}")
        seen.add(problem_id)
    return payload


def theory_status() -> dict[str, Any]:
    registry = load_theory_registry()
    problems = registry["problems"]
    conjectures = [row for problem in problems for row in problem.get("conjectures", [])]
    return {
        "schema_version": registry["schema_version"],
        "registry_hash": registry_hash(registry),
        "program_id": registry["program"]["program_id"],
        "program_name": registry["program"]["name"],
        "status": registry["program"]["status"],
        "track_count": len(registry["program"].get("tracks", [])),
        "problem_count": len(problems),
        "active_problem_count": sum(problem.get("status") == "ACTIVE" for problem in problems),
        "open_conjecture_count": sum(row.get("status") == "OPEN" for row in conjectures),
        "authority": dict(registry["program"]["authority"]),
    }


def public_theory_projection() -> dict[str, Any]:
    registry = load_theory_registry()
    public_problems = [
        problem for problem in registry["problems"]
        if problem.get("visibility") == "PUBLIC"
    ]
    return {
        "schema_version": registry["schema_version"],
        "registry_hash": registry_hash(registry),
        "program": {
            key: registry["program"].get(key)
            for key in ("program_id", "name", "status", "purpose", "standards", "tracks")
        },
        "problems": public_problems,
        "authority": {
            "theory_can_change_live_trading": False,
            "theory_can_open_protected_research_stages": False,
            "theory_can_claim_novelty_without_review": False,
        },
    }
