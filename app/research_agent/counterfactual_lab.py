from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import comb, sqrt
import json
from typing import Any

from .adaptation_proposal import DEFAULT_PARAMETER_POLICY

METHODOLOGY_VERSION = "asc-counterfactual-lab-v1"

PARAMETER_FEATURES = {
    "min_momentum_pct": ("momentum_pct", "minimum"),
    "min_vwap_edge_pct": ("vwap_edge_pct", "minimum"),
    "min_confirmations": ("confirmations_passed", "minimum"),
    "max_vwap_extension_pct": ("vwap_edge_pct", "maximum"),
}

MIN_INDEPENDENT_SESSIONS = 5
MIN_AFFECTED_CANDIDATES = 30
MIN_OUTCOME_COVERAGE = Decimal("0.95")
MIN_POSITIVE_SESSION_FRACTION = Decimal("0.60")
MAX_ADJUSTED_SIGN_P = Decimal("0.10")
MIN_NET_SCREENING_EFFECT = Decimal("0.0001")


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


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _exact_two_sided_sign_p(positive: int, negative: int) -> Decimal:
    n = positive + negative
    if n <= 0:
        return Decimal("1")
    tail = min(positive, negative)
    numerator = sum(comb(n, k) for k in range(tail + 1))
    p = Decimal(2 * numerator) / Decimal(2**n)
    return min(p, Decimal("1"))


