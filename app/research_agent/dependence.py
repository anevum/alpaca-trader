from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid5

from .math001 import (
    DEFAULT_LAMBDAS,
    hoeffding_lower_confidence_sequence,
    mixture_e_process,
)
from .models import ExperimentProposal, canonical_json
from .multiplicity import e_bh, multiplicity_plan_from_proposal
from .proposal import proposal_hash


DEPENDENCE_VERSION = "math001-dependence-v1"
DEPENDENCE_PLAN_VERSION = "math001-dependence-plan-v1"
PLAN_NAMESPACE = UUID("6b3c13d8-6320-550c-b6d9-45aece496917")

BASE_REQUIRED_CHECKS = (
    "conditional_mean_residual_check",
    "serial_autocorrelation_diagnostic",
    "volatility_clustering_stress",
    "rare_extreme_stress",
    "overlap_double_counting_check",
)
CROSS_CANDIDATE_CHECK = "cross_candidate_dependence_stress"

ROBUSTNESS_ALIASES = {
    "conditional mean residual check": "conditional_mean_residual_check",
    "conditional mean check": "conditional_mean_residual_check",
    "martingale residual check": "conditional_mean_residual_check",
    "serial autocorrelation diagnostic": "serial_autocorrelation_diagnostic",
    "serial dependence": "serial_autocorrelation_diagnostic",
    "temporal dependence": "serial_autocorrelation_diagnostic",
    "autocorrelation": "serial_autocorrelation_diagnostic",
    "volatility clustering stress": "volatility_clustering_stress",
    "volatility clustering": "volatility_clustering_stress",
    "heteroskedasticity": "volatility_clustering_stress",
    "heteroskedasticity stress": "volatility_clustering_stress",
    "rare extreme stress": "rare_extreme_stress",
    "rare extremes": "rare_extreme_stress",
    "heavy tail": "rare_extreme_stress",
    "heavy tails": "rare_extreme_stress",
    "heavy tail stress": "rare_extreme_stress",
    "overlap double counting check": "overlap_double_counting_check",
    "overlap": "overlap_double_counting_check",
    "overlapping outcomes": "overlap_double_counting_check",
    "overlap check": "overlap_double_counting_check",
    "cross candidate dependence stress": "cross_candidate_dependence_stress",
    "cross candidate dependence": "cross_candidate_dependence_stress",
    "common factor dependence": "cross_candidate_dependence_stress",
}

VALID_NULL_SCENARIOS = frozenset(
    {
        "iid_null",
        "predictable_volatility_null",
        "rare_extreme_null",
        "common_factor_null",
        "common_factor_volatility_null",
    }
)
ASSUMPTION_VIOLATION_SCENARIOS = frozenset(
    {
        "overlap_ma1_unconditional_zero",
        "ar1_unconditional_zero",
    }
)
ALL_SCENARIOS = tuple(
    sorted(VALID_NULL_SCENARIOS | ASSUMPTION_VIOLATION_SCENARIOS)
)


class DependenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DependenceBenchmark:
    project_id: str
    benchmark_version: str
    seed: int
    replicates: int
    candidates_per_replicate: int
    observations_per_candidate: int
    alpha: float
    common_factor_weight: float
    ar_phi: float
    results: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def _validate_alpha(alpha: float) -> float:
    value = float(alpha)
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise DependenceError("alpha must lie strictly between 0 and 1")
    return value


