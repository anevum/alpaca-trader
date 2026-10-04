from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-4h-consensus-v14-r2h"
CAMPAIGN_ID = "v14-r2h-btc-4h-consensus-faststart"
FAMILY = "btc_4h_momentum_or_sma_consensus"
UNIVERSE = ("BTC/USD",)

BAR_SCREEN_START = datetime(2021, 1, 1, tzinfo=UTC)
OOS_START = datetime(2025, 1, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

# Preserve the successful R2G economic horizons while sampling every four hours.
# Six 4-hour bars ~= one day.
MOMENTUM_LOOKBACK_BARS = 180 * 6
SMA_WINDOW_BARS = 250 * 6
MOMENTUM_NEIGHBORHOOD = tuple(days * 6 for days in (165, 180, 195))
SMA_NEIGHBORHOOD = tuple(days * 6 for days in (200, 250, 300))
BARS_PER_YEAR = 6 * 365

COST_SCENARIOS = {
    "maker_15bp": 0.0015,
    "taker_25bp": 0.0025,
    "taker_stress_30bp": 0.0030,
    "severe_stress_50bp": 0.0050,
}

MIN_OOS_BARS = 1800
MIN_OOS_ENTRIES = 2
MIN_OOS_SHARPE = 0.50
MAX_OOS_DRAWDOWN = -0.50
MIN_POSITIVE_QUARTER_SHARE = 0.50
MIN_NEIGHBORHOOD_POSITIVE_SHARE = 0.75
MIN_NEIGHBORHOOD_SHARPE_045_SHARE = 2.0 / 3.0


@dataclass(frozen=True, slots=True)
class Btc4hConsensusSpec:
    candidate_id: str = "V14-R2H-BTC-4H-CONSENSUS-1080-1500"
    symbol: str = "BTC/USD"
    momentum_lookback_bars: int = MOMENTUM_LOOKBACK_BARS
    sma_window_bars: int = SMA_WINDOW_BARS
    rule: str = "OR"
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_spec() -> Btc4hConsensusSpec:
    return Btc4hConsensusSpec()


def spec_from_dict(payload: Mapping[str, Any]) -> Btc4hConsensusSpec:
    return Btc4hConsensusSpec(**dict(payload))


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2h.btc-4h-consensus.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "origin": {
            "daily_candidate": "V14-R2G-BTC-CONSENSUS-180-250",
            "failed_4h_predecessor": "V14-R2E",
            "design_rule": (
                "Transfer the R2G 180-day momentum OR 250-day SMA rule to "
                "4-hour sampling without shortening the economic horizons."
            ),
        },
        "selection_disclosure": (
            "ANEVUM previously inspected this historical 4-hour BTC corpus while "
            "testing a different SMA-only family. R2H therefore treats the historical "
            "test as adaptive transfer evidence, not an independent holdout. Fixed "
            "parameters are frozen before R2H evaluation and fresh 4-hour shadow "
            "evidence remains required."
        ),
        "bar_timeframe": "4Hour",
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "oos_start": OOS_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "momentum_lookback_bars": MOMENTUM_LOOKBACK_BARS,
        "sma_window_bars": SMA_WINDOW_BARS,
        "momentum_neighborhood": list(MOMENTUM_NEIGHBORHOOD),
        "sma_neighborhood": list(SMA_NEIGHBORHOOD),
        "signal_rule": (
            "For bar t, use only completed data through t-1. Hold BTC long when "
            "the prior close has positive 1080-bar momentum OR is above the "
            "1500-bar SMA; otherwise flat."
        ),
        "cost_scenarios": dict(COST_SCENARIOS),
        "transfer_gate": {
            "taker_stress_30bp_total_return_gt": 0.0,
            "taker_stress_30bp_sharpe_gte": MIN_OOS_SHARPE,
            "taker_stress_30bp_max_drawdown_gt": MAX_OOS_DRAWDOWN,
            "minimum_oos_bars": MIN_OOS_BARS,
            "minimum_oos_entries": MIN_OOS_ENTRIES,
            "positive_time_quarter_share_gte": MIN_POSITIVE_QUARTER_SHARE,
            "severe_stress_50bp_total_return_gt": 0.0,
            "one_bar_delay_total_return_gt": 0.0,
            "neighborhood_positive_return_share_gte": MIN_NEIGHBORHOOD_POSITIVE_SHARE,
            "neighborhood_sharpe_045_share_gte": MIN_NEIGHBORHOOD_SHARPE_045_SHARE,
        },
        "next_stage_if_pass": "VELUM_REPLAY_THEN_4H_FORWARD_SHADOW",
        "evidence_role": "ADAPTIVE_CROSS_RESOLUTION_TRANSFER_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "research_only": True,
        "promotion_eligible": False,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_stamp: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        raw_stamp = row.get("t", row.get("timestamp"))
        if raw_stamp is None:
            continue
        try:
            stamp = _stamp(raw_stamp)
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError):
            continue
        if close <= 0 or not (BAR_SCREEN_START <= stamp < BAR_SCREEN_END):
            continue
        by_stamp[stamp] = {"timestamp": stamp, "close": close}
    return [by_stamp[key] for key in sorted(by_stamp)]


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


