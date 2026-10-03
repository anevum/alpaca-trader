from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-4h-trend-v14-r2e"
CAMPAIGN_ID = "v14-r2e-btc-4h-sma-trend"
FAMILY = "btc_4h_sma_long_flat_trend"
UNIVERSE = ("BTC/USD",)

BAR_SCREEN_START = datetime(2021, 1, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)
OOS_START = datetime(2025, 1, 1, tzinfo=UTC)

# The external study reports a broad 4h SMA plateau from 125-300 bars.
# Selection is restricted to development data before OOS_START.
SMA_WINDOWS = (125, 150, 175, 200, 225, 250, 300)
BARS_PER_YEAR = 6 * 365

# Per unit turnover. Alpaca's lowest-volume tier is 15bp maker / 25bp taker.
# The decisive 30bp case adds 5bp of execution stress to the taker fee.
COST_SCENARIOS = {
    "maker_15bp": 0.0015,
    "taker_25bp": 0.0025,
    "taker_stress_30bp": 0.0030,
}

MIN_OOS_BARS = 1800
MIN_OOS_ENTRIES = 2
MIN_OOS_SHARPE = 0.50
MAX_OOS_DRAWDOWN = -0.50
MIN_POSITIVE_QUARTER_SHARE = 0.50


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2e.btc-4h-trend.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source_reference": {
            "repository": "iolufemi/crypto-trend-research",
            "paper": "paper/PAPER.md",
            "methodology": "docs/METHODOLOGY.md",
            "published_rule": "4h long/flat SMA trend; broad SMA125-300 robustness plateau",
            "published_btc_walk_forward": {
                "trend_plus_vol_target_sharpe": 1.15,
                "buy_hold_sharpe": 0.92,
                "trend_plus_vol_target_max_drawdown": -0.30,
                "buy_hold_max_drawdown": -0.50,
            },
            "note": (
                "R2E tests the lower-turnover long/flat trend core on Alpaca BTC/USD. "
                "It does not claim exact reproduction of the source portfolio layer."
            ),
        },
        "universe": list(UNIVERSE),
        "bar_timeframe": "4Hour",
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "development_end_exclusive": OOS_START.isoformat(),
        "oos_start": OOS_START.isoformat(),
        "sma_windows": list(SMA_WINDOWS),
        "selection_rule": (
            "select SMA window using development-only taker_stress_30bp Sharpe; "
            "tie-break by development total return then smaller window"
        ),
        "execution_convention": (
            "decision for bar t uses only close history through t-1; "
            "long when prior close is above prior-window SMA, otherwise flat; "
            "cost charged on absolute position change"
        ),
        "cost_scenarios": dict(COST_SCENARIOS),
        "oos_gate": {
            "taker_stress_30bp_total_return_gt": 0.0,
            "taker_stress_30bp_sharpe_gte": MIN_OOS_SHARPE,
            "taker_stress_30bp_max_drawdown_gt": MAX_OOS_DRAWDOWN,
            "minimum_oos_bars": MIN_OOS_BARS,
            "minimum_oos_entries": MIN_OOS_ENTRIES,
            "positive_time_quarter_share_gte": MIN_POSITIVE_QUARTER_SHARE,
        },
        "next_stage_if_pass": "BTC_4H_TREND_FORWARD_SHADOW_ONLY",
        "research_only": True,
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
            open_px = float(row.get("o", row.get("open")))
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError):
            continue
        if open_px <= 0 or close <= 0:
            continue
        if not (BAR_SCREEN_START <= stamp < BAR_SCREEN_END):
            continue
        by_stamp[stamp] = {
            "timestamp": stamp,
            "open": open_px,
            "close": close,
        }
    return [by_stamp[key] for key in sorted(by_stamp)]


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(1.0 + float(value), 1e-12)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


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


