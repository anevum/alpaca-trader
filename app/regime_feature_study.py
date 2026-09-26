from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any, Iterable

from .entry_feature_study import FEATURES_FOR_QUARTILES
from .replay import d, stamp


DEFAULT_REGIME_REFERENCES = ("SPY", "QQQ", "SMH")
DEFAULT_REGIME_WINDOW = 5
DEFAULT_INTERACTION_HORIZON = 15
DEFAULT_BENCHMARK_MAP = {
    "AAPL": "QQQ",
    "MSFT": "QQQ",
    "TQQQ": "QQQ",
    "SMCI": "SMH",
    "SOXL": "SMH",
}
STRATEGY_005_INTERACTION_FEATURES = (
    *FEATURES_FOR_QUARTILES,
    "benchmark_window_return_pct",
    "benchmark_vwap_edge_pct",
    "relative_strength_pct",
)


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _return(current: Decimal, anchor: Decimal) -> Decimal:
    if current <= 0 or anchor <= 0:
        return Decimal("0")
    return (current - anchor) / anchor


def _session_vwap(bars: list[dict[str, Any]]) -> Decimal:
    total_volume = Decimal("0")
    weighted = Decimal("0")
    closes: list[Decimal] = []
    for bar in bars:
        close = d(bar.get("c"))
        closes.append(close)
        volume = d(bar.get("v"))
        bar_vwap = d(bar.get("vw")) or close
        if volume > 0 and bar_vwap > 0:
            total_volume += volume
            weighted += bar_vwap * volume
    if total_volume > 0:
        return weighted / total_volume
    positive = [value for value in closes if value > 0]
    return _mean(positive)


def _visible_session_bars(
    bars: Iterable[dict[str, Any]],
    *,
    decision_bar_time: datetime,
) -> list[dict[str, Any]]:
    session_day = decision_bar_time.date()
    visible = [
        bar
        for bar in bars
        if stamp(bar).date() == session_day
        and stamp(bar) <= decision_bar_time
    ]
    visible.sort(key=stamp)
    return visible


@dataclass(frozen=True)
class RegimeDecision:
    label: str
    available: bool
    breadth_score: int
    constructive_count: int
    weak_count: int
    neutral_count: int
    median_window_return_pct: Decimal
    details: dict[str, Any]


