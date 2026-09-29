from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import comb
import json
from typing import Any

from .adaptation_proposal import DEFAULT_PARAMETER_POLICY

METHODOLOGY_VERSION = "asc-adaptive-shadow-v1"
STRESS_ROUND_TRIP_COST = Decimal("0.0022")

ADAPTIVE_PARAMETERS = frozenset(DEFAULT_PARAMETER_POLICY)

MIN_VALIDATION_SESSIONS = 10
MIN_COMPLETE_CANDIDATES = 100
MIN_DIFFERENTIAL_DECISIONS = 30
MIN_FORWARD_COVERAGE = Decimal("0.95")
MIN_POSITIVE_SESSION_FRACTION = Decimal("0.60")
MAX_SIGN_TEST_P = Decimal("0.10")
MIN_MEAN_UTILITY_DELTA = Decimal("0.00005")


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
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json(item) for item in value]
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(_json(value), sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def _exact_two_sided_sign_p(positive: int, negative: int) -> Decimal:
    n = positive + negative
    if n <= 0:
        return Decimal("1")
    tail = min(positive, negative)
    numerator = sum(comb(n, k) for k in range(tail + 1))
    return min(
        Decimal(2 * numerator) / Decimal(2**n),
        Decimal("1"),
    )


def _session_after(source_session: str, evaluation_session: str) -> bool:
    return bool(
        source_session
        and evaluation_session
        and evaluation_session > source_session
    )


def build_adaptive_shadow_plan(
    *,
    source_session: str,
    source_strategy_version: str,
    baseline_configuration: Mapping[str, Any],
    proposals: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze a next-session research configuration from bounded proposals.

    The plan is shadow-only. Human authorization is not required to *observe*
    what the proposed configuration would have selected, because it cannot
    reach execution.
    """

    baseline = dict(baseline_configuration)
    adaptive = dict(baseline)
    accepted: dict[str, Any] = {}
    rejected: dict[str, str] = {}

    for parameter, proposal in sorted(proposals.items()):
        if parameter not in ADAPTIVE_PARAMETERS:
            rejected[parameter] = "PARAMETER_NOT_ADAPTIVE"
            continue
        if not isinstance(proposal, Mapping):
            rejected[parameter] = "INVALID_PROPOSAL"
            continue
        if proposal.get("automatic_application_authorized") is not False:
            rejected[parameter] = "INVALID_AUTOMATIC_APPLICATION_FLAG"
            continue
        if proposal.get("execution_authority") is not False:
            rejected[parameter] = "INVALID_EXECUTION_AUTHORITY"
            continue
        if proposal.get("proposed_value") in (None, ""):
            rejected[parameter] = "MISSING_PROPOSED_VALUE"
            continue

        rule = DEFAULT_PARAMETER_POLICY[parameter]
        proposed = _d(proposal.get("proposed_value"))
        minimum = _d(rule.get("minimum"))
        maximum = _d(rule.get("maximum"))
        if (
            proposed is None
            or minimum is None
            or maximum is None
            or proposed < minimum
            or proposed > maximum
        ):
            rejected[parameter] = "PROPOSED_VALUE_OUTSIDE_POLICY"
            continue

        adaptive[parameter] = str(proposed)
        accepted[parameter] = {
            "proposal_id": proposal.get("proposal_id"),
            "old_value": baseline.get(parameter),
            "proposed_value": str(proposed),
        }

    material = {
        "methodology_version": METHODOLOGY_VERSION,
        "source_session": source_session,
        "source_strategy_version": source_strategy_version,
        "baseline_configuration": baseline,
        "adaptive_configuration": adaptive,
        "accepted_proposals": accepted,
    }
    fingerprint = _canonical_hash(material)

    return _json(
        {
            **material,
            "plan_id": "ASP-" + fingerprint[:16].upper(),
            "baseline_fingerprint": _canonical_hash(baseline),
            "adaptive_fingerprint": _canonical_hash(adaptive),
            "rejected_proposals": rejected,
            "effective_from_next_session_only": True,
            "same_session_use_forbidden": True,
            "research_only": True,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    )


def prepare_shadow_rows(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project canonical candidates into strategy-level shadow evaluation rows."""

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        features = candidate.get("features")
        features = features if isinstance(features, Mapping) else {}
        checks_root = candidate.get("checks")
        checks_root = checks_root if isinstance(checks_root, Mapping) else {}
        strategy = checks_root.get("strategy")
        if not isinstance(strategy, Mapping):
            strategy = features.get("checks")
        strategy = strategy if isinstance(strategy, Mapping) else {}

        observed_at = str(candidate.get("observed_at") or "")
        session = str(candidate.get("session") or "")
        if not session and len(observed_at) >= 10:
            session = observed_at[:10]

        current_close = _d(features.get("current_close"))
        session_vwap = _d(features.get("session_vwap"))
        above_vwap = (
            current_close is not None
            and session_vwap is not None
            and current_close > session_vwap
        )

        static_values = {
            "fast_above_slow": strategy.get("fast_above_slow"),
            "rising": strategy.get("rising"),
            "regime_ok": strategy.get("regime_ok"),
            "above_vwap": above_vwap if (
                current_close is not None and session_vwap is not None
            ) else None,
        }
        static_known = all(value is not None for value in static_values.values())
        static_pass = static_known and all(
            bool(value) for value in static_values.values()
        )

        rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_key": candidate.get("candidate_key"),
                "strategy_version_id": candidate.get("strategy_version_id"),
                "session": session,
                "symbol": candidate.get("symbol"),
                "static_gates_known": static_known,
                "static_gates_passed": static_pass,
                "static_gate_values": static_values,
                "momentum_pct": features.get("momentum_pct"),
                "vwap_edge_pct": features.get("vwap_edge_pct"),
                "confirmations_passed": features.get("confirmation_passes"),
                "forward_outcomes": candidate.get("outcomes")
                or candidate.get("forward_outcomes")
                or [],
            }
        )
    return rows