def _window_backtest(
    bars: Sequence[Mapping[str, Any]],
    *,
    window: int,
    cost_per_turnover: float,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    closes = [float(row["close"]) for row in bars]
    stamps = [row["timestamp"] for row in bars]

    returns: list[float] = []
    gross_returns: list[float] = []
    positions: list[float] = []
    entry_count = 0
    exit_count = 0
    turnover = 0.0
    previous_position = 0.0

    rolling_sum = sum(closes[:window])
    for i in range(window, len(bars)):
        # SMA and decision use data through i-1 only.
        prior_close = closes[i - 1]
        sma = rolling_sum / window
        position = 1.0 if prior_close > sma else 0.0

        stamp = stamps[i]
        assert isinstance(stamp, datetime)
        if start <= stamp < end:
            asset_return = closes[i] / closes[i - 1] - 1.0
            change = abs(position - previous_position)
            if position > previous_position:
                entry_count += 1
            elif position < previous_position:
                exit_count += 1
            turnover += change
            gross = position * asset_return
            net = gross - change * cost_per_turnover
            gross_returns.append(gross)
            returns.append(net)
            positions.append(position)

        previous_position = position

        # Advance rolling window from [i-window, i-1] to [i-window+1, i].
        rolling_sum += closes[i] - closes[i - window]

    return {
        "window": window,
        "cost_per_turnover": cost_per_turnover,
        "bar_count": len(returns),
        "entry_count": entry_count,
        "exit_count": exit_count,
        "turnover_units": turnover,
        "exposure_share": fmean(positions) if positions else 0.0,
        "gross_total_return": _compound(gross_returns),
        "total_return": _compound(returns),
        "sharpe": _sharpe(returns),
        "max_drawdown": _max_drawdown(returns),
        "positive_time_quarter_share": _positive_quarter_share(returns),
    }


def _buy_hold(
    bars: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    scoped = [
        row for row in bars
        if isinstance(row.get("timestamp"), datetime)
        and start <= row["timestamp"] < end
    ]
    returns = [
        float(scoped[i]["close"]) / float(scoped[i - 1]["close"]) - 1.0
        for i in range(1, len(scoped))
    ]
    return {
        "bar_count": len(returns),
        "total_return": _compound(returns),
        "sharpe": _sharpe(returns),
        "max_drawdown": _max_drawdown(returns),
        "positive_time_quarter_share": _positive_quarter_share(returns),
    }


def evaluate_btc_4h_trend_preflight(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    bars = normalize_bars(bars_by_symbol.get("BTC/USD", []))
    if not bars:
        raise ValueError("v14_r2e_btc_4h_corpus_empty")

    first = bars[0]["timestamp"]
    last = bars[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    if first >= OOS_START:
        raise ValueError("v14_r2e_development_corpus_missing")
    if last < OOS_START:
        raise ValueError("v14_r2e_oos_corpus_missing")

    development: dict[str, Any] = {}
    for window in SMA_WINDOWS:
        result = _window_backtest(
            bars,
            window=window,
            cost_per_turnover=COST_SCENARIOS["taker_stress_30bp"],
            start=BAR_SCREEN_START,
            end=OOS_START,
        )
        development[str(window)] = result

    viable = [
        row for row in development.values()
        if int(row["bar_count"]) >= 500
    ]
    if not viable:
        raise ValueError("v14_r2e_development_corpus_too_small")

    selected = max(
        viable,
        key=lambda row: (
            float(row["sharpe"]),
            float(row["total_return"]),
            -int(row["window"]),
        ),
    )
    selected_window = int(selected["window"])

    scenarios: dict[str, Any] = {}
    for name, cost in COST_SCENARIOS.items():
        scenarios[name] = _window_backtest(
            bars,
            window=selected_window,
            cost_per_turnover=cost,
            start=OOS_START,
            end=BAR_SCREEN_END,
        )

    decisive = scenarios["taker_stress_30bp"]
    buy_hold = _buy_hold(bars, start=OOS_START, end=BAR_SCREEN_END)
    survived = bool(
        int(decisive["bar_count"]) >= MIN_OOS_BARS
        and int(decisive["entry_count"]) >= MIN_OOS_ENTRIES
        and float(decisive["total_return"]) > 0.0
        and float(decisive["sharpe"]) >= MIN_OOS_SHARPE
        and float(decisive["max_drawdown"]) > MAX_OOS_DRAWDOWN
        and float(decisive["positive_time_quarter_share"])
        >= MIN_POSITIVE_QUARTER_SHARE
    )

    return {
        "schema_version": "graen.v14-r2e.btc-4h-trend.preflight.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto bars",
            "symbol": "BTC/USD",
            "timeframe": "4Hour",
            "bar_count": len(bars),
            "first_bar": first.isoformat(),
            "last_bar": last.isoformat(),
        },
        "development": {
            "cost_scenario": "taker_stress_30bp",
            "candidates": development,
            "selected_window": selected_window,
            "selected_metrics": selected,
            "oos_inspected_during_selection": False,
        },
        "oos": {
            "start": OOS_START.isoformat(),
            "selected_window": selected_window,
            "scenarios": scenarios,
            "buy_hold": buy_hold,
        },
        "broker_feasibility_gate": {
            "survives_to_shadow": survived,
            "requirements": campaign_manifest()["oos_gate"],
            "decisive_scenario": "taker_stress_30bp",
        },
        "interpretation": (
            "V14_R2E_SURVIVES_TO_BTC_4H_TREND_SHADOW"
            if survived
            else "V14_R2E_BROKER_FEASIBILITY_FAIL"
        ),
        "shadow_only": survived,
        "development_opened": True,
        "validation_opened": False,
        "holdout_opened": False,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