def classify_broad_market_regime(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    decision_bar_time: datetime,
    reference_symbols: tuple[str, ...] = DEFAULT_REGIME_REFERENCES,
    window: int = DEFAULT_REGIME_WINDOW,
) -> RegimeDecision:
    """Classify a coarse broad-market state using only visible reference bars.

    A reference is constructive when its completed close is above session VWAP
    and its close-to-close return over the configured window is positive.
    A reference is weak when it is below session VWAP and the window return is
    negative. Everything else is neutral.

    At least two references must agree before the market is labeled broad_up or
    broad_down. Otherwise the state is mixed. No candidate-symbol outcome data
    or future bars are used.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if len(reference_symbols) < 2:
        raise ValueError("at least two reference symbols are required")

    details: dict[str, Any] = {}
    constructive = 0
    weak = 0
    neutral = 0
    returns: list[Decimal] = []
    available = 0

    for raw_symbol in reference_symbols:
        symbol = raw_symbol.upper()
        visible = _visible_session_bars(
            bars_by_symbol.get(symbol, []),
            decision_bar_time=decision_bar_time,
        )
        needed = window + 1
        if len(visible) < needed:
            details[symbol] = {
                "available": False,
                "reason": f"need at least {needed} visible session bars",
            }
            continue

        current_close = d(visible[-1].get("c"))
        anchor_close = d(visible[-needed].get("c"))
        session_vwap = _session_vwap(visible)
        window_return = _return(current_close, anchor_close)

        above_vwap = current_close > session_vwap
        below_vwap = current_close < session_vwap
        positive_return = window_return > 0
        negative_return = window_return < 0

        state = "neutral"
        if above_vwap and positive_return:
            state = "constructive"
            constructive += 1
        elif below_vwap and negative_return:
            state = "weak"
            weak += 1
        else:
            neutral += 1

        available += 1
        returns.append(window_return)
        details[symbol] = {
            "available": True,
            "state": state,
            "close": str(current_close),
            "session_vwap": str(session_vwap),
            "window_return_pct": str(window_return),
            "above_vwap": above_vwap,
            "below_vwap": below_vwap,
        }

    if available < 2:
        return RegimeDecision(
            label="unavailable",
            available=False,
            breadth_score=0,
            constructive_count=constructive,
            weak_count=weak,
            neutral_count=neutral,
            median_window_return_pct=Decimal("0"),
            details=details,
        )

    if constructive >= 2:
        label = "broad_up"
    elif weak >= 2:
        label = "broad_down"
    else:
        label = "mixed"

    return RegimeDecision(
        label=label,
        available=True,
        breadth_score=constructive - weak,
        constructive_count=constructive,
        weak_count=weak,
        neutral_count=neutral,
        median_window_return_pct=Decimal(str(median(returns))) if returns else Decimal("0"),
        details=details,
    )


def attach_regimes(
    entry_study_result: dict[str, Any],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    reference_symbols: tuple[str, ...] = DEFAULT_REGIME_REFERENCES,
    window: int = DEFAULT_REGIME_WINDOW,
) -> dict[str, Any]:
    """Attach decision-time regime labels to entry-feature observations."""
    payload = deepcopy(entry_study_result)
    observations: list[dict[str, Any]] = []

    for row in entry_study_result.get("observations") or []:
        copied = deepcopy(row)
        raw_time = str(row.get("decision_bar_time") or "")
        if not raw_time:
            copied["market_regime"] = {
                "label": "unavailable",
                "available": False,
                "reason": "missing decision_bar_time",
            }
            observations.append(copied)
            continue

        decision_bar_time = datetime.fromisoformat(
            raw_time.replace("Z", "+00:00")
        )
        decision = classify_broad_market_regime(
            bars_by_symbol,
            decision_bar_time=decision_bar_time,
            reference_symbols=reference_symbols,
            window=window,
        )
        copied["market_regime"] = {
            "label": decision.label,
            "available": decision.available,
            "breadth_score": decision.breadth_score,
            "constructive_count": decision.constructive_count,
            "weak_count": decision.weak_count,
            "neutral_count": decision.neutral_count,
            "median_window_return_pct": str(
                decision.median_window_return_pct
            ),
            "details": decision.details,
        }
        observations.append(copied)

    payload["observations"] = observations
    payload["regime_study"] = {
        "reference_symbols": list(reference_symbols),
        "window_bars": window,
        "confirmation_symbols_unchanged": True,
        "future_data_used": False,
    }
    return payload



def attach_benchmark_alignment(
    observations: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    benchmark_map: dict[str, str] = DEFAULT_BENCHMARK_MAP,
    window: int = DEFAULT_REGIME_WINDOW,
) -> list[dict[str, Any]]:
    """Attach candidate-versus-benchmark features available at decision time."""
    output: list[dict[str, Any]] = []

    for row in observations:
        copied = deepcopy(row)
        symbol = str(row.get("symbol") or "").upper()
        benchmark = benchmark_map.get(symbol)
        raw_time = str(row.get("decision_bar_time") or "")
        features = dict(copied.get("features") or {})

        if not benchmark or not raw_time:
            features["benchmark_symbol"] = benchmark or ""
            features["benchmark_alignment_available"] = False
            copied["features"] = features
            output.append(copied)
            continue

        decision_bar_time = datetime.fromisoformat(
            raw_time.replace("Z", "+00:00")
        )
        candidate_bars = _visible_session_bars(
            bars_by_symbol.get(symbol, []),
            decision_bar_time=decision_bar_time,
        )
        benchmark_bars = _visible_session_bars(
            bars_by_symbol.get(benchmark, []),
            decision_bar_time=decision_bar_time,
        )
        needed = window + 1
        if len(candidate_bars) < needed or len(benchmark_bars) < needed:
            features["benchmark_symbol"] = benchmark
            features["benchmark_alignment_available"] = False
            copied["features"] = features
            output.append(copied)
            continue

        candidate_close = d(candidate_bars[-1].get("c"))
        candidate_anchor = d(candidate_bars[-needed].get("c"))
        benchmark_close = d(benchmark_bars[-1].get("c"))
        benchmark_anchor = d(benchmark_bars[-needed].get("c"))
        benchmark_vwap = _session_vwap(benchmark_bars)

        candidate_return = _return(candidate_close, candidate_anchor)
        benchmark_return = _return(benchmark_close, benchmark_anchor)
        benchmark_vwap_edge = (
            (benchmark_close - benchmark_vwap) / benchmark_vwap
            if benchmark_vwap > 0
            else Decimal("0")
        )

        features.update(
            {
                "benchmark_symbol": benchmark,
                "benchmark_alignment_available": True,
                "candidate_window_return_pct": str(candidate_return),
                "benchmark_window_return_pct": str(benchmark_return),
                "benchmark_vwap_edge_pct": str(benchmark_vwap_edge),
                "relative_strength_pct": str(
                    candidate_return - benchmark_return
                ),
            }
        )
        copied["features"] = features
        output.append(copied)

    return output

def _forward_metrics(
    rows: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    key = str(horizon)
    forward = [
        (row.get("forward") or {}).get(key) or {}
        for row in rows
    ]
    if not forward:
        return {
            "n": 0,
            "target_before_stop_rate": 0.0,
            "stop_before_target_rate": 0.0,
            "mean_mfe_pct": "0",
            "mean_mae_pct": "0",
            "mean_close_return_pct": "0",
            "barrier_balance": 0.0,
        }

    targets = sum(bool(item.get("target_before_stop")) for item in forward)
    stops = sum(bool(item.get("stop_before_target")) for item in forward)
    target_rate = targets / len(forward)
    stop_rate = stops / len(forward)
    return {
        "n": len(forward),
        "target_before_stop_rate": target_rate,
        "stop_before_target_rate": stop_rate,
        "mean_mfe_pct": str(
            _mean([d(item.get("mfe_pct")) for item in forward])
        ),
        "mean_mae_pct": str(
            _mean([d(item.get("mae_pct")) for item in forward])
        ),
        "mean_close_return_pct": str(
            _mean([d(item.get("close_return_pct")) for item in forward])
        ),
        "barrier_balance": target_rate - stop_rate,
    }


def summarize_regimes(
    observations: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (5, 15),
) -> dict[str, Any]:
    eligible = [
        row
        for row in observations
        if row.get("quality_allowed")
        and (row.get("market_regime") or {}).get("available")
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        label = str((row.get("market_regime") or {}).get("label") or "unavailable")
        grouped[label].append(row)

    result: dict[str, Any] = {}
    for label in ("broad_up", "mixed", "broad_down"):
        rows = grouped.get(label, [])
        result[label] = {
            "n": len(rows),
            "horizons": {
                str(horizon): _forward_metrics(rows, horizon=horizon)
                for horizon in horizons
            },
        }
    return result


def _feature_value(row: dict[str, Any], feature: str) -> Decimal:
    return d((row.get("features") or {}).get(feature))


def _quartile_edges(
    rows: list[dict[str, Any]],
    feature: str,
) -> list[Decimal]:
    values = sorted(
        _feature_value(row, feature)
        for row in rows
        if feature in (row.get("features") or {})
    )
    if len(values) < 8:
        return []
    indexes = [
        int((len(values) - 1) * 0.25),
        int((len(values) - 1) * 0.50),
        int((len(values) - 1) * 0.75),
    ]
    return [values[index] for index in indexes]


def _quartile_number(value: Decimal, edges: list[Decimal]) -> int:
    quartile = 1
    for edge in edges:
        if value > edge:
            quartile += 1
    return min(quartile, 4)


def feature_regime_interactions(
    observations: list[dict[str, Any]],
    *,
    horizon: int = DEFAULT_INTERACTION_HORIZON,
    min_regime_sample: int = 20,
    features: tuple[str, ...] = STRATEGY_005_INTERACTION_FEATURES,
) -> dict[str, Any]:
    """Describe how feature quartiles behave inside each broad-market regime.

    This is an interaction screen, not a rule generator. It intentionally
    avoids emitting thresholds as trade authorization.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if min_regime_sample <= 0:
        raise ValueError("min_regime_sample must be positive")

    eligible = [
        row
        for row in observations
        if row.get("quality_allowed")
        and (row.get("market_regime") or {}).get("available")
    ]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[str((row.get("market_regime") or {}).get("label"))].append(row)

    screens: dict[str, Any] = {}
    for label in ("broad_up", "mixed", "broad_down"):
        rows = grouped.get(label, [])
        regime_payload = {
            "n": len(rows),
            "eligible_for_interaction_screen": len(rows) >= min_regime_sample,
            "features": {},
        }
        if len(rows) < min_regime_sample:
            screens[label] = regime_payload
            continue

        for feature in features:
            edges = _quartile_edges(rows, feature)
            if len(edges) != 3:
                continue
            buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                if feature not in (row.get("features") or {}):
                    continue
                bucket = _quartile_number(
                    _feature_value(row, feature),
                    edges,
                )
                buckets[bucket].append(row)

            quartiles = {
                str(index): _forward_metrics(
                    buckets.get(index, []),
                    horizon=horizon,
                )
                for index in range(1, 5)
            }
            q1 = quartiles["1"]
            q4 = quartiles["4"]
            regime_payload["features"][feature] = {
                "quartile_edges": [str(edge) for edge in edges],
                "quartiles": quartiles,
                "q4_minus_q1_barrier_balance": (
                    float(q4["barrier_balance"])
                    - float(q1["barrier_balance"])
                ),
                "q4_minus_q1_mfe_pct": str(
                    d(q4["mean_mfe_pct"]) - d(q1["mean_mfe_pct"])
                ),
                "q4_minus_q1_mae_pct": str(
                    d(q4["mean_mae_pct"]) - d(q1["mean_mae_pct"])
                ),
            }
        screens[label] = regime_payload

    return {
        "status": "research_only",
        "horizon_minutes": horizon,
        "minimum_regime_sample": min_regime_sample,
        "regimes": screens,
        "candidate_frozen": False,
        "promotion_authorized": False,
        "scalable_capital_merge_allowed": False,
        "notes": [
            "Regime labels use only broad-market bars visible at the decision time.",
            "SPY/QQQ/SMH are regime references and do not change entry-confirmation requirements.",
            "Feature quartiles are descriptive interaction evidence, not live thresholds.",
            "A Strategy 005 candidate must be frozen separately before any holdout or shadow test.",
        ],
    }