def _normalized_label(value: str) -> str:
    return " ".join(
        str(value or "")
        .strip()
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def canonical_robustness_tests(
    values: Sequence[str],
) -> tuple[str, ...]:
    canonical: set[str] = set()
    for value in values:
        label = _normalized_label(str(value))
        matched = ROBUSTNESS_ALIASES.get(label)
        if matched is not None:
            canonical.add(matched)
    return tuple(sorted(canonical))


def required_dependence_checks(
    proposal: ExperimentProposal,
) -> tuple[str, ...]:
    required = list(BASE_REQUIRED_CHECKS)
    if len(proposal.configurations) > 1:
        required.append(CROSS_CANDIDATE_CHECK)
    return tuple(required)


def dependence_plan_from_proposal(
    proposal: ExperimentProposal,
) -> dict[str, Any]:
    required = required_dependence_checks(proposal)
    present = canonical_robustness_tests(proposal.robustness_tests)
    missing = tuple(item for item in required if item not in present)
    multiplicity = multiplicity_plan_from_proposal(proposal)

    canonical = {
        "plan_version": DEPENDENCE_PLAN_VERSION,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.revision,
        "proposal_hash": proposal_hash(proposal),
        "outcome_contract": "BOUNDED_SCORE_IN_MINUS1_PLUS1",
        "null_contract": "CONDITIONAL_MEAN_NONPOSITIVE_GIVEN_GLOBAL_FILTRATION",
        "iid_required": False,
        "level_autocorrelation_under_null": "NOT_ASSUMED_AND_DIAGNOSTICALLY_SUSPICIOUS",
        "heteroskedasticity_policy": "ALLOWED_IF_CONDITIONAL_MEAN_NULL_IS_PRESERVED",
        "cross_candidate_dependence_policy": {
            "method": multiplicity["method"],
            "dependence_scope": multiplicity["dependence_scope"],
        },
        "heavy_tail_policy": "UNBOUNDED_RAW_OUTCOMES_FORBIDDEN_IN_B1",
        "overlap_policy": "ATOMIC_NONOVERLAPPING_OR_EXPLICIT_FILTRATION_REQUIRED",
        "required_checks": required,
        "present_checks": present,
        "missing_checks": missing,
        "dependence_ready": not missing,
        "policy_status": "STUDY_ONLY_UNFROZEN",
        "production_authority": False,
        "protected_stage_authority": False,
    }
    digest = hashlib.sha256(
        canonical_json(canonical).encode("utf-8")
    ).hexdigest()
    canonical["plan_hash"] = digest
    canonical["plan_id"] = str(uuid5(PLAN_NAMESPACE, digest))
    return canonical


def assert_dependence_ready(
    proposal: ExperimentProposal,
) -> dict[str, Any]:
    plan = dependence_plan_from_proposal(proposal)
    missing = tuple(plan["missing_checks"])
    if missing:
        raise DependenceError(
            "missing frozen dependence checks: " + ", ".join(missing)
        )
    return plan


def _naive_fixed_time_boundary(n: int, alpha: float) -> float:
    return math.sqrt(2.0 * math.log(1.0 / alpha) / n)


def _lag1_correlation(values: Sequence[float]) -> float:
    if len(values) < 3:
        return 0.0
    left = values[:-1]
    right = values[1:]
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    numerator = math.fsum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right)
    )
    left_ss = math.fsum((a - left_mean) ** 2 for a in left)
    right_ss = math.fsum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_ss * right_ss)
    if denominator <= 0.0:
        return 0.0
    return numerator / denominator


def _sign(rng: random.Random) -> float:
    return 1.0 if rng.getrandbits(1) else -1.0


def _rare_extreme(rng: random.Random) -> float:
    # Mean zero with a rare +1 extreme and frequent small negative outcomes:
    # 0.1 * 1 + 0.9 * (-1/9) = 0.
    return 1.0 if rng.random() < 0.1 else -(1.0 / 9.0)


