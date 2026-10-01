from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Mapping, Sequence

from .research_v7 import (
    CandidateSpec,
    CONTEXT_UNIVERSE,
    VALIDATION_ALPHA,
    _development_gate,
    _holdout_gate,
    _validation_gate,
    build_series,
    evaluate_candidate,
)


UTC = timezone.utc
METHODOLOGY_PREFIX = "graen-crypto-autonomous-v8"
CAMPAIGN_ID = "crypto-autonomous-campaign-v1"
MAX_GENERATIONS_PER_EPOCH = 6

# Each epoch owns a chronological development -> validation -> holdout sequence.
# Validation and holdout are never fetched during hypothesis generation.
# These ranges intentionally precede the already inspected v7/v6/v5 ranges.
CAMPAIGN_EPOCHS: tuple[dict[str, str], ...] = (
    {
        "epoch": "HISTORICAL-A",
        "development_start": "2024-01-01T00:00:00+00:00",
        "validation_start": "2024-07-01T00:00:00+00:00",
        "holdout_start": "2024-10-01T00:00:00+00:00",
        "holdout_end": "2025-01-01T00:00:00+00:00",
    },
    {
        "epoch": "HISTORICAL-B",
        "development_start": "2025-01-01T00:00:00+00:00",
        "validation_start": "2025-02-15T00:00:00+00:00",
        "holdout_start": "2025-03-15T00:00:00+00:00",
        "holdout_end": "2025-05-01T00:00:00+00:00",
    },
)


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def epoch_contract(epoch_index: int) -> dict[str, Any]:
    if epoch_index < 0 or epoch_index >= len(CAMPAIGN_EPOCHS):
        raise ValueError("campaign_epoch_out_of_range")
    row = CAMPAIGN_EPOCHS[epoch_index]
    return {
        "epoch_index": epoch_index,
        "epoch": row["epoch"],
        "development_start": _stamp(row["development_start"]),
        "validation_start": _stamp(row["validation_start"]),
        "holdout_start": _stamp(row["holdout_start"]),
        "holdout_end": _stamp(row["holdout_end"]),
    }


def methodology_version(epoch_index: int, generation: int, stage: str) -> str:
    return (
        f"{METHODOLOGY_PREFIX}-e{epoch_index + 1:02d}"
        f"-g{generation:02d}-{stage.lower()}"
    )


def _cycle(values: Sequence[Any], generation: int, offset: int = 0) -> Any:
    if not values:
        raise ValueError("empty_campaign_parameter_cycle")
    return values[(generation - 1 + offset) % len(values)]


def adaptive_candidate_specs(generation: int) -> tuple[CandidateSpec, ...]:
    if generation < 1 or generation > MAX_GENERATIONS_PER_EPOCH:
        raise ValueError("campaign_generation_out_of_range")

    holds = (30, 45, 60, 90, 120, 180)
    scans = (5, 10, 15, 20, 30, 60)
    lookbacks = (15, 30, 45, 60, 90, 120)
    leader_thresholds = (0.0025, 0.0035, 0.0045, 0.0055, 0.0065, 0.0075)
    lag_gaps = (0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0035)
    market_thresholds = (0.0010, 0.0020, 0.0030, 0.0040, 0.0050, 0.0060)

    hold = int(_cycle(holds, generation))
    scan = int(_cycle(scans, generation))
    lookback = int(_cycle(lookbacks, generation))
    leader_threshold = float(_cycle(leader_thresholds, generation))
    lag_gap = float(_cycle(lag_gaps, generation))
    market_threshold = float(_cycle(market_thresholds, generation))
    next_hold = int(_cycle(holds, generation, 2))
    next_lookback = int(_cycle(lookbacks, generation, 2))

    prefix = f"A8-G{generation:02d}"
    return (
        CandidateSpec(
            f"{prefix}-LL-BTC",
            "adaptive_cross_asset_lead_lag",
            "lead_lag",
            ("ETH/USD", "SOL/USD"),
            hold,
            scan,
            lookback,
            0.80,
            leader_symbol="BTC/USD",
            leader_threshold=leader_threshold,
            lag_gap_threshold=lag_gap,
            min_target_return=-0.0025,
            max_target_return=max(0.0025, leader_threshold),
            min_breadth_positive=3,
        ),
        CandidateSpec(
            f"{prefix}-LL-ETH-SOL",
            "adaptive_cross_asset_lead_lag",
            "lead_lag",
            ("SOL/USD",),
            next_hold,
            scan,
            next_lookback,
            1.00,
            leader_symbol="ETH/USD",
            leader_threshold=max(0.0030, leader_threshold - 0.0005),
            lag_gap_threshold=max(0.0010, lag_gap),
            min_target_return=-0.0025,
            max_target_return=max(0.0030, leader_threshold),
            min_breadth_positive=3,
            require_btc_nonnegative=True,
        ),
        CandidateSpec(
            f"{prefix}-BREADTH",
            "adaptive_broad_market_laggard_response",
            "breadth_laggard",
            ("ETH/USD", "SOL/USD"),
            next_hold,
            max(10, scan),
            max(30, lookback),
            0.80,
            lag_gap_threshold=max(0.0015, lag_gap),
            min_target_return=-0.0030,
            min_breadth_positive=4,
            market_threshold=market_threshold,
        ),
        CandidateSpec(
            f"{prefix}-CAL-WEEKDAY",
            "adaptive_calendar_regime_drift",
            "calendar",
            ("BTC/USD", "ETH/USD", "SOL/USD"),
            hold,
            max(15, scan),
            max(30, lookback),
            0.55,
            min_target_return=-0.0030,
            min_breadth_positive=3,
            market_threshold=0.0,
            calendar_scope="weekday",
            calendar_start_hour_utc=int(_cycle((0, 6, 12, 18), generation)),
        ),
        CandidateSpec(
            f"{prefix}-CAL-WEEKEND",
            "adaptive_calendar_regime_drift",
            "calendar",
            ("BTC/USD", "ETH/USD", "SOL/USD"),
            next_hold,
            max(15, scan),
            max(30, next_lookback),
            0.55,
            min_target_return=-0.0030,
            min_breadth_positive=3,
            market_threshold=0.0,
            calendar_scope="weekend",
            calendar_start_hour_utc=int(_cycle((0, 6, 12, 18), generation, 2)),
        ),
    )


