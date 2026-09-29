from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from statistics import median, pstdev
from typing import Any

from .nostra_regime import infer_market_regime

METHODOLOGY_VERSION = "nostra-session-state-v1"
TIMELINE_RESOLUTION_MINUTES = 5
BENCHMARKS = ("SPY", "QQQ", "IWM")


def _f(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _median(values: Sequence[float]) -> float | None:
    return median(values) if values else None


def _bucket_key(stamp: datetime) -> str:
    minute = stamp.minute - (stamp.minute % TIMELINE_RESOLUTION_MINUTES)
    return stamp.replace(minute=minute, second=0, microsecond=0).isoformat()


def _time_segment(stamp: datetime) -> str:
    minutes = stamp.hour * 60 + stamp.minute
    if minutes < 10 * 60 + 30:
        return "OPENING"
    if minutes < 14 * 60 + 30:
        return "MIDDAY"
    return "LATE"


def _regime_confirmation_return(
    candidates: Sequence[Mapping[str, Any]],
    symbol: str,
) -> float | None:
    for candidate in candidates:
        features = candidate.get("features")
        if not isinstance(features, Mapping):
            continue
        confirmations = features.get("regime_confirmations")
        if not isinstance(confirmations, Mapping):
            continue
        payload = confirmations.get(symbol)
        if not isinstance(payload, Mapping):
            continue
        value = _f(payload.get("window_return_pct"))
        if value is not None:
            return value
    return None


def _cycle_state(
    candidates: Sequence[Mapping[str, Any]],
    *,
    observed_at: str,
) -> dict[str, Any]:
    returns_5m: list[float] = []
    abs_returns_5m: list[float] = []
    trend_spreads: list[float] = []
    vwap_edges: list[float] = []
    relative_volumes: list[float] = []
    realized_vols: list[float] = []
    spreads_bps: list[float] = []
    qualified = 0
    above_vwap = 0

    for candidate in candidates:
        qualified += int(bool(candidate.get("qualified")))
        features = candidate.get("features")
        if not isinstance(features, Mapping):
            continue
        raw = features.get("ads002_v2_raw_features")
        raw = raw if isinstance(raw, Mapping) else {}

        r5 = _f(raw.get("return_5m"))
        if r5 is not None:
            returns_5m.append(r5)
            abs_returns_5m.append(abs(r5))

        trend = _f(raw.get("fast_slow_spread_pct"))
        if trend is not None:
            trend_spreads.append(trend)

        vwap = _f(features.get("vwap_edge_pct"))
        if vwap is None:
            vwap = _f(raw.get("vwap_edge_pct"))
        if vwap is not None:
            vwap_edges.append(vwap)
            above_vwap += int(vwap > 0)

        rv = _f(raw.get("relative_volume_ratio"))
        if rv is not None:
            relative_volumes.append(rv)

        vol = _f(raw.get("realized_vol_5m"))
        if vol is not None:
            realized_vols.append(vol)

        spread = _f(raw.get("spread_bps"))
        if spread is not None:
            spreads_bps.append(spread)

    count = len(candidates)
    benchmark_returns = {
        f"{symbol}_5m": _regime_confirmation_return(candidates, symbol)
        for symbol in BENCHMARKS
    }
    # The live v1 strategy currently persists a five-minute regime window.
    # A 15-minute benchmark return is intentionally left unavailable rather
    # than fabricated.
    benchmark_returns.update({
        f"{symbol}_15m": None for symbol in BENCHMARKS
    })

    stamp = _stamp(observed_at)
    segment = _time_segment(stamp) if stamp is not None else "OTHER"
    minutes_from_open = None
    if stamp is not None:
        minutes_from_open = (
            stamp.hour * 60 + stamp.minute - (9 * 60 + 30)
        )

    candidate_summary = {
        "breadth_above_vwap": (
            above_vwap / len(vwap_edges) if vwap_edges else None
        ),
        "breadth_positive_5m": (
            sum(value > 0 for value in returns_5m) / len(returns_5m)
            if returns_5m else None
        ),
        "median_fast_slow_spread_pct": _median(trend_spreads),
        "median_vwap_edge_pct": _median(vwap_edges),
        "median_abs_return_5m": _median(abs_returns_5m),
        "cross_sectional_dispersion_5m": (
            pstdev(returns_5m) if len(returns_5m) >= 2 else None
        ),
        "median_relative_volume": _median(relative_volumes),
        "eligible_candidate_ratio": (
            qualified / count if count else None
        ),
    }
    market_quality = {
        "average_spread_bps": (
            sum(spreads_bps) / len(spreads_bps) if spreads_bps else None
        ),
        "realized_volatility_5m": _median(realized_vols),
    }
    result = infer_market_regime(
        benchmark_returns=benchmark_returns,
        candidate_summary=candidate_summary,
        market_quality=market_quality,
        session_context={
            "time_segment": segment,
            "minutes_from_open": minutes_from_open,
        },
    )
    return {
        **result,
        "source_candidate_count": count,
        "observed_at": observed_at,
    }


def derive_nostra_regime_timeline(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive a five-minute NOSTRA timeline from canonical decision telemetry."""

    by_cycle: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    cycle_meta: dict[str, tuple[str, str, str]] = {}

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        cycle_id = str(
            candidate.get("scan_cycle_id")
            or (candidate.get("scan_cycle") or {}).get("scan_cycle_id")
            or ""
        )
        observed_at = str(candidate.get("observed_at") or "")
        if not cycle_id or not observed_at:
            continue
        session = str(candidate.get("session") or observed_at[:10])
        scan_cycle = candidate.get("scan_cycle")
        scan_cycle = scan_cycle if isinstance(scan_cycle, Mapping) else {}
        existing = cycle_meta.get(cycle_id)
        cycle_key = str(
            scan_cycle.get("cycle_key")
            or candidate.get("cycle_key")
            or (existing[2] if existing is not None else "")
            or cycle_id
        )
        by_cycle[cycle_id].append(candidate)
        if existing is None or observed_at >= existing[0]:
            cycle_meta[cycle_id] = (observed_at, session, cycle_key)

    # Keep the last complete RHEN cycle in each five-minute bucket.
    buckets: dict[str, tuple[str, str, str, str]] = {}
    for cycle_id, (observed_at, session, cycle_key) in cycle_meta.items():
        stamp = _stamp(observed_at)
        if stamp is None:
            continue
        bucket = _bucket_key(stamp)
        existing = buckets.get(bucket)
        if existing is None or observed_at > existing[0]:
            buckets[bucket] = (observed_at, session, cycle_key, cycle_id)

    timeline = []
    for bucket in sorted(buckets):
        observed_at, session, cycle_key, cycle_id = buckets[bucket]
        state = _cycle_state(
            by_cycle[cycle_id],
            observed_at=observed_at,
        )
        timeline.append(
            {
                "session": session,
                "bucket": bucket,
                "cycle_key": cycle_key,
                **state,
            }
        )

    latest = timeline[-1] if timeline else None
    transitions = 0
    previous = None
    for row in timeline:
        regime = row.get("regime")
        if previous is not None and regime != previous:
            transitions += 1
        previous = regime

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "timeline_resolution_minutes": TIMELINE_RESOLUTION_MINUTES,
        "observations": len(timeline),
        "regime_changes": transitions,
        "timeline": timeline,
        "latest": latest,
        "source": "canonical_decision_time_candidate_telemetry",
        "benchmark_15m_available": False,
        "read_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
    }
