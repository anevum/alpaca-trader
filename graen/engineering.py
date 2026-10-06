"""Bounded research implementation contracts. No network, broker, or model calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

SCHEMA = "graen.research-code-promotion.v1"
CANONICAL_BRANCH = "main"
REPOSITORY = "anevum/alpaca-trader"
REQUIRED_JOBS = frozenset({"test", "velum-graen", "graen-forward-shadow", "codex-postgres", "graen-v14-ml"})
TRIGGERS = frozenset({
    "MODEL_HYPOTHESIS_GENERATION_REQUIRED", "NEEDS_NEW_HYPOTHESIS_ENGINE",
    "RESEARCH_IMPLEMENTATION_REQUIRED",
})
MAX_ATTEMPTS = 3


class IntegrityError(ValueError):
    pass


class ProtectedChange(IntegrityError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise IntegrityError("timezone_required")
    return result.astimezone(timezone.utc)


def validate_spec(spec):
    required = {
        "schema_version", "hypothesis_id", "epoch", "mechanism", "universe",
        "parameters", "corpus", "gates", "falsification", "search_history",
        "exposure_artifact_id", "research_only", "execution_authority",
    }
    if set(spec) != required or spec["schema_version"] != SCHEMA:
        raise IntegrityError("complete_frozen_prespec_required")
    if not re.fullmatch(r"CRYPTO-[A-Z0-9-]{3,80}", spec["hypothesis_id"]):
        raise IntegrityError("invalid_hypothesis_id")
    if not isinstance(spec["epoch"], str) or not spec["epoch"]:
        raise IntegrityError("epoch_required")
    if spec["research_only"] is not True or spec["execution_authority"] is not False:
        raise ProtectedChange("execution_authority_forbidden")
    if spec["mechanism"] != "bar_flow_pressure_v1":
        raise IntegrityError("unsupported_mechanism_requires_new_trusted_compiler")
    if spec["universe"] != ["BTC/USD", "ETH/USD", "SOL/USD"]:
        raise IntegrityError("universe_changed")
    params = spec["parameters"]
    ranges = {
        "lookback_bars": (36, 288), "volume_z": (0.5, 5),
        "trade_count_z": (0.5, 5), "range_ratio": (1, 5),
        "body_strength": (0.5, 1), "close_location": (0.5, 1),
        "hold_minutes": (5, 120), "cooldown_minutes": (5, 240),
    }
    if set(params) != set(ranges):
        raise IntegrityError("complete_parameters_required")
    for key, (low, high) in ranges.items():
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise IntegrityError("invalid_parameter:" + key)
    for key in ("lookback_bars", "hold_minutes", "cooldown_minutes"):
        if not isinstance(params[key], int):
            raise IntegrityError("integer_parameter_required:" + key)
    if params["hold_minutes"] % 5 or params["cooldown_minutes"] < params["hold_minutes"]:
        raise IntegrityError("invalid_holding_contract")
    if spec["gates"] != {
        "policy": "activity_shock_v9_frozen_gates",
        "selection_cost": "high", "delay_minutes": 5,
        "confirmatory_candidates": 1,
    }:
        raise IntegrityError("frozen_gates_changed")
    if not all(spec[key] for key in ("falsification", "search_history", "exposure_artifact_id")):
        raise IntegrityError("provenance_required")
    corpus = spec["corpus"]
    if set(corpus) != {"development", "validation", "holdout"}:
        raise IntegrityError("all_corpus_boundaries_required")
    previous_end = None
    for stage in ("development", "validation", "holdout"):
        bounds = corpus[stage]
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise IntegrityError("invalid_corpus")
        start, end = map(stamp, bounds)
        if start >= end or (previous_end and start < previous_end):
            raise IntegrityError("overlapping_corpus")
        if (end - start).days < 30:
            raise IntegrityError("insufficient_corpus_duration")
        previous_end = end
    canonical(spec)
    return digest(spec)


def verify_exposure(spec, exposure):
    """An explicit complete ledger is required; absence never means uninspected."""
    if exposure.get("artifact_id") != spec["exposure_artifact_id"] or exposure.get("complete") is not True:
        raise IntegrityError("complete_exposure_ledger_required")
    for stage in ("validation", "holdout"):
        start, end = map(stamp, spec["corpus"][stage])
        for interval in exposure.get("inspected_intervals", []):
            left, right = map(stamp, interval)
            if left < end and start < right:
                raise IntegrityError("sealed_corpus_previously_inspected:" + stage)


def stage_window(spec, stage, predecessor=None):
    validate_spec(spec)
    if stage not in ("development", "validation", "holdout"):
        raise IntegrityError("invalid_stage")
    expected = {"validation": "development", "holdout": "validation"}.get(stage)
    if expected:
        if not predecessor or any((
            predecessor.get("stage") != expected,
            predecessor.get("passed") is not True,
            predecessor.get("spec_hash") != digest(spec),
            predecessor.get("epoch") != spec["epoch"],
            not predecessor.get("artifact_id"),
        )):
            raise IntegrityError("verified_predecessor_required")
    start, end = map(stamp, spec["corpus"][stage])
    return start, end


def stage_bars(rows, *, start, end):
    """Discard provider-inclusive end bars; never request end plus a lookahead."""
    return [row for row in rows if start <= stamp(row["t"]) < end]


def compile_bundle(spec):
    """Only these exact generated bytes can be autonomously committed.

    The worker is a bounded compiler, not a general Python editing agent. The
    compiler, evaluator, CI policy and promotion controller cannot edit themselves.
    """
    spec_hash = validate_spec(spec)
    slug = spec["hypothesis_id"].lower().replace("-", "_")
    module_path = "graen/crypto/generated/" + slug + ".py"
    spec_path = "research/crypto/prespecs/" + slug + ".json"
    test_path = "tests/test_generated_" + slug + ".py"
    literal = repr(json.loads(canonical(spec)))
    module = (
        '"""Generated research hypothesis; no execution authority."""\n'
        "from graen.crypto.flow_pressure import evaluate_stage\n\n"
        "SPEC = " + literal + "\n"
        "SPEC_HASH = " + repr(spec_hash) + "\n\n"
        "def evaluate(bars, *, stage, predecessor=None):\n"
        "    return evaluate_stage(bars, spec=SPEC, stage=stage, predecessor=predecessor)\n"
    )
    test = (
        "from graen.crypto.generated." + slug + " import SPEC, SPEC_HASH\n"
        "from graen.engineering import digest, stage_window, IntegrityError\n"
        "import pytest\n\n"
        "def test_generated_prespec_identity():\n"
        "    assert digest(SPEC) == SPEC_HASH\n"
        "    assert SPEC['execution_authority'] is False\n\n"
        "@pytest.mark.parametrize('stage', ['validation', 'holdout'])\n"
        "def test_generated_sealed_stages_require_predecessor(stage):\n"
        "    with pytest.raises(IntegrityError):\n"
        "        stage_window(SPEC, stage)\n"
    )
    return {module_path: module, spec_path: canonical(spec) + "\n", test_path: test}


def verify_bundle(spec, files):
    expected = compile_bundle(spec)
    if files != expected:
        raise ProtectedChange("only_exact_compiler_output_is_permitted")
    return expected


def verify_ci(runs, *, head, pr_number):
    eligible = [
        run for run in runs
        if run.get("head_sha") == head
        and run.get("event") == "pull_request"
        and run.get("path") == ".github/workflows/ci.yml"
        and any(pr.get("number") == pr_number for pr in run.get("pull_requests", []))
    ]
    if not eligible:
        return False
    latest = max(eligible, key=lambda run: (run.get("run_number", 0), run.get("run_attempt", 1)))
    jobs = latest.get("jobs", [])
    names = {job["name"] for job in jobs}
    return (
        latest.get("status") == "completed"
        and latest.get("conclusion") == "success"
        and REQUIRED_JOBS <= names
        and all(job.get("status") == "completed" and job.get("conclusion") == "success" for job in jobs)
    )
