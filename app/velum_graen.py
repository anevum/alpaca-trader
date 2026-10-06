from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from graen.crypto.autonomous_campaign import candidate_from_dict
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    CandidateSpec,
    build_series,
    evaluate_candidate,
)
from graen.crypto.activity_shock_v9 import (
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    evaluate_candidate as evaluate_v9_candidate,
    spec_from_dict as v9_spec_from_dict,
)
from graen.crypto.trend_pullback_v10 import (
    FAMILY as V10_FAMILY,
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    evaluate_candidate as evaluate_v10_candidate,
    spec_from_dict as v10_spec_from_dict,
)
from graen.crypto.btc_trend_pullback_v11 import (
    FAMILY as V11_FAMILY,
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    evaluate_candidate as evaluate_v11_candidate,
    spec_from_dict as v11_spec_from_dict,
)
from graen.crypto.btc_mechanisms_v12 import (
    FAMILY as V12_FAMILY,
    METHODOLOGY_VERSION as V12_METHODOLOGY_VERSION,
    evaluate_candidate as evaluate_v12_candidate,
    spec_from_dict as v12_spec_from_dict,
)
from graen.crypto.btc_hypotheses_v13 import (
    FAMILY as V13_FAMILY,
    METHODOLOGY_VERSION as V13_METHODOLOGY_VERSION,
    evaluate_candidate as evaluate_v13_candidate,
    spec_from_dict as v13_spec_from_dict,
)
from graen.crypto.btc_4h_consensus_v14_r2h import (
    FAMILY as V14_R2H_FAMILY,
    METHODOLOGY_VERSION as V14_R2H_METHODOLOGY_VERSION,
    candidate_spec as v14_r2h_candidate_spec,
    evaluate_btc_4h_consensus_replay as evaluate_v14_r2h_replay,
    spec_from_dict as v14_r2h_spec_from_dict,
)
from graen.crypto.flow_pressure import (
    METHODOLOGY_VERSION as FLOW_PRESSURE_METHODOLOGY_VERSION,
    evaluate_stage as evaluate_flow_pressure_stage,
)
from graen.crypto.cross_sectional_intraday import (
    METHODOLOGY_VERSION as CROSS_SECTIONAL_METHODOLOGY_VERSION,
    evaluate_stage as evaluate_cross_sectional_stage,
)
from graen.crypto.cross_sectional_acceleration import (
    METHODOLOGY_VERSION as ACCELERATION_METHODOLOGY_VERSION,
    evaluate_stage as evaluate_acceleration_stage,
)
from graen.engineering import (
    digest as engineering_digest,
    stamp as engineering_stamp,
    validate_spec as validate_engineering_spec,
)


METHODOLOGY_VERSION = "velum-graen-candidate-replay-v6"
GRAEN_CONTEXT_UNIVERSE = CONTEXT_UNIVERSE


def replay_fetch_contract(
    candidate_methodology: str,
    *,
    start: datetime,
    end: datetime,
) -> tuple[tuple[str, ...], datetime, datetime, str | None]:
    if candidate_methodology == V14_R2H_METHODOLOGY_VERSION:
        # R2H needs the frozen 250-day SMA and 180-day momentum warmup before
        # the bounded replay window. This warmup is input-only and is never
        # scored as replay evidence. The frozen candidate is defined on 4-hour
        # bars, so VELUM must not inherit its generic high-frequency timeframe.
        return ("BTC/USD",), start - timedelta(days=260), end, "4Hour"
    if candidate_methodology in {
        FLOW_PRESSURE_METHODOLOGY_VERSION,
        CROSS_SECTIONAL_METHODOLOGY_VERSION,
        ACCELERATION_METHODOLOGY_VERSION,
    }:
        # Compiled adaptive programs share a fixed crypto universe. VELUM
        # requests canonical five-minute bars independently from GRAEN; each
        # evaluator scores only the frozen windows in the prespec.
        return (
            ("BTC/USD", "ETH/USD", "SOL/USD"),
            start - timedelta(days=2),
            end,
            "5Min",
        )
    return (
        tuple(GRAEN_CONTEXT_UNIVERSE),
        start - timedelta(hours=8),
        end + timedelta(hours=3),
        None,
    )