def _positive_quarter_share(values: Sequence[float]) -> float:
    observed = 0
    positive = 0
    for quarter in range(4):
        start = len(values) * quarter // 4
        end = len(values) * (quarter + 1) // 4
        chunk = values[start:end]
        if not chunk:
            continue
        observed += 1
        if _compound(chunk) > 0:
            positive += 1
    return positive / observed if observed else 0.0


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


def _simulate(
    bars: Sequence[Mapping[str, Any]],
    *,
    momentum_bars: int,
    sma_bars: int,
    cost_per_turnover: float,
    start: datetime,
    end: datetime,
    signal_delay_bars: int = 0,
) -> dict[str, Any]:
    closes = [float(row["close"]) for row in bars]
    stamps = [row["timestamp"] for row in bars]
    sma = _sma_series(bars, sma_bars)
    minimum = max(momentum_bars, sma_bars)

    position = 0.0
    returns: list[float] = []
    gross_returns: list[float] = []
    positions: list[float] = []
    entries = 0
    exits = 0
    turnover = 0.0

    for index in range(minimum + 1 + signal_delay_bars, len(bars)):
        signal_index = index - 1 - signal_delay_bars
        anchor_index = signal_index - momentum_bars
        average = sma[signal_index]
        if anchor_index < 0 or average is None:
            continue
        momentum_positive = closes[signal_index] / closes[anchor_index] - 1.0 > 0.0
        above_sma = closes[signal_index] > float(average)
        next_position = 1.0 if (momentum_positive or above_sma) else 0.0

        stamp = stamps[index]
        if not isinstance(stamp, datetime):
            continue
        if start <= stamp < end:
            change = abs(next_position - position)
            if next_position > position:
                entries += 1
            elif next_position < position:
                exits += 1
            turnover += change
            asset_return = closes[index] / closes[index - 1] - 1.0
            gross = next_position * asset_return
            net = gross - change * cost_per_turnover
            gross_returns.append(gross)
            returns.append(net)
            positions.append(next_position)
        position = next_position

    return {
        "momentum_lookback_bars": momentum_bars,
        "sma_window_bars": sma_bars,
        "signal_delay_bars": signal_delay_bars,
        "cost_per_turnover": cost_per_turnover,
        "bar_count": len(returns),
        "entry_count": entries,
        "exit_count": exits,
        "turnover_units": turnover,
        "exposure_share": fmean(positions) if positions else 0.0,
        "gross_total_return": _compound(gross_returns),
        "total_return": _compound(returns),
        "sharpe": _sharpe(returns),
        "max_drawdown": _max_drawdown(returns),
        "positive_time_quarter_share": _positive_quarter_share(returns),
    }


