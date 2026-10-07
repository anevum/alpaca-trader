"""Completed, contiguous source bars only; incomplete cross sections stay UNKNOWN."""
from datetime import timedelta
from statistics import median, pstdev
from zoneinfo import ZoneInfo

from app.nostra.runtime_regime import observe_regime
from .contracts import utc


def completed_features(store, now, *, fast_window=3, slow_window=8):
    now = utc(now)
    features, unavailable = {}, []
    for symbol in store.symbols:
        rows = [b for b in store.rows.get(symbol, {}).get("bars", ()) if utc(b["timestamp"])+timedelta(minutes=1) <= now]
        rows = rows[-max(16, slow_window):]
        if (not store.snapshot(symbol, now)["evaluable"] or len(rows) < max(16, slow_window)
                or any((utc(b["timestamp"])-utc(a["timestamp"])).total_seconds() != 60 for a,b in zip(rows, rows[1:]))
                or any(b.get("volume") is None or b["volume"] < 0 for b in rows)):
            unavailable.append(symbol)
            continue
        closes = [float(b["close"]) for b in rows]
        volume = sum(b["volume"] for b in rows)
        if volume <= 0:
            unavailable.append(symbol)
            continue
        # Same bar-VWAP-or-close arithmetic as the champion's completed-bar helper.
        vwap = sum(b.get("vwap", b["close"])*b["volume"] for b in rows)/volume
        fast, slow = sum(closes[-fast_window:])/fast_window, sum(closes[-slow_window:])/slow_window
        features[symbol] = {"return_5m": closes[-1]/closes[-6]-1, "return_15m": closes[-1]/closes[-16]-1,
            "vwap_edge_pct": closes[-1]/vwap-1, "fast_slow_spread_pct": fast/slow-1,
            "vwap": vwap, "feature_as_of": (utc(rows[-1]["timestamp"])+timedelta(minutes=1)).isoformat(),
            "bar_timestamp": rows[-1]["timestamp"], "source": "ALPACA/"+store.context[0],
            "provenance": "DERIVED", "methodology_version": "completed-rolling-bar-features-v1"}
    return features, unavailable


def regime_observation(store, now, *, fast_window=3, slow_window=8):
    features, unavailable = completed_features(store, now, fast_window=fast_window, slow_window=slow_window)
    benchmarks = {f"{s}_{m}m": features[s][f"return_{m}m"] for s in ("SPY", "QQQ", "IWM") if s in features for m in (5,15)}
    cross = {}
    if features and not unavailable:
        values = list(features.values())
        cross = {"breadth_above_vwap": sum(v["vwap_edge_pct"] > 0 for v in values)/len(values),
            "breadth_positive_5m": sum(v["return_5m"] > 0 for v in values)/len(values),
            "median_fast_slow_spread_pct": median(v["fast_slow_spread_pct"] for v in values),
            "median_vwap_edge_pct": median(v["vwap_edge_pct"] for v in values),
            "median_abs_return_5m": median(abs(v["return_5m"]) for v in values),
            "cross_sectional_dispersion_5m": pstdev(v["return_5m"] for v in values)}
    as_of = min((utc(v["feature_as_of"]) for v in features.values()), default=utc(now))
    minute = now.astimezone(ZoneInfo("America/New_York")).hour*60+now.astimezone(ZoneInfo("America/New_York")).minute-570
    regime = observe_regime(observed_at=now, feature_as_of=as_of, benchmark_returns=benchmarks, candidate_summary=cross,
        session_context={"minutes_from_open": minute, "time_segment": "OPENING" if 0 <= minute < 60 else "MIDDAY" if 60 <= minute < 300 else "LATE" if 300 <= minute < 390 else "OTHER"})
    return {**regime, "source": "RHEN/market_fabric", "provenance": "DERIVED",
        "cross_section_coverage": len(features)/len(store.symbols), "unavailable_symbols": unavailable,
        "feature_methodology_version": "completed-rolling-bar-features-v1"}, features
