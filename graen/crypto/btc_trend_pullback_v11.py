from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Mapping, Sequence

from .activity_shock_v9 import (
    BAR_MINUTES,
    _grid,
    build_series,
    simulate,
    summarize,
)
from .trend_pullback_v10 import (
    DELAY_ROBUSTNESS_MINUTES,
    Opportunity,
    TrendPullbackSpec,
    candidate_specs as v10_candidate_specs,
    opportunity_at,
)


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-btc-trend-pullback-forward-v11"
FAMILY = "btc_activity_confirmed_trend_pullback_recovery"
CAMPAIGN_ID = "btc-trend-pullback-forward-v11"
UNIVERSE = ("BTC/USD",)

# All of this history has already been used by prior ANEVUM crypto research.
# v11 therefore treats it as DEVELOPMENT ONLY. It is not independent
# validation or holdout evidence. Fresh confirmation begins only after the
# candidate is frozen and activated in native forward shadow.
DEVELOPMENT_START = datetime(2025, 1, 1, tzinfo=UTC)
DEVELOPMENT_END = datetime(2026, 10, 1, tzinfo=UTC)
DEVELOPMENT_FOLDS: tuple[tuple[str, datetime, datetime], ...] = (
    ("2025-Q1", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC)),
    ("2025-Q2", datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 7, 1, tzinfo=UTC)),
    ("2025-Q3", datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)),
    ("2025-Q4", datetime(2025, 10, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ("2026-Q1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC)),
    ("2026-Q2", datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)),
    ("2026-Q3", datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
)


def candidate_specs() -> tuple[TrendPullbackSpec, ...]:
    specs: list[TrendPullbackSpec] = []
    for source in v10_candidate_specs():
        specs.append(
            replace(
                source,
                candidate_id=source.candidate_id.replace("V10-TPR", "V11-BTC-TPR"),
                concentration_limit=1.0,
            )
        )
    return tuple(specs)


def spec_from_dict(payload: Mapping[str, Any]) -> TrendPullbackSpec:
    return TrendPullbackSpec(**dict(payload))


def verify_development_corpus(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime = DEVELOPMENT_START,
    end: datetime = DEVELOPMENT_END,
    min_fraction: float = 0.80,
) -> dict[str, Any]:
    expected = max(int((end - start).total_seconds() // (BAR_MINUTES * 60)), 1)
    count = 0
    for row in bars_by_symbol.get("BTC/USD", ()):
        raw = row.get("t")
        if raw is None:
            continue
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        stamp = stamp.astimezone(UTC)
        if start <= stamp < end:
            count += 1
    fraction = count / expected
    report = {
        "symbol": "BTC/USD",
        "expected_bars": expected,
        "count": count,
        "fraction": fraction,
        "minimum_fraction": min_fraction,
        "passed": fraction >= min_fraction,
        "evidence_role": "DEVELOPMENT_ONLY",
    }
    if fraction < min_fraction:
        raise ValueError("v11_btc_development_corpus_incomplete:BTC/USD")
    return report


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: TrendPullbackSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    cooldown: datetime | None = None
    for stamp in _grid(start, end):
        if cooldown is not None and cooldown > stamp:
            continue
        opportunity = opportunity_at(series, spec, "BTC/USD", stamp)
        if opportunity is None:
            continue
        output.append(opportunity)
        from datetime import timedelta
        cooldown = stamp + timedelta(minutes=spec.cooldown_minutes)
    return output


def evaluate_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: TrendPullbackSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    warmup_hours = max(spec.activity_lookback_hours + 2, 26)
    series = build_series(
        bars_by_symbol,
        start=start,
        end=end,
        warmup_hours=warmup_hours,
    )
    opportunities = collect_opportunities(
        series,
        spec,
        start=start,
        end=end,
    )
    primary = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )
    return {
        "candidate": spec.to_dict(),
        "family": FAMILY,
        "symbol": "BTC/USD",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": summarize(delayed, start=start, end=end, seed=seed + 1),
        "opportunity_sample": [row.to_dict() for row in opportunities[:5]],
    }


def _fold_pass(result: Mapping[str, Any]) -> bool:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    pf = primary.get("profit_factor")
    return bool(
        int(primary["trade_count"]) >= 5
        and int(primary["independent_day_blocks"]) >= 4
        and float(primary["expectancy_per_trade"]) > 0
        and pf is not None
        and float(pf) > 1.0
        and float(delayed["expectancy_per_trade"]) > 0
    )


def development_gate(
    aggregate: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    primary = aggregate["primary"]
    delayed = aggregate["one_bar_delay"]
    pf = primary.get("profit_factor")
    fold_passes = sum(1 for row in folds if bool(row.get("passed")))
    reasons: list[str] = []
    if int(primary["trade_count"]) < 40:
        reasons.append("development_trade_count_below_40")
    if int(primary["independent_day_blocks"]) < 30:
        reasons.append("development_independent_days_below_30")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_expectancy_nonpositive")
    if pf is None or float(pf) <= 1.0:
        reasons.append("development_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("development_delay_expectancy_nonpositive")
    if fold_passes < 5:
        reasons.append("development_positive_temporal_folds_below_5_of_7")
    return not reasons, reasons


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    survivors: list[dict[str, Any]] = []
    for index, spec in enumerate(candidate_specs()):
        aggregate = evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=DEVELOPMENT_START,
            end=DEVELOPMENT_END,
            scenario="high",
            seed=111000 + index * 100,
        )
        folds: list[dict[str, Any]] = []
        for fold_index, (label, start, end) in enumerate(DEVELOPMENT_FOLDS):
            result = evaluate_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario="high",
                seed=112000 + index * 100 + fold_index * 10,
            )
            folds.append({
                "label": label,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "passed": _fold_pass(result),
                "result": result,
            })
        passed, reasons = development_gate(aggregate, folds)
        primary = aggregate["primary"]
        fold_passes = sum(1 for row in folds if row["passed"])
        selection_score = (
            float(primary["expectancy_per_trade"])
            * sqrt(max(int(primary["trade_count"]), 1))
            * (fold_passes / len(DEVELOPMENT_FOLDS))
        )
        results[spec.candidate_id] = {
            "aggregate": aggregate,
            "folds": folds,
            "passed": passed,
            "reasons": reasons,
            "positive_temporal_folds": fold_passes,
            "selection_score": selection_score,
        }
        if passed:
            survivors.append({
                "candidate_id": spec.candidate_id,
                "selection_score": selection_score,
            })

    selected = (
        max(survivors, key=lambda row: (row["selection_score"], row["candidate_id"]))
        if survivors else None
    )
    selected_id = str(selected["candidate_id"]) if selected else None
    selected_spec = next(
        (spec for spec in candidate_specs() if spec.candidate_id == selected_id),
        None,
    )
    return {
        "stage": "HISTORICAL_DEVELOPMENT",
        "evidence_role": "DEVELOPMENT_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "NATIVE_FORWARD_SHADOW",
        "methodology_version": METHODOLOGY_VERSION,
        "family": FAMILY,
        "symbol": "BTC/USD",
        "candidate_count": len(candidate_specs()),
        "fold_count": len(DEVELOPMENT_FOLDS),
        "results": results,
        "survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": selected_spec.to_dict() if selected_spec else None,
        "development_start": DEVELOPMENT_START.isoformat(),
        "development_end": DEVELOPMENT_END.isoformat(),
    }
