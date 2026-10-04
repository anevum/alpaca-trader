from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite, log1p
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

from graen.crypto.activity_shock_v9 import (
    BAR_MINUTES,
    Opportunity,
    _bar,
    _grid,
    _open,
    build_series,
)

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-intraday-flow-v14-r2h"
CAMPAIGN_ID = "v14-r2h-btc-intraday-flow-pressure"
FAMILY = "btc_intraday_activity_flow_pressure"
UNIVERSE = ("BTC/USD",)

BAR_SCREEN_START = datetime(2026, 1, 1, tzinfo=UTC)
ADAPTIVE_RECENT_START = datetime(2026, 6, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

ROUND_TRIP_COSTS = {
    "maker_base_30bp": 0.0030,
    "mixed_stress_40bp": 0.0040,
    "taker_stress_50bp": 0.0050,
}
DELAY_ROBUSTNESS_MINUTES = 5

MIN_DEVELOPMENT_TRADES = 30
MIN_RECENT_TRADES = 40
MIN_RECENT_DAYS = 25
MIN_RECENT_PROFIT_FACTOR = 1.05
MAX_RECENT_DRAWDOWN = -0.25
MIN_POSITIVE_MONTH_SHARE = 0.50
MIN_NEIGHBORHOOD_POSITIVE_SHARE = 2.0 / 3.0


@dataclass(frozen=True, slots=True)
class BtcIntradayFlowSpec:
    candidate_id: str = "V14-R2H-BTC-FLOW-12H-120M"
    symbol: str = "BTC/USD"
    lookback_bars: int = 144
    volume_z_threshold: float = 1.50
    trade_count_z_threshold: float = 1.50
    range_ratio_threshold: float = 1.50
    body_strength_threshold: float = 0.45
    close_location_threshold: float = 0.75
    hold_minutes: int = 120
    cooldown_minutes: int = 180
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_spec() -> BtcIntradayFlowSpec:
    return BtcIntradayFlowSpec()


def spec_from_dict(payload: Mapping[str, Any]) -> BtcIntradayFlowSpec:
    return BtcIntradayFlowSpec(**dict(payload))


def robustness_specs() -> tuple[BtcIntradayFlowSpec, ...]:
    base = candidate_spec()
    return (
        replace(
            base,
            candidate_id=base.candidate_id + "-LOOSE",
            volume_z_threshold=1.25,
            trade_count_z_threshold=1.25,
            range_ratio_threshold=1.40,
            body_strength_threshold=0.40,
            close_location_threshold=0.70,
        ),
        base,
        replace(
            base,
            candidate_id=base.candidate_id + "-STRICT",
            volume_z_threshold=1.75,
            trade_count_z_threshold=1.75,
            range_ratio_threshold=1.60,
            body_strength_threshold=0.50,
            close_location_threshold=0.80,
        ),
    )


def campaign_manifest() -> dict[str, Any]:
    spec = candidate_spec()
    return {
        "schema_version": "graen.v14-r2h.btc-intraday-flow.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": spec.to_dict(),
        "mechanism": (
            "Long-only continuation after a completed five-minute BTC bar shows "
            "simultaneous abnormal volume, abnormal trade count, range expansion, "
            "strong positive body, and a close near the bar high. Entry is the "
            "next five-minute bar open; the canonical hold is 120 minutes."
        ),
        "why_orthogonal": (
            "R2H is not another raw momentum/lookback permutation. The primary "
            "information is participation/activity pressure (volume and trade-count "
            "surprise) conditioned on directional bar structure."
        ),
        "bar_timeframe": "5Min",
        "universe": list(UNIVERSE),
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "adaptive_recent_start": ADAPTIVE_RECENT_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "cost_scenarios": dict(ROUND_TRIP_COSTS),
        "decisive_cost_scenario": "taker_stress_50bp",
        "execution_delay_test_minutes": DELAY_ROBUSTNESS_MINUTES,
        "robustness_specs": [row.to_dict() for row in robustness_specs()],
        "selection_disclosure": (
            "The 2026 Alpaca BTC corpus has been inspected by earlier ANEVUM "
            "research. Both chronological windows are adaptive discovery evidence, "
            "not independent validation or holdout. The R2H specification is frozen "
            "before this campaign fetches that corpus and any survivor must earn "
            "fresh forward evidence."
        ),
        "adaptive_gate": {
            "development_50bp_min_trades": MIN_DEVELOPMENT_TRADES,
            "development_50bp_expectancy_gt": 0.0,
            "recent_50bp_min_trades": MIN_RECENT_TRADES,
            "recent_50bp_min_independent_days": MIN_RECENT_DAYS,
            "recent_50bp_expectancy_gt": 0.0,
            "recent_50bp_profit_factor_gte": MIN_RECENT_PROFIT_FACTOR,
            "recent_50bp_total_return_gt": 0.0,
            "recent_50bp_max_drawdown_gt": MAX_RECENT_DRAWDOWN,
            "one_bar_delay_50bp_expectancy_gt": 0.0,
            "recent_positive_month_share_gte": MIN_POSITIVE_MONTH_SHARE,
            "robustness_positive_expectancy_share_gte": MIN_NEIGHBORHOOD_POSITIVE_SHARE,
        },
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW",
        "r2f_forward_shadow_preserved": True,
        "r2g_forward_shadow_preserved": True,
        "research_only": True,
        "promotion_eligible": False,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }


def _f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def _valid_bar(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    values = [_f(row.get(key)) for key in ("o", "h", "l", "c", "v", "n")]
    o, h, l, c, volume, trades = values
    return (
        all(isfinite(value) for value in values)
        and min(o, h, l, c) > 0.0
        and h > l
        and l <= min(o, c) <= max(o, c) <= h
        and volume > 0.0
        and trades > 0.0
    )


def _flow_features(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcIntradayFlowSpec,
    stamp: datetime,
) -> dict[str, float] | None:
    current = _bar(series, spec.symbol, stamp)
    if not _valid_bar(current):
        return None

    history = [
        _bar(
            series,
            spec.symbol,
            stamp - timedelta(minutes=BAR_MINUTES * offset),
        )
        for offset in range(spec.lookback_bars, 0, -1)
    ]
    if len(history) != spec.lookback_bars or not all(_valid_bar(row) for row in history):
        return None

    volume_history = [log1p(_f(row.get("v"))) for row in history if row is not None]
    trade_history = [log1p(_f(row.get("n"))) for row in history if row is not None]
    volume_sigma = pstdev(volume_history)
    trade_sigma = pstdev(trade_history)
    if volume_sigma <= 1e-12 or trade_sigma <= 1e-12:
        return None

    current_volume = log1p(_f(current.get("v")))
    current_trades = log1p(_f(current.get("n")))
    volume_z = (current_volume - fmean(volume_history)) / volume_sigma
    trade_count_z = (current_trades - fmean(trade_history)) / trade_sigma

    baseline_ranges = [
        (_f(row.get("h")) - _f(row.get("l"))) / _f(row.get("o"))
        for row in history
        if row is not None and _f(row.get("o")) > 0.0
    ]
    baseline_range = fmean(baseline_ranges) if baseline_ranges else 0.0
    current_range = _f(current.get("h")) - _f(current.get("l"))
    current_open = _f(current.get("o"))
    if baseline_range <= 1e-12 or current_range <= 1e-12 or current_open <= 0:
        return None
    range_ratio = (current_range / current_open) / baseline_range
    body_strength = (_f(current.get("c")) - current_open) / current_range
    close_location = (_f(current.get("c")) - _f(current.get("l"))) / current_range

    return {
        "volume_z": volume_z,
        "trade_count_z": trade_count_z,
        "range_ratio": range_ratio,
        "body_strength": body_strength,
        "close_location": close_location,
    }


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcIntradayFlowSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    if symbol != spec.symbol:
        return None
    features = _flow_features(series, spec, stamp)
    if features is None:
        return None
    if features["volume_z"] < spec.volume_z_threshold:
        return None
    if features["trade_count_z"] < spec.trade_count_z_threshold:
        return None
    if features["range_ratio"] < spec.range_ratio_threshold:
        return None
    if features["body_strength"] < spec.body_strength_threshold:
        return None
    if features["close_location"] < spec.close_location_threshold:
        return None
    return Opportunity(
        candidate_id=spec.candidate_id,
        symbol=spec.symbol,
        opportunity_at=stamp,
        hold_minutes=spec.hold_minutes,
        signal={
            "family": FAMILY,
            "mechanism": "activity_flow_pressure_continuation",
            **features,
        },
    )


def _collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcIntradayFlowSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    suppressed_until = datetime.min.replace(tzinfo=UTC)
    for stamp in _grid(start, end):
        if stamp < suppressed_until:
            continue
        opportunity = opportunity_at(series, spec, spec.symbol, stamp)
        if opportunity is None:
            continue
        output.append(opportunity)
        suppressed_until = stamp + timedelta(minutes=spec.cooldown_minutes)
    return output


def _simulate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcIntradayFlowSpec,
    *,
    start: datetime,
    end: datetime,
    round_trip_cost: float,
    extra_entry_delay_minutes: int = 0,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    opportunities = _collect_opportunities(series, spec, start=start, end=end)
    for opportunity in opportunities:
        entry_bar_end = (
            opportunity.opportunity_at
            + timedelta(minutes=BAR_MINUTES + extra_entry_delay_minutes)
        )
        exit_bar_end = entry_bar_end + timedelta(minutes=spec.hold_minutes)
        if exit_bar_end > end:
            continue
        entry = _open(series, spec.symbol, entry_bar_end)
        exit_ = _open(series, spec.symbol, exit_bar_end)
        if entry is None or exit_ is None or entry <= 0.0 or exit_ <= 0.0:
            continue
        gross = exit_ / entry - 1.0
        net = gross - round_trip_cost
        trades.append(
            {
                "signal_at": opportunity.opportunity_at,
                "entry_at": entry_bar_end - timedelta(minutes=BAR_MINUTES),
                "exit_at": exit_bar_end - timedelta(minutes=BAR_MINUTES),
                "gross_return": gross,
                "net_return": net,
                "round_trip_cost": round_trip_cost,
                "signal": dict(opportunity.signal),
            }
        )
    return trades


def _compound(values: Sequence[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= max(1.0 + float(value), 1e-12)
    return equity - 1.0


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= max(1.0 + float(value), 1e-12)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0.0)
    losses = -sum(value for value in values if value < 0.0)
    return gains / losses if losses > 0.0 else None


def _summarize(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float(row["net_return"]) for row in trades]
    by_day: dict[str, list[float]] = defaultdict(list)
    by_month: dict[str, list[float]] = defaultdict(list)
    for row in trades:
        key = row["entry_at"].date().isoformat()
        month = key[:7]
        by_day[key].append(float(row["net_return"]))
        by_month[month].append(float(row["net_return"]))
    positive_months = sum(
        1 for rows in by_month.values() if _compound(rows) > 0.0
    )
    positive_month_share = positive_months / len(by_month) if by_month else 0.0
    return {
        "trade_count": len(values),
        "independent_day_blocks": len(by_day),
        "expectancy_per_trade": fmean(values) if values else 0.0,
        "win_rate": sum(value > 0.0 for value in values) / len(values) if values else 0.0,
        "profit_factor": _profit_factor(values),
        "total_return": _compound(values),
        "max_drawdown": _max_drawdown(values),
        "positive_month_share": positive_month_share,
        "observed_month_count": len(by_month),
        "sample": [
            {
                **dict(row),
                "signal_at": row["signal_at"].isoformat(),
                "entry_at": row["entry_at"].isoformat(),
                "exit_at": row["exit_at"].isoformat(),
            }
            for row in trades[:5]
        ],
    }


def _scenario_summary(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcIntradayFlowSpec,
    *,
    start: datetime,
    end: datetime,
    round_trip_cost: float,
    extra_entry_delay_minutes: int = 0,
) -> dict[str, Any]:
    return _summarize(
        _simulate(
            series,
            spec,
            start=start,
            end=end,
            round_trip_cost=round_trip_cost,
            extra_entry_delay_minutes=extra_entry_delay_minutes,
        )
    )


def evaluate_btc_intraday_flow_discovery(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    rows = list(bars_by_symbol.get("BTC/USD", ()))
    if len(rows) < 10000:
        raise ValueError(f"v14_r2h_btc_corpus_too_small:{len(rows)}")

    warmup_hours = candidate_spec().lookback_bars * BAR_MINUTES // 60 + 2
    series = build_series(
        {"BTC/USD": rows},
        start=BAR_SCREEN_START,
        end=BAR_SCREEN_END,
        warmup_hours=warmup_hours,
    )
    canonical = candidate_spec()

    development = {
        name: _scenario_summary(
            series,
            canonical,
            start=BAR_SCREEN_START,
            end=ADAPTIVE_RECENT_START,
            round_trip_cost=cost,
        )
        for name, cost in ROUND_TRIP_COSTS.items()
    }
    recent = {
        name: _scenario_summary(
            series,
            canonical,
            start=ADAPTIVE_RECENT_START,
            end=BAR_SCREEN_END,
            round_trip_cost=cost,
        )
        for name, cost in ROUND_TRIP_COSTS.items()
    }
    delayed = _scenario_summary(
        series,
        canonical,
        start=ADAPTIVE_RECENT_START,
        end=BAR_SCREEN_END,
        round_trip_cost=ROUND_TRIP_COSTS["taker_stress_50bp"],
        extra_entry_delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )

    neighborhood = []
    for spec in robustness_specs():
        row = _scenario_summary(
            series,
            spec,
            start=ADAPTIVE_RECENT_START,
            end=BAR_SCREEN_END,
            round_trip_cost=ROUND_TRIP_COSTS["taker_stress_50bp"],
        )
        neighborhood.append({"spec": spec.to_dict(), "result": row})
    neighborhood_positive_share = (
        sum(
            float(row["result"]["expectancy_per_trade"]) > 0.0
            for row in neighborhood
        )
        / len(neighborhood)
    )

    development_decisive = development["taker_stress_50bp"]
    decisive = recent["taker_stress_50bp"]
    pf = decisive.get("profit_factor")
    survives = bool(
        int(development_decisive["trade_count"]) >= MIN_DEVELOPMENT_TRADES
        and float(development_decisive["expectancy_per_trade"]) > 0.0
        and int(decisive["trade_count"]) >= MIN_RECENT_TRADES
        and int(decisive["independent_day_blocks"]) >= MIN_RECENT_DAYS
        and float(decisive["expectancy_per_trade"]) > 0.0
        and pf is not None
        and float(pf) >= MIN_RECENT_PROFIT_FACTOR
        and float(decisive["total_return"]) > 0.0
        and float(decisive["max_drawdown"]) > MAX_RECENT_DRAWDOWN
        and float(delayed["expectancy_per_trade"]) > 0.0
        and float(decisive["positive_month_share"]) >= MIN_POSITIVE_MONTH_SHARE
        and neighborhood_positive_share >= MIN_NEIGHBORHOOD_POSITIVE_SHARE
    )

    return {
        "schema_version": "graen.v14-r2h.btc-intraday-flow.discovery.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": canonical.to_dict(),
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto five-minute bars",
            "symbol": "BTC/USD",
            "raw_bar_count": len(rows),
        },
        "development_reference": {
            "start": BAR_SCREEN_START.isoformat(),
            "end": ADAPTIVE_RECENT_START.isoformat(),
            "scenarios": development,
            "independent_oos": False,
            "historical_holdout": False,
        },
        "adaptive_recent_window": {
            "start": ADAPTIVE_RECENT_START.isoformat(),
            "end": BAR_SCREEN_END.isoformat(),
            "scenarios": recent,
            "one_bar_execution_delay_50bp": delayed,
            "independent_oos": False,
            "historical_holdout": False,
        },
        "robustness_neighborhood": {
            "cells": neighborhood,
            "positive_expectancy_share_50bp": neighborhood_positive_share,
        },
        "adaptive_gate": {
            "survives_to_forward_shadow": survives,
            "requirements": campaign_manifest()["adaptive_gate"],
            "decisive_scenario": "taker_stress_50bp",
        },
        "interpretation": (
            "V14_R2H_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW"
            if survives
            else "V14_R2H_ADAPTIVE_DISCOVERY_BROKER_FEASIBILITY_FAIL"
        ),
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW",
        "r2f_forward_shadow_preserved": True,
        "r2g_forward_shadow_preserved": True,
        "shadow_only": survives,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
