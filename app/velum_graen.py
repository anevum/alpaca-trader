from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from graen.crypto.autonomous_campaign import candidate_from_dict
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    CandidateSpec,
    build_series,
    evaluate_candidate,
)


METHODOLOGY_VERSION = "velum-graen-candidate-replay-v1"
GRAEN_CONTEXT_UNIVERSE = CONTEXT_UNIVERSE


def engineering_gate(
    spec: CandidateSpec,
    scenarios: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    high = scenarios["high"]["primary"]
    high_delay = scenarios["high"]["one_bar_delay"]
    reasons: list[str] = []

    if int(high["trade_count"]) < 20:
        reasons.append("replay_trade_count_below_20")
    if int(high["independent_day_blocks"]) < 10:
        reasons.append("replay_independent_days_below_10")
    if float(high["trades_per_day"]) < 0.30:
        reasons.append("replay_frequency_below_0.30_per_day")
    if float(high["expectancy_per_trade"]) <= 0:
        reasons.append("replay_high_cost_expectancy_nonpositive")

    profit_factor = high.get("profit_factor")
    if profit_factor is None or float(profit_factor) <= 1.0:
        reasons.append("replay_profit_factor_not_above_one")

    if float(high_delay["expectancy_per_trade"]) <= 0:
        reasons.append("replay_delay_expectancy_nonpositive")

    concentration = float(high["symbol_concentration"]["max_share"])
    if concentration > spec.concentration_limit:
        reasons.append("replay_symbol_concentration_above_limit")

    for scenario in ("low", "base"):
        primary = scenarios[scenario]["primary"]
        if float(primary["expectancy_per_trade"]) <= 0:
            reasons.append(f"replay_{scenario}_cost_expectancy_nonpositive")

    return not reasons, reasons


def replay_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
    seed: int = 91000,
) -> dict[str, Any]:
    spec = candidate_from_dict(candidate_spec)
    series = build_series(
        bars_by_symbol,
        start=start,
        end=end,
    )

    scenarios = {
        scenario: evaluate_candidate(
            series,
            spec,
            start=start,
            end=end,
            scenario=scenario,
            seed=seed + index * 20,
        )
        for index, scenario in enumerate(("low", "base", "high"))
    }
    passed, reasons = engineering_gate(spec, scenarios)

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "candidate_id": spec.candidate_id,
        "candidate_family": spec.family,
        "candidate_spec": spec.to_dict(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scenarios": scenarios,
        "engineering_gate": {
            "passed": passed,
            "reasons": reasons,
        },
        "evidence_role": "POST_HOLDOUT_ENGINEERING_REPLAY",
        "independent_confirmatory_evidence": False,
        "statistical_promotion_authority": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "risk_or_sizing_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
