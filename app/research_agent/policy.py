from __future__ import annotations

from dataclasses import dataclass

from .models import ExperimentWorkflowState


class SafetyPolicyViolation(ValueError):
    pass


RDR_V21_EXPERIMENT_KEY = "edge-discovery-v2-residual-downshock-rebound-v2.1"
EDGE_DISCOVERY_V1_EXPERIMENT_KEY = "edge-corpus-v1"
EDGE_DISCOVERY_V1_REJECTED_FAMILIES = frozenset(
    {
        "controlled continuation",
        "pullback reclaim",
        "compression breakout",
        "relative-strength impulse",
        "opening-breakout retest",
    }
)
PROTECTED_STAGE_TARGETS = frozenset(
    {
        ExperimentWorkflowState.DEVELOPMENT_RUNNING,
        ExperimentWorkflowState.VALIDATION_RUNNING,
        ExperimentWorkflowState.HOLDOUT_RUNNING,
    }
)


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    agent_version: str = "v1"

    def require_no_live_strategy_mutation(self, action: str) -> None:
        raise SafetyPolicyViolation(
            f"Research Agent {self.agent_version} cannot mutate live strategy: {action}"
        )

    def require_no_live_risk_or_sizing_mutation(self, action: str) -> None:
        raise SafetyPolicyViolation(
            f"Research Agent {self.agent_version} cannot mutate live risk or sizing: {action}"
        )

    def assert_experiment_mutable(self, experiment_key: str) -> None:
        if experiment_key == RDR_V21_EXPERIMENT_KEY:
            raise SafetyPolicyViolation("Residual Downshock Rebound v2.1 is terminal")

    def assert_family_may_be_proposed(self, family: str) -> None:
        if family.strip().casefold() in EDGE_DISCOVERY_V1_REJECTED_FAMILIES:
            raise SafetyPolicyViolation(
                "Edge Discovery v1 rejected families cannot be automatically revived"
            )

    def assert_no_automatic_promotion(self) -> None:
        raise SafetyPolicyViolation("automatic production promotion is forbidden")

    def assert_deterministic_gate_controls(
        self,
        *,
        deterministic_gate_passed: bool,
        semantic_recommendation: bool,
    ) -> None:
        if not deterministic_gate_passed:
            raise SafetyPolicyViolation("deterministic gate did not pass")
        if semantic_recommendation and not deterministic_gate_passed:
            raise SafetyPolicyViolation(
                "semantic recommendation cannot override a deterministic gate"
            )

    @staticmethod
    def requires_authorization(target: ExperimentWorkflowState) -> bool:
        return target in PROTECTED_STAGE_TARGETS
