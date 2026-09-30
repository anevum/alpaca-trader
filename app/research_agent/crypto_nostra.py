from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..crypto_features import cyclic_time_features


METHODOLOGY_VERSION = "nostra-crypto-regime-v1"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def infer_crypto_regime(
    *,
    feature_state: Mapping[str, Any],
    btc_context: Mapping[str, Any] | None = None,
    eth_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(feature_state.get("raw") or {})
    normalized = dict(feature_state.get("normalized") or {})
    time_state = dict(feature_state.get("time_state") or {})
    btc = dict(btc_context or {})
    eth = dict(eth_context or {})

    vol = _f(raw.get("realized_volatility"))
    spread = _f(raw.get("spread_bps"))
    depth = _f(raw.get("available_depth"))
    activity = _f(raw.get("trade_count") or raw.get("trade_volume"))
    momentum = _f(normalized.get("volatility_normalized_momentum"))
    btc_momentum = _f(btc.get("volatility_normalized_momentum"))
    eth_momentum = _f(eth.get("volatility_normalized_momentum"))

    if activity <= 0:
        regime = "LOW_ACTIVITY"
    elif spread > 50:
        regime = "WIDE_SPREAD"
    elif abs(momentum) >= 2.0 and vol > 0:
        regime = "TREND_EXPANSION"
    elif vol >= 0.01:
        regime = "HIGH_VOLATILITY"
    elif vol <= 0.001:
        regime = "LOW_VOLATILITY"
    else:
        regime = "BALANCED"

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "regime": regime,
        "market_lane": "crypto",
        "time_state": time_state,
        "features": {
            "realized_volatility": vol,
            "spread_bps": spread,
            "available_depth": depth,
            "trade_activity": activity,
            "momentum": momentum,
            "btc_momentum": btc_momentum,
            "eth_momentum": eth_momentum,
            "is_weekend": bool(time_state.get("is_weekend")),
            "hour_sin": time_state.get("hour_sin"),
            "hour_cos": time_state.get("hour_cos"),
            "weekday_sin": time_state.get("weekday_sin"),
            "weekday_cos": time_state.get("weekday_cos"),
        },
        "interpretation": "descriptive_only",
        "read_only": True,
        "execution_authority": False,
        "hard_coded_good_bad_hours": False,
    }