def engineering_gate(
    spec: Any,
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
    candidate_methodology: str = "graen-crypto-autonomous-v8",
    start: datetime,
    end: datetime,
    seed: int = 91000,
) -> dict[str, Any]:
    if candidate_methodology in {
        FLOW_PRESSURE_METHODOLOGY_VERSION,
        CROSS_SECTIONAL_METHODOLOGY_VERSION,
        ACCELERATION_METHODOLOGY_VERSION,
    }:
        spec = dict(candidate_spec)
        spec_hash = validate_engineering_spec(spec)
        expected_start = engineering_stamp(
            spec["corpus"]["development"][0]
        )
        expected_end = engineering_stamp(spec["corpus"]["holdout"][1])
        if start != expected_start or end != expected_end:
            raise ValueError("compiled_velum_replay_window_mismatch")

        evaluate_compiled_stage = {
            FLOW_PRESSURE_METHODOLOGY_VERSION: evaluate_flow_pressure_stage,
            CROSS_SECTIONAL_METHODOLOGY_VERSION: evaluate_cross_sectional_stage,
            ACCELERATION_METHODOLOGY_VERSION: evaluate_acceleration_stage,
        }[candidate_methodology]
        predecessor = None
        stage_results: dict[str, dict[str, Any]] = {}
        reasons: list[str] = []
        for stage_name in ("development", "validation", "holdout"):
            result = evaluate_compiled_stage(
                bars_by_symbol,
                spec=spec,
                stage=stage_name,
                predecessor=predecessor,
            )
            stage_results[stage_name] = result
            if result.get("passed") is not True:
                reasons.extend(
                    stage_name + ":" + str(reason)
                    for reason in result.get("reasons") or []
                )
                break
            predecessor = {
                **result,
                "artifact_id": (
                    "velum:"
                    + stage_name
                    + ":"
                    + engineering_digest(result)[:24]
                ),
            }

        verified = (
            len(stage_results) == 3
            and all(
                row.get("passed") is True
                for row in stage_results.values()
            )
        )
        if not verified and not reasons:
            reasons.append("independent_replay_incomplete")

        return {
            "methodology_version": METHODOLOGY_VERSION,
            "candidate_methodology": candidate_methodology,
            "candidate_id": spec["hypothesis_id"],
            "candidate_family": spec["mechanism"],
            "candidate_spec": spec,
            "spec_hash": spec_hash,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "stage_results": stage_results,
            "engineering_gate": {
                "passed": verified,
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

    if candidate_methodology == V14_R2H_METHODOLOGY_VERSION:
        spec = v14_r2h_spec_from_dict(candidate_spec)
        canonical_spec = v14_r2h_candidate_spec()
        if spec.to_dict() != canonical_spec.to_dict():
            raise ValueError("r2h_velum_candidate_spec_not_frozen")
        replay = evaluate_v14_r2h_replay(
            bars_by_symbol,
            start=start,
            end=end,
        )
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "candidate_methodology": candidate_methodology,
            "candidate_id": spec.candidate_id,
            "candidate_family": V14_R2H_FAMILY,
            "candidate_spec": spec.to_dict(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "scenarios": replay["scenarios"],
            "one_bar_execution_delay": replay["one_bar_execution_delay"],
            "engineering_gate": replay["engineering_gate"],
            "evidence_role": "POST_TRANSFER_ENGINEERING_REPLAY_ONLY",
            "independent_confirmatory_evidence": False,
            "statistical_promotion_authority": False,
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
            "promotion_authorized": False,
        }
    if candidate_methodology == V13_METHODOLOGY_VERSION:
        spec = v13_spec_from_dict(candidate_spec)
        scenarios = {
            scenario: evaluate_v13_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario=scenario,
                seed=seed + index * 20,
            )
            for index, scenario in enumerate(("low", "base", "high"))
        }
        candidate_family = V13_FAMILY
    elif candidate_methodology == V12_METHODOLOGY_VERSION:
        spec = v12_spec_from_dict(candidate_spec)
        scenarios = {
            scenario: evaluate_v12_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario=scenario,
                seed=seed + index * 20,
            )
            for index, scenario in enumerate(("low", "base", "high"))
        }
        candidate_family = V12_FAMILY
    elif candidate_methodology == V11_METHODOLOGY_VERSION:
        spec = v11_spec_from_dict(candidate_spec)
        scenarios = {
            scenario: evaluate_v11_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario=scenario,
                seed=seed + index * 20,
            )
            for index, scenario in enumerate(("low", "base", "high"))
        }
        candidate_family = V11_FAMILY
    elif candidate_methodology == V10_METHODOLOGY_VERSION:
        spec = v10_spec_from_dict(candidate_spec)
        scenarios = {
            scenario: evaluate_v10_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario=scenario,
                seed=seed + index * 20,
            )
            for index, scenario in enumerate(("low", "base", "high"))
        }
        candidate_family = V10_FAMILY
    elif candidate_methodology == V9_METHODOLOGY_VERSION:
        spec = v9_spec_from_dict(candidate_spec)
        scenarios = {
            scenario: evaluate_v9_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario=scenario,
                seed=seed + index * 20,
            )
            for index, scenario in enumerate(("low", "base", "high"))
        }
        candidate_family = "activity_confirmed_momentum_continuation"
    else:
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
        candidate_family = spec.family

    passed, reasons = engineering_gate(spec, scenarios)

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "candidate_methodology": candidate_methodology,
        "candidate_id": spec.candidate_id,
        "candidate_family": candidate_family,
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