def _generate_paths(
    rng: random.Random,
    *,
    scenario: str,
    candidates: int,
    observations: int,
    common_factor_weight: float,
    ar_phi: float,
) -> tuple[tuple[float, ...], ...]:
    if scenario not in VALID_NULL_SCENARIOS | ASSUMPTION_VIOLATION_SCENARIOS:
        raise DependenceError(f"unsupported dependence scenario: {scenario}")
    if candidates <= 0 or observations <= 0:
        raise DependenceError("candidate and observation counts must be positive")
    rho = float(common_factor_weight)
    if not 0.0 <= rho <= 1.0:
        raise DependenceError("common_factor_weight must lie in [0, 1]")
    phi = float(ar_phi)
    if not 0.0 <= phi < 1.0:
        raise DependenceError("ar_phi must lie in [0, 1)")

    paths = [[] for _ in range(candidates)]

    if scenario == "iid_null":
        for _ in range(observations):
            for candidate in range(candidates):
                paths[candidate].append(_sign(rng))

    elif scenario == "predictable_volatility_null":
        high = [False for _ in range(candidates)]
        for _ in range(observations):
            for candidate in range(candidates):
                amplitude = 1.0 if high[candidate] else 0.15
                paths[candidate].append(amplitude * _sign(rng))
                stay_or_enter = rng.random()
                high[candidate] = (
                    stay_or_enter < 0.88
                    if high[candidate]
                    else stay_or_enter < 0.08
                )

    elif scenario == "rare_extreme_null":
        for _ in range(observations):
            for candidate in range(candidates):
                paths[candidate].append(_rare_extreme(rng))

    elif scenario == "common_factor_null":
        for _ in range(observations):
            common = _sign(rng)
            for candidate in range(candidates):
                idiosyncratic = _sign(rng)
                paths[candidate].append(
                    rho * common + (1.0 - rho) * idiosyncratic
                )

    elif scenario == "common_factor_volatility_null":
        high = False
        for _ in range(observations):
            amplitude = 1.0 if high else 0.15
            common = _sign(rng)
            for candidate in range(candidates):
                idiosyncratic = _sign(rng)
                paths[candidate].append(
                    amplitude
                    * (rho * common + (1.0 - rho) * idiosyncratic)
                )
            transition = rng.random()
            high = transition < 0.9 if high else transition < 0.06

    elif scenario == "overlap_ma1_unconditional_zero":
        previous = [_sign(rng) for _ in range(candidates)]
        for _ in range(observations):
            for candidate in range(candidates):
                innovation = _sign(rng)
                paths[candidate].append(
                    0.5 * (innovation + previous[candidate])
                )
                previous[candidate] = innovation

    elif scenario == "ar1_unconditional_zero":
        previous = [0.0 for _ in range(candidates)]
        for _ in range(observations):
            for candidate in range(candidates):
                innovation = _sign(rng)
                value = (
                    phi * previous[candidate]
                    + (1.0 - phi) * innovation
                )
                paths[candidate].append(value)
                previous[candidate] = value

    return tuple(tuple(path) for path in paths)


def _candidate_metrics(
    outcomes: Sequence[float],
    *,
    alpha: float,
    lambdas: Sequence[float],
) -> dict[str, float | bool]:
    e_path = mixture_e_process(outcomes, lambdas=lambdas)
    max_e = max(1.0, max(e_path, default=1.0))
    terminal_e = e_path[-1] if e_path else 1.0
    naive_hit = False
    running_sum = 0.0
    for n, value in enumerate(outcomes, start=1):
        running_sum += value
        if n >= 10 and (
            running_sum / n
        ) >= _naive_fixed_time_boundary(n, alpha):
            naive_hit = True
            break

    lower_cs = hoeffding_lower_confidence_sequence(
        outcomes,
        alpha=alpha,
    )
    cs_positive = any(value > 0.0 for value in lower_cs)
    return {
        "terminal_e": terminal_e,
        "max_e": max_e,
        "e_cross": max_e >= 1.0 / alpha,
        "naive_hit": naive_hit,
        "cs_positive": cs_positive,
        "lag1": _lag1_correlation(outcomes),
    }


