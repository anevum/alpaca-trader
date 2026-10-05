from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-r2h-breakout-v15-r1"
CAMPAIGN_ID = "v15-r1-btc-r2h-breakout"
FAMILY = "btc_r2h_regime_donchian_breakout"
SYMBOL = "BTC/USD"
BAR_TIMEFRAME = "4Hour"
BARS_PER_YEAR = 6 * 365

MOMENTUM_LOOKBACK_BARS = 180 * 6
SMA_WINDOW_BARS = 250 * 6
ENTRY_LOOKBACK_BARS = 42
EXIT_LOOKBACK_BARS = 15
HARD_STOP_PCT = 0.05

DEVELOPMENT_START = datetime(2025, 1, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 1, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 10, 1, tzinfo=UTC)

ENTRY_NEIGHBORHOOD = (36, 42, 48)
EXIT_NEIGHBORHOOD = (12, 15, 18)

COST_SCENARIOS = {
    "taker_25bp": 0.0025,
    "taker_stress_30bp": 0.0030,
    "severe_stress_50bp": 0.0050,
}


@dataclass(frozen=True, slots=True)
class BtcR2hBreakoutSpec:
    candidate_id: str = "V15-R1-BTC-R2H-BREAKOUT-42-15"
    symbol: str = SYMBOL
    timeframe: str = BAR_TIMEFRAME
    regime_momentum_bars: int = MOMENTUM_LOOKBACK_BARS
    regime_sma_bars: int = SMA_WINDOW_BARS
    entry_lookback_bars: int = ENTRY_LOOKBACK_BARS
    exit_lookback_bars: int = EXIT_LOOKBACK_BARS
    hard_stop_pct: float = HARD_STOP_PCT
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_spec() -> BtcR2hBreakoutSpec:
    return BtcR2hBreakoutSpec()


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v15-r1.btc-r2h-breakout.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "origin": {
            "regime_source": "V14-R2H-BTC-4H-CONSENSUS-1080-1500",
            "design_rule": (
                "Keep the frozen R2H 180-day momentum OR 250-day SMA regime, "
                "then require a completed-bar 42-bar Donchian breakout for entry "
                "and use a 15-bar completed-bar channel break for exit."
            ),
        },
        "selection_protocol": {
            "development_window": {
                "start": DEVELOPMENT_START.isoformat(),
                "end": HOLDOUT_START.isoformat(),
            },
            "development_grid": {
                "entry_lookback_bars": [18, 30, 42],
                "exit_lookback_bars": [9, 15, 21],
            },
            "selection_metric": "highest development taker_stress_30bp Sharpe",
            "frozen_before_holdout": True,
            "holdout_window": {
                "start": HOLDOUT_START.isoformat(),
                "end": HOLDOUT_END.isoformat(),
            },
        },
        "signal_rule": (
            "Use completed 4-hour bars only. Regime is long when prior close has "
            "positive 1080-bar momentum OR is above the 1500-bar SMA. While flat, "
            "enter long only when the prior close is above the preceding 42-bar high. "
            "While long, exit when the prior close is below the preceding 15-bar low "
            "or the R2H regime turns flat. A fixed 5% entry catastrophe stop is also "
            "modeled as a risk boundary."
        ),
        "cost_scenarios": dict(COST_SCENARIOS),
        "evidence_role": "FROZEN_DEVELOPMENT_THEN_LATER_HOLDOUT",
        "independent_from_r2h_family_selection": False,
        "holdout_parameters_frozen": True,
        "research_only": True,
        "promotion_eligible": False,
        "execution_authority": False,
        "broker_orders_possible": False,
        "live_execution_authorized": False,
    }


