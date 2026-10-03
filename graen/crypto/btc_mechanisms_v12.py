from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import log1p, sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

from .activity_shock_v9 import (
    BAR_MINUTES,
    Opportunity,
    _bar,
    _close,
    _f,
    _grid,
    _history,
    _return,
    _return_history,
    _z,
    build_series,
    simulate,
    summarize,
)
from .btc_trend_pullback_v11 import (
    DEVELOPMENT_END,
    DEVELOPMENT_FOLDS,
    DEVELOPMENT_START,
    UNIVERSE,
    verify_development_corpus,
)


METHODOLOGY_VERSION = "graen-btc-mechanism-tournament-v12"
CAMPAIGN_ID = "btc-mechanism-tournament-v12"
FAMILY = "btc_orthogonal_mechanism_tournament"
DELAY_ROBUSTNESS_MINUTES = 5


@dataclass(frozen=True, slots=True)
class BtcMechanismSpec:
    candidate_id: str
    mechanism: str
    fast_minutes: int
    slow_minutes: int
    trigger: float
    secondary_minutes: int
    activity_z_threshold: float
    hold_minutes: int
    cooldown_minutes: int
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_specs() -> tuple[BtcMechanismSpec, ...]:
    return (
        BtcMechanismSpec(
            "V12-BTC-COMP-60-30-A",
            "compression_breakout",
            60,
            720,
            0.55,
            30,
            0.50,
            60,
            120,
        ),
        BtcMechanismSpec(
            "V12-BTC-COMP-120-60-A",
            "compression_breakout",
            120,
            1440,
            0.60,
            60,
            0.50,
            120,
            180,
        ),
        BtcMechanismSpec(
            "V12-BTC-COMP-60-60-B",
            "compression_breakout",
            60,
            1440,
            0.65,
            60,
            1.00,
            90,
            120,
        ),
        BtcMechanismSpec(
            "V12-BTC-VNT-60-A",
            "vol_normalized_trend",
            60,
            1440,
            1.75,
            0,
            0.50,
            60,
            120,
        ),
        BtcMechanismSpec(
            "V12-BTC-VNT-120-A",
            "vol_normalized_trend",
            120,
            1440,
            2.00,
            0,
            0.25,
            120,
            180,
        ),
        BtcMechanismSpec(
            "V12-BTC-VNT-30-B",
            "vol_normalized_trend",
            30,
            720,
            1.60,
            0,
            0.75,
            60,
            90,
        ),
        BtcMechanismSpec(
            "V12-BTC-RECLAIM-30-A",
            "downshock_reclaim",
            30,
            1440,
            2.00,
            0,
            0.50,
            60,
            120,
        ),
        BtcMechanismSpec(
            "V12-BTC-RECLAIM-60-A",
            "downshock_reclaim",
            60,
            1440,
            2.25,
            0,
            0.25,
            120,
            180,
        ),
    )


def spec_from_dict(payload: Mapping[str, Any]) -> BtcMechanismSpec:
    return BtcMechanismSpec(**dict(payload))


def _activity_z(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    stamp: datetime,
    *,
    hours: int = 24,
) -> float | None:
    current = _bar(series, "BTC/USD", stamp)
    rows = _history(series, "BTC/USD", end=stamp, hours=hours)
    if current is None or len(rows) < 36:
        return None
    trade_value = log1p(max(_f(current.get("n")), 0.0))
    volume_value = log1p(max(_f(current.get("v")), 0.0))
    trade_history = [log1p(max(_f(row.get("n")), 0.0)) for row in rows]
    volume_history = [log1p(max(_f(row.get("v")), 0.0)) for row in rows]
    trade_z = _z(trade_value, trade_history)
    volume_z = _z(volume_value, volume_history)
    if trade_z is None or volume_z is None:
        return None
    return max(trade_z, volume_z)


def _prior_high(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    stamp: datetime,
    minutes: int,
) -> float | None:
    values: list[float] = []
    current = stamp - timedelta(minutes=minutes)
    while current < stamp:
        row = _bar(series, "BTC/USD", current)
        if row is not None:
            value = _f(row.get("h"))
            if value > 0:
                values.append(value)
        current += timedelta(minutes=BAR_MINUTES)
    return max(values) if values else None