def simulate_dependence_benchmark(
    *,
    replicates: int = 500,
    candidates_per_replicate: int = 20,
    observations_per_candidate: int = 120,
    alpha: float = 0.05,
    common_factor_weight: float = 0.8,
    ar_phi: float = 0.75,
    seed: int = 1003,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
) -> DependenceBenchmark:
    error = _validate_alpha(alpha)
    if replicates <= 0:
        raise DependenceError("replicates must be positive")
    if candidates_per_replicate <= 0:
        raise DependenceError("candidates_per_replicate must be positive")
    if observations_per_candidate < 10:
        raise DependenceError(
            "observations_per_candidate must be at least 10"
        )

    rng = random.Random(seed)
    results: dict[str, dict[str, Any]] = {}
    familywise_threshold = candidates_per_replicate / error

    for scenario in ALL_SCENARIOS:
        candidate_count = replicates * candidates_per_replicate
        candidate_e_crosses = 0
        candidate_naive_hits = 0
        candidate_cs_positive = 0
        familywise_e_crosses = 0
        terminal_e_bh_any = 0
        lag1_sum = 0.0
        lag1_abs_sum = 0.0
        terminal_e_sum = 0.0
        max_e_sum = 0.0

        for _ in range(replicates):
            paths = _generate_paths(
                rng,
                scenario=scenario,
                candidates=candidates_per_replicate,
                observations=observations_per_candidate,
                common_factor_weight=common_factor_weight,
                ar_phi=ar_phi,
            )
            terminal_e_values: list[float] = []
            family_cross = False

            for path in paths:
                metrics = _candidate_metrics(
                    path,
                    alpha=error,
                    lambdas=lambdas,
                )
                terminal_e_values.append(float(metrics["terminal_e"]))
                candidate_e_crosses += int(bool(metrics["e_cross"]))
                candidate_naive_hits += int(bool(metrics["naive_hit"]))
                candidate_cs_positive += int(bool(metrics["cs_positive"]))
                lag1 = float(metrics["lag1"])
                lag1_sum += lag1
                lag1_abs_sum += abs(lag1)
                terminal_e_sum += float(metrics["terminal_e"])
                max_e_sum += float(metrics["max_e"])
                family_cross = family_cross or (
                    float(metrics["max_e"]) >= familywise_threshold
                )

            familywise_e_crosses += int(family_cross)
            terminal_e_bh_any += int(
                bool(
                    e_bh(
                        terminal_e_values,
                        alpha=error,
                    ).rejected_indices
                )
            )

        known_valid = scenario in VALID_NULL_SCENARIOS
        results[scenario] = {
            "classification": (
                "VALID_CONDITIONAL_NULL"
                if known_valid
                else "KNOWN_CONDITIONAL_NULL_VIOLATION"
            ),
            "b1_calibration_claim_applies": known_valid,
            "candidate_anytime_e_crossing_rate": (
                candidate_e_crosses / candidate_count
            ),
            "candidate_naive_repeated_hit_rate": (
                candidate_naive_hits / candidate_count
            ),
            "candidate_cs_false_positive_rate": (
                candidate_cs_positive / candidate_count
            ),
            "familywise_anytime_e_crossing_rate": (
                familywise_e_crosses / replicates
            ),
            "terminal_e_bh_any_rejection_rate": (
                terminal_e_bh_any / replicates
            ),
            "mean_lag1_autocorrelation": lag1_sum / candidate_count,
            "mean_abs_lag1_autocorrelation": (
                lag1_abs_sum / candidate_count
            ),
            "mean_terminal_e_value": terminal_e_sum / candidate_count,
            "mean_max_e_value": max_e_sum / candidate_count,
            "familywise_e_threshold": familywise_threshold,
        }

    return DependenceBenchmark(
        project_id="MATH-001",
        benchmark_version="math001-d1-benchmark-v1",
        seed=seed,
        replicates=replicates,
        candidates_per_replicate=candidates_per_replicate,
        observations_per_candidate=observations_per_candidate,
        alpha=error,
        common_factor_weight=common_factor_weight,
        ar_phi=ar_phi,
        results=results,
    )