def evaluate_btc_4h_consensus_transfer(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    bars = normalize_bars(bars_by_symbol.get("BTC/USD", []))
    minimum_history = max(MOMENTUM_LOOKBACK_BARS, SMA_WINDOW_BARS)
    if len(bars) < minimum_history + MIN_OOS_BARS:
        raise ValueError(f"v14_r2h_4h_corpus_too_small:{len(bars)}")

    first = bars[0]["timestamp"]
    last = bars[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    if first >= OOS_START:
        raise ValueError("v14_r2h_development_corpus_missing")
    if last < OOS_START:
        raise ValueError("v14_r2h_oos_corpus_missing")

    development_reference = _simulate(
        bars,
        momentum_bars=MOMENTUM_LOOKBACK_BARS,
        sma_bars=SMA_WINDOW_BARS,
        cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
        start=BAR_SCREEN_START,
        end=OOS_START,
    )
    scenarios = {
        name: _simulate(
            bars,
            momentum_bars=MOMENTUM_LOOKBACK_BARS,
            sma_bars=SMA_WINDOW_BARS,
            cost_per_turnover=cost,
            start=OOS_START,
            end=BAR_SCREEN_END,
        )
        for name, cost in COST_SCENARIOS.items()
    }
    delayed = _simulate(
        bars,
        momentum_bars=MOMENTUM_LOOKBACK_BARS,
        sma_bars=SMA_WINDOW_BARS,
        cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
        start=OOS_START,
        end=BAR_SCREEN_END,
        signal_delay_bars=1,
    )

    neighborhood: list[dict[str, Any]] = []
    for momentum_bars in MOMENTUM_NEIGHBORHOOD:
        for sma_bars in SMA_NEIGHBORHOOD:
            neighborhood.append(
                _simulate(
                    bars,
                    momentum_bars=momentum_bars,
                    sma_bars=sma_bars,
                    cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
                    start=OOS_START,
                    end=BAR_SCREEN_END,
                )
            )

    positive_share = (
        sum(float(row["total_return"]) > 0.0 for row in neighborhood)
        / len(neighborhood)
    )
    sharpe_045_share = (
        sum(float(row["sharpe"]) >= 0.45 for row in neighborhood)
        / len(neighborhood)
    )
    decisive = scenarios["taker_stress_30bp"]
    severe = scenarios["severe_stress_50bp"]

    survives = bool(
        int(decisive["bar_count"]) >= MIN_OOS_BARS
        and int(decisive["entry_count"]) >= MIN_OOS_ENTRIES
        and float(decisive["total_return"]) > 0.0
        and float(decisive["sharpe"]) >= MIN_OOS_SHARPE
        and float(decisive["max_drawdown"]) > MAX_OOS_DRAWDOWN
        and float(decisive["positive_time_quarter_share"]) >= MIN_POSITIVE_QUARTER_SHARE
        and float(severe["total_return"]) > 0.0
        and float(delayed["total_return"]) > 0.0
        and positive_share >= MIN_NEIGHBORHOOD_POSITIVE_SHARE
        and sharpe_045_share >= MIN_NEIGHBORHOOD_SHARPE_045_SHARE
    )

    return {
        "schema_version": "graen.v14-r2h.btc-4h-consensus.transfer.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto 4-hour bars",
            "symbol": "BTC/USD",
            "bar_count": len(bars),
            "first_bar": first.isoformat(),
            "last_bar": last.isoformat(),
        },
        "development_reference": development_reference,
        "adaptive_transfer_window": {
            "start": OOS_START.isoformat(),
            "end": BAR_SCREEN_END.isoformat(),
            "scenarios": scenarios,
            "one_bar_execution_delay": delayed,
            "independent_oos": False,
            "historical_holdout": False,
        },
        "robustness_neighborhood": {
            "momentum_lookbacks": list(MOMENTUM_NEIGHBORHOOD),
            "sma_windows": list(SMA_NEIGHBORHOOD),
            "cells": neighborhood,
            "positive_return_share": positive_share,
            "sharpe_gte_045_share": sharpe_045_share,
        },
        "transfer_gate": {
            "survives_to_velum_and_forward_shadow": survives,
            "requirements": campaign_manifest()["transfer_gate"],
            "decisive_scenario": "taker_stress_30bp",
        },
        "interpretation": (
            "V14_R2H_4H_TRANSFER_SURVIVES_TO_VELUM_AND_FORWARD_SHADOW"
            if survives
            else "V14_R2H_4H_TRANSFER_BROKER_FEASIBILITY_FAIL"
        ),
        "evidence_role": "ADAPTIVE_CROSS_RESOLUTION_TRANSFER_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "shadow_only": survives,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
