from __future__ import annotations

from math import sqrt
from typing import Any, Mapping, Sequence

from graen.crypto.research_v7 import (
    CandidateSpec,
    VALIDATION_ALPHA,
    _development_gate,
    _holdout_gate,
    _validation_gate,
    build_series,
    evaluate_candidate,
)

from .strategy_grammar import StrategyManifest, compiler_profile, validate_manifest


RUNNER_VERSION = "graen.strategy-runner.v1"
SUPPORTED_COMPILER_PROFILE = "research_v7_candidate_v1"


class StrategyRunnerError(ValueError):
    pass


def _int(params: Mapping[str, Any], name: str, low: int, high: int) -> int:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise StrategyRunnerError(f"invalid parameter: {name}")
    return value


def _float(
    params: Mapping[str, Any],
    name: str,
    low: float,
    high: float,
    *,
    default: float | None = None,
) -> float:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyRunnerError(f"invalid parameter: {name}")
    value = float(value)
    if not low <= value <= high:
        raise StrategyRunnerError(f"invalid parameter: {name}")
    return value



def compile_candidate(manifest: StrategyManifest) -> CandidateSpec:
    validation = validate_manifest(manifest, require_compiler=False)
    if validation["missing_primitives"]:
        raise StrategyRunnerError("manifest contains unsupported primitives")
    if compiler_profile(manifest) != SUPPORTED_COMPILER_PROFILE:
        raise StrategyRunnerError("manifest requires a new trusted compiler profile")

    params = dict(manifest.parameters)
    hold_from_exit = {
        "time_60m": 60,
        "time_120m": 120,
        "time_240m": 240,
    }[manifest.exit]
    hold = _int(params, "hold_minutes", 5, 480)
    if hold != hold_from_exit:
        raise StrategyRunnerError("hold_minutes must match the frozen exit primitive")
    scan = _int(params, "scan_minutes", 5, 120)
    lookback = _int(params, "lookback_minutes", 5, 480)
    concentration = _float(params, "concentration_limit", 0.2, 1.0, default=0.8)
    min_breadth = _int(params, "min_breadth_positive", 0, 20)

    if manifest.family == "cross_asset_diffusion":
        leader = str(params.get("leader_symbol") or "")
        if leader not in manifest.symbols:
            raise StrategyRunnerError("leader_symbol must be in the frozen universe")
        targets = tuple(symbol for symbol in manifest.symbols if symbol != leader)
        if not targets:
            raise StrategyRunnerError("cross-asset diffusion requires follower targets")
        return CandidateSpec(
            candidate_id=manifest.hypothesis_id,
            family=manifest.family,
            mode="lead_lag",
            targets=targets,
            hold_minutes=hold,
            scan_minutes=scan,
            lookback_minutes=lookback,
            concentration_limit=concentration,
            leader_symbol=leader,
            leader_threshold=_float(params, "leader_threshold", 0.0001, 0.10),
            lag_gap_threshold=_float(params, "lag_gap_threshold", 0.0001, 0.10),
            min_target_return=_float(params, "min_target_return", -0.10, 0.10, default=-0.01),
            max_target_return=_float(params, "max_target_return", -0.10, 0.10, default=0.03),
            min_breadth_positive=min_breadth,
            require_btc_nonnegative=bool(params.get("require_btc_nonnegative", False)),
        )

    return CandidateSpec(
        candidate_id=manifest.hypothesis_id,
        family=manifest.family,
        mode="breadth_laggard",
        targets=tuple(manifest.symbols),
        hold_minutes=hold,
        scan_minutes=scan,
        lookback_minutes=lookback,
        concentration_limit=concentration,
        lag_gap_threshold=_float(params, "lag_gap_threshold", 0.0001, 0.10),
        min_target_return=_float(params, "min_target_return", -0.10, 0.10, default=-0.02),
        max_target_return=_float(params, "max_target_return", -0.10, 0.10, default=0.05),
        min_breadth_positive=min_breadth,
        market_threshold=_float(params, "market_threshold", -0.10, 0.10, default=0.0),
    )


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    manifest: StrategyManifest,
    *,
    start,
    end,
    seed: int,
) -> dict[str, Any]:
    spec = compile_candidate(manifest)
    series = build_series(bars_by_symbol, start=start, end=end)
    result = evaluate_candidate(
        series,
        spec,
        start=start,
        end=end,
        scenario="high",
        seed=seed,
    )
    passed, reasons = _development_gate(result)
    primary = result["primary"]
    return {
        "stage": "DEVELOPMENT",
        "opened": True,
        "manifest": manifest.as_dict(),
        "candidate": spec.to_dict(),
        "result": result,
        "passed": passed,
        "reasons": reasons,
        "selection_score": float(primary["expectancy_per_trade"])
        * sqrt(max(int(primary["trade_count"]), 1)),
        "runner_version": RUNNER_VERSION,
    }


def evaluate_validation(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    manifest: StrategyManifest,
    *,
    start,
    end,
    development_result: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    spec = compile_candidate(manifest)
    series = build_series(bars_by_symbol, start=start, end=end)
    result = evaluate_candidate(
        series,
        spec,
        start=start,
        end=end,
        scenario="high",
        seed=seed,
    )
    p_value = float(result["primary"]["dependence_adjusted_null"]["p_value"])
    passed, reasons = _validation_gate(
        spec,
        development_result["result"],
        result,
        multiplicity_rejected=p_value <= VALIDATION_ALPHA,
    )
    return {
        "stage": "VALIDATION",
        "opened": True,
        "manifest": manifest.as_dict(),
        "result": result,
        "passed": passed,
        "reasons": reasons,
        "single_candidate_multiplicity_threshold": VALIDATION_ALPHA,
        "runner_version": RUNNER_VERSION,
    }


def evaluate_holdout(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    manifest: StrategyManifest,
    *,
    start,
    end,
    seed: int,
) -> dict[str, Any]:
    spec = compile_candidate(manifest)
    series = build_series(bars_by_symbol, start=start, end=end)
    scenarios = {
        scenario: evaluate_candidate(
            series,
            spec,
            start=start,
            end=end,
            scenario=scenario,
            seed=seed + offset * 10,
        )
        for offset, scenario in enumerate(("low", "base", "high"))
    }
    passed, reasons = _holdout_gate(spec, scenarios)
    return {
        "stage": "HOLDOUT",
        "opened": True,
        "manifest": manifest.as_dict(),
        "scenarios": scenarios,
        "passed": passed,
        "reasons": reasons,
        "runner_version": RUNNER_VERSION,
    }