def build_regime_report(
    entry_study_result: dict[str, Any],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    reference_symbols: tuple[str, ...] = DEFAULT_REGIME_REFERENCES,
    window: int = DEFAULT_REGIME_WINDOW,
    horizons: tuple[int, ...] = (5, 15),
    interaction_horizon: int = DEFAULT_INTERACTION_HORIZON,
    min_regime_sample: int = 20,
) -> dict[str, Any]:
    attached = attach_regimes(
        entry_study_result,
        bars_by_symbol,
        reference_symbols=reference_symbols,
        window=window,
    )
    observations = attach_benchmark_alignment(\n        attached.get("observations") or [],\n        bars_by_symbol,\n        window=window,\n    )
    return {
        "status": "research_only",
        "purpose": (
            "Test whether entry-feature behavior changes across coarse broad-"
            "market regimes before defining a new Strategy 005 entry gate."
        ),
        "reference_symbols": list(reference_symbols),
        "regime_window_bars": window,
        "entry_confirmation_symbols_changed": False,\n        "benchmark_map": DEFAULT_BENCHMARK_MAP,
        "regime_summary": summarize_regimes(
            observations,
            horizons=horizons,
        ),
        "feature_regime_interactions": feature_regime_interactions(
            observations,
            horizon=interaction_horizon,
            min_regime_sample=min_regime_sample,
        ),
        "observations": observations,
        "candidate_frozen": False,
        "promotion_authorized": False,
        "scalable_capital_merge_allowed": False,
    }
