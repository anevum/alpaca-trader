from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import log1p, sqrt
from typing import Any, Mapping, Sequence

from .activity_shock_v9 import (
    BAR_MINUTES,
    UNIVERSE,
    _bar,
    _f,
    _grid,
    _history,
    _return,
    _return_history,
    _z,
    build_series,
    simulate,
    summarize,
    verify_stage_corpus,
)


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-crypto-trend-pullback-v10"
FAMILY = "activity_confirmed_trend_pullback_recovery"
DELAY_ROBUSTNESS_MINUTES = 5


@dataclass(frozen=True, slots=True)
class TrendPullbackSpec:
    candidate_id: str
    trend_minutes: int
    pullback_minutes: int
    activity_lookback_hours: int
    trend_min_return: float
    pullback_z_threshold: float
    activity_z_threshold: float
    close_location_min: float
    hold_minutes: int
    cooldown_minutes: int
    concentration_limit: float = 0.70

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Opportunity:
    candidate_id: str
    symbol: str
    opportunity_at: datetime
    hold_minutes: int
    signal: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["opportunity_at"] = self.opportunity_at.isoformat()
        return payload


def candidate_specs() -> tuple[TrendPullbackSpec, ...]:
    return (
        TrendPullbackSpec(
            "V10-TPR-60-10-A",
            60, 10, 8, 0.0040, 1.00, 0.50, 0.65, 30, 45,
        ),
        TrendPullbackSpec(
            "V10-TPR-120-15-A",
            120, 15, 12, 0.0060, 1.00, 0.50, 0.65, 45, 60,
        ),
        TrendPullbackSpec(
            "V10-TPR-120-30-B",
            120, 30, 12, 0.0080, 1.25, 0.75, 0.70, 60, 90,
        ),
        TrendPullbackSpec(
            "V10-TPR-240-15-A",
            240, 15, 24, 0.0100, 1.00, 0.50, 0.65, 60, 90,
        ),
        TrendPullbackSpec(
            "V10-TPR-240-30-B",
            240, 30, 24, 0.0125, 1.25, 0.75, 0.70, 90, 120,
        ),
        TrendPullbackSpec(
            "V10-TPR-360-30-C",
            360, 30, 24, 0.0150, 1.50, 0.50, 0.75, 120, 150,
        ),
    )


def spec_from_dict(payload: Mapping[str, Any]) -> TrendPullbackSpec:
    return TrendPullbackSpec(**dict(payload))


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: TrendPullbackSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    trend_return = _return(series, symbol, stamp, spec.trend_minutes)
    pullback_return = _return(series, symbol, stamp, spec.pullback_minutes)
    if (
        trend_return is None
        or pullback_return is None
        or trend_return < spec.trend_min_return
        or pullback_return >= 0
    ):
        return None

    pullback_history = _return_history(
        series,
        symbol,
        end=stamp,
        impulse_minutes=spec.pullback_minutes,
        hours=spec.activity_lookback_hours,
    )
    pullback_z = _z(pullback_return, pullback_history)
    if (
        pullback_z is None
        or pullback_z > -spec.pullback_z_threshold
    ):
        return None

    current = _bar(series, symbol, stamp)
    if current is None:
        return None
    open_price = _f(current.get("o"))
    high = _f(current.get("h"))
    low = _f(current.get("l"))
    close = _f(current.get("c"))
    vwap = _f(current.get("vw"))
    if min(open_price, high, low, close) <= 0 or high <= low:
        return None

    close_location = (close - low) / (high - low)
    if close <= open_price or close_location < spec.close_location_min:
        return None
    if vwap > 0 and close < vwap:
        return None

    rows = _history(
        series,
        symbol,
        end=stamp,
        hours=spec.activity_lookback_hours,
    )
    if len(rows) < 36:
        return None
    trade_value = log1p(max(_f(current.get("n")), 0.0))
    volume_value = log1p(max(_f(current.get("v")), 0.0))
    trade_history = [log1p(max(_f(row.get("n")), 0.0)) for row in rows]
    volume_history = [log1p(max(_f(row.get("v")), 0.0)) for row in rows]
    trade_z = _z(trade_value, trade_history)
    volume_z = _z(volume_value, volume_history)
    if trade_z is None or volume_z is None:
        return None
    activity_z = max(trade_z, volume_z)
    if activity_z < spec.activity_z_threshold:
        return None

    return Opportunity(
        candidate_id=spec.candidate_id,
        symbol=symbol,
        opportunity_at=stamp,
        hold_minutes=spec.hold_minutes,
        signal={
            "family": FAMILY,
            "trend_minutes": spec.trend_minutes,
            "trend_return": trend_return,
            "pullback_minutes": spec.pullback_minutes,
            "pullback_return": pullback_return,
            "pullback_z": pullback_z,
            "trade_count_z": trade_z,
            "volume_z": volume_z,
            "activity_z": activity_z,
            "close_location": close_location,
            "bar_vwap": vwap,
        },
    )


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: TrendPullbackSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    cooldown: dict[str, datetime] = {}
    for stamp in _grid(start, end):
        for symbol in UNIVERSE:
            if cooldown.get(
                symbol,
                datetime.min.replace(tzinfo=UTC),
            ) > stamp:
                continue
            opportunity = opportunity_at(
                series,
                spec,
                symbol,
                stamp,
            )
            if opportunity is None:
                continue
            output.append(opportunity)
            cooldown[symbol] = (
                stamp + timedelta(minutes=spec.cooldown_minutes)
            )
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
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize(
            primary,
            start=start,
            end=end,
            seed=seed,
        ),
        "one_bar_delay": summarize(
            delayed,
            start=start,
            end=end,
            seed=seed + 1,
        ),
        "opportunity_sample": [
            row.to_dict() for row in opportunities[:5]
        ],
    }


