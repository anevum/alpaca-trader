"""Cross-sectional acceleration research evaluator for GRAEN V3.

This mechanism is intentionally different from the exhausted V2 absolute
momentum/VWAP threshold sweep. It looks for a coin accelerating relative to
its peers while volume participation and bar-range expansion confirm the move.
The evaluator is research-only and shares the same sealed corpus, cost, delay,
and promotion gates as the other trusted GRAEN mechanisms.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from graen.engineering import digest, stage_window
from .activity_shock_v9 import (
    Opportunity,
    development_gate,
    holdout_gate,
    simulate,
    summarize,
    validation_gate,
)
from .cross_sectional_intraday import (
    BAR_MINUTES,
    UNIVERSE,
    _close,
    _return,
    _vwap,
    _window,
    build_series,
    verify_stage_corpus,
)


METHODOLOGY_VERSION = "graen-crypto-cross-sectional-acceleration-v1"


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _range_pct(row: Mapping[str, Any]) -> float:
    close = _f(row.get("c"))
    if close <= 0:
        return 0.0
    return max(_f(row.get("h")) - _f(row.get("l")), 0.0) / close


def _activity_features(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    at: datetime,
) -> tuple[float, float] | None:
    current = series.get(symbol, {}).get(at)
    prior = _window(series, symbol, at - timedelta(minutes=BAR_MINUTES), 60)
    if current is None or len(prior) != 12:
        return None

    prior_volumes = [_f(row.get("v")) for row in prior if _f(row.get("v")) > 0]
    prior_ranges = [_range_pct(row) for row in prior if _range_pct(row) > 0]
    if not prior_volumes or not prior_ranges:
        return None

    volume_base = median(prior_volumes)
    range_base = median(prior_ranges)
    if volume_base <= 0 or range_base <= 0:
        return None

    return (
        max(_f(current.get("v")), 0.0) / volume_base,
        _range_pct(current) / range_base,
    )


def _candidate_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: Mapping[str, Any],
    symbol: str,
    at: datetime,
) -> dict[str, Any] | None:
    params = spec["parameters"]
    current = _close(series, symbol, at)
    ret5 = _return(series, symbol, at, 5)
    ret15 = _return(series, symbol, at, 15)
    ret60 = _return(series, symbol, at, 60)
    rows60 = _window(series, symbol, at, 60)
    if (
        current is None
        or ret5 is None
        or ret15 is None
        or ret60 is None
        or len(rows60) != 12
    ):
        return None

    peer_returns = [
        value
        for peer in spec["universe"]
        if (value := _return(series, peer, at, 15)) is not None
    ]
    if len(peer_returns) != len(spec["universe"]):
        return None
    relative_15m = ret15 - median(peer_returns)
    acceleration_5m = ret5 - (ret15 / 3.0)

    rolling_vwap = _vwap(rows60)
    if rolling_vwap is None or rolling_vwap <= 0:
        return None
    vwap_edge = (current - rolling_vwap) / rolling_vwap

    activity = _activity_features(series, symbol, at)
    if activity is None:
        return None
    volume_ratio, range_ratio = activity

    recent_high = max(_f(row.get("h")) for row in rows60)
    recent_low = min(_f(row.get("l")) for row in rows60)
    range60 = (recent_high - recent_low) / current if current > 0 else 0.0
    expected_move = max(max(ret15, 0.0), range60 * 0.40)

    checks = {
        "positive_15m": ret15 > 0,
        "relative_strength_ok": relative_15m >= params["relative_15m_min"],
        "acceleration_ok": acceleration_5m >= params["acceleration_5m_min"],
        "volume_participation_ok": volume_ratio >= params["volume_ratio_min"],
        "range_expansion_ok": range_ratio >= params["range_ratio_min"],
        "above_vwap": current > rolling_vwap,
        "vwap_extension_ok": vwap_edge <= params["max_vwap_extension_pct"],
        "expected_move_ok": expected_move >= params["min_expected_move_pct"],
    }
    if not all(checks.values()):
        return None

    score = (
        relative_15m * 0.35
        + acceleration_5m * 0.30
        + max(ret15, 0.0) * 0.15
        + max(volume_ratio - 1.0, 0.0) * 0.0010
        + max(range_ratio - 1.0, 0.0) * 0.0010
    )
    return {
        "symbol": symbol,
        "score": score,
        "return_5m": ret5,
        "return_15m": ret15,
        "return_60m": ret60,
        "relative_15m": relative_15m,
        "acceleration_5m": acceleration_5m,
        "volume_ratio": volume_ratio,
        "range_ratio": range_ratio,
        "vwap_edge_pct": vwap_edge,
        "expected_gross_move_pct": expected_move,
        "checks": checks,
    }


def opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    params = spec["parameters"]
    result: list[Opportunity] = []
    cooldown: dict[str, datetime] = {}
    at = start + timedelta(minutes=65)
    while at < end:
        ranked: list[dict[str, Any]] = []
        for symbol in spec["universe"]:
            if cooldown.get(symbol, datetime.min.replace(tzinfo=at.tzinfo)) > at:
                continue
            candidate = _candidate_at(series, spec, symbol, at)
            if candidate is not None:
                ranked.append(candidate)
        ranked.sort(key=lambda row: (row["score"], row["symbol"]), reverse=True)
        for candidate in ranked[: params["rank_top_n"]]:
            symbol = str(candidate["symbol"])
            result.append(
                Opportunity(
                    candidate_id=spec["hypothesis_id"],
                    symbol=symbol,
                    opportunity_at=at,
                    hold_minutes=params["hold_minutes"],
                    signal={
                        "family": "cross_sectional_acceleration_v1",
                        **candidate,
                    },
                )
            )
            cooldown[symbol] = at + timedelta(minutes=params["cooldown_minutes"])
        at += timedelta(minutes=BAR_MINUTES)
    return result


def evaluate_stage(
    bars: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: Mapping[str, Any],
    stage: str,
    predecessor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    start, end = stage_window(spec, stage, predecessor)
    series = build_series(bars)
    coverage = verify_stage_corpus(series, start=start, end=end)
    signals = opportunities(series, spec, start, end)

    scenarios: dict[str, Any] = {}
    names = ("low", "base", "high") if stage == "holdout" else ("high",)
    for index, cost in enumerate(names):
        primary = simulate(series, signals, start=start, end=end, scenario=cost)
        delayed = simulate(
            series,
            signals,
            start=start,
            end=end,
            scenario=cost,
            extra_entry_delay_minutes=5,
        )
        scenarios[cost] = {
            "primary": summarize(
                primary,
                start=start,
                end=end,
                seed=230000 + index * 2,
            ),
            "one_bar_delay": summarize(
                delayed,
                start=start,
                end=end,
                seed=230001 + index * 2,
            ),
        }

    gate_spec = SimpleNamespace(concentration_limit=0.70)
    if stage == "development":
        passed, reasons = development_gate(scenarios["high"])
    elif stage == "validation":
        passed, reasons = validation_gate(gate_spec, scenarios["high"])
    else:
        passed, reasons = holdout_gate(gate_spec, scenarios)

    return {
        "stage": stage,
        "passed": passed,
        "reasons": reasons,
        "spec_hash": digest(spec),
        "epoch": spec["epoch"],
        "candidate_id": spec["hypothesis_id"],
        "mechanism": spec["mechanism"],
        "coverage": coverage,
        "opportunity_count": len(signals),
        "scenarios": scenarios,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "live_execution_authorized": False,
    }
