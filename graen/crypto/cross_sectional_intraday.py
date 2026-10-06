"""Bounded cross-sectional intraday crypto research evaluator.

This evaluator mirrors the information set of RHEN's paper-only multi-asset
candidate without importing execution, broker, account, sizing, or risk code.
It accepts either one-minute or five-minute Alpaca bars, normalizes them to
complete five-minute bars, ranks only BTC/USD, ETH/USD, and SOL/USD, and uses
the same frozen high-cost/delay gates as the existing compiled GRAEN lane.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from graen.engineering import digest, stage_window, stamp
from .activity_shock_v9 import (
    Opportunity,
    development_gate,
    holdout_gate,
    simulate,
    summarize,
    validation_gate,
)


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-crypto-cross-sectional-intraday-v1"
UNIVERSE = ("BTC/USD", "ETH/USD", "SOL/USD")
BAR_MINUTES = 5
MIN_STAGE_COVERAGE = 0.80


def _f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def _raw_stamp(row: Mapping[str, Any]) -> datetime | None:
    raw = row.get("t", row.get("timestamp"))
    if raw is None:
        return None
    try:
        value = stamp(str(raw))
    except Exception:
        return None
    return value.astimezone(UTC)


def _source_is_one_minute(rows: Sequence[Mapping[str, Any]]) -> bool:
    values = sorted({
        value
        for row in rows
        if (value := _raw_stamp(row)) is not None
    })
    if len(values) < 3:
        return False
    deltas = [
        (right - left).total_seconds()
        for left, right in zip(values, values[1:])
        if right > left
    ]
    return bool(deltas and median(deltas[:200]) <= 90)


def _valid_ohlcv(row: Mapping[str, Any]) -> bool:
    o, h, l, c, v = (_f(row.get(key)) for key in ("o", "h", "l", "c", "v"))
    return (
        min(o, h, l, c) > 0
        and h >= max(o, c)
        and l <= min(o, c)
        and h >= l
        and v >= 0
    )


def _five_minute_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[datetime, dict[str, Any]]:
    """Return bars keyed by bar-end timestamp.

    Alpaca production research currently supplies one-minute bars while VELUM
    explicitly requests five-minute bars. Normalizing both forms here keeps
    GRAEN and VELUM on the same candidate semantics.
    """
    clean = [
        (value, dict(row))
        for row in rows
        if (value := _raw_stamp(row)) is not None and _valid_ohlcv(row)
    ]
    if not clean:
        return {}

    if not _source_is_one_minute([row for _, row in clean]):
        output: dict[datetime, dict[str, Any]] = {}
        for value, row in clean:
            output[value + timedelta(minutes=BAR_MINUTES)] = row
        return output

    buckets: dict[datetime, dict[datetime, dict[str, Any]]] = defaultdict(dict)
    for value, row in clean:
        bucket = value.replace(
            minute=value.minute - value.minute % BAR_MINUTES,
            second=0,
            microsecond=0,
        )
        buckets[bucket][value] = row

    output: dict[datetime, dict[str, Any]] = {}
    for bucket, members in buckets.items():
        expected = {
            bucket + timedelta(minutes=offset)
            for offset in range(BAR_MINUTES)
        }
        if set(members) != expected:
            continue
        ordered = [members[bucket + timedelta(minutes=i)] for i in range(BAR_MINUTES)]
        output[bucket + timedelta(minutes=BAR_MINUTES)] = {
            "t": bucket.isoformat(),
            "o": ordered[0]["o"],
            "h": max(_f(row["h"]) for row in ordered),
            "l": min(_f(row["l"]) for row in ordered),
            "c": ordered[-1]["c"],
            "v": sum(_f(row.get("v")) for row in ordered),
            "n": sum(_f(row.get("n")) for row in ordered),
        }
    return output


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[datetime, dict[str, Any]]]:
    return {
        symbol: _five_minute_rows(bars_by_symbol.get(symbol, ()))
        for symbol in UNIVERSE
    }


def verify_stage_corpus(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    expected = max(int((end - start).total_seconds() // 300), 1)
    counts: dict[str, int] = {}
    fractions: dict[str, float] = {}
    failed: list[str] = []
    for symbol in UNIVERSE:
        count = sum(
            1 for value in series.get(symbol, {})
            if start < value <= end
        )
        fraction = count / expected
        counts[symbol] = count
        fractions[symbol] = fraction
        if fraction < MIN_STAGE_COVERAGE:
            failed.append(symbol)
    report = {
        "expected_bars_per_symbol": expected,
        "counts": counts,
        "fractions": fractions,
        "minimum_fraction": MIN_STAGE_COVERAGE,
        "passed": not failed,
        "failed_symbols": failed,
    }
    if failed:
        raise ValueError(
            "cross_sectional_stage_corpus_incomplete:"
            + ",".join(sorted(failed))
        )
    return report


def _row(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    at: datetime,
) -> Mapping[str, Any] | None:
    return series.get(symbol, {}).get(at)


def _close(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    at: datetime,
) -> float | None:
    row = _row(series, symbol, at)
    value = _f(row.get("c")) if row else 0.0
    return value if value > 0 else None


def _return(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    at: datetime,
    minutes: int,
) -> float | None:
    left = _close(series, symbol, at - timedelta(minutes=minutes))
    right = _close(series, symbol, at)
    if left is None or right is None or left <= 0:
        return None
    return right / left - 1.0


def _window(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    at: datetime,
    minutes: int,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    cursor = at - timedelta(minutes=minutes - BAR_MINUTES)
    while cursor <= at:
        row = _row(series, symbol, cursor)
        if row is None:
            return []
        rows.append(row)
        cursor += timedelta(minutes=BAR_MINUTES)
    return rows


def _vwap(rows: Sequence[Mapping[str, Any]]) -> float | None:
    total_volume = sum(max(_f(row.get("v")), 0.0) for row in rows)
    if total_volume <= 0:
        return None
    value = sum(
        _f(row.get("c")) * max(_f(row.get("v")), 0.0)
        for row in rows
    )
    return value / total_volume


def _candidate_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: Mapping[str, Any],
    symbol: str,
    at: datetime,
) -> dict[str, Any] | None:
    params = spec["parameters"]
    current = _close(series, symbol, at)
    previous = _close(series, symbol, at - timedelta(minutes=BAR_MINUTES))
    ret5 = _return(series, symbol, at, 5)
    ret15 = _return(series, symbol, at, 15)
    ret60 = _return(series, symbol, at, 60)
    rows60 = _window(series, symbol, at, 60)
    if (
        current is None
        or previous is None
        or ret5 is None
        or ret15 is None
        or ret60 is None
        or len(rows60) != 12
    ):
        return None

    rolling_vwap = _vwap(rows60)
    if rolling_vwap is None or rolling_vwap <= 0:
        return None
    recent_high = max(_f(row.get("h")) for row in rows60)
    recent_low = min(_f(row.get("l")) for row in rows60)
    range60 = (recent_high - recent_low) / current if current > 0 else 0.0
    vwap_edge = (current - rolling_vwap) / rolling_vwap
    expected_move = max(max(ret15, 0.0), range60 * 0.50)
    score = (
        ret5 * 0.40
        + ret15 * 0.30
        + max(ret60, 0.0) * 0.15
        + range60 * 0.15
    )

    checks = {
        "rising": current > previous,
        "momentum_5m_ok": ret5 >= params["momentum_5m_min"],
        "momentum_15m_ok": ret15 >= params["momentum_15m_min"],
        "hour_floor_ok": ret60 >= params["momentum_60m_floor"],
        "above_vwap": current > rolling_vwap,
        "vwap_extension_ok": (
            vwap_edge <= params["max_vwap_extension_pct"]
        ),
        "expected_move_ok": (
            expected_move >= params["min_expected_move_pct"]
        ),
    }
    if not all(checks.values()):
        return None
    return {
        "symbol": symbol,
        "score": score,
        "return_5m": ret5,
        "return_15m": ret15,
        "return_60m": ret60,
        "range_60m_pct": range60,
        "vwap_edge_pct": vwap_edge,
        "expected_gross_move_pct": expected_move,
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
    at = start + timedelta(minutes=60)
    while at < end:
        ranked: list[dict[str, Any]] = []
        for symbol in spec["universe"]:
            if cooldown.get(symbol, datetime.min.replace(tzinfo=UTC)) > at:
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
                        "family": "cross_sectional_intraday_v1",
                        **candidate,
                    },
                )
            )
            cooldown[symbol] = at + timedelta(
                minutes=params["cooldown_minutes"]
            )
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
        primary = simulate(
            series,
            signals,
            start=start,
            end=end,
            scenario=cost,
        )
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
                seed=220000 + index * 2,
            ),
            "one_bar_delay": summarize(
                delayed,
                start=start,
                end=end,
                seed=220001 + index * 2,
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
