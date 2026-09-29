from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .adaptation_proposal import (
    DEFAULT_PARAMETER_POLICY,
    proposal_from_counterfactual,
)
from .adaptive_shadow import (
    aggregate_shadow_validation,
    build_adaptive_shadow_plan,
    evaluate_shadow_session,
    prepare_shadow_rows,
)
from .control_state import ADAPT, RESEARCH, transition_control_state
from .graen_validation import validate_adaptive_evidence
from .nostra_session import derive_nostra_regime_timeline
from .parameter_pressure import compute_parameter_pressure
from .promotion_gate import evaluate_promotion_gate
from .strategy_health import compute_strategy_health
from .strategy_router import rank_strategy_families

METHODOLOGY_VERSION = "iren-asc-shadow-pipeline-v1"


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _prior_asc(report: Mapping[str, Any]) -> Mapping[str, Any]:
    value = report.get("adaptive_strategy_control")
    return value if isinstance(value, Mapping) else {}


def _prior_state(report: Mapping[str, Any]) -> str | None:
    asc = _prior_asc(report)
    transition = asc.get("control_transition")
    if isinstance(transition, Mapping):
        value = transition.get("state")
        if value:
            return str(value)
    value = asc.get("control_state")
    if value:
        return str(value)
    return None


def _consecutive_state(
    reports: Sequence[Mapping[str, Any]],
    state: str,
) -> int:
    count = 0
    for report in reversed([row for row in reports if isinstance(row, Mapping)]):
        value = _prior_state(report)
        if value == state:
            count += 1
        else:
            break
    return count


def _latest_shadow_plan(
    reports: Sequence[Mapping[str, Any]],
    session: str,
) -> Mapping[str, Any] | None:
    for report in reversed([row for row in reports if isinstance(row, Mapping)]):
        asc = _prior_asc(report)
        plan = asc.get("shadow_plan")
        if not isinstance(plan, Mapping):
            continue
        source = str(plan.get("source_session") or "")
        if source and source < session:
            return plan
    return None


def _prior_shadow_results(
    reports: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for report in reports:
        asc = _prior_asc(report)
        validation = asc.get("shadow_validation")
        if not isinstance(validation, Mapping):
            continue
        for row in validation.get("session_results") or []:
            if isinstance(row, Mapping):
                rows.append(row)
    unique: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("plan_id") or ""),
            str(row.get("evaluation_session") or ""),
        )
        unique[key] = row
    return list(unique.values())


def _ads_evidence_quality(
    daily_report: Mapping[str, Any],
) -> dict[str, Any]:
    ads = _m(daily_report.get("ads002_v2"))
    models = [
        row for row in (ads.get("models") or [])
        if isinstance(row, Mapping)
    ]
    sessions = 0
    candidates = 0
    trades = 0
    direct_cov = 0.0
    forward_cov = 0.0

    for model in models:
        confidence = _m(model.get("confidence"))
        samples = _m(confidence.get("samples"))
        coverage = _m(confidence.get("coverage"))
        sessions = max(sessions, int(samples.get("independent_sessions") or 0))
        candidates = max(
            candidates,
            int(samples.get("research_eligible_candidates") or 0),
        )
        trades = max(
            trades,
            int(samples.get("closed_direct_trades") or 0),
        )
        try:
            direct_cov = max(
                direct_cov,
                float(coverage.get("direct_attribution") or 0),
            )
            forward_cov = max(
                forward_cov,
                float(coverage.get("forward_15m") or 0),
            )
        except (TypeError, ValueError):
            pass

    return {
        "independent_sessions": sessions,
        "eligible_candidates": candidates,
        "directly_attributed_closed_trades": trades,
        "direct_attribution_coverage": direct_cov,
        "forward_15m_coverage": forward_cov,
    }


def _proposals(
    *,
    control_state: str,
    counterfactual_lab: Mapping[str, Any],
    current_configuration: Mapping[str, Any],
    source_strategy_version: str,
) -> dict[str, Mapping[str, Any]]:
    if control_state not in {ADAPT, RESEARCH}:
        return {}

    rolling = counterfactual_lab.get("rolling_searches")
    if not isinstance(rolling, Mapping):
        return {}

    output: dict[str, Mapping[str, Any]] = {}
    for parameter in sorted(DEFAULT_PARAMETER_POLICY):
        result = rolling.get(parameter)
        if not isinstance(result, Mapping):
            continue
        alternatives = [
            row for row in (result.get("results") or [])
            if isinstance(row, Mapping)
        ]
        if not alternatives:
            continue
        current = current_configuration.get(parameter)
        if current in (None, ""):
            continue
        proposal = proposal_from_counterfactual(
            parameter=parameter,
            current_value=current,
            alternatives=alternatives,
            control_state=control_state,
            source_strategy_version=source_strategy_version,
        )
        if proposal is not None:
            output[parameter] = proposal
    return output


