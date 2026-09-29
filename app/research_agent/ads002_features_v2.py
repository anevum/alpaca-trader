from __future__ import annotations

from datetime import datetime
from math import sqrt
from typing import Any, Mapping, Sequence


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ordered_bars(bars: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    stamped: list[tuple[datetime, Mapping[str, Any]]] = []
    for bar in bars:
        stamp = _timestamp(bar.get("t"))
        close = _f(bar.get("c"))
        if stamp is None or close is None or close <= 0:
            continue
        stamped.append((stamp, bar))
    if not stamped:
        return []
    stamped.sort(key=lambda item: item[0])
    latest_date = stamped[-1][0].date()
    return [bar for stamp, bar in stamped if stamp.date() == latest_date]


def _return(closes: Sequence[float], periods: int) -> float | None:
    if periods <= 0 or len(closes) <= periods:
        return None
    anchor = closes[-(periods + 1)]
    if anchor <= 0:
        return None
    return closes[-1] / anchor - 1.0


def _sample_std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(max(variance, 0.0))


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def build_ads002_v2_features(
    *,
    bars: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    market_quality: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    session = _ordered_bars(bars)
    closes = [float(bar["c"]) for bar in session if _f(bar.get("c")) not in (None, 0)]
    volumes = [max(_f(bar.get("v"), 0.0) or 0.0, 0.0) for bar in session]

    return_1m = _return(closes, 1)
    return_3m = _return(closes, 3)
    return_5m = _return(closes, 5)
    previous_1m = None
    if len(closes) >= 3 and closes[-3] > 0:
        previous_1m = closes[-2] / closes[-3] - 1.0
    accel_1m = (
        return_1m - previous_1m
        if return_1m is not None and previous_1m is not None
        else None
    )

    one_minute_returns: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous > 0:
            one_minute_returns.append(current / previous - 1.0)
    recent_vol = _sample_std(one_minute_returns[-5:]) if len(one_minute_returns) >= 5 else None
    prior_slice = one_minute_returns[-25:-5] if len(one_minute_returns) >= 10 else one_minute_returns[:-5]
    prior_vol = _sample_std(prior_slice) if len(prior_slice) >= 2 else None
    volatility_expansion_ratio = _safe_ratio(recent_vol, prior_vol)

    range_pcts: list[float] = []
    for bar in session:
        high = _f(bar.get("h"))
        low = _f(bar.get("l"))
        close = _f(bar.get("c"))
        if high is None or low is None or close is None or close <= 0:
            continue
        range_pcts.append(max(high - low, 0.0) / close)
    latest_range = range_pcts[-1] if range_pcts else None
    prior_ranges = range_pcts[-6:-1] if len(range_pcts) >= 6 else range_pcts[:-1]
    prior_range_mean = sum(prior_ranges) / len(prior_ranges) if prior_ranges else None
    range_expansion_ratio = _safe_ratio(latest_range, prior_range_mean)

    relative_volume_ratio = None
    if len(volumes) >= 2 and volumes[-1] > 0:
        history = [value for value in volumes[-21:-1] if value > 0]
        if history:
            relative_volume_ratio = volumes[-1] / (sum(history) / len(history))

    dollar_volume_5m = None
    if session:
        rows = session[-5:]
        pieces = []
        for bar in rows:
            close = _f(bar.get("c"))
            volume = _f(bar.get("v"))
            if close is not None and close > 0 and volume is not None and volume >= 0:
                pieces.append(close * volume)
        if pieces:
            dollar_volume_5m = sum(pieces)

    persistence = None
    if len(closes) >= 2:
        tail = closes[-9:]
        positive = sum(1 for previous, current in zip(tail, tail[1:]) if current > previous)
        persistence = positive / max(len(tail) - 1, 1)

    fast_average = _f(metadata.get("fast_average"))
    slow_average = _f(metadata.get("slow_average"))
    fast_slow_spread_pct = None
    if fast_average is not None and slow_average is not None and slow_average > 0:
        fast_slow_spread_pct = fast_average / slow_average - 1.0

    confirmations = dict(metadata.get("confirmations") or {})
    independent = [
        payload for symbol, payload in confirmations.items()
        if str(symbol).upper() != str(metadata.get("symbol") or "").upper()
    ]
    if not independent:
        independent = list(confirmations.values())
    confirmation_ratio = (
        sum(bool((payload or {}).get("ok")) for payload in independent) / len(independent)
        if independent else None
    )

    regime_confirmations = dict(metadata.get("regime_confirmations") or {})
    regime_ratio = (
        sum(bool((payload or {}).get("ok")) for payload in regime_confirmations.values())
        / len(regime_confirmations)
        if regime_confirmations else None
    )

    quality = dict(market_quality or metadata.get("market_quality") or {})
    spread_pct = _f(quality.get("spread_pct"))
    spread_bps = spread_pct * 10000.0 if spread_pct is not None else None
    bar_age_seconds = _f(quality.get("bar_age_seconds"))
    quote_age_seconds = _f(quality.get("quote_age_seconds"))

    return {
        "return_1m": return_1m,
        "return_3m": return_3m,
        "return_5m": return_5m,
        "abs_return_5m": abs(return_5m) if return_5m is not None else None,
        "accel_1m": accel_1m,
        "realized_vol_5m": recent_vol,
        "volatility_expansion_ratio": volatility_expansion_ratio,
        "range_expansion_ratio": range_expansion_ratio,
        "relative_volume_ratio": relative_volume_ratio,
        "relative_volume_ratio_raw": relative_volume_ratio,
        "dollar_volume_5m": dollar_volume_5m,
        "trend_persistence": persistence,
        "fast_slow_spread_pct": fast_slow_spread_pct,
        "momentum_pct": _f(metadata.get("momentum_pct")),
        "vwap_edge_pct": _f(metadata.get("vwap_edge_pct")),
        "confirmation_ratio": confirmation_ratio,
        "regime_ratio": regime_ratio,
        "spread_bps": spread_bps,
        "quote_age_ms": quote_age_seconds * 1000.0 if quote_age_seconds is not None else None,
        "bar_age_ms": bar_age_seconds * 1000.0 if bar_age_seconds is not None else None,
        "bar_count": len(session),
        "feature_time": metadata.get("bar_time"),
        "feature_source": "decision_time_observed_bars",
    }
