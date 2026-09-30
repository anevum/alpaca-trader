from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from math import cos, pi, sin, sqrt
from statistics import fmean
from typing import Any, Mapping, Sequence

from .math_kernel import (
    arithmetic_return,
    rolling_realized_volatility,
    spread_bps,
    volatility_normalized_momentum,
)


METHODOLOGY_VERSION = "crypto-features-v1"


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def cyclic_time_features(now: datetime) -> dict[str, Any]:
    utc = now.astimezone(timezone.utc)
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    weekday = utc.weekday()
    return {
        "timestamp_utc": utc.isoformat(),
        "hour_utc": hour,
        "weekday_utc": weekday,
        "is_weekend": weekday >= 5,
        "hour_sin": sin(2 * pi * hour / 24.0),
        "hour_cos": cos(2 * pi * hour / 24.0),
        "weekday_sin": sin(2 * pi * weekday / 7.0),
        "weekday_cos": cos(2 * pi * weekday / 7.0),
    }


def _zscore(value: float, population: Sequence[float]) -> float | None:
    clean = [float(x) for x in population]
    if len(clean) < 3:
        return None
    mean = fmean(clean)
    variance = sum((x - mean) ** 2 for x in clean) / (len(clean) - 1)
    sd = sqrt(max(variance, 0.0))
    return (float(value) - mean) / sd if sd > 0 else 0.0


def bar_activity_state(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {
            "bar_count": 0,
            "trade_volume": 0.0,
            "trade_count": None,
            "zero_volume_bars": 0,
            "quote_derived_zero_volume_moves": 0,
            "active_trade_bars": 0,
        }
    zero_volume = 0
    quote_moves = 0
    active = 0
    total_volume = Decimal("0")
    total_trades = 0
    trade_count_available = False
    previous_close: Decimal | None = None
    for bar in bars:
        close = _d(bar.get("c"))
        volume = _d(bar.get("v"))
        total_volume += max(volume, Decimal("0"))
        count_raw = bar.get("n")
        if count_raw not in (None, ""):
            trade_count_available = True
            try:
                total_trades += max(int(count_raw), 0)
            except (TypeError, ValueError):
                pass
        if volume > 0:
            active += 1
        else:
            zero_volume += 1
            if previous_close is not None and close > 0 and close != previous_close:
                quote_moves += 1
        if close > 0:
            previous_close = close
    return {
        "bar_count": len(bars),
        "trade_volume": float(total_volume),
        "trade_count": total_trades if trade_count_available else None,
        "zero_volume_bars": zero_volume,
        "quote_derived_zero_volume_moves": quote_moves,
        "active_trade_bars": active,
    }


def crypto_feature_state(
    bars: Sequence[Mapping[str, Any]],
    quote: Mapping[str, Any] | None,
    *,
    now: datetime,
    fast_window: int,
    volatility_lookback: int,
) -> dict[str, Any]:
    completed = list(bars)
    closes = [_d(row.get("c")) for row in completed if _d(row.get("c")) > 0]
    activity = bar_activity_state(completed)
    bid = _d((quote or {}).get("bp"))
    ask = _d((quote or {}).get("ap"))
    bid_size = _d((quote or {}).get("bs"))
    ask_size = _d((quote or {}).get("as"))
    spread = spread_bps(bid, ask)
    quote_at = _timestamp((quote or {}).get("t"))
    quote_age_ms = (
        max((now.astimezone(timezone.utc) - quote_at.astimezone(timezone.utc)).total_seconds() * 1000.0, 0.0)
        if quote_at is not None else None
    )

    momentum = float(arithmetic_return(closes[-fast_window - 1], closes[-1])) if len(closes) > fast_window else 0.0
    realized_vol = rolling_realized_volatility(closes, min(volatility_lookback, max(len(closes) - 1, 1)))
    vol_norm_momentum = volatility_normalized_momentum(
        closes,
        min(fast_window, max(len(closes) - 1, 1)),
        min(volatility_lookback, max(len(closes) - 1, 1)),
    ) if len(closes) > 2 else 0.0

    rolling_returns = [
        float(arithmetic_return(closes[i - 1], closes[i]))
        for i in range(1, len(closes))
    ]
    recent_volumes = [float(max(_d(row.get("v")), Decimal("0"))) for row in completed]
    trade_counts = [
        float(row.get("n") or 0)
        for row in completed
        if row.get("n") not in (None, "")
    ]

    raw = {
        "momentum_return": momentum,
        "realized_volatility": realized_vol,
        "volatility_normalized_momentum": vol_norm_momentum,
        "spread_bps": float(spread),
        "bid": str(bid) if bid > 0 else None,
        "ask": str(ask) if ask > 0 else None,
        "bid_depth": str(bid_size) if bid_size > 0 else None,
        "ask_depth": str(ask_size) if ask_size > 0 else None,
        "available_depth": str(min(bid_size, ask_size)) if bid_size > 0 and ask_size > 0 else None,
        "quote_timestamp": quote_at.isoformat() if quote_at else None,
        "quote_age_ms": quote_age_ms,
        **activity,
    }
    normalized = {
        "momentum_z": _zscore(momentum, rolling_returns),
        "volume_z": _zscore(float(activity["trade_volume"]) / max(len(completed), 1), recent_volumes),
        "trade_activity_z": _zscore(float(activity["trade_count"] or 0) / max(len(completed), 1), trade_counts) if trade_counts else None,
        "spread_bps": float(spread),
        "volatility_normalized_momentum": vol_norm_momentum,
    }
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "market_lane": "crypto",
        "reference_population": "crypto_only_rolling",
        "raw": raw,
        "normalized": normalized,
        "time_state": cyclic_time_features(now),
        "semantics": {
            "zero_volume_quote_move_is_trade_activity": False,
            "volume_and_quote_movement_separated": True,
            "timezone": "UTC",
        },
    }
