from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from .research_agent.counterfactual_lab import (
    COST_MODEL_VERSION,
    PARAMETER_FEATURES,
    STRESS_ROUND_TRIP_COST_V1,
    aggregate_counterfactual_searches,
    prepare_counterfactual_rows,
    run_counterfactual_search,
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def identity(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def build_counterfactual_parity(
    *,
    session: date,
    candidates: list[dict[str, Any]],
    current_version: str,
    baseline_parameters: dict[str, Any],
    prior_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    scoped_candidates = [
        row
        for row in candidates
        if isinstance(row, dict)
        and (
            not current_version
            or str(row.get("strategy_version_id") or "") == current_version
        )
    ]

    session_searches: dict[str, Any] = {}
    for parameter, current_value in baseline_parameters.items():
        if parameter not in PARAMETER_FEATURES or current_value in (None, ""):
            continue
        rows = prepare_counterfactual_rows(
            scoped_candidates,
            parameter=parameter,
        )
        session_searches[parameter] = run_counterfactual_search(
            rows=rows,
            parameter=parameter,
            current_value=current_value,
            horizon_minutes=15,
            round_trip_cost=STRESS_ROUND_TRIP_COST_V1,
            cadence="daily",
        )

    rolling_searches: dict[str, Any] = {}
    for parameter, current_search in session_searches.items():
        searches: list[dict[str, Any]] = []
        for report in prior_reports:
            if (
                current_version
                and str(report.get("strategy_version_id") or "")
                != current_version
            ):
                continue
            lab = report.get("counterfactual_lab")
            if not isinstance(lab, dict):
                continue
            prior = (lab.get("session_searches") or {}).get(parameter)
            if isinstance(prior, dict):
                searches.append(prior)
        searches.append(current_search)
        rolling = aggregate_counterfactual_searches(searches)
        if rolling is not None:
            rolling_searches[parameter] = rolling

    ready = sorted(
        parameter
        for parameter, result in rolling_searches.items()
        if isinstance(result, dict) and result.get("validity_passed") is True
    )

    frozen_inputs = {
        "session": session.isoformat(),
        "strategy_version_id": current_version or None,
        "baseline_parameters": baseline_parameters,
        "candidates": scoped_candidates,
        "prior_counterfactual_history": [
            {
                "session": report.get("session"),
                "strategy_version_id": report.get("strategy_version_id"),
                "counterfactual_lab": report.get("counterfactual_lab"),
            }
            for report in prior_reports
            if isinstance(report, dict)
        ],
    }

    lab = {
        "methodology_version": "asc-counterfactual-lab-v1",
        "session": session.isoformat(),
        "strategy_version_id": current_version or None,
        "baseline_parameters": baseline_parameters,
        "candidate_rows_received": len(candidates),
        "candidate_rows_in_strategy_scope": len(scoped_candidates),
        "history_window_calendar_days": 35,
        "cost_model": {
            "version": COST_MODEL_VERSION,
            "round_trip_cost": str(STRESS_ROUND_TRIP_COST_V1),
            "round_trip_bps": "22",
            "role": "conservative research stress floor",
        },
        "session_searches": session_searches,
        "rolling_searches": rolling_searches,
        "proposal_ready_parameters": ready,
        "screening_only": True,
        "counterfactual_not_realized_trades": True,
        "read_only": True,
        "execution_authority": False,
        "risk_or_sizing_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }

    return {
        "input_identity": identity(frozen_inputs),
        "output_identity": identity(lab),
        "frozen_inputs": frozen_inputs,
        "counterfactual_lab": lab,
    }