def _stamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def normalize_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        raw_stamp = row.get("t", row.get("timestamp"))
        if raw_stamp is None:
            continue
        try:
            stamp = _stamp(raw_stamp)
            open_ = float(row.get("o", row.get("open")))
            high = float(row.get("h", row.get("high")))
            low = float(row.get("l", row.get("low")))
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError):
            continue
        if min(open_, high, low, close) <= 0:
            continue
        if high < low or high < max(open_, close) or low > min(open_, close):
            continue
        by_stamp[stamp] = {
            "timestamp": stamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    return [by_stamp[key] for key in sorted(by_stamp)]


def _sma_series(bars: Sequence[Mapping[str, Any]], window: int) -> list[float | None]:
    closes = [float(row["close"]) for row in bars]
    out: list[float | None] = [None] * len(closes)
    rolling = 0.0
    for index, close in enumerate(closes):
        rolling += close
        if index >= window:
            rolling -= closes[index - window]
        if index >= window - 1:
            out[index] = rolling / window
    return out


def _compound(values: Sequence[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= max(1.0 + float(value), 1e-12)
    return equity - 1.0


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = pstdev(values)
    if sigma <= 1e-15:
        return 0.0
    return fmean(values) / sigma * sqrt(BARS_PER_YEAR)


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= max(1.0 + float(value), 1e-12)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _channel_high(bars: Sequence[Mapping[str, Any]], end: int, width: int) -> float:
    start = end - width
    if start < 0:
        return float("nan")
    return max(float(bars[index]["high"]) for index in range(start, end))


def _channel_low(bars: Sequence[Mapping[str, Any]], end: int, width: int) -> float:
    start = end - width
    if start < 0:
        return float("nan")
    return min(float(bars[index]["low"]) for index in range(start, end))


def _simulate(
    bars: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
    entry_bars: int,
    exit_bars: int,
    cost_per_turnover: float,
    stop_pct: float = HARD_STOP_PCT,
) -> dict[str, Any]:
    closes = [float(row["close"]) for row in bars]
    sma = _sma_series(bars, SMA_WINDOW_BARS)
    minimum = max(
        MOMENTUM_LOOKBACK_BARS,
        SMA_WINDOW_BARS,
        entry_bars,
        exit_bars,
    )

    position = 0.0
    entry_price = 0.0
    returns: list[float] = []
    positions: list[float] = []
    entries = 0
    exits = 0
    stop_hits = 0
    turnover = 0.0

    for index in range(minimum + 2, len(bars)):
        signal_index = index - 1
        anchor_index = signal_index - MOMENTUM_LOOKBACK_BARS
        average = sma[signal_index]
        if anchor_index < 0 or average is None:
            continue

        stamp = bars[index]["timestamp"]
        if not isinstance(stamp, datetime):
            continue
        # Every evaluation window begins flat. Indicator warmup may come from
        # earlier bars, but positions/costs cannot leak across evidence windows.
        if stamp < start:
            continue
        if stamp >= end:
            break

        momentum_positive = (
            closes[signal_index] / closes[anchor_index] - 1.0 > 0.0
        )
        above_sma = closes[signal_index] > float(average)
        regime_long = bool(momentum_positive or above_sma)
        breakout = closes[signal_index] > _channel_high(
            bars, signal_index, entry_bars
        )
        breakdown = closes[signal_index] < _channel_low(
            bars, signal_index, exit_bars
        )

        in_window = True
        bar_return = 0.0

        if position == 0.0 and regime_long and breakout:
            position = 1.0
            entry_price = closes[signal_index]
            if in_window:
                entries += 1
                turnover += 1.0
                bar_return -= cost_per_turnover

        if position == 1.0 and (not regime_long or breakdown):
            realized = closes[index] / closes[index - 1] - 1.0
            position = 0.0
            entry_price = 0.0
            if in_window:
                exits += 1
                turnover += 1.0
                bar_return += realized - cost_per_turnover
        elif position == 1.0:
            stop_price = entry_price * (1.0 - stop_pct)
            if float(bars[index]["low"]) <= stop_price:
                exit_price = min(stop_price, float(bars[index]["open"]))
                realized = exit_price / closes[index - 1] - 1.0
                position = 0.0
                entry_price = 0.0
                if in_window:
                    exits += 1
                    stop_hits += 1
                    turnover += 1.0
                    bar_return += realized - cost_per_turnover
            elif in_window:
                bar_return += closes[index] / closes[index - 1] - 1.0

        if in_window:
            returns.append(bar_return)
            positions.append(position)

    return {
        "entry_lookback_bars": entry_bars,
        "exit_lookback_bars": exit_bars,
        "hard_stop_pct": stop_pct,
        "cost_per_turnover": cost_per_turnover,
        "bar_count": len(returns),
        "entry_count": entries,
        "exit_count": exits,
        "stop_hits": stop_hits,
        "turnover_units": turnover,
        "exposure_share": fmean(positions) if positions else 0.0,
        "total_return": _compound(returns),
        "sharpe": _sharpe(returns),
        "max_drawdown": _max_drawdown(returns),
    }


def evaluate_btc_r2h_breakout(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    bars = normalize_bars(bars_by_symbol.get(SYMBOL, []))
    minimum = max(MOMENTUM_LOOKBACK_BARS, SMA_WINDOW_BARS)
    if len(bars) < minimum + 1000:
        raise ValueError(f"v15_r1_corpus_too_small:{len(bars)}")

    development_grid: list[dict[str, Any]] = []
    for entry_bars in (18, 30, 42):
        for exit_bars in (9, 15, 21):
            development_grid.append(
                _simulate(
                    bars,
                    start=DEVELOPMENT_START,
                    end=HOLDOUT_START,
                    entry_bars=entry_bars,
                    exit_bars=exit_bars,
                    cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
                )
            )
    development_grid.sort(
        key=lambda row: float(row["sharpe"]),
        reverse=True,
    )
    selected = development_grid[0]
    frozen_matches = (
        int(selected["entry_lookback_bars"]) == ENTRY_LOOKBACK_BARS
        and int(selected["exit_lookback_bars"]) == EXIT_LOOKBACK_BARS
    )

    holdout_scenarios = {
        name: _simulate(
            bars,
            start=HOLDOUT_START,
            end=HOLDOUT_END,
            entry_bars=ENTRY_LOOKBACK_BARS,
            exit_bars=EXIT_LOOKBACK_BARS,
            cost_per_turnover=cost,
        )
        for name, cost in COST_SCENARIOS.items()
    }

    neighborhood: list[dict[str, Any]] = []
    for entry_bars in ENTRY_NEIGHBORHOOD:
        for exit_bars in EXIT_NEIGHBORHOOD:
            neighborhood.append(
                _simulate(
                    bars,
                    start=HOLDOUT_START,
                    end=HOLDOUT_END,
                    entry_bars=entry_bars,
                    exit_bars=exit_bars,
                    cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
                )
            )

    decisive = holdout_scenarios["taker_stress_30bp"]
    severe = holdout_scenarios["severe_stress_50bp"]
    positive_neighbors = sum(
        float(row["total_return"]) > 0.0 for row in neighborhood
    ) / len(neighborhood)

    passes = bool(
        frozen_matches
        and int(decisive["entry_count"]) >= 2
        and float(decisive["total_return"]) > 0.0
        and float(decisive["sharpe"]) >= 0.75
        and float(decisive["max_drawdown"]) > -0.20
        and float(severe["total_return"]) > 0.0
        and positive_neighbors >= 0.75
    )

    return {
        "schema_version": "graen.v15-r1.btc-r2h-breakout.result.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto 4-hour bars",
            "symbol": SYMBOL,
            "bar_count": len(bars),
            "first_bar": bars[0]["timestamp"].isoformat() if bars else None,
            "last_bar": bars[-1]["timestamp"].isoformat() if bars else None,
        },
        "development": {
            "grid": development_grid,
            "selected": selected,
            "frozen_candidate_matches_development_winner": frozen_matches,
        },
        "holdout": {
            "start": HOLDOUT_START.isoformat(),
            "end": HOLDOUT_END.isoformat(),
            "scenarios": holdout_scenarios,
            "neighborhood": {
                "entry_lookbacks": list(ENTRY_NEIGHBORHOOD),
                "exit_lookbacks": list(EXIT_NEIGHBORHOOD),
                "cells": neighborhood,
                "positive_return_share": positive_neighbors,
            },
        },
        "gate": {
            "passed": passes,
            "requirements": {
                "frozen_candidate_matches_development_winner": True,
                "minimum_holdout_entries": 2,
                "stress_30bp_total_return_gt": 0.0,
                "stress_30bp_sharpe_gte": 0.75,
                "stress_30bp_max_drawdown_gt": -0.20,
                "severe_50bp_total_return_gt": 0.0,
                "holdout_neighborhood_positive_share_gte": 0.75,
            },
        },
        "interpretation": (
            "V15_R1_HOLDOUT_PASS_READY_FOR_ISOLATED_FORWARD_PAPER"
            if passes
            else "V15_R1_HOLDOUT_FAIL"
        ),
        "research_only": True,
        "paper_canary_eligible": passes,
        "promotion_eligible": False,
        "execution_authority": False,
        "broker_orders_possible": False,
        "live_execution_authorized": False,
    }
