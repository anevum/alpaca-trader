from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid5

from .models import ExperimentProposal, canonical_json


MULTIPLICITY_VERSION = "math001-multiplicity-v1"
MULTIPLICITY_PLAN_VERSION = "math001-multiplicity-plan-v1"
PLAN_NAMESPACE = UUID("65e585a1-3020-52d4-b6e1-cb78618120db")
DEFAULT_ALPHA = 0.05

METHOD_SPECS: dict[str, dict[str, str]] = {
    "bonferroni": {
        "input_type": "P_VALUE",
        "family_mode": "FIXED",
        "error_metric": "FWER",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
    "holm": {
        "input_type": "P_VALUE",
        "family_mode": "FIXED",
        "error_metric": "FWER",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
    "bh": {
        "input_type": "P_VALUE",
        "family_mode": "FIXED",
        "error_metric": "FDR",
        "dependence_scope": "INDEPENDENCE_OR_PRDS",
        "provenance": "KNOWN",
    },
    "by": {
        "input_type": "P_VALUE",
        "family_mode": "FIXED",
        "error_metric": "FDR",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
    "e_bh": {
        "input_type": "E_VALUE",
        "family_mode": "FIXED",
        "error_metric": "FDR",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
    "e_lond": {
        "input_type": "E_VALUE",
        "family_mode": "ONLINE",
        "error_metric": "FDR",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
    "async_e_lond": {
        "input_type": "E_VALUE",
        "family_mode": "ONLINE_ASYNC",
        "error_metric": "FDR",
        "dependence_scope": "ARBITRARY",
        "provenance": "KNOWN",
    },
}

ALIASES = {
    "bonferroni": "bonferroni",
    "holm": "holm",
    "bh": "bh",
    "benjamini_hochberg": "bh",
    "benjamini hochberg": "bh",
    "by": "by",
    "benjamini_yekutieli": "by",
    "benjamini yekutieli": "by",
    "e_bh": "e_bh",
    "e bh": "e_bh",
    "ebh": "e_bh",
    "e_lond": "e_lond",
    "e lond": "e_lond",
    "elond": "e_lond",
    "async_e_lond": "async_e_lond",
    "async e lond": "async_e_lond",
}

FIXED_PROPOSAL_METHODS = frozenset(
    {"bonferroni", "holm", "bh", "by", "e_bh"}
)


class MultiplicityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MultiplicityResult:
    method: str
    alpha: float
    input_count: int
    rejected_indices: tuple[int, ...]
    ordered_indices: tuple[int, ...]
    decision_thresholds: tuple[float, ...]
    input_type: str
    error_metric: str
    dependence_scope: str
    policy_status: str = "STUDY_ONLY_UNFROZEN"

    @property
    def rejection_count(self) -> int:
        return len(self.rejected_indices)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rejection_count"] = self.rejection_count
        return payload


@dataclass(frozen=True, slots=True)
class OnlineMultiplicityResult:
    method: str
    alpha: float
    e_values: tuple[float, ...]
    test_levels: tuple[float, ...]
    e_thresholds: tuple[float, ...]
    rejected: tuple[bool, ...]
    gamma: tuple[float, ...]
    dependence_scope: str = "ARBITRARY"
    error_metric: str = "FDR"
    policy_status: str = "STUDY_ONLY_UNFROZEN"

    @property
    def rejected_indices(self) -> tuple[int, ...]:
        return tuple(index for index, value in enumerate(self.rejected) if value)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rejected_indices"] = self.rejected_indices
        payload["rejection_count"] = len(self.rejected_indices)
        return payload


@dataclass(frozen=True, slots=True)
class MultiplicityBenchmark:
    project_id: str
    benchmark_version: str
    seed: int
    replicates: int
    alpha: float
    hypothesis_count: int
    nonnull_count: int
    effect: float
    rho: float
    results: Mapping[str, Mapping[str, Mapping[str, float]]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def _validate_alpha(alpha: float) -> float:
    value = float(alpha)
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise MultiplicityError("alpha must lie strictly between 0 and 1")
    return value


def _validate_p_values(values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    for value in result:
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise MultiplicityError("p-values must be finite and lie in [0, 1]")
    return result


def _validate_e_values(values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    for value in result:
        if math.isnan(value) or value < 0.0:
            raise MultiplicityError("e-values must be nonnegative")
    return result


def normalize_method_name(value: str) -> str:
    normalized = " ".join(
        str(value or "")
        .strip()
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
        .split()
    )
    canonical = ALIASES.get(normalized)
    if canonical is None:
        raise MultiplicityError(f"unsupported multiplicity method: {value}")
    return canonical


def harmonic_number(k: int) -> float:
    if k <= 0:
        raise MultiplicityError("harmonic number requires a positive integer")
    return math.fsum(1.0 / index for index in range(1, k + 1))


def bonferroni(
    p_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultiplicityResult:
    values = _validate_p_values(p_values)
    error = _validate_alpha(alpha)
    k = len(values)
    threshold = error / k if k else 0.0
    rejected = tuple(index for index, value in enumerate(values) if value <= threshold)
    return MultiplicityResult(
        method="bonferroni",
        alpha=error,
        input_count=k,
        rejected_indices=rejected,
        ordered_indices=tuple(sorted(range(k), key=lambda i: (values[i], i))),
        decision_thresholds=tuple(threshold for _ in values),
        input_type="P_VALUE",
        error_metric="FWER",
        dependence_scope="ARBITRARY",
    )


def holm(
    p_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultiplicityResult:
    values = _validate_p_values(p_values)
    error = _validate_alpha(alpha)
    k = len(values)
    order = tuple(sorted(range(k), key=lambda i: (values[i], i)))
    thresholds = tuple(
        error / (k - rank + 1)
        for rank in range(1, k + 1)
    )
    rejection_count = 0
    for rank, index in enumerate(order, start=1):
        if values[index] <= thresholds[rank - 1]:
            rejection_count = rank
        else:
            break
    rejected = tuple(sorted(order[:rejection_count]))
    return MultiplicityResult(
        method="holm",
        alpha=error,
        input_count=k,
        rejected_indices=rejected,
        ordered_indices=order,
        decision_thresholds=thresholds,
        input_type="P_VALUE",
        error_metric="FWER",
        dependence_scope="ARBITRARY",
    )


def benjamini_hochberg(
    p_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultiplicityResult:
    values = _validate_p_values(p_values)
    error = _validate_alpha(alpha)
    k = len(values)
    order = tuple(sorted(range(k), key=lambda i: (values[i], i)))
    thresholds = tuple(
        error * rank / k if k else 0.0
        for rank in range(1, k + 1)
    )
    rejection_count = 0
    for rank, index in enumerate(order, start=1):
        if values[index] <= thresholds[rank - 1]:
            rejection_count = rank
    rejected = tuple(sorted(order[:rejection_count]))
    return MultiplicityResult(
        method="bh",
        alpha=error,
        input_count=k,
        rejected_indices=rejected,
        ordered_indices=order,
        decision_thresholds=thresholds,
        input_type="P_VALUE",
        error_metric="FDR",
        dependence_scope="INDEPENDENCE_OR_PRDS",
    )


def benjamini_yekutieli(
    p_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultiplicityResult:
    values = _validate_p_values(p_values)
    error = _validate_alpha(alpha)
    k = len(values)
    order = tuple(sorted(range(k), key=lambda i: (values[i], i)))
    correction = harmonic_number(k) if k else 1.0
    thresholds = tuple(
        error * rank / (k * correction) if k else 0.0
        for rank in range(1, k + 1)
    )
    rejection_count = 0
    for rank, index in enumerate(order, start=1):
        if values[index] <= thresholds[rank - 1]:
            rejection_count = rank
    rejected = tuple(sorted(order[:rejection_count]))
    return MultiplicityResult(
        method="by",
        alpha=error,
        input_count=k,
        rejected_indices=rejected,
        ordered_indices=order,
        decision_thresholds=thresholds,
        input_type="P_VALUE",
        error_metric="FDR",
        dependence_scope="ARBITRARY",
    )


def e_bh(
    e_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultiplicityResult:
    values = _validate_e_values(e_values)
    error = _validate_alpha(alpha)
    k = len(values)
    order = tuple(sorted(range(k), key=lambda i: (-values[i], i)))
    thresholds = tuple(
        k / (error * rank) if k else math.inf
        for rank in range(1, k + 1)
    )
    rejection_count = 0
    for rank, index in enumerate(order, start=1):
        if values[index] >= thresholds[rank - 1]:
            rejection_count = rank
    rejected = tuple(sorted(order[:rejection_count]))
    return MultiplicityResult(
        method="e_bh",
        alpha=error,
        input_count=k,
        rejected_indices=rejected,
        ordered_indices=order,
        decision_thresholds=thresholds,
        input_type="E_VALUE",
        error_metric="FDR",
        dependence_scope="ARBITRARY",
    )


def telescoping_gamma(length: int) -> tuple[float, ...]:
    if length < 0:
        raise MultiplicityError("gamma length cannot be negative")
    return tuple(1.0 / (t * (t + 1.0)) for t in range(1, length + 1))


def _validate_gamma(
    gamma: Sequence[float],
    length: int,
) -> tuple[float, ...]:
    values = tuple(float(value) for value in gamma)
    if len(values) != length:
        raise MultiplicityError("gamma sequence length must match the hypothesis stream")
    if any((not math.isfinite(value)) or value < 0.0 for value in values):
        raise MultiplicityError("gamma values must be finite and nonnegative")
    if math.fsum(values) > 1.0 + 1e-12:
        raise MultiplicityError("gamma values must sum to at most one")
    return values


def e_lond(
    e_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
    gamma: Sequence[float] | None = None,
) -> OnlineMultiplicityResult:
    values = _validate_e_values(e_values)
    error = _validate_alpha(alpha)
    discounts = _validate_gamma(
        gamma if gamma is not None else telescoping_gamma(len(values)),
        len(values),
    )
    rejected: list[bool] = []
    levels: list[float] = []
    thresholds: list[float] = []
    discoveries = 0

    for e_value, discount in zip(values, discounts):
        level = error * discount * (discoveries + 1)
        threshold = math.inf if level <= 0.0 else 1.0 / level
        decision = e_value >= threshold
        levels.append(level)
        thresholds.append(threshold)
        rejected.append(decision)
        discoveries += int(decision)

    return OnlineMultiplicityResult(
        method="e_lond",
        alpha=error,
        e_values=values,
        test_levels=tuple(levels),
        e_thresholds=tuple(thresholds),
        rejected=tuple(rejected),
        gamma=discounts,
    )


def async_e_lond(
    e_values: Sequence[float],
    completed_before_launch: Sequence[Sequence[int]],
    *,
    alpha: float = DEFAULT_ALPHA,
    gamma: Sequence[float] | None = None,
) -> OnlineMultiplicityResult:
    values = _validate_e_values(e_values)
    error = _validate_alpha(alpha)
    if len(completed_before_launch) != len(values):
        raise MultiplicityError(
            "completed_before_launch must have one entry per hypothesis"
        )
    discounts = _validate_gamma(
        gamma if gamma is not None else telescoping_gamma(len(values)),
        len(values),
    )
    rejected: list[bool] = []
    levels: list[float] = []
    thresholds: list[float] = []

    for t, (e_value, discount, completed) in enumerate(
        zip(values, discounts, completed_before_launch)
    ):
        normalized = tuple(sorted({int(index) for index in completed}))
        if any(index < 0 or index >= t for index in normalized):
            raise MultiplicityError(
                "async completion sets may contain only prior hypotheses"
            )
        prior_completed_rejections = sum(
            int(rejected[index]) for index in normalized
        )
        level = error * discount * (prior_completed_rejections + 1)
        threshold = math.inf if level <= 0.0 else 1.0 / level
        decision = e_value >= threshold
        levels.append(level)
        thresholds.append(threshold)
        rejected.append(decision)

    return OnlineMultiplicityResult(
        method="async_e_lond",
        alpha=error,
        e_values=values,
        test_levels=tuple(levels),
        e_thresholds=tuple(thresholds),
        rejected=tuple(rejected),
        gamma=discounts,
    )


def multiplicity_plan_from_proposal(
    proposal: ExperimentProposal,
) -> dict[str, Any]:
    family_size = len(proposal.configurations)
    raw = dict(proposal.multiple_testing_method or {})

    if family_size <= 1 and not raw:
        canonical = {
            "plan_version": MULTIPLICITY_PLAN_VERSION,
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.revision,
            "proposal_hash": None,
            "method": "none",
            "alpha": None,
            "family_scope": "PROPOSAL_CONFIGURATIONS",
            "family_size": family_size,
            "input_type": "NONE",
            "error_metric": "NONE",
            "dependence_scope": "NOT_APPLICABLE",
            "method_configuration": {},
            "search_generation": proposal.revision - 1,
            "policy_status": "STUDY_ONLY_UNFROZEN",
            "production_authority": False,
            "protected_stage_authority": False,
        }
    else:
        name = normalize_method_name(str(raw.get("name") or ""))
        if name not in FIXED_PROPOSAL_METHODS:
            raise MultiplicityError(
                "proposal configuration families require a fixed-family method"
            )
        if "alpha" not in raw:
            raise MultiplicityError(
                "multiple_testing_method.alpha must be fixed before freeze"
            )
        alpha = _validate_alpha(float(raw["alpha"]))
        spec = METHOD_SPECS[name]

        if name == "bh":
            assumption = str(raw.get("dependence_assumption") or "").strip().upper()
            if assumption not in {"INDEPENDENCE", "PRDS", "INDEPENDENCE_OR_PRDS"}:
                raise MultiplicityError(
                    "BH requires an explicit independence/PRDS dependence assumption"
                )
        if name == "e_bh" and not str(raw.get("e_value_source") or "").strip():
            raise MultiplicityError(
                "e-BH requires a frozen e_value_source"
            )

        canonical = {
            "plan_version": MULTIPLICITY_PLAN_VERSION,
            "proposal_id": proposal.proposal_id,
            "proposal_revision": proposal.revision,
            "proposal_hash": None,
            "method": name,
            "alpha": alpha,
            "family_scope": "PROPOSAL_CONFIGURATIONS",
            "family_size": family_size,
            "input_type": spec["input_type"],
            "error_metric": spec["error_metric"],
            "dependence_scope": spec["dependence_scope"],
            "method_configuration": raw,
            "search_generation": proposal.revision - 1,
            "policy_status": "STUDY_ONLY_UNFROZEN",
            "production_authority": False,
            "protected_stage_authority": False,
        }

    digest = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    canonical["plan_hash"] = digest
    canonical["plan_id"] = str(uuid5(PLAN_NAMESPACE, digest))
    return canonical


def online_study_policy(
    *,
    alpha: float = DEFAULT_ALPHA,
    asynchronous: bool = False,
) -> dict[str, Any]:
    error = _validate_alpha(alpha)
    method = "async_e_lond" if asynchronous else "e_lond"
    canonical = {
        "plan_version": MULTIPLICITY_PLAN_VERSION,
        "scope": "CROSS_EXPERIMENT_CONFIRMATORY_STREAM",
        "method": method,
        "alpha": error,
        "input_type": "E_VALUE",
        "error_metric": "FDR",
        "dependence_scope": "ARBITRARY",
        "gamma_rule": "gamma_t=1/(t(t+1))",
        "gamma_total_mass": 1.0,
        "policy_status": "STUDY_ONLY_UNFROZEN",
        "production_authority": False,
        "protected_stage_authority": False,
    }
    digest = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    canonical["plan_hash"] = digest
    canonical["plan_id"] = str(uuid5(PLAN_NAMESPACE, digest))
    return canonical


def _normal_survival(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _normal_e_value(z: float, theta: float) -> float:
    exponent = theta * z - 0.5 * theta * theta
    return math.exp(min(700.0, exponent))


def _draw_statistics(
    rng: random.Random,
    *,
    hypothesis_count: int,
    nonnull_indices: set[int],
    effect: float,
    rho: float,
    dependence: str,
) -> tuple[float, ...]:
    if not 0.0 <= rho < 1.0:
        raise MultiplicityError("rho must lie in [0, 1)")
    if dependence not in {"independent", "positive_common", "signed_common"}:
        raise MultiplicityError("unsupported benchmark dependence")

    common = rng.gauss(0.0, 1.0)
    shared_scale = math.sqrt(rho)
    residual_scale = math.sqrt(1.0 - rho)
    values: list[float] = []
    for index in range(hypothesis_count):
        if dependence == "independent":
            noise = rng.gauss(0.0, 1.0)
        else:
            sign = 1.0
            if dependence == "signed_common" and index % 2:
                sign = -1.0
            noise = sign * shared_scale * common + residual_scale * rng.gauss(0.0, 1.0)
        mean = effect if index in nonnull_indices else 0.0
        values.append(mean + noise)
    return tuple(values)


def _fdp_power(
    rejected_indices: Sequence[int],
    nonnull_indices: set[int],
) -> tuple[float, float, float]:
    rejected = set(rejected_indices)
    false_discoveries = len(rejected - nonnull_indices)
    true_discoveries = len(rejected & nonnull_indices)
    fdp = false_discoveries / max(1, len(rejected))
    power = (
        true_discoveries / len(nonnull_indices)
        if nonnull_indices
        else 0.0
    )
    any_false = float(false_discoveries > 0)
    return fdp, power, any_false


def simulate_multiplicity_benchmark(
    *,
    replicates: int = 400,
    hypothesis_count: int = 40,
    nonnull_count: int = 5,
    effect: float = 2.5,
    rho: float = 0.65,
    alpha: float = DEFAULT_ALPHA,
    e_theta: float = 1.0,
    async_lag: int = 3,
    seed: int = 1002,
) -> MultiplicityBenchmark:
    error = _validate_alpha(alpha)
    if replicates <= 0:
        raise MultiplicityError("replicates must be positive")
    if hypothesis_count <= 0:
        raise MultiplicityError("hypothesis_count must be positive")
    if not 0 <= nonnull_count <= hypothesis_count:
        raise MultiplicityError("nonnull_count must lie within the family size")
    if effect < 0.0 or not math.isfinite(effect):
        raise MultiplicityError("effect must be finite and nonnegative")
    if e_theta <= 0.0 or not math.isfinite(e_theta):
        raise MultiplicityError("e_theta must be finite and positive")
    if async_lag < 0:
        raise MultiplicityError("async_lag cannot be negative")

    rng = random.Random(seed)
    scenarios = (
        ("independent_null", "independent", 0),
        ("signed_dependence_null", "signed_common", 0),
        ("independent_sparse_signal", "independent", nonnull_count),
        ("signed_dependence_sparse_signal", "signed_common", nonnull_count),
    )
    method_names = (
        "bonferroni",
        "holm",
        "bh",
        "by",
        "e_bh",
        "e_lond",
        "async_e_lond",
    )
    totals: dict[str, dict[str, dict[str, float]]] = {
        scenario: {
            method: {"fdp": 0.0, "power": 0.0, "any_false": 0.0, "discoveries": 0.0}
            for method in method_names
        }
        for scenario, _dependence, _nonnull in scenarios
    }

    for scenario, dependence, scenario_nonnull_count in scenarios:
        for _ in range(replicates):
            nonnull_indices = set(
                rng.sample(range(hypothesis_count), scenario_nonnull_count)
            )
            z_values = _draw_statistics(
                rng,
                hypothesis_count=hypothesis_count,
                nonnull_indices=nonnull_indices,
                effect=effect,
                rho=rho,
                dependence=dependence,
            )
            p_values = tuple(_normal_survival(value) for value in z_values)
            e_values = tuple(_normal_e_value(value, e_theta) for value in z_values)

            fixed_results = (
                bonferroni(p_values, alpha=error),
                holm(p_values, alpha=error),
                benjamini_hochberg(p_values, alpha=error),
                benjamini_yekutieli(p_values, alpha=error),
                e_bh(e_values, alpha=error),
            )
            online = e_lond(e_values, alpha=error)
            completed = tuple(
                tuple(range(max(0, t - async_lag)))
                for t in range(hypothesis_count)
            )
            async_online = async_e_lond(
                e_values,
                completed,
                alpha=error,
            )

            decisions: dict[str, tuple[int, ...]] = {
                result.method: result.rejected_indices
                for result in fixed_results
            }
            decisions["e_lond"] = online.rejected_indices
            decisions["async_e_lond"] = async_online.rejected_indices

            for method, rejected in decisions.items():
                fdp, power, any_false = _fdp_power(rejected, nonnull_indices)
                bucket = totals[scenario][method]
                bucket["fdp"] += fdp
                bucket["power"] += power
                bucket["any_false"] += any_false
                bucket["discoveries"] += len(rejected)

    results: dict[str, dict[str, dict[str, float]]] = {}
    for scenario, methods in totals.items():
        results[scenario] = {}
        for method, totals_for_method in methods.items():
            results[scenario][method] = {
                "empirical_fdr": totals_for_method["fdp"] / replicates,
                "empirical_power": totals_for_method["power"] / replicates,
                "familywise_false_discovery_rate": (
                    totals_for_method["any_false"] / replicates
                ),
                "mean_discoveries": totals_for_method["discoveries"] / replicates,
            }

    return MultiplicityBenchmark(
        project_id="MATH-001",
        benchmark_version="math001-c1-benchmark-v1",
        seed=seed,
        replicates=replicates,
        alpha=error,
        hypothesis_count=hypothesis_count,
        nonnull_count=nonnull_count,
        effect=effect,
        rho=rho,
        results=results,
    )