def _forward_return(
    row: Mapping[str, Any],
    horizon_minutes: int,
) -> tuple[Decimal | None, str]:
    outcomes = row.get("forward_outcomes")
    if isinstance(outcomes, Sequence) and not isinstance(
        outcomes, (str, bytes, bytearray)
    ):
        for item in outcomes:
            if not isinstance(item, Mapping):
                continue
            try:
                horizon = int(item.get("horizon_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if horizon != horizon_minutes:
                continue
            return _d(item.get("forward_return")), str(
                item.get("status") or "complete"
            )
    if isinstance(outcomes, Mapping):
        item = outcomes.get(str(horizon_minutes), outcomes.get(horizon_minutes))
        if isinstance(item, Mapping):
            return _d(item.get("forward_return")), str(
                item.get("status") or "complete"
            )
    return None, "missing"


def _selected(
    row: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> bool | None:
    if row.get("static_gates_known") is not True:
        return None
    if row.get("static_gates_passed") is not True:
        return False

    momentum = _d(row.get("momentum_pct"))
    vwap_edge = _d(row.get("vwap_edge_pct"))
    confirmations = _d(row.get("confirmations_passed"))
    min_momentum = _d(configuration.get("min_momentum_pct"))
    min_vwap = _d(configuration.get("min_vwap_edge_pct"))
    min_confirmations = _d(configuration.get("min_confirmations"))
    max_extension = _d(configuration.get("max_vwap_extension_pct"))

    values = (
        momentum,
        vwap_edge,
        confirmations,
        min_momentum,
        min_vwap,
        min_confirmations,
        max_extension,
    )
    if any(value is None for value in values):
        return None

    assert momentum is not None
    assert vwap_edge is not None
    assert confirmations is not None
    assert min_momentum is not None
    assert min_vwap is not None
    assert min_confirmations is not None
    assert max_extension is not None

    return (
        momentum >= min_momentum
        and vwap_edge >= min_vwap
        and vwap_edge <= max_extension
        and confirmations >= min_confirmations
    )


def evaluate_shadow_session(
    *,
    rows: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    evaluation_session: str,
    horizon_minutes: int = 15,
    round_trip_cost: Any = STRESS_ROUND_TRIP_COST,
) -> dict[str, Any]:
    """Compare fixed versus adaptive strategy-level selection after the session."""

    source_session = str(plan.get("source_session") or "")
    if not _session_after(source_session, evaluation_session):
        raise ValueError(
            "adaptive shadow plan may only be evaluated on a later session"
        )

    baseline = plan.get("baseline_configuration")
    adaptive = plan.get("adaptive_configuration")
    if not isinstance(baseline, Mapping) or not isinstance(adaptive, Mapping):
        raise ValueError("shadow plan is missing frozen configurations")

    cost = _d(round_trip_cost)
    if cost is None or cost < 0:
        raise ValueError("round_trip_cost must be non-negative")

    total_scope = 0
    complete = 0
    fixed_selected = 0
    adaptive_selected = 0
    differential = 0
    fixed_utility_sum = Decimal("0")
    adaptive_utility_sum = Decimal("0")
    excluded: dict[str, int] = {}

    for row in rows:
        if str(row.get("session") or "") != evaluation_session:
            continue
        total_scope += 1
        fixed = _selected(row, baseline)
        adapt = _selected(row, adaptive)
        if fixed is None or adapt is None:
            excluded["unreconstructable_strategy_inputs"] = (
                excluded.get("unreconstructable_strategy_inputs", 0) + 1
            )
            continue

        forward, status = _forward_return(row, horizon_minutes)
        if status != "complete" or forward is None:
            excluded["incomplete_forward_outcome"] = (
                excluded.get("incomplete_forward_outcome", 0) + 1
            )
            continue
        complete += 1

        fixed_selected += int(fixed)
        adaptive_selected += int(adapt)
        differential += int(fixed != adapt)

        if fixed:
            fixed_utility_sum += forward - cost
        if adapt:
            adaptive_utility_sum += forward - cost

    coverage = (
        Decimal(complete) / Decimal(total_scope)
        if total_scope > 0
        else Decimal("0")
    )
    fixed_mean = (
        fixed_utility_sum / Decimal(complete)
        if complete > 0 else Decimal("0")
    )
    adaptive_mean = (
        adaptive_utility_sum / Decimal(complete)
        if complete > 0 else Decimal("0")
    )
    delta = adaptive_mean - fixed_mean

    return _json(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "plan_id": plan.get("plan_id"),
            "source_session": source_session,
            "evaluation_session": evaluation_session,
            "source_strategy_version": plan.get("source_strategy_version"),
            "baseline_fingerprint": plan.get("baseline_fingerprint"),
            "adaptive_fingerprint": plan.get("adaptive_fingerprint"),
            "horizon_minutes": horizon_minutes,
            "round_trip_cost": cost,
            "candidate_scope": total_scope,
            "complete_candidates": complete,
            "forward_coverage": coverage,
            "fixed_selected_candidates": fixed_selected,
            "adaptive_selected_candidates": adaptive_selected,
            "differential_decisions": differential,
            "fixed_mean_candidate_utility": fixed_mean,
            "adaptive_mean_candidate_utility": adaptive_mean,
            "adaptive_minus_fixed_utility": delta,
            "no_trade_utility": "0",
            "excluded": excluded,
            "screening_only": True,
            "portfolio_backtest": False,
            "read_only": True,
            "execution_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    )


def aggregate_shadow_validation(
    session_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate whether bounded adaptation beats a fixed baseline cross-session."""

    usable: list[Mapping[str, Any]] = []
    baseline_fingerprint: str | None = None
    rejected_results = 0

    for row in session_results:
        if not isinstance(row, Mapping):
            continue
        source = str(row.get("source_session") or "")
        evaluation = str(row.get("evaluation_session") or "")
        if not _session_after(source, evaluation):
            rejected_results += 1
            continue
        fingerprint = str(row.get("baseline_fingerprint") or "")
        if not fingerprint:
            rejected_results += 1
            continue
        if baseline_fingerprint is None:
            baseline_fingerprint = fingerprint
        if fingerprint != baseline_fingerprint:
            rejected_results += 1
            continue
        usable.append(row)

    sessions = len(usable)
    candidates = sum(int(row.get("complete_candidates") or 0) for row in usable)
    scope = sum(int(row.get("candidate_scope") or 0) for row in usable)
    differential = sum(
        int(row.get("differential_decisions") or 0)
        for row in usable
    )
    coverage = (
        Decimal(candidates) / Decimal(scope)
        if scope > 0 else Decimal("0")
    )

    deltas = [
        _d(row.get("adaptive_minus_fixed_utility")) or Decimal("0")
        for row in usable
    ]
    positive = sum(1 for value in deltas if value > 0)
    negative = sum(1 for value in deltas if value < 0)
    nonzero = positive + negative
    positive_fraction = (
        Decimal(positive) / Decimal(nonzero)
        if nonzero else Decimal("0")
    )
    sign_p = _exact_two_sided_sign_p(positive, negative)
    mean_delta = (
        sum(deltas, Decimal("0")) / Decimal(len(deltas))
        if deltas else Decimal("0")
    )

    reasons: list[str] = []
    if sessions < MIN_VALIDATION_SESSIONS:
        reasons.append("INSUFFICIENT_INDEPENDENT_SESSIONS")
    if candidates < MIN_COMPLETE_CANDIDATES:
        reasons.append("INSUFFICIENT_COMPLETE_CANDIDATES")
    if differential < MIN_DIFFERENTIAL_DECISIONS:
        reasons.append("INSUFFICIENT_DIFFERENTIAL_DECISIONS")
    if coverage < MIN_FORWARD_COVERAGE:
        reasons.append("INSUFFICIENT_FORWARD_COVERAGE")
    if positive_fraction < MIN_POSITIVE_SESSION_FRACTION:
        reasons.append("WEAK_POSITIVE_SESSION_CONSISTENCY")
    if sign_p > MAX_SIGN_TEST_P:
        reasons.append("SESSION_SIGN_TEST_NOT_PASSED")
    if mean_delta < MIN_MEAN_UTILITY_DELTA:
        reasons.append("MEAN_UTILITY_DELTA_BELOW_FLOOR")
    if rejected_results:
        reasons.append("INCOMPATIBLE_SESSION_RESULTS_EXCLUDED")

    core_reasons = [
        reason for reason in reasons
        if reason != "INCOMPATIBLE_SESSION_RESULTS_EXCLUDED"
    ]
    validation_passed = not core_reasons

    return _json(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "baseline_fingerprint": baseline_fingerprint,
            "independent_sessions": sessions,
            "complete_candidates": candidates,
            "candidate_scope": scope,
            "differential_decisions": differential,
            "forward_coverage": coverage,
            "positive_sessions": positive,
            "negative_sessions": negative,
            "positive_session_fraction": positive_fraction,
            "session_sign_test_p": sign_p,
            "mean_adaptive_minus_fixed_utility": mean_delta,
            "validation_passed": validation_passed,
            "reason_codes": reasons,
            "rejected_incompatible_results": rejected_results,
            "comparison": "fixed_champion_vs_next_session_adaptive_shadow",
            "no_lookahead_enforced": True,
            "screening_only": True,
            "portfolio_backtest": False,
            "research_only": True,
            "execution_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    )