def _outcome(
    row: Mapping[str, Any],
    horizon_minutes: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
    outcomes = row.get("forward_outcomes")
    if isinstance(outcomes, Mapping):
        item = outcomes.get(str(horizon_minutes), outcomes.get(horizon_minutes))
        if isinstance(item, Mapping):
            status = str(item.get("status") or "complete")
            return (
                _d(item.get("forward_return")),
                _d(item.get("max_favorable_return")),
                _d(item.get("max_adverse_return")),
                status,
            )
    if isinstance(outcomes, Sequence) and not isinstance(
        outcomes, (str, bytes, bytearray)
    ):
        for item in outcomes:
            if not isinstance(item, Mapping):
                continue
            try:
                item_horizon = int(item.get("horizon_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if item_horizon == horizon_minutes:
                return (
                    _d(item.get("forward_return")),
                    _d(item.get("max_favorable_return")),
                    _d(item.get("max_adverse_return")),
                    str(item.get("status") or "complete"),
                )
    if horizon_minutes == 15 and row.get("forward_15m") not in (None, ""):
        return _d(row.get("forward_15m")), _d(row.get("mfe_15m")), _d(row.get("mae_15m")), "complete"
    if int(row.get("horizon_minutes") or 0) == horizon_minutes:
        return (
            _d(row.get("forward_return")),
            _d(row.get("max_favorable_return")),
            _d(row.get("max_adverse_return")),
            str(row.get("status") or "complete"),
        )
    return None, None, None, "missing"


def _feature(row: Mapping[str, Any], feature_name: str) -> Decimal | None:
    gate_inputs = row.get("gate_inputs")
    if isinstance(gate_inputs, Mapping):
        value = _d(gate_inputs.get(feature_name))
        if value is not None:
            return value
    features = row.get("features")
    if isinstance(features, Mapping):
        value = _d(features.get(feature_name))
        if value is not None:
            return value
    return _d(row.get(feature_name))


def _other_gates_passed(row: Mapping[str, Any]) -> bool | None:
    gate_inputs = row.get("gate_inputs")
    if isinstance(gate_inputs, Mapping) and "other_gates_passed" in gate_inputs:
        return bool(gate_inputs.get("other_gates_passed"))
    if "other_gates_passed" in row:
        return bool(row.get("other_gates_passed"))
    return None


def _passes(value: Decimal, threshold: Decimal, comparator: str) -> bool:
    if comparator == "minimum":
        return value >= threshold
    if comparator == "maximum":
        return value <= threshold
    raise ValueError(f"unknown comparator: {comparator}")


def frozen_search_grid(
    parameter: str,
    current_value: Any,
    *,
    cadence: str = "daily",
    policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Decimal, ...]:
    rules = dict(policy or DEFAULT_PARAMETER_POLICY)
    if parameter not in PARAMETER_FEATURES or parameter not in rules:
        raise ValueError(f"parameter is not counterfactual-enabled: {parameter}")
    rule = rules[parameter]
    current = _d(current_value)
    if current is None:
        raise ValueError("current_value must be numeric")
    minimum = _d(rule.get("minimum"))
    maximum = _d(rule.get("maximum"))
    if minimum is None or maximum is None:
        raise ValueError("adaptive policy is missing bounds")
    key = "daily_step" if str(cadence).lower() == "daily" else "weekly_step"
    step = _d(rule.get(key))
    if step is None or step <= 0:
        raise ValueError("adaptive policy step must be positive")

    values: set[Decimal] = set()
    for multiplier in (-2, -1, 1, 2):
        value = current + step * multiplier
        value = min(max(value, minimum), maximum)
        if value != current:
            values.add(value)
    return tuple(sorted(values))


def _search_id(
    *,
    parameter: str,
    current_value: Decimal,
    grid: Sequence[Decimal],
    horizon_minutes: int,
    sessions: Sequence[str],
) -> str:
    payload = {
        "methodology": METHODOLOGY_VERSION,
        "parameter": parameter,
        "current_value": str(current_value),
        "grid": [str(value) for value in grid],
        "horizon_minutes": horizon_minutes,
        "sessions": sorted(set(sessions)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "CF-" + sha256(encoded.encode()).hexdigest()[:16].upper()


def _evaluate_alternative(
    *,
    rows: Sequence[Mapping[str, Any]],
    feature_name: str,
    comparator: str,
    current: Decimal,
    alternative: Decimal,
    horizon_minutes: int,
    round_trip_cost: Decimal | None,
    comparison_count: int,
) -> dict[str, Any]:
    eligible = 0
    affected = 0
    complete = 0
    added = 0
    removed = 0
    effects: list[Decimal] = []
    mfes: list[Decimal] = []
    maes: list[Decimal] = []
    session_effects: dict[str, list[Decimal]] = defaultdict(list)
    excluded: dict[str, int] = defaultdict(int)

    for row in rows:
        other = _other_gates_passed(row)
        if other is None:
            excluded["missing_other_gates_passed"] += 1
            continue
        if not other:
            excluded["other_gate_failed"] += 1
            continue

        observed = _feature(row, feature_name)
        if observed is None:
            excluded["missing_target_feature"] += 1
            continue

        session = str(row.get("session") or "")
        if not session:
            excluded["missing_session"] += 1
            continue

        eligible += 1
        baseline_selected = _passes(observed, current, comparator)
        alternative_selected = _passes(observed, alternative, comparator)
        if baseline_selected == alternative_selected:
            continue

        affected += 1
        if alternative_selected and not baseline_selected:
            added += 1
            direction = Decimal("1")
        elif baseline_selected and not alternative_selected:
            removed += 1
            direction = Decimal("-1")
        else:
            continue

        forward, mfe, mae, status = _outcome(row, horizon_minutes)
        if status != "complete" or forward is None:
            excluded["incomplete_forward_outcome"] += 1
            continue
        complete += 1

        net = None if round_trip_cost is None else forward - round_trip_cost
        effect = direction * (net if net is not None else forward)
        effects.append(effect)
        session_effects[session].append(effect)
        if mfe is not None:
            mfes.append(mfe)
        if mae is not None:
            maes.append(mae)

    changed = affected
    coverage = Decimal(complete) / Decimal(changed) if changed > 0 else Decimal("0")

    per_session = []
    for session in sorted(session_effects):
        mean_effect = _mean(session_effects[session])
        if mean_effect is None:
            continue
        per_session.append(
            {
                "session": session,
                "changed_candidates": len(session_effects[session]),
                "mean_screening_effect": mean_effect,
            }
        )

    positive = sum(1 for row in per_session if row["mean_screening_effect"] > 0)
    negative = sum(1 for row in per_session if row["mean_screening_effect"] < 0)
    nonzero_sessions = positive + negative
    positive_fraction = (
        Decimal(positive) / Decimal(nonzero_sessions)
        if nonzero_sessions
        else Decimal("0")
    )
    raw_p = _exact_two_sided_sign_p(positive, negative)
    adjusted_p = min(raw_p * Decimal(max(comparison_count, 1)), Decimal("1"))
    effect = _mean(effects) or Decimal("0")

    sample_component = Decimal(str(min(
        sqrt(min(len(per_session) / 10.0, 1.0) * min(changed / 100.0, 1.0)),
        1.0,
    )))
    coverage_component = min(coverage, Decimal("1"))
    consistency_component = min(positive_fraction, Decimal("1"))
    multiplicity_component = max(Decimal("0"), Decimal("1") - adjusted_p)
    confidence = (
        Decimal("0.30") * sample_component
        + Decimal("0.25") * coverage_component
        + Decimal("0.25") * consistency_component
        + Decimal("0.20") * multiplicity_component
    )

    cost_status = "APPLIED" if round_trip_cost is not None else "MISSING"
    reason_codes: list[str] = []
    if len(per_session) < MIN_INDEPENDENT_SESSIONS:
        reason_codes.append("INSUFFICIENT_INDEPENDENT_SESSIONS")
    if changed < MIN_AFFECTED_CANDIDATES:
        reason_codes.append("INSUFFICIENT_AFFECTED_CANDIDATES")
    if coverage < MIN_OUTCOME_COVERAGE:
        reason_codes.append("INSUFFICIENT_FORWARD_COVERAGE")
    if positive_fraction < MIN_POSITIVE_SESSION_FRACTION:
        reason_codes.append("WEAK_CROSS_SESSION_CONSISTENCY")
    if adjusted_p > MAX_ADJUSTED_SIGN_P:
        reason_codes.append("MULTIPLICITY_ADJUSTED_SIGN_TEST_NOT_PASSED")
    if effect < MIN_NET_SCREENING_EFFECT:
        reason_codes.append("NET_SCREENING_EFFECT_BELOW_FLOOR")
    if round_trip_cost is None:
        reason_codes.append("TRANSACTION_COST_MODEL_REQUIRED")

    validity_passed = not reason_codes
    if not validity_passed:
        confidence = min(confidence, Decimal("0.49"))

    return {
        "requested_value": alternative,
        "baseline_value": current,
        "affected_candidates": changed,
        "added_candidates": added,
        "removed_candidates": removed,
        "eligible_target_gate_candidates": eligible,
        "complete_affected_outcomes": complete,
        "outcome_coverage": coverage,
        "independent_sessions": len(per_session),
        "positive_sessions": positive,
        "negative_sessions": negative,
        "positive_session_fraction": positive_fraction,
        "raw_sign_test_p": raw_p,
        "adjusted_sign_test_p": adjusted_p,
        "expected_improvement": effect,
        "screening_effect_sum": sum(effects, Decimal("0")),
        "average_mfe": _mean(mfes),
        "average_mae": _mean(maes),
        "confidence": min(max(confidence, Decimal("0")), Decimal("1")),
        "evidence_score": max(Decimal("0"), effect * Decimal("10000")) * confidence,
        "selection_bias_status": "CONTROLLED",
        "dependence_status": "CONTROLLED",
        "cost_status": cost_status,
        "validity_passed": validity_passed,
        "reason_codes": reason_codes,
        "session_effects": per_session,
        "excluded": dict(excluded),
        "interpretation": "screening counterfactual only; not a realized-trade backtest",
    }


def run_counterfactual_search(
    *,
    rows: Sequence[Mapping[str, Any]],
    parameter: str,
    current_value: Any,
    horizon_minutes: int = 15,
    round_trip_cost: Any | None,
    cadence: str = "daily",
    policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate a frozen one-parameter search with session-level inference.

    The lab changes exactly one gate while holding an explicit
    other_gates_passed indicator fixed. It does not simulate capital,
    position limits, order sequencing, fills, or portfolio interactions.
    """

    if parameter not in PARAMETER_FEATURES:
        raise ValueError(f"parameter is not counterfactual-enabled: {parameter}")
    current = _d(current_value)
    if current is None:
        raise ValueError("current_value must be numeric")
    cost = _d(round_trip_cost) if round_trip_cost is not None else None
    if cost is not None and cost < 0:
        raise ValueError("round_trip_cost cannot be negative")

    feature_name, comparator = PARAMETER_FEATURES[parameter]
    grid = frozen_search_grid(
        parameter,
        current,
        cadence=cadence,
        policy=policy,
    )
    sessions = [
        str(row.get("session"))
        for row in rows
        if isinstance(row, Mapping) and row.get("session")
    ]
    search_id = _search_id(
        parameter=parameter,
        current_value=current,
        grid=grid,
        horizon_minutes=horizon_minutes,
        sessions=sessions,
    )

    results = []
    for alternative in grid:
        evaluated = _evaluate_alternative(
            rows=rows,
            feature_name=feature_name,
            comparator=comparator,
            current=current,
            alternative=alternative,
            horizon_minutes=horizon_minutes,
            round_trip_cost=cost,
            comparison_count=len(grid),
        )
        counterfactual_material = {
            "search_id": search_id,
            "parameter": parameter,
            "alternative": str(alternative),
        }
        encoded = json.dumps(
            counterfactual_material,
            sort_keys=True,
            separators=(",", ":"),
        )
        evaluated["counterfactual_id"] = (
            "CFA-" + sha256(encoded.encode()).hexdigest()[:16].upper()
        )
        results.append(evaluated)

    eligible = [row for row in results if row["validity_passed"]]
    eligible.sort(
        key=lambda row: (
            row["evidence_score"],
            row["expected_improvement"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else None

    search_ledger = {
        "search_id": search_id,
        "methodology_version": METHODOLOGY_VERSION,
        "hypothesis_family": f"{parameter}-one-gate-counterfactual",
        "parameter": parameter,
        "feature_name": feature_name,
        "comparator": comparator,
        "current_value": current,
        "candidate_values": list(grid),
        "horizon_minutes": horizon_minutes,
        "round_trip_cost": cost,
        "comparison_count": len(grid),
        "multiple_testing_method": "bonferroni_exact_session_sign_test",
        "dependence_method": "session_level_effects",
        "selection_rule": "highest evidence_score then expected_improvement among validity-passed alternatives",
        "grid_frozen_before_evaluation": True,
        "sessions_present": sorted(set(sessions)),
        "selected_counterfactual_id": (
            selected.get("counterfactual_id") if selected else None
        ),
        "research_only": True,
    }

    return _json(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "search_ledger": search_ledger,
            "results": results,
            "selected": selected,
            "validity_passed": selected is not None,
            "read_only": True,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    )


def _bool_check(checks: Mapping[str, Any], key: str) -> bool | None:
    if key not in checks:
        return None
    return bool(checks.get(key))


def prepare_counterfactual_rows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    parameter: str,
) -> list[dict[str, Any]]:
    """Project canonical RHEN candidates into one-gate screening rows.

    The projector uses only strategy-level checks that were calculated for the
    candidate at decision time. It intentionally does not claim that downstream
    market-quality, allocation, risk, or execution gates would have passed.
    """

    if parameter not in PARAMETER_FEATURES:
        raise ValueError(f"parameter is not counterfactual-enabled: {parameter}")

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        features = candidate.get("features")
        features = features if isinstance(features, Mapping) else {}
        checks_root = candidate.get("checks")
        checks_root = checks_root if isinstance(checks_root, Mapping) else {}
        strategy_checks = checks_root.get("strategy")
        if not isinstance(strategy_checks, Mapping):
            strategy_checks = features.get("checks")
        strategy_checks = (
            strategy_checks if isinstance(strategy_checks, Mapping) else {}
        )

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

        common = {
            "fast_above_slow": _bool_check(strategy_checks, "fast_above_slow"),
            "rising": _bool_check(strategy_checks, "rising"),
            "momentum_ok": _bool_check(strategy_checks, "momentum_ok"),
            "vwap_ok": _bool_check(strategy_checks, "vwap_ok"),
            "vwap_extension_ok": _bool_check(
                strategy_checks, "vwap_extension_ok"
            ),
            "confirmations_ok": _bool_check(
                strategy_checks, "confirmations_ok"
            ),
            "regime_ok": _bool_check(strategy_checks, "regime_ok"),
        }

        required_by_parameter = {
            "min_momentum_pct": (
                "fast_above_slow",
                "rising",
                "vwap_ok",
                "vwap_extension_ok",
                "confirmations_ok",
                "regime_ok",
            ),
            "min_vwap_edge_pct": (
                "fast_above_slow",
                "rising",
                "momentum_ok",
                "vwap_extension_ok",
                "confirmations_ok",
                "regime_ok",
            ),
            "min_confirmations": (
                "fast_above_slow",
                "rising",
                "momentum_ok",
                "vwap_ok",
                "vwap_extension_ok",
                "regime_ok",
            ),
            "max_vwap_extension_pct": (
                "fast_above_slow",
                "rising",
                "momentum_ok",
                "vwap_ok",
                "confirmations_ok",
                "regime_ok",
            ),
        }[parameter]

        known = all(common.get(name) is not None for name in required_by_parameter)
        other_gates_passed = known and all(
            bool(common.get(name)) for name in required_by_parameter
        )
        if parameter == "min_vwap_edge_pct":
            known = known and current_close is not None and session_vwap is not None
            other_gates_passed = bool(other_gates_passed and above_vwap)

        gate_inputs = {
            "momentum_pct": features.get("momentum_pct"),
            "vwap_edge_pct": features.get("vwap_edge_pct"),
            "confirmations_passed": features.get("confirmation_passes"),
            "other_gates_passed": other_gates_passed if known else None,
        }

        rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_key": candidate.get("candidate_key"),
                "strategy_version_id": candidate.get("strategy_version_id"),
                "session": session,
                "symbol": candidate.get("symbol"),
                "gate_inputs": gate_inputs,
                "forward_outcomes": candidate.get("outcomes")
                or candidate.get("forward_outcomes")
                or [],
                "screening_scope": "strategy_gates_only",
                "downstream_execution_gates_replayed": False,
            }
        )
    return rows


def aggregate_counterfactual_searches(
    searches: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Aggregate compatible per-session ASC-005 searches across sessions."""

    compatible = [
        search for search in searches
        if isinstance(search, Mapping)
        and isinstance(search.get("search_ledger"), Mapping)
    ]
    if not compatible:
        return None

    first_ledger = compatible[0]["search_ledger"]
    signature = (
        first_ledger.get("parameter"),
        str(first_ledger.get("current_value")),
        tuple(str(x) for x in first_ledger.get("candidate_values") or []),
        int(first_ledger.get("horizon_minutes") or 0),
        str(first_ledger.get("round_trip_cost")),
        first_ledger.get("multiple_testing_method"),
        first_ledger.get("dependence_method"),
    )
    filtered = []
    for search in compatible:
        ledger = search["search_ledger"]
        candidate_signature = (
            ledger.get("parameter"),
            str(ledger.get("current_value")),
            tuple(str(x) for x in ledger.get("candidate_values") or []),
            int(ledger.get("horizon_minutes") or 0),
            str(ledger.get("round_trip_cost")),
            ledger.get("multiple_testing_method"),
            ledger.get("dependence_method"),
        )
        if candidate_signature == signature:
            filtered.append(search)
    if not filtered:
        return None

    by_value: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for search in filtered:
        for row in search.get("results") or []:
            if isinstance(row, Mapping) and row.get("requested_value") is not None:
                by_value[str(row.get("requested_value"))].append(row)

    comparison_count = max(len(by_value), 1)
    aggregated_results = []
    for requested_value in sorted(by_value, key=lambda value: Decimal(value)):
        parts = by_value[requested_value]
        affected = sum(int(part.get("affected_candidates") or 0) for part in parts)
        complete = sum(
            int(part.get("complete_affected_outcomes") or 0) for part in parts
        )
        added = sum(int(part.get("added_candidates") or 0) for part in parts)
        removed = sum(int(part.get("removed_candidates") or 0) for part in parts)
        effect_sum = sum(
            (_d(part.get("screening_effect_sum")) or Decimal("0"))
            for part in parts
        )
        effect = (
            effect_sum / Decimal(complete)
            if complete > 0
            else Decimal("0")
        )
        coverage = (
            Decimal(complete) / Decimal(affected)
            if affected > 0
            else Decimal("0")
        )

        sessions: dict[str, Decimal] = {}
        session_counts: dict[str, int] = {}
        for part in parts:
            for item in part.get("session_effects") or []:
                if not isinstance(item, Mapping):
                    continue
                session = str(item.get("session") or "")
                mean_effect = _d(item.get("mean_screening_effect"))
                count = int(item.get("changed_candidates") or 0)
                if not session or mean_effect is None or count <= 0:
                    continue
                sessions[session] = sessions.get(session, Decimal("0")) + (
                    mean_effect * Decimal(count)
                )
                session_counts[session] = session_counts.get(session, 0) + count

        session_effects = []
        for session in sorted(sessions):
            count = session_counts[session]
            mean_effect = sessions[session] / Decimal(count)
            session_effects.append(
                {
                    "session": session,
                    "changed_candidates": count,
                    "mean_screening_effect": mean_effect,
                }
            )

        positive = sum(
            1 for item in session_effects
            if item["mean_screening_effect"] > 0
        )
        negative = sum(
            1 for item in session_effects
            if item["mean_screening_effect"] < 0
        )
        nonzero = positive + negative
        positive_fraction = (
            Decimal(positive) / Decimal(nonzero)
            if nonzero else Decimal("0")
        )
        raw_p = _exact_two_sided_sign_p(positive, negative)
        adjusted_p = min(
            raw_p * Decimal(comparison_count),
            Decimal("1"),
        )

        sample_component = Decimal(str(min(
            sqrt(
                min(len(session_effects) / 10.0, 1.0)
                * min(affected / 100.0, 1.0)
            ),
            1.0,
        )))
        confidence = (
            Decimal("0.30") * sample_component
            + Decimal("0.25") * min(coverage, Decimal("1"))
            + Decimal("0.25") * min(positive_fraction, Decimal("1"))
            + Decimal("0.20") * max(
                Decimal("0"),
                Decimal("1") - adjusted_p,
            )
        )

        reasons = []
        if len(session_effects) < MIN_INDEPENDENT_SESSIONS:
            reasons.append("INSUFFICIENT_INDEPENDENT_SESSIONS")
        if affected < MIN_AFFECTED_CANDIDATES:
            reasons.append("INSUFFICIENT_AFFECTED_CANDIDATES")
        if coverage < MIN_OUTCOME_COVERAGE:
            reasons.append("INSUFFICIENT_FORWARD_COVERAGE")
        if positive_fraction < MIN_POSITIVE_SESSION_FRACTION:
            reasons.append("WEAK_CROSS_SESSION_CONSISTENCY")
        if adjusted_p > MAX_ADJUSTED_SIGN_P:
            reasons.append("MULTIPLICITY_ADJUSTED_SIGN_TEST_NOT_PASSED")
        if effect < MIN_NET_SCREENING_EFFECT:
            reasons.append("NET_SCREENING_EFFECT_BELOW_FLOOR")
        if str(first_ledger.get("round_trip_cost")) in {"None", ""}:
            reasons.append("TRANSACTION_COST_MODEL_REQUIRED")

        validity = not reasons
        if not validity:
            confidence = min(confidence, Decimal("0.49"))

        aggregated_results.append(
            {
                "requested_value": requested_value,
                "baseline_value": str(first_ledger.get("current_value")),
                "affected_candidates": affected,
                "added_candidates": added,
                "removed_candidates": removed,
                "complete_affected_outcomes": complete,
                "outcome_coverage": coverage,
                "independent_sessions": len(session_effects),
                "positive_sessions": positive,
                "negative_sessions": negative,
                "positive_session_fraction": positive_fraction,
                "raw_sign_test_p": raw_p,
                "adjusted_sign_test_p": adjusted_p,
                "expected_improvement": effect,
                "screening_effect_sum": effect_sum,
                "confidence": min(max(confidence, Decimal("0")), Decimal("1")),
                "evidence_score": max(
                    Decimal("0"),
                    effect * Decimal("10000"),
                ) * confidence,
                "selection_bias_status": "CONTROLLED",
                "dependence_status": "CONTROLLED",
                "cost_status": (
                    "MISSING"
                    if str(first_ledger.get("round_trip_cost")) in {"None", ""}
                    else "APPLIED"
                ),
                "validity_passed": validity,
                "reason_codes": reasons,
                "session_effects": session_effects,
                "interpretation": (
                    "rolling screening counterfactual only; "
                    "not a realized-trade backtest"
                ),
            }
        )

    eligible = [row for row in aggregated_results if row["validity_passed"]]
    eligible.sort(
        key=lambda row: (
            row["evidence_score"],
            row["expected_improvement"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else None

    combined_sessions = sorted({
        session
        for search in filtered
        for session in (
            (search.get("search_ledger") or {}).get("sessions_present") or []
        )
    })
    ledger = dict(first_ledger)
    ledger.update(
        {
            "search_id": "CFR-" + sha256(
                json.dumps(
                    {
                        "signature": [str(x) for x in signature],
                        "sessions": combined_sessions,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:16].upper(),
            "aggregation": "cross_session_from_frozen_daily_searches",
            "sessions_present": combined_sessions,
            "selected_counterfactual_id": None,
        }
    )

    return _json(
        {
            "methodology_version": METHODOLOGY_VERSION,
            "search_ledger": ledger,
            "results": aggregated_results,
            "selected": selected,
            "validity_passed": selected is not None,
            "read_only": True,
            "execution_authority": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    )