def candidate_from_dict(payload: Mapping[str, Any]) -> CandidateSpec:
    fields = dict(payload)
    fields["targets"] = tuple(str(value) for value in fields.get("targets") or ())
    return CandidateSpec(**fields)


def prespecification(epoch_index: int, generation: int) -> dict[str, Any]:
    contract = epoch_contract(epoch_index)
    return {
        "schema_version": "graen.autonomous_crypto_campaign.v1",
        "campaign_id": CAMPAIGN_ID,
        "methodology_version": methodology_version(
            epoch_index,
            generation,
            "development",
        ),
        "epoch": contract["epoch"],
        "epoch_index": epoch_index,
        "generation": generation,
        "max_generations_per_epoch": MAX_GENERATIONS_PER_EPOCH,
        "candidate_registry": [
            spec.to_dict() for spec in adaptive_candidate_specs(generation)
        ],
        "selection_rule": (
            "first generation with a development survivor; within that generation "
            "select maximum expectancy_per_trade * sqrt(trade_count)"
        ),
        "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT"],
        "data_access_contract": {
            "development": [
                contract["development_start"].isoformat(),
                contract["validation_start"].isoformat(),
            ],
            "validation": [
                contract["validation_start"].isoformat(),
                contract["holdout_start"].isoformat(),
            ],
            "holdout": [
                contract["holdout_start"].isoformat(),
                contract["holdout_end"].isoformat(),
            ],
            "validation_unseen_until_candidate_frozen": True,
            "holdout_unseen_until_validation_pass": True,
            "failed_validation_or_holdout_burns_epoch": True,
        },
        "authority": {
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "model_execution_enabled": False,
        },
    }


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    generation: int,
) -> dict[str, Any]:
    specs = adaptive_candidate_specs(generation)
    series = build_series(bars_by_symbol, start=start, end=end)
    results: dict[str, dict[str, Any]] = {}
    gates: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        result = evaluate_candidate(
            series,
            spec,
            start=start,
            end=end,
            scenario="high",
            seed=88000 + generation * 100 + index * 10,
        )
        results[spec.candidate_id] = result
        passed, reasons = _development_gate(result)
        primary = result["primary"]
        score = float(primary["expectancy_per_trade"]) * sqrt(
            max(int(primary["trade_count"]), 1)
        )
        row = {
            "candidate_id": spec.candidate_id,
            "family": spec.family,
            "passed": passed,
            "reasons": reasons,
            "selection_score": score,
        }
        gates.append(row)
        if passed:
            survivors.append(row)

    selected_row = (
        max(
            survivors,
            key=lambda row: (
                float(row["selection_score"]),
                str(row["candidate_id"]),
            ),
        )
        if survivors
        else None
    )
    selected_id = str(selected_row["candidate_id"]) if selected_row else None
    selected_spec = next(
        (spec for spec in specs if spec.candidate_id == selected_id),
        None,
    )
    return {
        "stage": "DEVELOPMENT",
        "opened": True,
        "generation": generation,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candidate_count": len(specs),
        "results": results,
        "gates": gates,
        "survivors": [str(row["candidate_id"]) for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": (
            selected_spec.to_dict() if selected_spec is not None else None
        ),
        "selected_development_result": (
            results[selected_id] if selected_id is not None else None
        ),
    }


def evaluate_validation(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    candidate_spec: Mapping[str, Any],
    development_result: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    spec = candidate_from_dict(candidate_spec)
    series = build_series(bars_by_symbol, start=start, end=end)
    validation = evaluate_candidate(
        series,
        spec,
        start=start,
        end=end,
        scenario="high",
        seed=seed,
    )
    p_value = float(
        validation["primary"]["dependence_adjusted_null"]["p_value"]
    )
    passed, reasons = _validation_gate(
        spec,
        development_result,
        validation,
        multiplicity_rejected=p_value <= VALIDATION_ALPHA,
    )
    return {
        "stage": "VALIDATION",
        "opened": True,
        "candidate_id": spec.candidate_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "result": validation,
        "passed": passed,
        "reasons": reasons,
    }


def evaluate_holdout(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    candidate_spec: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    spec = candidate_from_dict(candidate_spec)
    series = build_series(bars_by_symbol, start=start, end=end)
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
    passed, reasons = _holdout_gate(spec, scenarios)
    return {
        "stage": "HOLDOUT",
        "opened": True,
        "candidate_id": spec.candidate_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scenarios": scenarios,
        "passed": passed,
        "reasons": reasons,
    }


def next_epoch_index(epoch_index: int) -> int | None:
    candidate = epoch_index + 1
    return candidate if candidate < len(CAMPAIGN_EPOCHS) else None


def stage_metadata(
    *,
    epoch_index: int,
    generation: int,
    candidate_spec: Mapping[str, Any] | None = None,
    development_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "campaign_id": CAMPAIGN_ID,
        "campaign_epoch": epoch_index,
        "campaign_generation": generation,
    }
    if candidate_spec is not None:
        payload["candidate_spec"] = dict(candidate_spec)
    if development_result is not None:
        payload["development_result"] = dict(development_result)
    return payload