def _compression_breakout_signal(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcMechanismSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    long_returns = _return_history(
        series,
        "BTC/USD",
        end=stamp,
        impulse_minutes=BAR_MINUTES,
        hours=max(spec.slow_minutes // 60, 1),
    )
    fast_count = max(spec.fast_minutes // BAR_MINUTES, 2)
    if len(long_returns) < max(fast_count, 36):
        return None
    short_returns = long_returns[-fast_count:]
    long_sigma = pstdev(long_returns)
    short_sigma = pstdev(short_returns)
    if long_sigma <= 1e-12:
        return None
    compression_ratio = short_sigma / long_sigma
    if compression_ratio > spec.trigger:
        return None
    row = _bar(series, "BTC/USD", stamp)
    prior_high = _prior_high(series, stamp, spec.secondary_minutes)
    if row is None or prior_high is None:
        return None
    close = _f(row.get("c"))
    open_ = _f(row.get("o"))
    if close <= prior_high or close <= open_:
        return None
    activity_z = _activity_z(series, stamp)
    if activity_z is None or activity_z < spec.activity_z_threshold:
        return None
    return {
        "mechanism": spec.mechanism,
        "compression_ratio": compression_ratio,
        "prior_high": prior_high,
        "breakout_close": close,
        "activity_z": activity_z,
    }


def _vol_normalized_trend_signal(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcMechanismSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    trailing = _return(series, "BTC/USD", stamp, spec.fast_minutes)
    if trailing is None or trailing <= 0:
        return None
    history = _return_history(
        series,
        "BTC/USD",
        end=stamp,
        impulse_minutes=BAR_MINUTES,
        hours=max(spec.slow_minutes // 60, 1),
    )
    if len(history) < 36:
        return None
    sigma = pstdev(history)
    if sigma <= 1e-12:
        return None
    normalized = trailing / (sigma * sqrt(max(spec.fast_minutes / BAR_MINUTES, 1.0)))
    if normalized < spec.trigger:
        return None
    row = _bar(series, "BTC/USD", stamp)
    if row is None or _f(row.get("c")) <= _f(row.get("o")):
        return None
    activity_z = _activity_z(series, stamp)
    if activity_z is None or activity_z < spec.activity_z_threshold:
        return None
    return {
        "mechanism": spec.mechanism,
        "trailing_return": trailing,
        "vol_normalized_return": normalized,
        "activity_z": activity_z,
    }


def _downshock_reclaim_signal(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcMechanismSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    shock_end = stamp - timedelta(minutes=BAR_MINUTES)
    shock = _return(series, "BTC/USD", shock_end, spec.fast_minutes)
    if shock is None or shock >= 0:
        return None
    history = _return_history(
        series,
        "BTC/USD",
        end=shock_end,
        impulse_minutes=spec.fast_minutes,
        hours=max(spec.slow_minutes // 60, 1),
    )
    shock_z = _z(shock, history)
    if shock_z is None or shock_z > -spec.trigger:
        return None
    current = _bar(series, "BTC/USD", stamp)
    previous_close = _close(series, "BTC/USD", shock_end)
    if current is None or previous_close is None:
        return None
    close = _f(current.get("c"))
    open_ = _f(current.get("o"))
    if close <= open_ or close <= previous_close:
        return None
    activity_z = _activity_z(series, stamp)
    if activity_z is None or activity_z < spec.activity_z_threshold:
        return None
    return {
        "mechanism": spec.mechanism,
        "shock_return": shock,
        "shock_z": shock_z,
        "reclaim_close": close,
        "activity_z": activity_z,
    }


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcMechanismSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    if symbol != "BTC/USD":
        return None
    if spec.mechanism == "compression_breakout":
        signal = _compression_breakout_signal(series, spec, stamp)
    elif spec.mechanism == "vol_normalized_trend":
        signal = _vol_normalized_trend_signal(series, spec, stamp)
    elif spec.mechanism == "downshock_reclaim":
        signal = _downshock_reclaim_signal(series, spec, stamp)
    else:
        raise ValueError(f"unsupported_v12_mechanism:{spec.mechanism}")
    if signal is None:
        return None
    return Opportunity(
        candidate_id=spec.candidate_id,
        symbol="BTC/USD",
        opportunity_at=stamp,
        hold_minutes=spec.hold_minutes,
        signal={"family": FAMILY, **signal},
    )


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcMechanismSpec,
    *,
    start: datetime,
    end: datetime,
    signal_cache: dict[datetime, Opportunity | None] | None = None,
) -> list[Opportunity]:
    rows: list[Opportunity] = []
    cooldown_until: datetime | None = None
    for stamp in _grid(start, end):
        if cooldown_until is not None and stamp < cooldown_until:
            continue
        if signal_cache is not None and stamp in signal_cache:
            opportunity = signal_cache[stamp]
        else:
            opportunity = opportunity_at(series, spec, "BTC/USD", stamp)
            if signal_cache is not None:
                signal_cache[stamp] = opportunity
        if opportunity is None:
            continue
        rows.append(opportunity)
        cooldown_until = stamp + timedelta(minutes=spec.cooldown_minutes)
    return rows


def evaluate_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: BtcMechanismSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
    prepared_series: Mapping[str, Mapping[datetime, Mapping[str, Any]]] | None = None,
    signal_cache: dict[datetime, Opportunity | None] | None = None,
) -> dict[str, Any]:
    warmup_hours = max(spec.slow_minutes // 60 + 2, 26)
    series = (
        prepared_series
        if prepared_series is not None
        else build_series(
            bars_by_symbol,
            start=start,
            end=end,
            warmup_hours=warmup_hours,
        )
    )
    opportunities = collect_opportunities(
        series,
        spec,
        start=start,
        end=end,
        signal_cache=signal_cache,
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
        "mechanism": spec.mechanism,
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
        int(primary["trade_count"]) >= 6
        and int(primary["independent_day_blocks"]) >= 5
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
    if int(primary["trade_count"]) < 60:
        reasons.append("development_trade_count_below_60")
    if int(primary["independent_day_blocks"]) < 40:
        reasons.append("development_independent_days_below_40")
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
        # Build the full deterministic series once per frozen candidate and
        # cache raw signal decisions by timestamp. Temporal folds still reset
        # cooldown state and use their original seeds/gates; only duplicate
        # feature computation is removed.
        warmup_hours = max(spec.slow_minutes // 60 + 2, 26)
        prepared_series = build_series(
            bars_by_symbol,
            start=DEVELOPMENT_START,
            end=DEVELOPMENT_END,
            warmup_hours=warmup_hours,
        )
        signal_cache: dict[datetime, Opportunity | None] = {}
        aggregate = evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=DEVELOPMENT_START,
            end=DEVELOPMENT_END,
            scenario="high",
            seed=121000 + index * 100,
            prepared_series=prepared_series,
            signal_cache=signal_cache,
        )
        folds: list[dict[str, Any]] = []
        for fold_index, (label, start, end) in enumerate(DEVELOPMENT_FOLDS):
            result = evaluate_candidate(
                bars_by_symbol,
                spec=spec,
                start=start,
                end=end,
                scenario="high",
                seed=122000 + index * 100 + fold_index * 10,
                prepared_series=prepared_series,
                signal_cache=signal_cache,
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
                "mechanism": spec.mechanism,
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
        "adaptive_lineage": "v5-v11 historical research already inspected; v12 is hypothesis generation only",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "NATIVE_FORWARD_SHADOW",
        "methodology_version": METHODOLOGY_VERSION,
        "family": FAMILY,
        "symbol": "BTC/USD",
        "candidate_count": len(candidate_specs()),
        "mechanisms": sorted({spec.mechanism for spec in candidate_specs()}),
        "fold_count": len(DEVELOPMENT_FOLDS),
        "results": results,
        "survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": selected_spec.to_dict() if selected_spec else None,
        "development_start": DEVELOPMENT_START.isoformat(),
        "development_end": DEVELOPMENT_END.isoformat(),
    }
