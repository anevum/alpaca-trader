from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp
from typing import Any

METHODOLOGY_VERSION = "nostra-regime-state-v1"

REGIMES = (
    "TREND_EXPANSION",
    "TREND_DECAY",
    "BROAD_ADVANCE",
    "BROAD_DECLINE",
    "ROTATION",
    "HIGH_VOLATILITY",
    "LOW_VOLATILITY",
    "CHOP",
    "OPENING_DISCOVERY",
    "MIDDAY_COMPRESSION",
    "LATE_SESSION_EXPANSION",
    "UNKNOWN",
)

REQUIRED_FEATURES = (
    "benchmark_return_5m",
    "benchmark_return_15m",
    "breadth_above_vwap",
    "breadth_positive_5m",
    "median_fast_slow_spread_pct",
    "median_vwap_edge_pct",
    "median_abs_return_5m",
    "cross_sectional_dispersion_5m",
)


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = exp(max(value, -60.0))
    return z / (1.0 + z)


def _softmax(scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    highest = max(scores.values())
    weights = {key: exp(min(value - highest, 60.0)) for key, value in scores.items()}
    total = sum(weights.values())
    if total <= 0:
        return {key: 0.0 for key in scores}
    return {key: value / total for key, value in weights.items()}


def _time_segment(value: Any) -> str:
    segment = str(value or "").strip().upper()
    if segment in {"OPENING", "MIDDAY", "LATE"}:
        return segment
    return "OTHER"


def build_market_state(
    *,
    benchmark_returns: Mapping[str, Any],
    candidate_summary: Mapping[str, Any],
    market_quality: Mapping[str, Any] | None = None,
    session_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a transparent, point-in-time NOSTRA market-state vector.

    Inputs must already be based only on observations available at decision time.
    This function performs no market-data reads and has no execution authority.
    """

    quality = dict(market_quality or {})
    context = dict(session_context or {})

    spy_5 = _f(benchmark_returns.get("SPY_5m"))
    qqq_5 = _f(benchmark_returns.get("QQQ_5m"))
    iwm_5 = _f(benchmark_returns.get("IWM_5m"))
    spy_15 = _f(benchmark_returns.get("SPY_15m"))
    qqq_15 = _f(benchmark_returns.get("QQQ_15m"))
    iwm_15 = _f(benchmark_returns.get("IWM_15m"))

    five = [value for value in (spy_5, qqq_5, iwm_5) if value is not None]
    fifteen = [value for value in (spy_15, qqq_15, iwm_15) if value is not None]

    benchmark_5 = sum(five) / len(five) if five else None
    benchmark_15 = sum(fifteen) / len(fifteen) if fifteen else None

    state = {
        "benchmark_return_5m": benchmark_5,
        "benchmark_return_15m": benchmark_15,
        "benchmark_spy_5m": spy_5,
        "benchmark_qqq_5m": qqq_5,
        "benchmark_iwm_5m": iwm_5,
        "breadth_above_vwap": _f(candidate_summary.get("breadth_above_vwap")),
        "breadth_positive_5m": _f(candidate_summary.get("breadth_positive_5m")),
        "median_fast_slow_spread_pct": _f(
            candidate_summary.get("median_fast_slow_spread_pct")
        ),
        "median_vwap_edge_pct": _f(candidate_summary.get("median_vwap_edge_pct")),
        "median_abs_return_5m": _f(candidate_summary.get("median_abs_return_5m")),
        "cross_sectional_dispersion_5m": _f(
            candidate_summary.get("cross_sectional_dispersion_5m")
        ),
        "median_relative_volume": _f(candidate_summary.get("median_relative_volume")),
        "eligible_candidate_ratio": _f(candidate_summary.get("eligible_candidate_ratio")),
        "average_spread_bps": _f(quality.get("average_spread_bps")),
        "realized_volatility_5m": _f(quality.get("realized_volatility_5m")),
        "time_segment": _time_segment(context.get("time_segment")),
        "minutes_from_open": _f(context.get("minutes_from_open")),
        "feature_source": "point_in_time_market_state",
    }

    available = sum(state.get(name) is not None for name in REQUIRED_FEATURES)
    state["feature_completeness"] = available / len(REQUIRED_FEATURES)
    return state


def classify_regime(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic regime probabilities from transparent feature rules.

    The probabilities are research labels, not trade instructions. UNKNOWN grows
    when feature completeness is low or the observed state is internally
    contradictory.
    """

    r5 = _f(state.get("benchmark_return_5m"), 0.0) or 0.0
    r15 = _f(state.get("benchmark_return_15m"), 0.0) or 0.0
    breadth_vwap = _f(state.get("breadth_above_vwap"), 0.5)
    breadth_pos = _f(state.get("breadth_positive_5m"), 0.5)
    trend = _f(state.get("median_fast_slow_spread_pct"), 0.0) or 0.0
    vwap = _f(state.get("median_vwap_edge_pct"), 0.0) or 0.0
    movement = _f(state.get("median_abs_return_5m"), 0.0) or 0.0
    dispersion = _f(state.get("cross_sectional_dispersion_5m"), 0.0) or 0.0
    rv = _f(state.get("median_relative_volume"), 1.0)
    vol = _f(state.get("realized_volatility_5m"), movement)
    completeness = _clamp01(_f(state.get("feature_completeness"), 0.0) or 0.0)
    segment = _time_segment(state.get("time_segment"))

    breadth_vwap = 0.5 if breadth_vwap is None else _clamp01(breadth_vwap)
    breadth_pos = 0.5 if breadth_pos is None else _clamp01(breadth_pos)
    rv = 1.0 if rv is None else max(rv, 0.0)
    vol = 0.0 if vol is None else max(vol, 0.0)

    directional = 0.5 * r5 + 0.5 * r15
    breadth = 0.5 * (breadth_vwap - 0.5) + 0.5 * (breadth_pos - 0.5)
    aligned_up = _sigmoid(900.0 * directional + 450.0 * trend + 250.0 * vwap + 3.0 * breadth)
    aligned_down = _sigmoid(-900.0 * directional - 450.0 * trend - 250.0 * vwap - 3.0 * breadth)
    momentum_strength = _clamp01(abs(directional) / 0.004)
    breadth_strength = _clamp01(abs(breadth) / 0.35)
    dispersion_strength = _clamp01(dispersion / 0.008)
    volatility_strength = _clamp01(vol / 0.006)
    quiet_strength = 1.0 - volatility_strength
    rotation_strength = _clamp01(
        0.6 * dispersion_strength
        + 0.4 * (1.0 - min(abs(breadth) / 0.25, 1.0))
    )
    decay_signal = _clamp01(
        0.45 * _sigmoid(-800.0 * r5 * (1 if r15 >= 0 else -1))
        + 0.30 * (1.0 - breadth_strength)
        + 0.25 * _sigmoid(-500.0 * trend * (1 if r15 >= 0 else -1))
    )

    raw = {
        "TREND_EXPANSION": 1.8 * max(aligned_up, aligned_down) * momentum_strength,
        "TREND_DECAY": 1.4 * decay_signal * max(_clamp01(abs(r15) / 0.002), 0.25),
        "BROAD_ADVANCE": 1.6 * aligned_up * _clamp01((breadth_vwap + breadth_pos) / 2.0),
        "BROAD_DECLINE": 1.6 * aligned_down * _clamp01(1.0 - (breadth_vwap + breadth_pos) / 2.0),
        "ROTATION": 1.25 * rotation_strength,
        "HIGH_VOLATILITY": 1.2 * volatility_strength + 0.3 * min(max(rv - 1.0, 0.0), 1.0),
        "LOW_VOLATILITY": 1.0 * quiet_strength * (1.0 - momentum_strength),
        "CHOP": 1.3 * (1.0 - momentum_strength) * (1.0 - breadth_strength) * (0.5 + dispersion_strength),
        "OPENING_DISCOVERY": 1.1 if segment == "OPENING" else 0.0,
        "MIDDAY_COMPRESSION": 1.1 * quiet_strength if segment == "MIDDAY" else 0.0,
        "LATE_SESSION_EXPANSION": 1.1 * momentum_strength if segment == "LATE" else 0.0,
    }

    contradiction = _clamp01(
        abs((breadth_vwap - 0.5) - (breadth_pos - 0.5))
        + (0.35 if r5 * r15 < 0 else 0.0)
    )
    unfamiliarity = _clamp01((1.0 - completeness) * 1.25 + 0.45 * contradiction)
    raw["UNKNOWN"] = 0.35 + 2.0 * unfamiliarity

    probabilities = _softmax(raw)
    ordered = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    primary, primary_probability = ordered[0]
    second_probability = ordered[1][1] if len(ordered) > 1 else 0.0

    confidence = _clamp01(
        completeness
        * (primary_probability - second_probability + primary_probability)
        * (1.0 - 0.5 * unfamiliarity)
    )
    familiarity = _clamp01(completeness * (1.0 - unfamiliarity))

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "regime": primary,
        "probabilities": {
            key: round(value, 6) for key, value in sorted(probabilities.items())
        },
        "confidence": round(confidence, 6),
        "unknown_probability": round(probabilities.get("UNKNOWN", 0.0), 6),
        "market_familiarity": round(familiarity, 6),
        "feature_completeness": round(completeness, 6),
        "diagnostics": {
            "directional_return": round(directional, 8),
            "breadth_signal": round(breadth, 6),
            "momentum_strength": round(momentum_strength, 6),
            "dispersion_strength": round(dispersion_strength, 6),
            "volatility_strength": round(volatility_strength, 6),
            "contradiction": round(contradiction, 6),
            "unfamiliarity": round(unfamiliarity, 6),
        },
        "read_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
    }


def infer_market_regime(
    *,
    benchmark_returns: Mapping[str, Any],
    candidate_summary: Mapping[str, Any],
    market_quality: Mapping[str, Any] | None = None,
    session_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = build_market_state(
        benchmark_returns=benchmark_returns,
        candidate_summary=candidate_summary,
        market_quality=market_quality,
        session_context=session_context,
    )
    result = classify_regime(state)
    return {**result, "market_state": state}
