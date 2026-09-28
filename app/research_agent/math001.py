from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Sequence

MATH001_ID = "MATH-001"
DEFAULT_LAMBDAS = (0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.7)


class Math001Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    project_id: str
    observation_count: int
    alpha: float
    current_e_value: float
    max_e_value: float
    anytime_p_value: float
    lower_conditional_mean_cs: float
    evidence_threshold: float
    threshold_reached: bool
    policy_status: str = "STUDY_ONLY_UNFROZEN"

    def to_dict(self) -> dict[str, float | int | bool | str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NullSearchSimulation:
    project_id: str
    seed: int
    replicates: int
    candidates_per_replicate: int
    observations_per_candidate: int
    alpha: float
    naive_false_discovery_rate: float
    anytime_familywise_false_discovery_rate: float
    anytime_familywise_threshold: float

    def to_dict(self) -> dict[str, float | int | str]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def _validate_alpha(alpha: float) -> float:
    value = float(alpha)
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise Math001Error("alpha must lie strictly between 0 and 1")
    return value


def _validate_outcomes(outcomes: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(value) for value in outcomes)
    for value in values:
        if not math.isfinite(value):
            raise Math001Error("outcomes must be finite")
        if value < -1.0 or value > 1.0:
            raise Math001Error("MATH-001 outcomes must be normalized to [-1, 1]")
    return values


def _normalized_mixture(
    lambdas: Sequence[float],
    weights: Sequence[float] | None,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    lam = tuple(float(value) for value in lambdas)
    if not lam:
        raise Math001Error("at least one betting fraction is required")
    if any((not math.isfinite(value)) or value < 0.0 or value >= 1.0 for value in lam):
        raise Math001Error("betting fractions must lie in [0, 1)")
    if len(set(lam)) != len(lam):
        raise Math001Error("betting fractions must be unique")

    if weights is None:
        weight = tuple(1.0 / len(lam) for _ in lam)
    else:
        raw = tuple(float(value) for value in weights)
        if len(raw) != len(lam):
            raise Math001Error("weights must match the betting-fraction count")
        if any((not math.isfinite(value)) or value < 0.0 for value in raw):
            raise Math001Error("mixture weights must be finite and nonnegative")
        total = sum(raw)
        if total <= 0.0:
            raise Math001Error("mixture weights must have positive total mass")
        weight = tuple(value / total for value in raw)
    return lam, weight


def fixed_lambda_e_process(
    outcomes: Sequence[float],
    betting_fraction: float,
) -> tuple[float, ...]:
    """Return E_n = product_i (1 + lambda Y_i) for normalized outcomes.

    Under H0: E[Y_i | F_{i-1}] <= 0, Y_i in [-1, 1], and predictable
    lambda in [0, 1), this is a nonnegative supermartingale.
    """

    values = _validate_outcomes(outcomes)
    lam = float(betting_fraction)
    if not math.isfinite(lam) or lam < 0.0 or lam >= 1.0:
        raise Math001Error("betting_fraction must lie in [0, 1)")

    wealth = 1.0
    history: list[float] = []
    for value in values:
        wealth *= 1.0 + lam * value
        history.append(wealth)
    return tuple(history)


def mixture_e_process(
    outcomes: Sequence[float],
    *,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    weights: Sequence[float] | None = None,
) -> tuple[float, ...]:
    """Return a convex mixture of fixed-lambda e-processes.

    The mixture is valid under the same conditional-mean null because a
    nonnegative weighted sum of e-processes with weights summing to one is
    itself an e-process.
    """

    values = _validate_outcomes(outcomes)
    lam, weight = _normalized_mixture(lambdas, weights)
    component_wealth = [1.0 for _ in lam]
    history: list[float] = []

    for value in values:
        for index, betting_fraction in enumerate(lam):
            component_wealth[index] *= 1.0 + betting_fraction * value
        history.append(
            sum(
                mixture_weight * wealth
                for mixture_weight, wealth in zip(weight, component_wealth)
            )
        )
    return tuple(history)


def anytime_p_values(e_values: Sequence[float]) -> tuple[float, ...]:
    """Convert an e-process path to conservative anytime p-values.

    p_n = min(1, 1 / max_{k <= n} E_k).
    """

    running_max = 1.0
    result: list[float] = []
    for raw in e_values:
        value = float(raw)
        if math.isnan(value) or value < 0.0:
            raise Math001Error("e-values must be nonnegative")
        running_max = max(running_max, value)
        result.append(0.0 if math.isinf(running_max) else min(1.0, 1.0 / running_max))
    return tuple(result)


def hoeffding_lower_confidence_sequence(
    outcomes: Sequence[float],
    *,
    alpha: float = 0.05,
) -> tuple[float, ...]:
    """Lower CS for the running average conditional expectation.

    Let mu_i = E[Y_i | F_{i-1}] with Y_i in [-1, 1]. For each n, Hoeffding's
    inequality gives a one-sided fixed-time error bound. Allocating
    alpha_n = 6 alpha / (pi^2 n^2) and union-bounding over n yields a
    simultaneous lower confidence sequence for

        (1/n) * sum_{i=1}^n mu_i.

    This is deliberately conservative and does not assume IID observations.
    """

    values = _validate_outcomes(outcomes)
    error = _validate_alpha(alpha)
    running_sum = 0.0
    result: list[float] = []

    for n, value in enumerate(values, start=1):
        running_sum += value
        alpha_n = (6.0 * error) / (math.pi * math.pi * n * n)
        radius = math.sqrt(2.0 * math.log(1.0 / alpha_n) / n)
        lower = (running_sum / n) - radius
        result.append(max(-1.0, lower))
    return tuple(result)


def summarize_evidence(
    outcomes: Sequence[float],
    *,
    alpha: float = 0.05,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    weights: Sequence[float] | None = None,
) -> EvidenceSummary:
    """Produce a research-only MATH-001 evidence snapshot.

    Crossing the study threshold is not a RHEN stage transition and confers
    no production, validation, holdout, or promotion authority.
    """

    values = _validate_outcomes(outcomes)
    error = _validate_alpha(alpha)
    if not values:
        return EvidenceSummary(
            project_id=MATH001_ID,
            observation_count=0,
            alpha=error,
            current_e_value=1.0,
            max_e_value=1.0,
            anytime_p_value=1.0,
            lower_conditional_mean_cs=-1.0,
            evidence_threshold=1.0 / error,
            threshold_reached=False,
        )

    e_path = mixture_e_process(values, lambdas=lambdas, weights=weights)
    p_path = anytime_p_values(e_path)
    lower_path = hoeffding_lower_confidence_sequence(values, alpha=error)
    maximum = max(1.0, max(e_path))
    return EvidenceSummary(
        project_id=MATH001_ID,
        observation_count=len(values),
        alpha=error,
        current_e_value=e_path[-1],
        max_e_value=maximum,
        anytime_p_value=p_path[-1],
        lower_conditional_mean_cs=lower_path[-1],
        evidence_threshold=1.0 / error,
        threshold_reached=maximum >= (1.0 / error),
    )


def _naive_fixed_time_boundary(n: int, alpha: float) -> float:
    # For Y_i in [-1, 1], Hoeffding gives
    # P(mean(Y) >= r) <= exp(-n r^2 / 2) under the zero-mean null.
    return math.sqrt(2.0 * math.log(1.0 / alpha) / n)


def simulate_null_search(
    *,
    replicates: int = 500,
    candidates_per_replicate: int = 50,
    observations_per_candidate: int = 100,
    alpha: float = 0.05,
    min_observations: int = 10,
    seed: int = 1001,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    weights: Sequence[float] | None = None,
) -> NullSearchSimulation:
    """MATH-001-G1: demonstrate repeated-search false discoveries under a null.

    Each candidate receives IID Rademacher outcomes (+1/-1 with equal
    probability), so every candidate has exactly zero mean.

    Naive comparator:
      At every n >= min_observations, every candidate is checked against a
      single-test, fixed-time Hoeffding threshold at alpha. It intentionally
      ignores both repeated looks and the number of candidates.

    Anytime comparator:
      Each candidate uses the MATH-001 mixture e-process. A Bonferroni/Ville
      familywise threshold candidates_per_replicate / alpha is used. This is
      only the G1 safety comparator; MATH-001-C later studies e-BH/e-LOND.
    """

    error = _validate_alpha(alpha)
    if replicates <= 0:
        raise Math001Error("replicates must be positive")
    if candidates_per_replicate <= 0:
        raise Math001Error("candidates_per_replicate must be positive")
    if observations_per_candidate <= 0:
        raise Math001Error("observations_per_candidate must be positive")
    if min_observations <= 0 or min_observations > observations_per_candidate:
        raise Math001Error("min_observations must be within the observation horizon")

    lam, weight = _normalized_mixture(lambdas, weights)
    rng = random.Random(seed)
    naive_false_discoveries = 0
    anytime_false_discoveries = 0
    familywise_threshold = candidates_per_replicate / error

    for _ in range(replicates):
        naive_hit = False
        anytime_hit = False

        for _candidate in range(candidates_per_replicate):
            running_sum = 0.0
            component_wealth = [1.0 for _ in lam]

            for n in range(1, observations_per_candidate + 1):
                outcome = 1.0 if rng.getrandbits(1) else -1.0
                running_sum += outcome

                for index, betting_fraction in enumerate(lam):
                    component_wealth[index] *= 1.0 + betting_fraction * outcome
                mixture = sum(
                    mixture_weight * wealth
                    for mixture_weight, wealth in zip(weight, component_wealth)
                )

                if n >= min_observations:
                    if (running_sum / n) >= _naive_fixed_time_boundary(n, error):
                        naive_hit = True
                    if mixture >= familywise_threshold:
                        anytime_hit = True

                if naive_hit and anytime_hit:
                    break

            if naive_hit and anytime_hit:
                break

        naive_false_discoveries += int(naive_hit)
        anytime_false_discoveries += int(anytime_hit)

    return NullSearchSimulation(
        project_id=MATH001_ID,
        seed=seed,
        replicates=replicates,
        candidates_per_replicate=candidates_per_replicate,
        observations_per_candidate=observations_per_candidate,
        alpha=error,
        naive_false_discovery_rate=naive_false_discoveries / replicates,
        anytime_familywise_false_discovery_rate=anytime_false_discoveries / replicates,
        anytime_familywise_threshold=familywise_threshold,
    )
