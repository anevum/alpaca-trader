from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import DataStatus, SourceTier

NY = ZoneInfo("America/New_York")


def parse_bar_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(NY)


def _float(bar: dict[str, Any], key: str) -> float | None:
    try:
        value = bar.get(key)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _completed(
    bars: list[dict[str, Any]],
    *,
    start: datetime | None = None,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    result = []
    for bar in bars:
        stamp = parse_bar_time(str(bar.get("t") or ""))
        if stamp >= cutoff:
            continue
        if start is not None and stamp < start:
            continue
        result.append(bar)
    return sorted(result, key=lambda item: str(item.get("t") or ""))


def _return_pct(bars: list[dict[str, Any]]) -> float | None:
    if len(bars) < 2:
        return None
    first = _float(bars[0], "o") or _float(bars[0], "c")
    last = _float(bars[-1], "c")
    if not first or last is None:
        return None
    return (last / first - 1.0) * 100.0


def _prior_close(
    daily_bars: list[dict[str, Any]],
    *,
    trade_date,
) -> float | None:
    eligible: list[tuple[datetime, float]] = []
    for bar in daily_bars:
        try:
            stamp = parse_bar_time(str(bar.get("t") or ""))
        except ValueError:
            continue
        close = _float(bar, "c")
        if close is not None and stamp.date() < trade_date:
            eligible.append((stamp, close))
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0])
    return eligible[-1][1]


def _vwap(bars: list[dict[str, Any]]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for bar in bars:
        volume = _float(bar, "v") or 0.0
        price = _float(bar, "vw")
        if price is None:
            high = _float(bar, "h")
            low = _float(bar, "l")
            close = _float(bar, "c")
            if high is None or low is None or close is None:
                continue
            price = (high + low + close) / 3.0
        if volume > 0:
            numerator += price * volume
            denominator += volume
    return numerator / denominator if denominator else None


def build_symbol_features(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    daily_bars: list[dict[str, Any]],
    cutoff: datetime,
    tier: SourceTier,
) -> dict[str, Any]:
    cutoff = cutoff.astimezone(NY)
    premarket_start = cutoff.replace(hour=4, minute=0, second=0, microsecond=0)
    premarket = _completed(bars, start=premarket_start, cutoff=cutoff)
    prior_close = _prior_close(daily_bars, trade_date=cutoff.date())

    latest = _float(premarket[-1], "c") if premarket else None
    latest_at = (
        parse_bar_time(str(premarket[-1].get("t") or ""))
        if premarket
        else None
    )
    freshness_seconds = (
        max((cutoff - latest_at).total_seconds(), 0.0)
        if latest_at is not None
        else None
    )
    status = DataStatus.UNAVAILABLE
    if premarket:
        status = (
            DataStatus.AVAILABLE
            if freshness_seconds is not None and freshness_seconds <= 600
            else DataStatus.DEGRADED
        )

    metrics: dict[str, float | int | None] = {
        "prior_close": prior_close,
        "last_price": latest,
        "premarket_return_pct": _return_pct(premarket),
        "gap_vs_prior_close_pct": (
            (latest / prior_close - 1.0) * 100.0
            if latest is not None and prior_close
            else None
        ),
        "premarket_vwap": _vwap(premarket),
        "premarket_volume": int(sum(_float(bar, "v") or 0.0 for bar in premarket)),
        "premarket_bar_count": len(premarket),
        "freshness_seconds": freshness_seconds,
    }

    if latest is not None and metrics["premarket_vwap"]:
        metrics["vwap_distance_pct"] = (
            latest / float(metrics["premarket_vwap"]) - 1.0
        ) * 100.0
    else:
        metrics["vwap_distance_pct"] = None

    highs = [_float(bar, "h") for bar in premarket]
    lows = [_float(bar, "l") for bar in premarket]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    if highs and lows and min(lows) > 0:
        metrics["premarket_range_pct"] = (max(highs) / min(lows) - 1.0) * 100.0
    else:
        metrics["premarket_range_pct"] = None

    for minutes in (5, 15, 30, 60):
        window = _completed(
            bars,
            start=cutoff - timedelta(minutes=minutes),
            cutoff=cutoff,
        )
        metrics[f"return_{minutes}m_pct"] = _return_pct(window)

    return {
        "symbol": symbol.upper(),
        "tier": tier.value,
        "status": status.value,
        "as_of": latest_at.isoformat() if latest_at else None,
        "metrics": metrics,
    }


def flatten_numeric_features(
    features_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, float]:
    flattened: dict[str, float] = {}
    for symbol, detail in sorted(features_by_symbol.items()):
        metrics = detail.get("metrics") or {}
        for name, value in metrics.items():
            if isinstance(value, bool):
                flattened[f"{symbol}.{name}"] = float(value)
            elif isinstance(value, (int, float)):
                flattened[f"{symbol}.{name}"] = float(value)
    return flattened


def summarize_state(
    features_by_symbol: dict[str, dict[str, Any]],
    *,
    target_symbols: tuple[str, ...],
) -> dict[str, Any]:
    direct_signs: list[int] = []
    proxy_signs: list[int] = []
    for symbol, detail in features_by_symbol.items():
        value = (detail.get("metrics") or {}).get("premarket_return_pct")
        if value is None:
            continue
        sign = 1 if float(value) > 0 else (-1 if float(value) < 0 else 0)
        if symbol in target_symbols:
            direct_signs.append(sign)
        else:
            proxy_signs.append(sign)

    if direct_signs and all(value > 0 for value in direct_signs):
        directional_state = "BROAD_POSITIVE"
    elif direct_signs and all(value < 0 for value in direct_signs):
        directional_state = "BROAD_NEGATIVE"
    else:
        directional_state = "MIXED_OR_INCOMPLETE"

    return {
        "directional_state": directional_state,
        "direct_positive": sum(value > 0 for value in direct_signs),
        "direct_negative": sum(value < 0 for value in direct_signs),
        "proxy_positive": sum(value > 0 for value in proxy_signs),
        "proxy_negative": sum(value < 0 for value in proxy_signs),
        "interpretation": (
            "Descriptive market state only; this label is not an entry signal "
            "and carries no authorization to trade."
        ),
    }