def build_adaptive_control_artifact(
    *,
    session: str,
    source_strategy_version: str,
    current_configuration: Mapping[str, Any],
    daily_report: Mapping[str, Any],
    prior_daily_reports: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    counterfactual_lab: Mapping[str, Any],
    freeze_manifest: Mapping[str, Any] | None = None,
    strategy_families: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Assemble IREN ASC research state without mutating production."""

    nostra = derive_nostra_regime_timeline(candidates)
    latest_nostra = nostra.get("latest")
    latest_nostra = latest_nostra if isinstance(latest_nostra, Mapping) else None

    current_report_for_pressure = dict(daily_report)
    current_report_for_pressure["counterfactual_lab"] = dict(counterfactual_lab)
    pressure = compute_parameter_pressure(
        [*prior_daily_reports, current_report_for_pressure]
    )

    health = compute_strategy_health(
        daily_report=daily_report,
        weekly_report=None,
        nostra_state=latest_nostra,
        parameter_pressure=pressure,
    )

    previous = _prior_state(prior_daily_reports[-1]) if prior_daily_reports else None
    previous = previous or "NORMAL"
    clear_count = _consecutive_state(prior_daily_reports, "NORMAL")
    adapt_count = _consecutive_state(prior_daily_reports, "ADAPT")
    research_count = _consecutive_state(prior_daily_reports, "RESEARCH")

    requested = str(health.get("control_state") or "NORMAL")
    if requested == "NORMAL":
        clear_count += 1
    elif requested == "ADAPT":
        adapt_count += 1
    elif requested == "RESEARCH":
        research_count += 1

    transition = transition_control_state(
        previous_state=previous,
        health_snapshot=health,
        consecutive_clear_observations=clear_count,
        consecutive_adapt_observations=adapt_count,
        consecutive_research_observations=research_count,
    )
    control_state = str(transition.get("state") or requested)

    proposals = _proposals(
        control_state=control_state,
        counterfactual_lab=counterfactual_lab,
        current_configuration=current_configuration,
        source_strategy_version=source_strategy_version,
    )

    shadow_plan = None
    if proposals:
        shadow_plan = build_adaptive_shadow_plan(
            source_session=session,
            source_strategy_version=source_strategy_version,
            baseline_configuration=current_configuration,
            proposals=proposals,
        )

    session_results = _prior_shadow_results(prior_daily_reports)
    prior_plan = _latest_shadow_plan(prior_daily_reports, session)
    if prior_plan is not None:
        try:
            evaluated = evaluate_shadow_session(
                rows=prepare_shadow_rows(candidates),
                plan=prior_plan,
                evaluation_session=session,
            )
        except ValueError:
            evaluated = None
        if evaluated is not None:
            session_results.append(evaluated)

    shadow_aggregate = aggregate_shadow_validation(session_results)
    shadow_validation = {
        **shadow_aggregate,
        "session_results": session_results,
    }

    graen = validate_adaptive_evidence(
        counterfactual_lab=counterfactual_lab,
        shadow_validation=shadow_aggregate,
        freeze_manifest=freeze_manifest,
    )

    evidence_quality = _ads_evidence_quality(daily_report)
    promotion = {}
    for parameter, proposal in proposals.items():
        promotion[parameter] = evaluate_promotion_gate(
            proposal=proposal,
            source_strategy_version=source_strategy_version,
            strategy_health=health,
            shadow_validation=shadow_aggregate,
            evidence_quality=evidence_quality,
            graen_validation=graen,
            human_authorization=None,
        )

    routing = (
        rank_strategy_families(
            regime_state=latest_nostra or {"regime": "UNKNOWN"},
            families=strategy_families,
        )
        if strategy_families
        else {
            "methodology_version": "asc-strategy-router-v1",
            "status": "NOT_CONFIGURED",
            "selected_research_family": "NO_TRADE",
            "research_only": True,
            "execution_authority": False,
        }
    )

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "session": session,
        "source_strategy_version": source_strategy_version,
        "nostra": nostra,
        "parameter_pressure": pressure,
        "strategy_health": health,
        "control_transition": transition,
        "control_state": control_state,
        "adaptation_proposals": proposals,
        "shadow_plan": shadow_plan,
        "shadow_validation": shadow_validation,
        "graen_validation": graen,
        "evidence_quality": evidence_quality,
        "promotion_gates": promotion,
        "strategy_family_routing": routing,
        "read_only": True,
        "execution_authority": False,
        "risk_or_sizing_authority": False,
        "broker_calls": 0,
        "railway_changes": 0,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