def development_gate(
    result: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 30:
        reasons.append("development_trade_count_below_30")
    if int(primary["independent_day_blocks"]) < 20:
        reasons.append("development_independent_days_below_20")
    if float(primary["trades_per_day"]) < 0.35:
        reasons.append("development_frequency_below_0.35_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_expectancy_nonpositive")
    pf = primary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("development_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("development_delay_expectancy_nonpositive")
    return not reasons, reasons


def validation_gate(
    spec: TrendPullbackSpec,
    result: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 20:
        reasons.append("validation_trade_count_below_20")
    if int(primary["independent_day_blocks"]) < 12:
        reasons.append("validation_independent_days_below_12")
    if float(primary["trades_per_day"]) < 0.35:
        reasons.append("validation_frequency_below_0.35_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("validation_expectancy_nonpositive")
    if float(primary["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("validation_dependence_p_above_0.05")
    pf = primary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("validation_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("validation_delay_expectancy_nonpositive")
    if (
        float(primary["symbol_concentration"]["max_share"])
        > spec.concentration_limit
    ):
        reasons.append("validation_symbol_concentration_above_limit")
    return not reasons, reasons


def holdout_gate(
    spec: TrendPullbackSpec,
    scenarios: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    high = scenarios["high"]["primary"]
    high_delay = scenarios["high"]["one_bar_delay"]
    reasons: list[str] = []
    if int(high["trade_count"]) < 20:
        reasons.append("holdout_trade_count_below_20")
    if int(high["independent_day_blocks"]) < 12:
        reasons.append("holdout_independent_days_below_12")
    if float(high["trades_per_day"]) < 0.30:
        reasons.append("holdout_frequency_below_0.30_per_day")
    if float(high["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_high_cost_expectancy_nonpositive")
    if float(high["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("holdout_dependence_p_above_0.05")
    pf = high.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("holdout_profit_factor_not_above_one")
    if float(high_delay["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_delay_expectancy_nonpositive")
    if (
        float(high["symbol_concentration"]["max_share"])
        > spec.concentration_limit
    ):
        reasons.append("holdout_symbol_concentration_above_limit")
    for scenario in ("low", "base"):
        if (
            float(
                scenarios[scenario]["primary"][
                    "expectancy_per_trade"
                ]
            )
            <= 0
        ):
            reasons.append(
                f"holdout_{scenario}_cost_expectancy_nonpositive"
            )
    return not reasons, reasons


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    survivors: list[dict[str, Any]] = []
    for index, spec in enumerate(candidate_specs()):
        result = evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=start,
            end=end,
            scenario="high",
            seed=101000 + index * 10,
        )
        passed, reasons = development_gate(result)
        primary = result["primary"]
        score = (
            float(primary["expectancy_per_trade"])
            * sqrt(max(int(primary["trade_count"]), 1))
        )
        results[spec.candidate_id] = {
            "result": result,
            "passed": passed,
            "reasons": reasons,
            "selection_score": score,
        }
        if passed:
            survivors.append({
                "candidate_id": spec.candidate_id,
                "selection_score": score,
            })
    selected = (
        max(
            survivors,
            key=lambda row: (
                row["selection_score"],
                row["candidate_id"],
            ),
        )
        if survivors
        else None
    )
    selected_id = (
        str(selected["candidate_id"]) if selected else None
    )
    selected_spec = next(
        (
            spec
            for spec in candidate_specs()
            if spec.candidate_id == selected_id
        ),
        None,
    )
    return {
        "stage": "DEVELOPMENT",
        "family": FAMILY,
        "methodology_version": METHODOLOGY_VERSION,
        "candidate_count": len(candidate_specs()),
        "results": results,
        "survivors": [
            row["candidate_id"] for row in survivors
        ],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": (
            selected_spec.to_dict()
            if selected_spec
            else None
        ),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def evaluate_validation(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    spec = spec_from_dict(candidate_spec)
    result = evaluate_candidate(
        bars_by_symbol,
        spec=spec,
        start=start,
        end=end,
        scenario="high",
        seed=seed,
    )
    passed, reasons = validation_gate(spec, result)
    return {
        "stage": "VALIDATION",
        "candidate_id": spec.candidate_id,
        "result": result,
        "passed": passed,
        "reasons": reasons,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def evaluate_holdout(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    spec = spec_from_dict(candidate_spec)
    scenarios = {
        scenario: evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=start,
            end=end,
            scenario=scenario,
            seed=seed + index * 20,
        )
        for index, scenario in enumerate(
            ("low", "base", "high")
        )
    }
    passed, reasons = holdout_gate(spec, scenarios)
    return {
        "stage": "HOLDOUT",
        "candidate_id": spec.candidate_id,
        "scenarios": scenarios,
        "passed": passed,
        "reasons": reasons,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
