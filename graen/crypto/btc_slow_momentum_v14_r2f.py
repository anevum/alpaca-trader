from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-slow-momentum-v14-r2f"
CAMPAIGN_ID = "v14-r2f-btc-daily-180d-momentum"
FAMILY = "btc_daily_slow_time_series_momentum"
UNIVERSE = ("BTC/USD",)

BAR_SCREEN_START = datetime(2021, 1, 1, tzinfo=UTC)
ADAPTIVE_RECENT_START = datetime(2025, 1, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

LOOKBACK_DAYS = 180
BARS_PER_YEAR = 365

# Per unit position change. The 30bp scenario deliberately adds execution
# stress above Alpaca's lowest-volume 25bp taker fee.
COST_SCENARIOS = {
    "maker_15bp": 0.0015,
    "taker_25bp": 0.0025,
    "taker_stress_30bp": 0.0030,
}

MIN_RECENT_BARS = 600
MIN_RECENT_ENTRIES = 2
MIN_RECENT_SHARPE = 0.50
MAX_RECENT_DRAWDOWN = -0.50
MIN_POSITIVE_QUARTER_SHARE = 0.50


@dataclass(frozen=True, slots=True)
class BtcSlowMomentumSpec:
    candidate_id: str = "V14-R2F-BTC-MOM-180D"
    lookback_days: int = LOOKBACK_DAYS
    symbol: str = "BTC/USD"
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_spec() -> BtcSlowMomentumSpec:
    return BtcSlowMomentumSpec()


def spec_from_dict(payload: Mapping[str, Any]) -> BtcSlowMomentumSpec:
    return BtcSlowMomentumSpec(**dict(payload))


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2f.btc-slow-momentum.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "candidate_spec": candidate_spec().to_dict(),
        "source_references": [
            {
                "title": "Time-series momentum and market timing in Bitcoin",
                "authors": ["Yeonchan Kang", "Doojin Ryu"],
                "year": 2026,
                "doi": "10.1057/s41283-026-00234-7",
                "relevance": (
                    "slow Bitcoin momentum signals outperform faster signals; "
                    "rapid adjustment is vulnerable to short-horizon noise"
                ),
            },
            {
                "title": "Dynamic time series momentum of cryptocurrencies",
                "author": "Oliver Borgards",
                "year": 2021,
                "doi": "10.1016/j.najef.2021.101428",
                "relevance": (
                    "documents cryptocurrency time-series momentum and a "
                    "risk-adjusted trading application"
                ),
            },
        ],
        "selection_disclosure": (
            "The fixed 180-day candidate was identified during adaptive ANEVUM "
            "research after 2025-2026 Alpaca history had already been inspected. "
            "Historical results therefore establish broker feasibility only and "
            "must not be described as independent validation or holdout evidence."
        ),
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW",
        "universe": list(UNIVERSE),
        "bar_timeframe": "1Day",
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "adaptive_recent_start": ADAPTIVE_RECENT_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "signal_rule": (
            "At day t, use only the completed close through t-1. Hold BTC long "
            "for day t when close[t-1] / close[t-1-180] - 1 > 0; otherwise flat."
        ),
        "cost_scenarios": dict(COST_SCENARIOS),
        "broker_feasibility_screen": {
            "taker_stress_30bp_total_return_gt": 0.0,
            "taker_stress_30bp_sharpe_gte": MIN_RECENT_SHARPE,
            "taker_stress_30bp_max_drawdown_gt": MAX_RECENT_DRAWDOWN,
            "minimum_recent_bars": MIN_RECENT_BARS,
            "minimum_recent_entries": MIN_RECENT_ENTRIES,
            "positive_time_quarter_share_gte": MIN_POSITIVE_QUARTER_SHARE,
        },
        "next_stage_if_pass": "BTC_DAILY_180D_MOMENTUM_FORWARD_SHADOW_ONLY",
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


def _positive_quarter_share(returns: Sequence[float]) -> float:
    if not returns:
        return 0.0
    observed = 0
    positive = 0
    for quarter in range(4):
        start = len(returns) * quarter // 4
        end = len(returns) * (quarter + 1) // 4
        chunk = returns[start:end]
        if not chunk:
            continue
        observed += 1
        if _compound(chunk) > 0:
            positive += 1
    return positive / observed if observed else 0.0


def momentum_long_at(
    bars: Sequence[Mapping[str, Any]],
    index: int,
    *,
    lookback_days: int = LOOKBACK_DAYS,
) -> bool | None:
    signal_index = index - 1
    anchor_index = signal_index - lookback_days
    if anchor_index < 0:
        return None
    current = float(bars[signal_index]["close"])
    anchor = float(bars[anchor_index]["close"])
    if current <= 0 or anchor <= 0:
        return None
    return current / anchor - 1.0 > 0.0


def _simulate(
    bars: Sequence[Mapping[str, Any]],
    *,
    cost_per_turnover: float,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    position = 0.0
    returns: list[float] = []
    gross_returns: list[float] = []
    positions: list[float] = []
    entries = 0
    exits = 0
    turnover = 0.0

    for index in range(1, len(bars)):
        signal = momentum_long_at(bars, index)
        if signal is None:
            continue
        next_position = 1.0 if signal else 0.0
        stamp = bars[index]["timestamp"]
        assert isinstance(stamp, datetime)
        if start <= stamp < end:
            change = abs(next_position - position)
            if next_position > position:
                entries += 1
            elif next_position < position:
                exits += 1
            turnover += change
            asset_return = (
                float(bars[index]["close"])
                / float(bars[index - 1]["close"])
                - 1.0
            )
            gross = next_position * asset_return
            net = gross - change * cost_per_turnover
            gross_returns.append(gross)
            returns.append(net)
            positions.append(next_position)
        position = next_position

    return {
        "lookback_days": LOOKBACK_DAYS,
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


def evaluate_btc_slow_momentum_discovery(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    bars = normalize_bars(bars_by_symbol.get("BTC/USD", []))
    if len(bars) < LOOKBACK_DAYS + MIN_RECENT_BARS:
        raise ValueError(f"v14_r2f_daily_corpus_too_small:{len(bars)}")

    first = bars[0]["timestamp"]
    last = bars[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    if first >= ADAPTIVE_RECENT_START:
        raise ValueError("v14_r2f_pre_recent_history_missing")
    if last < ADAPTIVE_RECENT_START:
        raise ValueError("v14_r2f_recent_history_missing")

    pre_recent = _simulate(
        bars,
        cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
        start=BAR_SCREEN_START,
        end=ADAPTIVE_RECENT_START,
    )
    scenarios = {
        name: _simulate(
            bars,
            cost_per_turnover=cost,
            start=ADAPTIVE_RECENT_START,
            end=BAR_SCREEN_END,
        )
        for name, cost in COST_SCENARIOS.items()
    }
    decisive = scenarios["taker_stress_30bp"]
    survives = bool(
        int(decisive["bar_count"]) >= MIN_RECENT_BARS
        and int(decisive["entry_count"]) >= MIN_RECENT_ENTRIES
        and float(decisive["total_return"]) > 0.0
        and float(decisive["sharpe"]) >= MIN_RECENT_SHARPE
        and float(decisive["max_drawdown"]) > MAX_RECENT_DRAWDOWN
        and float(decisive["positive_time_quarter_share"])
        >= MIN_POSITIVE_QUARTER_SHARE
    )

    return {
        "schema_version": "graen.v14-r2f.btc-slow-momentum.discovery.v1",
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
        "pre_recent_reference": pre_recent,
        "adaptive_recent_window": {
            "start": ADAPTIVE_RECENT_START.isoformat(),
            "end": BAR_SCREEN_END.isoformat(),
            "scenarios": scenarios,
            "independent_oos": False,
            "historical_holdout": False,
        },
        "broker_feasibility_screen": {
            "survives_to_forward_shadow": survives,
            "requirements": campaign_manifest()["broker_feasibility_screen"],
            "decisive_scenario": "taker_stress_30bp",
        },
        "interpretation": (
            "V14_R2F_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW"
            if survives
            else "V14_R2F_ADAPTIVE_DISCOVERY_BROKER_FEASIBILITY_FAIL"
        ),
        "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_SHADOW",
        "shadow_only": survives,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
