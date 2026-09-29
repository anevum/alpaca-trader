from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any

from .adaptation_proposal import DEFAULT_PARAMETER_POLICY

METHODOLOGY_VERSION = "asc-promotion-gate-v1"

MIN_INDEPENDENT_SESSIONS = 10
MIN_ELIGIBLE_CANDIDATES = 100
MIN_DIRECT_CLOSED_TRADES = 30
MIN_DIRECT_ATTRIBUTION_COVERAGE = Decimal("0.995")
MIN_FORWARD_15M_COVERAGE = Decimal("0.95")

ALLOWED_PARAMETERS = frozenset(DEFAULT_PARAMETER_POLICY)


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(_json(value), sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def _validate_human_authorization(
    authorization: Mapping[str, Any] | None,
    *,
    proposal: Mapping[str, Any],
    source_strategy_version: str,
) -> tuple[bool, list[str]]:
    if authorization is None:
        return False, ["HUMAN_AUTHORIZATION_REQUIRED"]

    reasons: list[str] = []
    required = (
        "reference",
        "authorized_by",
        "authorized_at",
        "proposal_id",
        "source_strategy_version",
        "parameter",
        "proposed_value",
    )
    for key in required:
        if authorization.get(key) in (None, ""):
            reasons.append(f"AUTHORIZATION_MISSING_{key.upper()}")

    if str(authorization.get("proposal_id") or "") != str(
        proposal.get("proposal_id") or ""
    ):
        reasons.append("AUTHORIZATION_PROPOSAL_MISMATCH")
    if str(authorization.get("source_strategy_version") or "") != (
        source_strategy_version
    ):
        reasons.append("AUTHORIZATION_STRATEGY_VERSION_MISMATCH")
    if str(authorization.get("parameter") or "") != str(
        proposal.get("parameter") or ""
    ):
        reasons.append("AUTHORIZATION_PARAMETER_MISMATCH")
    if str(authorization.get("proposed_value") or "") != str(
        proposal.get("proposed_value") or ""
    ):
        reasons.append("AUTHORIZATION_VALUE_MISMATCH")

    return not reasons, reasons


def evaluate_promotion_gate(
    *,
    proposal: Mapping[str, Any],
    source_strategy_version: str,
    strategy_health: Mapping[str, Any],
    shadow_validation: Mapping[str, Any],
    evidence_quality: Mapping[str, Any],
    graen_validation: Mapping[str, Any],
    human_authorization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether one bounded proposal may enter operator activation review.

    This function never mutates configuration, deploys code, writes broker
    state, or treats prior/general authorization as proposal-specific approval.
    """

    reasons: list[str] = []
    parameter = str(proposal.get("parameter") or "")

    if parameter not in ALLOWED_PARAMETERS:
        reasons.append("PARAMETER_NOT_ELIGIBLE_FOR_BOUNDED_ADAPTATION")
    if proposal.get("authorization_required") is not True:
        reasons.append("PROPOSAL_MUST_REQUIRE_AUTHORIZATION")
    if proposal.get("automatic_application_authorized") is not False:
        reasons.append("AUTOMATIC_APPLICATION_MUST_BE_DISABLED")
    if proposal.get("execution_authority") is not False:
        reasons.append("PROPOSAL_EXECUTION_AUTHORITY_MUST_BE_FALSE")
    if proposal.get("live_configuration_changed") is not False:
        reasons.append("PROPOSAL_ALREADY_MUTATED_LIVE_CONFIGURATION")
    if str(proposal.get("source_strategy_version") or "") != (
        source_strategy_version
    ):
        reasons.append("PROPOSAL_STRATEGY_VERSION_MISMATCH")

    rule = DEFAULT_PARAMETER_POLICY.get(parameter)
    proposed_value = _d(proposal.get("proposed_value"))
    if rule is not None and proposed_value is not None:
        minimum = _d(rule.get("minimum"))
        maximum = _d(rule.get("maximum"))
        if (
            minimum is None
            or maximum is None
            or proposed_value < minimum
            or proposed_value > maximum
        ):
            reasons.append("PROPOSED_VALUE_OUTSIDE_ADAPTIVE_POLICY")
    elif parameter in ALLOWED_PARAMETERS:
        reasons.append("INVALID_PROPOSED_VALUE")

    evidence_integrity = (
        strategy_health.get("dimensions", {})
        if isinstance(strategy_health.get("dimensions"), Mapping)
        else {}
    )
    integrity = evidence_integrity.get("evidence_integrity", {})
    integrity = integrity if isinstance(integrity, Mapping) else {}
    if str(integrity.get("status") or "") != "HEALTHY":
        reasons.append("EVIDENCE_INTEGRITY_NOT_HEALTHY")

    if shadow_validation.get("validation_passed") is not True:
        reasons.append("ASC007_SHADOW_VALIDATION_NOT_PASSED")
    if int(shadow_validation.get("independent_sessions") or 0) < (
        MIN_INDEPENDENT_SESSIONS
    ):
        reasons.append("INSUFFICIENT_ASC007_SESSIONS")
    if int(shadow_validation.get("complete_candidates") or 0) < (
        MIN_ELIGIBLE_CANDIDATES
    ):
        reasons.append("INSUFFICIENT_ASC007_CANDIDATES")
    if int(shadow_validation.get("differential_decisions") or 0) < 30:
        reasons.append("INSUFFICIENT_ASC007_DIFFERENTIAL_DECISIONS")
    shadow_coverage = _d(shadow_validation.get("forward_coverage"))
    if shadow_coverage is None or shadow_coverage < MIN_FORWARD_15M_COVERAGE:
        reasons.append("INSUFFICIENT_ASC007_FORWARD_COVERAGE")

    independent_sessions = int(
        evidence_quality.get("independent_sessions") or 0
    )
    eligible_candidates = int(
        evidence_quality.get("eligible_candidates") or 0
    )
    direct_trades = int(
        evidence_quality.get("directly_attributed_closed_trades") or 0
    )
    direct_coverage = _d(
        evidence_quality.get("direct_attribution_coverage")
    )
    forward_coverage = _d(
        evidence_quality.get("forward_15m_coverage")
    )

    if independent_sessions < MIN_INDEPENDENT_SESSIONS:
        reasons.append("INSUFFICIENT_INDEPENDENT_SESSIONS")
    if eligible_candidates < MIN_ELIGIBLE_CANDIDATES:
        reasons.append("INSUFFICIENT_ELIGIBLE_CANDIDATES")
    if direct_trades < MIN_DIRECT_CLOSED_TRADES:
        reasons.append("INSUFFICIENT_DIRECTLY_ATTRIBUTED_CLOSED_TRADES")
    if (
        direct_coverage is None
        or direct_coverage < MIN_DIRECT_ATTRIBUTION_COVERAGE
    ):
        reasons.append("DIRECT_ATTRIBUTION_COVERAGE_GATE_FAILED")
    if (
        forward_coverage is None
        or forward_coverage < MIN_FORWARD_15M_COVERAGE
    ):
        reasons.append("FORWARD_15M_COVERAGE_GATE_FAILED")

    if graen_validation.get("frozen_validation_passed") is not True:
        reasons.append("GRAEN_FROZEN_VALIDATION_REQUIRED")
    if graen_validation.get("walk_forward_passed") is not True:
        reasons.append("GRAEN_WALK_FORWARD_REQUIRED")
    if graen_validation.get("selection_bias_status") not in {
        "PASS",
        "CONTROLLED",
    }:
        reasons.append("GRAEN_SELECTION_BIAS_GATE_FAILED")
    if graen_validation.get("dependence_status") not in {
        "PASS",
        "CONTROLLED",
    }:
        reasons.append("GRAEN_DEPENDENCE_GATE_FAILED")
    if graen_validation.get("multiple_testing_status") not in {
        "PASS",
        "CONTROLLED",
    }:
        reasons.append("GRAEN_MULTIPLE_TESTING_GATE_FAILED")

    eligible_for_human_authorization = not reasons
    authorization_valid = False
    authorization_reasons: list[str] = []
    if eligible_for_human_authorization:
        authorization_valid, authorization_reasons = (
            _validate_human_authorization(
                human_authorization,
                proposal=proposal,
                source_strategy_version=source_strategy_version,
            )
        )

    activation_contract = None
    if eligible_for_human_authorization and authorization_valid:
        contract_material = {
            "proposal_id": proposal.get("proposal_id"),
            "source_strategy_version": source_strategy_version,
            "parameter": parameter,
            "old_value": proposal.get("old_value"),
            "proposed_value": proposal.get("proposed_value"),
            "rollback_value": proposal.get("rollback_value"),
            "authorization_reference": human_authorization.get("reference"),
            "authorized_by": human_authorization.get("authorized_by"),
            "authorized_at": human_authorization.get("authorized_at"),
            "asc007_baseline_fingerprint": shadow_validation.get(
                "baseline_fingerprint"
            ),
            "promotion_methodology": METHODOLOGY_VERSION,
        }
        activation_contract = {
            **contract_material,
            "contract_id": "ACA-" + _hash(contract_material)[:16].upper(),
            "deployment_requires_separate_operator_action": True,
            "production_mutation_performed": False,
            "broker_call_performed": False,
            "railway_change_performed": False,
            "execution_authority": False,
        }

    return _json(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "proposal_id": proposal.get("proposal_id"),
            "source_strategy_version": source_strategy_version,
            "parameter": parameter,
            "eligible_for_human_authorization": (
                eligible_for_human_authorization
            ),
            "human_authorization_present": human_authorization is not None,
            "human_authorization_valid": authorization_valid,
            "gate_reason_codes": reasons,
            "authorization_reason_codes": authorization_reasons,
            "activation_contract": activation_contract,
            "operator_activation_eligible": activation_contract is not None,
            "automatic_promotion_authorized": False,
            "deployment_authorized_by_gate": False,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "production_mutation_performed": False,
            "broker_call_performed": False,
            "railway_change_performed": False,
        }
    )
