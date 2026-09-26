from __future__ import annotations

from decimal import Decimal
from typing import Any

from .replay import d, price


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _session_vwap(bars: list[dict[str, Any]]) -> Decimal:
    total_volume = Decimal("0")
    weighted = Decimal("0")
    closes: list[Decimal] = []
    for bar in bars:
        close = price(bar)
        closes.append(close)
        volume = d(bar.get("v"))
        bar_vwap = d(bar.get("vw")) or close
        if volume > 0:
            total_volume += volume
            weighted += bar_vwap * volume
    if total_volume > 0:
        return weighted / total_volume
    return _mean([value for value in closes if value > 0])


def market_regime_features(
    confirmation_bars: dict[str, list[dict[str, Any]]],
    *,
    regime_window: int = 5,
    volatility_window: int = 8,
) -> dict[str, Any]:
    """Describe broad-market state using only bars visible at decision time.

    The constructive test mirrors the production regime idea: current close at
    or above the recent mean and session VWAP with a nonnegative regime-window
    return. Volatility and dispersion remain continuous research features; no
    candidate thresholds are chosen here.
    """
    references: dict[str, dict[str, Any]] = {}
    window_returns: list[Decimal] = []
    abs_returns: list[Decimal] = []
    vwap_distances: list[Decimal] = []
    constructive_count = 0

    for raw_symbol, bars in sorted(confirmation_bars.items()):
        symbol = str(raw_symbol).upper()
        if len(bars) < regime_window + 1:
            continue

        closes = [price(bar) for bar in bars if price(bar) > 0]
        if len(closes) < regime_window + 1:
            continue

        current = closes[-1]
        anchor = closes[-(regime_window + 1)]
        recent_mean = _mean(closes[-regime_window:])
        session_vwap = _session_vwap(bars)
        window_return = (
            (current - anchor) / anchor
            if anchor > 0
            else Decimal("0")
        )
        vwap_distance = (
            (current - session_vwap) / session_vwap
            if session_vwap > 0
            else Decimal("0")
        )

        one_minute_returns: list[Decimal] = []
        recent_closes = closes[-(volatility_window + 1):]
        for previous, latest in zip(recent_closes, recent_closes[1:]):
            if previous > 0:
                one_minute_returns.append((latest - previous) / previous)

        realized_abs_return = _mean(
            [abs(value) for value in one_minute_returns]
        )
        constructive = (
            current >= recent_mean
            and current >= session_vwap
            and window_return >= 0
        )
        if constructive:
            constructive_count += 1

        window_returns.append(window_return)
        abs_returns.append(realized_abs_return)
        vwap_distances.append(vwap_distance)
        references[symbol] = {
            "constructive": constructive,
            "window_return_pct": str(window_return),
            "realized_abs_1m_return_pct": str(realized_abs_return),
            "vwap_distance_pct": str(vwap_distance),
            "current_close": str(current),
            "recent_mean": str(recent_mean),
            "session_vwap": str(session_vwap),
        }

    available = len(references)
    constructive_fraction = (
        Decimal(constructive_count) / Decimal(available)
        if available
        else Decimal("0")
    )
    average_window_return = _mean(window_returns)
    average_abs_return = _mean(abs_returns)
    average_vwap_distance = _mean(vwap_distances)
    return_dispersion = (
        max(window_returns) - min(window_returns)
        if len(window_returns) >= 2
        else Decimal("0")
    )

    if available == 0:
        agreement_band = "unavailable"
    elif constructive_count == 0:
        agreement_band = "none"
    elif constructive_count == available:
        agreement_band = "full"
    else:
        agreement_band = "partial"

    return {
        "agreement_band": agreement_band,
        "references_available": available,
        "constructive_count": constructive_count,
        "constructive_fraction": str(constructive_fraction),
        "average_window_return_pct": str(average_window_return),
        "average_abs_1m_return_pct": str(average_abs_return),
        "return_dispersion_pct": str(return_dispersion),
        "average_vwap_distance_pct": str(average_vwap_distance),
        "references": references,
        "features": {
            "market_references_available": str(available),
            "market_constructive_count": str(constructive_count),
            "market_constructive_fraction": str(constructive_fraction),
            "market_avg_window_return_pct": str(average_window_return),
            "market_avg_abs_1m_return_pct": str(average_abs_return),
            "market_return_dispersion_pct": str(return_dispersion),
            "market_avg_vwap_distance_pct": str(average_vwap_distance),
        },
    }
