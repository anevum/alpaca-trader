from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TwoStateSwitchingConfig:
    """Minimal ATP-001 model.

    state/action are both binary. A correct action earns correct_reward, an
    incorrect action earns wrong_reward, and changing action costs switch_cost.
    The latent state follows a two-state Markov chain and a binary observation
    arrives before the next decision.
    """

    correct_reward: float = 1.0
    wrong_reward: float = -1.0
    switch_cost: float = 0.4
    state1_given_state0: float = 0.08
    state1_given_state1: float = 0.92
    positive_given_state0: float = 0.25
    positive_given_state1: float = 0.75
    discount: float = 1.0

    def validate(self) -> None:
        if not self.correct_reward > self.wrong_reward:
            raise ValueError("correct_reward must exceed wrong_reward")
        if self.switch_cost < 0:
            raise ValueError("switch_cost must be non-negative")
        for name in (
            "state1_given_state0",
            "state1_given_state1",
            "positive_given_state0",
            "positive_given_state1",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must lie in [0, 1]")
        if not 0.0 < self.discount <= 1.0:
            raise ValueError("discount must lie in (0, 1]")


def expected_reward(belief_state1: float, action: int, config: TwoStateSwitchingConfig) -> float:
    config.validate()
    p = _probability(belief_state1, "belief_state1")
    if action not in (0, 1):
        raise ValueError("action must be 0 or 1")
    correct_probability = p if action == 1 else 1.0 - p
    return (
        correct_probability * config.correct_reward
        + (1.0 - correct_probability) * config.wrong_reward
    )


def myopic_switching_thresholds(config: TwoStateSwitchingConfig) -> dict[str, float]:
    """Return exact one-period switching thresholds.

    If the previous action was 0, action 1 is selected only above upper.
    If the previous action was 1, action 0 is selected only below lower.

    Values may lie outside [0, 1]. That correctly represents a switching cost
    so large that a one-period reward cannot justify switching.
    """

    config.validate()
    reward_spread = config.correct_reward - config.wrong_reward
    half_adjustment = config.switch_cost / (2.0 * reward_spread)
    return {
        "switch_1_to_0_below": 0.5 - half_adjustment,
        "switch_0_to_1_above": 0.5 + half_adjustment,
        "hysteresis_width": config.switch_cost / reward_spread,
    }


def predict_belief(belief_state1: float, config: TwoStateSwitchingConfig) -> float:
    config.validate()
    p = _probability(belief_state1, "belief_state1")
    return (
        (1.0 - p) * config.state1_given_state0
        + p * config.state1_given_state1
    )


def observation_update(
    prior_state1: float,
    positive: bool,
    config: TwoStateSwitchingConfig,
) -> tuple[float, float]:
    """Return (P(observation), posterior P(state=1 | observation))."""

    config.validate()
    p = _probability(prior_state1, "prior_state1")
    if positive:
        like1 = config.positive_given_state1
        like0 = config.positive_given_state0
    else:
        like1 = 1.0 - config.positive_given_state1
        like0 = 1.0 - config.positive_given_state0
    evidence = p * like1 + (1.0 - p) * like0
    if evidence <= 0.0:
        return 0.0, p
    return evidence, (p * like1) / evidence


def solve_finite_horizon(
    config: TwoStateSwitchingConfig,
    *,
    horizon: int = 20,
    grid_size: int = 1001,
) -> list[dict[str, float | int | None]]:
    """Solve the finite-horizon belief-state control problem numerically.

    The solver uses a uniform belief grid and linear interpolation for the
    continuation value. It returns policy boundaries for every remaining
    horizon. This is a numerical experiment, not a proof of threshold
    structure for arbitrary models.
    """

    config.validate()
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    if grid_size < 101:
        raise ValueError("grid_size must be at least 101")

    grid = [i / (grid_size - 1) for i in range(grid_size)]
    next_values = {0: [0.0] * grid_size, 1: [0.0] * grid_size}
    reversed_results: list[dict[str, float | int | None]] = []

    for periods_remaining in range(1, horizon + 1):
        current_values = {0: [0.0] * grid_size, 1: [0.0] * grid_size}
        current_policy = {0: [0] * grid_size, 1: [0] * grid_size}

        for index, belief in enumerate(grid):
            predicted = predict_belief(belief, config)
            p_pos, posterior_pos = observation_update(predicted, True, config)
            p_neg, posterior_neg = observation_update(predicted, False, config)

            for previous_action in (0, 1):
                action_values: list[float] = []
                for action in (0, 1):
                    immediate = expected_reward(belief, action, config)
                    switching = config.switch_cost if action != previous_action else 0.0
                    continuation = (
                        p_pos * _interpolate(grid, next_values[action], posterior_pos)
                        + p_neg * _interpolate(grid, next_values[action], posterior_neg)
                    )
                    action_values.append(
                        immediate - switching + config.discount * continuation
                    )

                chosen = 1 if action_values[1] > action_values[0] else 0
                current_policy[previous_action][index] = chosen
                current_values[previous_action][index] = action_values[chosen]

        reversed_results.append(
            {
                "periods_remaining": periods_remaining,
                "switch_1_to_0_below": _first_action_one_boundary(
                    grid, current_policy[1]
                ),
                "switch_0_to_1_at_or_above": _first_action_one_boundary(
                    grid, current_policy[0]
                ),
            }
        )
        next_values = current_values

    return list(reversed(reversed_results))


def _first_action_one_boundary(
    grid: Sequence[float],
    policy: Sequence[int],
) -> float | None:
    for belief, action in zip(grid, policy):
        if action == 1:
            return belief
    return None


def _interpolate(grid: Sequence[float], values: Sequence[float], point: float) -> float:
    p = min(1.0, max(0.0, point))
    position = p * (len(grid) - 1)
    lo = int(position)
    hi = min(lo + 1, len(grid) - 1)
    if lo == hi:
        return values[lo]
    weight = position - lo
    return values[lo] * (1.0 - weight) + values[hi] * weight


def _probability(value: float, name: str) -> float:
    p = float(value)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"{name} must lie in [0, 1]")
    return p
