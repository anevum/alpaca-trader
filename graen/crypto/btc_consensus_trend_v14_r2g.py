from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-consensus-trend-v14-r2g"
CAMPAIGN_ID = "v14-r2g-btc-daily-consensus-trend"
FAMILY = "btc_daily_momentum_or_sma_consensus"
UNIVERSE = ("BTC/USD",)

BAR_SCREEN_START = datetime(2021, 1, 1, tzinfo=UTC)
ADAPTIVE_RECENT_START = datetime(2025, 1, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

MOMENTUM_LOOKBACK_DAYS = 180
SMA_WINDOW_DAYS = 250
MOMENTUM_NEIGHBORHOOD = (165, 180, 195)
SMA_NEIGHBORHOOD = (200, 250, 300)
BARS_PER_YEAR = 365

COST_SCENARIOS = {
    "maker_15bp": 0.0015,
    "taker_25bp": 0.0025,
    "taker_stress_30bp": 0.0030,
    "severe_stress_50bp": 0.0050,
}

MIN_RECENT_BARS = 600
MIN_RECENT_ENTRIES = 2
MIN_RECENT_SHARPE = 0.50
MAX_RECENT_DRAWDOWN = -0.50
MIN_NEIGHBORHOOD_POSITIVE_SHARE = 0.75
MIN_NEIGHBORHOOD_SHARPE_045_SHARE = 2.0 / 3.0


@dataclass(frozen=True, slots=True)
class BtcConsensusTrendSpec:
    candidate_id: str = "V14-R2G-BTC-CONSENSUS-180-250"
    symbol: str = "BTC/USD"
    momentum_lookback_days: int = MOMENTUM_LOOKBACK_DAYS
    sma_window_days: int = SMA_WINDOW_DAYS
    rule: str = "OR"
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_spec() -> BtcConsensusTrendSpec:
    return BtcConsensusTrendSpec()


def spec_from_dict(payload: Mapping[str, Any]) -> BtcConsensusTrendSpec:
    return BtcConsensusTrendSpec(**dict(payload))


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2g.btc-consensus-trend.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "selection_disclosure": (
            "R2G was designed after ANEVUM had inspected 2025-2026 Alpaca BTC "
            "history while diagnosing R2F. Historical results are adaptive "
            "discovery evidence only and cannot be treated as independent "
            "validation or holdout evidence."
        ),
        "mechanism": (
            "Long BTC when either medium-horizon time-series momentum is "
            "positive or price remains above a slow daily moving average. "
            "Flat only when both trend measures are negative."
        ),
        "universe": list(UNIVERSE),
        "bar_timeframe": "1Day",
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "adaptive_recent_start": ADAPTIVE_RECENT_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "momentum_lookback_days": MOMENTUM_LOOKBACK_DAYS,
        "sma_window_days": SMA_WINDOW_DAYS,
        "momentum_neighborhood": list(MOMENTUM_NEIGHBORHOOD),
        "sma_neighborhood": list(SMA_NEIGHBORHOOD),
        "signal_rule": (
            "At day t use only data through completed day t-1. Hold long when "
            "close[t-1]/close[t-1-180]-1 > 0 OR close[t-1] is above the "
            "250-day SMA ending at t-1; otherwise flat."
        ),
        "cost_scenarios": dict(COST_SCENARIOS),
        "adaptive_gate": {
            "taker_stress_30bp_total_return_gt": 0.0,
            "taker_stress_30bp_sharpe_gte": MIN_RECENT_SHARPE,
            "taker_stress_30bp_max_drawdown_gt": MAX_RECENT_DRAWDOWN,
            "minimum_recent_bars": MIN_RECENT_BARS,
            "minimum_recent_entries": MIN_RECENT_ENTRIES,
            "severe_stress_50bp_total_return_gt": 0.0,
            "one_day_delay_total_return_gt": 0.0,
            "neighborhood_positive_return_share_gte": MIN_NEIGHBORHOOD_POSITIVE_SHARE,
            "neighborhood_sharpe_045_share_gte": MIN_NEIGHBORHOOD_SHARPE_045_SHARE,
        },
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW_COMPARISON",
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
        if close <= 0 or not (BAR_SCREEN_START <= stamp <= BAR_SCREEN_END):
            continue
        by_stamp[stamp] = {"timestamp": stamp, "close": close}
    return [by_stamp[key] for key in sorted(by_stamp)]


def _compound(returns: Sequence[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= max(1.0 + float(value), 1e-12)
    return equity - 1.0


def _sharpe(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if sigma <= 1e-15:
        return 0.0
    return fmean(returns) / sigma * sqrt(BARS_PER_YEAR)


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(1.0 + float(value), 1e-12)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _sma_series(
    bars: Sequence[Mapping[str, Any]],
    window: int,
) -> list[float | None]:
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
    momentum_days: int,
    sma_days: int,
    cost_per_turnover: float,
    start: datetime,
    end: datetime,
    signal_delay_days: int = 0,
) -> dict[str, Any]:
    closes = [float(row["close"]) for row in bars]
    stamps = [row["timestamp"] for row in bars]
    sma = _sma_series(bars, sma_days)
    minimum = max(momentum_days, sma_days)

    position = 0.0
    returns: list[float] = []
    gross_returns: list[float] = []
    positions: list[float] = []
    entries = 0
    exits = 0
    turnover = 0.0

    for index in range(minimum + 1 + signal_delay_days, len(bars)):
        signal_index = index - 1 - signal_delay_days
        anchor_index = signal_index - momentum_days
        average = sma[signal_index]
        if anchor_index < 0 or average is None:
            continue
        momentum_positive = (
            closes[signal_index] / closes[anchor_index] - 1.0
        ) > 0.0
        above_sma = closes[signal_index] > float(average)
        next_position = 1.0 if (momentum_positive or above_sma) else 0.0

        stamp = stamps[index]
        assert isinstance(stamp, datetime)
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
        "momentum_lookback_days": momentum_days,
        "sma_window_days": sma_days,
        "signal_delay_days": signal_delay_days,
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
    }


def evaluate_btc_consensus_trend_discovery(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    bars = normalize_bars(bars_by_symbol.get("BTC/USD", []))
    if len(bars) < max(MOMENTUM_LOOKBACK_DAYS, SMA_WINDOW_DAYS) + MIN_RECENT_BARS:
        raise ValueError(f"v14_r2g_daily_corpus_too_small:{len(bars)}")

    first = bars[0]["timestamp"]
    last = bars[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    if first >= ADAPTIVE_RECENT_START:
        raise ValueError("v14_r2g_pre_recent_history_missing")
    if last < ADAPTIVE_RECENT_START:
        raise ValueError("v14_r2g_recent_history_missing")

    development = _simulate(
        bars,
        momentum_days=MOMENTUM_LOOKBACK_DAYS,
        sma_days=SMA_WINDOW_DAYS,
        cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
        start=BAR_SCREEN_START,
        end=ADAPTIVE_RECENT_START,
    )
    scenarios = {
        name: _simulate(
            bars,
            momentum_days=MOMENTUM_LOOKBACK_DAYS,
            sma_days=SMA_WINDOW_DAYS,
            cost_per_turnover=cost,
            start=ADAPTIVE_RECENT_START,
            end=BAR_SCREEN_END,
        )
        for name, cost in COST_SCENARIOS.items()
    }
    delayed = _simulate(
        bars,
        momentum_days=MOMENTUM_LOOKBACK_DAYS,
        sma_days=SMA_WINDOW_DAYS,
        cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
        start=ADAPTIVE_RECENT_START,
        end=BAR_SCREEN_END,
        signal_delay_days=1,
    )

    neighborhood: list[dict[str, Any]] = []
    for momentum_days in MOMENTUM_NEIGHBORHOOD:
        for sma_days in SMA_NEIGHBORHOOD:
            neighborhood.append(
                _simulate(
                    bars,
                    momentum_days=momentum_days,
                    sma_days=sma_days,
                    cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
                    start=ADAPTIVE_RECENT_START,
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
        int(decisive["bar_count"]) >= MIN_RECENT_BARS
        and int(decisive["entry_count"]) >= MIN_RECENT_ENTRIES
        and float(decisive["total_return"]) > 0.0
        and float(decisive["sharpe"]) >= MIN_RECENT_SHARPE
        and float(decisive["max_drawdown"]) > MAX_RECENT_DRAWDOWN
        and float(severe["total_return"]) > 0.0
        and float(delayed["total_return"]) > 0.0
        and positive_share >= MIN_NEIGHBORHOOD_POSITIVE_SHARE
        and sharpe_045_share >= MIN_NEIGHBORHOOD_SHARPE_045_SHARE
    )

    return {
        "schema_version": "graen.v14-r2g.btc-consensus-trend.discovery.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto daily bars",
            "symbol": "BTC/USD",
            "bar_count": len(bars),
            "first_bar": first.isoformat(),
            "last_bar": last.isoformat(),
        },
        "development_reference": development,
        "adaptive_recent_window": {
            "start": ADAPTIVE_RECENT_START.isoformat(),
            "end": BAR_SCREEN_END.isoformat(),
            "scenarios": scenarios,
            "one_day_execution_delay": delayed,
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
        "adaptive_gate": {
            "survives_to_forward_shadow_comparison": survives,
            "requirements": campaign_manifest()["adaptive_gate"],
            "decisive_scenario": "taker_stress_30bp",
        },
        "interpretation": (
            "V14_R2G_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW_COMPARISON"
            if survives
            else "V14_R2G_ADAPTIVE_DISCOVERY_BROKER_FEASIBILITY_FAIL"
        ),
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW_COMPARISON",
        "shadow_only": survives,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
