from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil, isfinite, sqrt
import random
from statistics import fmean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from app.research_agent.multiplicity import benjamini_yekutieli


METHODOLOGY_VERSION = "graen-crypto-native-v5"
REGISTRY_VERSION = "graen-crypto-candidate-registry-v5"

UNIVERSE = (
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "XRP/USD",
    "AVAX/USD",
    "LINK/USD",
)

DEVELOPMENT_START = datetime(2026, 2, 1, tzinfo=timezone.utc)
VALIDATION_START = datetime(2026, 4, 2, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2026, 5, 2, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2026, 6, 1, tzinfo=timezone.utc)
BAR_MINUTES = 5
OBSERVATION_MINUTES = 60

FEATURE_NAMES = (
    "absolute_return_15",
    "absolute_return_60",
    "market_residual_15",
    "market_residual_60",
    "negative_market_residual_15",
    "volnorm_60",
    "persistence_60",
    "acceleration_15_vs_60",
    "activity_ratio_60_vs_360",
)
FEATURE_HORIZONS = (30, 60, 120, 240)

COSTS_BPS: dict[str, dict[str, dict[str, float]]] = {
    "BTC/USD": {
        "spread": {"low": 2.076962, "base": 2.617880, "high": 3.591734},
        "slippage": {"low": 1.0, "base": 2.0, "high": 5.0},
    },
    "ETH/USD": {
        "spread": {"low": 2.200149, "base": 2.620987, "high": 3.710373},
        "slippage": {"low": 1.0, "base": 2.0, "high": 5.0},
    },
    "SOL/USD": {
        "spread": {"low": 7.360699, "base": 8.413967, "high": 12.024213},
        "slippage": {"low": 1.0, "base": 2.103492, "high": 6.012106},
    },
    "LINK/USD": {
        "spread": {"low": 17.813954, "base": 19.916186, "high": 23.479042},
        "slippage": {"low": 1.781395, "base": 4.979047, "high": 11.739521},
    },
    "XRP/USD": {
        "spread": {"low": 36.689904, "base": 38.695875, "high": 42.133423},
        "slippage": {"low": 3.668990, "base": 9.673969, "high": 21.066711},
    },
    "AVAX/USD": {
        "spread": {"low": 56.013078, "base": 59.736209, "high": 63.266699},
        "slippage": {"low": 5.601308, "base": 14.934052, "high": 31.633350},
    },
}


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    family: str
    source_feature: str | None
    timing: str
    hold_minutes: int
    complexity_score: int
    confirmatory: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Trade:
    candidate_id: str
    symbol: str
    opportunity_at: datetime
    entry_at: datetime
    exit_at: datetime
    entry_reference: float
    exit_reference: float
    net_return: float
    mae: float
    mfe: float
    hold_minutes: int
    scenario: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("opportunity_at", "entry_at", "exit_at"):
            payload[key] = payload[key].isoformat()
        return payload


def candidate_specs() -> tuple[CandidateSpec, ...]:
    rows: list[CandidateSpec] = []
    for hold in (60, 120, 240):
        rows.extend(
            [
                CandidateSpec("MOM_CONT", "simple_momentum", "absolute_return_60", "instant", hold, 1),
                CandidateSpec("RS_CONT", "market_residual_relative_strength", "market_residual_60", "instant", hold, 2),
                CandidateSpec("RS_DELAY15", "market_residual_relative_strength", "market_residual_60", "delayed_confirmation_15m", hold, 3),
                CandidateSpec("RS_PULLBACK", "market_residual_relative_strength", "market_residual_60", "pullback_reclaim_30m", hold, 4),
                CandidateSpec("VNT_CONT", "volatility_normalized_trend", "volnorm_60", "instant", hold, 2),
            ]
        )
    for hold in (60, 120):
        rows.append(
            CandidateSpec(
                "MR_BTC_NONBEAR",
                "controlled_mean_reversion",
                "negative_market_residual_15",
                "instant_btc_nonbearish",
                hold,
                3,
            )
        )
    return tuple(rows)


def benchmark_specs() -> tuple[CandidateSpec, ...]:
    return (
        CandidateSpec("BENCH_RMVWAP", "rolling_momentum_vwap", None, "instant", 60, 0, False),
        CandidateSpec("BENCH_COMPRESSION", "compression_breakout", None, "instant", 120, 0, False),
        CandidateSpec("RANDOM_CONTROL", "random_entry_control", None, "matched_random", 120, 0, False),
    )


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def _bar_end(bar: Mapping[str, Any]) -> datetime:
    return _stamp(bar.get("t")) + timedelta(minutes=BAR_MINUTES)


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[datetime, dict[str, Any]]]:
    output: dict[str, dict[datetime, dict[str, Any]]] = {}
    for symbol in UNIVERSE:
        series: dict[datetime, dict[str, Any]] = {}
        for raw in bars_by_symbol.get(symbol, ()):
            try:
                end = _bar_end(raw)
            except Exception:
                continue
            if not (DEVELOPMENT_START - timedelta(hours=8) <= end <= HOLDOUT_END):
                continue
            series[end] = dict(raw)
        output[symbol] = series
    return output


def _bar(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> Mapping[str, Any] | None:
    return series.get(symbol, {}).get(end)


def _close(series: Mapping[str, Mapping[datetime, Mapping[str, Any]]], symbol: str, end: datetime) -> float | None:
    row = _bar(series, symbol, end)
    if row is None:
        return None
    value = _f(row.get("c"))
    return value if value > 0 else None


def _open(series: Mapping[str, Mapping[datetime, Mapping[str, Any]]], symbol: str, end: datetime) -> float | None:
    row = _bar(series, symbol, end)
    if row is None:
        return None
    value = _f(row.get("o"))
    return value if value > 0 else None


def _ret(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return right / left - 1.0


def _window_bars(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
    minutes: int,
) -> list[Mapping[str, Any]]:
    steps = minutes // BAR_MINUTES
    rows: list[Mapping[str, Any]] = []
    for index in range(steps):
        row = _bar(series, symbol, end - timedelta(minutes=BAR_MINUTES * index))
        if row is None:
            return []
        rows.append(row)
    rows.reverse()
    return rows


def _window_returns(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
    minutes: int,
) -> list[float]:
    rows = _window_bars(series, symbol, end, minutes + BAR_MINUTES)
    if len(rows) < 2:
        return []
    closes = [_f(row.get("c")) for row in rows]
    if any(value <= 0 for value in closes):
        return []
    return [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]


def _activity_state(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> dict[str, Any]:
    rows = _window_bars(series, symbol, end, 60)
    if len(rows) != 12:
        return {"ok": False, "nonzero_trade_bars": 0, "trade_count": 0, "volume": 0.0}
    nonzero_trade_bars = sum(_f(row.get("n")) > 0 and _f(row.get("v")) > 0 for row in rows)
    trade_count = sum(int(_f(row.get("n"))) for row in rows)
    volume = sum(_f(row.get("v")) for row in rows)
    return {
        "ok": nonzero_trade_bars >= 6 and trade_count >= 12 and volume > 0,
        "nonzero_trade_bars": nonzero_trade_bars,
        "trade_count": trade_count,
        "volume": volume,
    }


def _market_returns(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    end: datetime,
    minutes: int,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for symbol in UNIVERSE:
        value = _ret(
            _close(series, symbol, end - timedelta(minutes=minutes)),
            _close(series, symbol, end),
        )
        if value is not None:
            values[symbol] = value
    return values


def _leave_one_out(value_map: Mapping[str, float], symbol: str) -> float | None:
    others = [value for name, value in value_map.items() if name != symbol]
    return fmean(others) if others else None


def feature_snapshot(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    end: datetime,
) -> dict[str, dict[str, float | bool | int]]:
    r15 = _market_returns(series, end, 15)
    r60 = _market_returns(series, end, 60)
    output: dict[str, dict[str, float | bool | int]] = {}

    for symbol in UNIVERSE:
        own15 = r15.get(symbol)
        own60 = r60.get(symbol)
        if own15 is None or own60 is None:
            continue
        factor15 = _leave_one_out(r15, symbol)
        factor60 = _leave_one_out(r60, symbol)
        if factor15 is None or factor60 is None:
            continue

        returns_60 = _window_returns(series, symbol, end, 60)
        returns_180 = _window_returns(series, symbol, end, 180)
        rows_60 = _window_bars(series, symbol, end, 60)
        rows_360 = _window_bars(series, symbol, end, 360)
        if len(returns_60) != 12 or len(returns_180) != 36 or len(rows_360) != 72:
            continue

        five_vol = pstdev(returns_180) if len(returns_180) > 1 else 0.0
        hourly_vol = five_vol * sqrt(12.0)
        persistence = sum(value > 0 for value in returns_60) / len(returns_60) - 0.5

        volume_60 = sum(_f(row.get("v")) for row in rows_60)
        volume_360 = sum(_f(row.get("v")) for row in rows_360)
        activity_ratio = 0.0
        if volume_360 > 0:
            baseline_hour = volume_360 / 6.0
            activity_ratio = volume_60 / baseline_hour - 1.0 if baseline_hour > 0 else 0.0

        activity = _activity_state(series, symbol, end)
        output[symbol] = {
            "absolute_return_15": own15,
            "absolute_return_60": own60,
            "market_residual_15": own15 - factor15,
            "market_residual_60": own60 - factor60,
            "negative_market_residual_15": factor15 - own15,
            "volnorm_60": own60 / hourly_vol if hourly_vol > 1e-12 else 0.0,
            "persistence_60": persistence,
            "acceleration_15_vs_60": own15 - own60 / 4.0,
            "activity_ratio_60_vs_360": activity_ratio,
            "btc_return_60": r60.get("BTC/USD", 0.0),
            "activity_ok": bool(activity["ok"]),
            "trade_count_60": int(activity["trade_count"]),
            "nonzero_trade_bars_60": int(activity["nonzero_trade_bars"]),
        }
    return output


def _rank(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for index in range(cursor, end):
            result[indexed[index][0]] = average_rank
        cursor = end
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    lm = fmean(left)
    rm = fmean(right)
    numerator = sum((a - lm) * (b - rm) for a, b in zip(left, right))
    lss = sum((a - lm) ** 2 for a in left)
    rss = sum((b - rm) ** 2 for b in right)
    if lss <= 0 or rss <= 0:
        return None
    return numerator / sqrt(lss * rss)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _correlation(_rank(left), _rank(right))


def _grid(start: datetime, end: datetime, step_minutes: int) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(minutes=step_minutes)


def _daily_means(values: Sequence[tuple[datetime, float]]) -> list[float]:
    groups: dict[date, list[float]] = defaultdict(list)
    for stamp, value in values:
        groups[stamp.date()].append(float(value))
    return [fmean(groups[key]) for key in sorted(groups)]


def _bootstrap_ci(values: Sequence[float], *, seed: int, replicates: int = 2000) -> dict[str, float | int]:
    data = [float(value) for value in values]
    n = len(data)
    if n < 2:
        mean_value = fmean(data) if data else 0.0
        return {"n": n, "mean": mean_value, "p05": mean_value, "p95": mean_value, "block_length": 0}
    block_length = max(2, min(10, int(ceil(sqrt(n)))))
    centered_starts = list(range(n))
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(replicates):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.choice(centered_starts)
            for offset in range(block_length):
                sample.append(data[(start + offset) % n])
                if len(sample) >= n:
                    break
        means.append(fmean(sample))
    means.sort()
    return {
        "n": n,
        "mean": fmean(data),
        "p05": means[int(0.05 * (replicates - 1))],
        "p95": means[int(0.95 * (replicates - 1))],
        "block_length": block_length,
    }


def feature_research(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    apply_multiplicity: bool,
) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    p_values: list[float] = []

    for feature in FEATURE_NAMES:
        for horizon in FEATURE_HORIZONS:
            observations: list[tuple[datetime, float]] = []
            for stamp in _grid(start, end, OBSERVATION_MINUTES):
                snapshot = feature_snapshot(series, stamp)
                feature_values: list[float] = []
                future_values: list[float] = []
                for symbol, row in snapshot.items():
                    forward = _ret(
                        _close(series, symbol, stamp),
                        _close(series, symbol, stamp + timedelta(minutes=horizon)),
                    )
                    value = row.get(feature)
                    if forward is None or not isinstance(value, (int, float)):
                        continue
                    feature_values.append(float(value))
                    future_values.append(float(forward))
                if len(feature_values) < 4:
                    continue
                ic = _spearman(feature_values, future_values)
                if ic is not None:
                    observations.append((stamp, ic))

            daily = _daily_means(observations)
            p_state = _moving_block_null_pvalue(
                daily,
                replicates=2000,
                seed=51000 + len(cells),
            )
            cell = {
                "feature": feature,
                "horizon_minutes": horizon,
                "hourly_cross_sections": len(observations),
                "independent_day_blocks": len(daily),
                "mean_daily_spearman_ic": fmean(daily) if daily else 0.0,
                "median_daily_spearman_ic": median(daily) if daily else 0.0,
                "positive_day_fraction": (
                    sum(value > 0 for value in daily) / len(daily) if daily else 0.0
                ),
                "bootstrap_ci": _bootstrap_ci(daily, seed=52000 + len(cells)),
                "null_test": p_state,
                "multiplicity_rejected": False,
            }
            cells.append(cell)
            p_values.append(float(p_state["p_value"]))

    multiplicity = None
    if apply_multiplicity:
        multiplicity_state = benjamini_yekutieli(p_values, alpha=0.05)
        rejected = set(multiplicity_state.rejected_indices)
        for index, cell in enumerate(cells):
            cell["multiplicity_rejected"] = index in rejected
        multiplicity = multiplicity_state.to_dict()

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tests": cells,
        "test_count": len(cells),
        "multiplicity": multiplicity,
    }


def _round_trip_cost_bps(symbol: str, scenario: str) -> float:
    state = COSTS_BPS[symbol]
    return float(state["spread"][scenario]) + 2.0 * float(state["slippage"][scenario])


def _fill_prices(symbol: str, entry: float, exit: float, scenario: str) -> tuple[float, float]:
    state = COSTS_BPS[symbol]
    half_spread = float(state["spread"][scenario]) / 20000.0
    slippage = float(state["slippage"][scenario]) / 10000.0
    return (
        entry * (1.0 + half_spread + slippage),
        exit * (1.0 - half_spread - slippage),
    )


def _source_magnitude(row: Mapping[str, Any], spec: CandidateSpec) -> float:
    if spec.family == "volatility_normalized_trend":
        return abs(float(row.get("absolute_return_60") or 0.0))
    if spec.family == "controlled_mean_reversion":
        return abs(float(row.get("market_residual_15") or 0.0))
    if spec.source_feature:
        return abs(float(row.get(spec.source_feature) or 0.0))
    return abs(float(row.get("absolute_return_60") or 0.0))


def _market_quality(row: Mapping[str, Any], symbol: str, spec: CandidateSpec) -> bool:
    if not bool(row.get("activity_ok")):
        return False
    hurdle = _round_trip_cost_bps(symbol, "high") / 10000.0
    return _source_magnitude(row, spec) > hurdle


def _opportunity_symbols(
    snapshot: Mapping[str, Mapping[str, Any]],
    spec: CandidateSpec,
) -> list[str]:
    if spec.family == "random_entry_control":
        return [symbol for symbol, row in snapshot.items() if bool(row.get("activity_ok"))]

    if spec.family == "rolling_momentum_vwap" or spec.family == "compression_breakout":
        return list(snapshot)

    rows: list[tuple[float, str]] = []
    for symbol, state in snapshot.items():
        if not spec.source_feature:
            continue
        value = float(state.get(spec.source_feature) or 0.0)
        if value <= 0:
            continue
        if spec.family == "controlled_mean_reversion" and float(state.get("btc_return_60") or 0.0) < 0:
            continue
        if not _market_quality(state, symbol, spec):
            continue
        rows.append((value, symbol))
    rows.sort(reverse=True)
    return [symbol for _, symbol in rows[:2]]


def _rolling_vwap(rows: Sequence[Mapping[str, Any]]) -> float:
    weighted = 0.0
    volume = 0.0
    for row in rows:
        v = _f(row.get("v"))
        if v <= 0:
            continue
        price = _f(row.get("vw") or row.get("c"))
        if price <= 0:
            continue
        weighted += price * v
        volume += v
    return weighted / volume if volume > 0 else 0.0


def _rmvwap_ok(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> bool:
    rows = _window_bars(series, symbol, end, 240)
    if len(rows) != 48:
        return False
    closes = [_f(row.get("c")) for row in rows]
    if any(value <= 0 for value in closes):
        return False
    fast = fmean(closes[-3:])
    slow = fmean(closes[-8:])
    momentum = closes[-1] / closes[-4] - 1.0
    vwap = _rolling_vwap(rows)
    if vwap <= 0:
        return False
    extension = closes[-1] / vwap - 1.0
    activity = _activity_state(series, symbol, end)
    return bool(
        activity["ok"]
        and fast > slow
        and closes[-1] > closes[-2]
        and momentum >= 0.0005
        and closes[-1] > vwap
        and 0 <= extension <= 0.008
        and momentum > _round_trip_cost_bps(symbol, "high") / 10000.0
    )


def _compression_ok(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> bool:
    rows = _window_bars(series, symbol, end, 155)
    if len(rows) != 31:
        return False
    closes = [_f(row.get("c")) for row in rows]
    if any(value <= 0 for value in closes):
        return False
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
    short_vol = pstdev(returns[-8:]) if len(returns) >= 8 else 0.0
    long_vol = pstdev(returns[-30:]) if len(returns) >= 30 else 0.0
    prior_high = max(_f(row.get("h")) for row in rows[-13:-1])
    breakout = closes[-1] > prior_high * 1.0004
    move = closes[-1] / closes[-13] - 1.0
    activity = _activity_state(series, symbol, end)
    return bool(
        activity["ok"]
        and long_vol > 0
        and short_vol <= 0.60 * long_vol
        and breakout
        and move > _round_trip_cost_bps(symbol, "high") / 10000.0
    )


def _timed_entry(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    opportunity_at: datetime,
    spec: CandidateSpec,
) -> datetime | None:
    if spec.timing in {"instant", "instant_btc_nonbearish"}:
        return opportunity_at

    if spec.timing == "delayed_confirmation_15m":
        delayed = opportunity_at + timedelta(minutes=15)
        original = feature_snapshot(series, opportunity_at).get(symbol, {})
        current = feature_snapshot(series, delayed).get(symbol, {})
        if not original or not current or not spec.source_feature:
            return None
        if float(current.get(spec.source_feature) or 0.0) <= 0:
            return None
        first = _close(series, symbol, opportunity_at)
        latest = _close(series, symbol, delayed)
        if first is None or latest is None or latest <= first:
            return None
        return delayed

    if spec.timing == "pullback_reclaim_30m":
        original = _close(series, symbol, opportunity_at)
        if original is None:
            return None
        pulled_back = False
        for offset in range(5, 31, 5):
            current_at = opportunity_at + timedelta(minutes=offset)
            current = _close(series, symbol, current_at)
            previous = _close(series, symbol, current_at - timedelta(minutes=5))
            if current is None or previous is None:
                continue
            draw = current / original - 1.0
            if -0.012 <= draw <= -0.001:
                pulled_back = True
            recent = [
                _close(series, symbol, current_at - timedelta(minutes=5 * index))
                for index in range(3)
            ]
            if pulled_back and all(value is not None for value in recent):
                recent_values = [float(value) for value in recent if value is not None]
                if current > previous and current >= fmean(recent_values):
                    return current_at
        return None

    return None


def _trade(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    symbol: str,
    opportunity_at: datetime,
    entry_at: datetime,
    spec: CandidateSpec,
    scenario: str,
) -> Trade | None:
    entry_bar_end = entry_at + timedelta(minutes=BAR_MINUTES)
    exit_at = entry_at + timedelta(minutes=spec.hold_minutes)
    exit_bar_end = exit_at + timedelta(minutes=BAR_MINUTES)
    entry_reference = _open(series, symbol, entry_bar_end)
    exit_reference = _open(series, symbol, exit_bar_end)
    if entry_reference is None or exit_reference is None:
        return None
    entry_fill, exit_fill = _fill_prices(symbol, entry_reference, exit_reference, scenario)
    if entry_fill <= 0 or exit_fill <= 0:
        return None
    net_return = exit_fill / entry_fill - 1.0

    highs: list[float] = []
    lows: list[float] = []
    current = entry_bar_end
    while current <= exit_at:
        row = _bar(series, symbol, current)
        if row is not None:
            highs.append(_f(row.get("h")))
            lows.append(_f(row.get("l")))
        current += timedelta(minutes=BAR_MINUTES)
    mfe = max((value / entry_reference - 1.0 for value in highs if value > 0), default=0.0)
    mae = min((value / entry_reference - 1.0 for value in lows if value > 0), default=0.0)

    return Trade(
        candidate_id=f"{spec.candidate_id}_{spec.hold_minutes}",
        symbol=symbol,
        opportunity_at=opportunity_at,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_reference=entry_reference,
        exit_reference=exit_reference,
        net_return=net_return,
        mae=mae,
        mfe=mfe,
        hold_minutes=spec.hold_minutes,
        scenario=scenario,
    )


def simulate_candidate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: CandidateSpec,
    *,
    start: datetime,
    end: datetime,
    scenario: str,
    random_seed: int = 0,
    excluded_symbol: str | None = None,
    extra_entry_delay_minutes: int = 0,
) -> list[Trade]:
    trades: list[Trade] = []
    busy_until: dict[str, datetime] = {}
    rng = random.Random(random_seed)

    for stamp in _grid(start, end, OBSERVATION_MINUTES):
        snapshot = feature_snapshot(series, stamp)
        if not snapshot:
            continue

        if spec.family == "rolling_momentum_vwap":
            symbols = [symbol for symbol in UNIVERSE if _rmvwap_ok(series, symbol, stamp)]
        elif spec.family == "compression_breakout":
            symbols = [symbol for symbol in UNIVERSE if _compression_ok(series, symbol, stamp)]
        else:
            symbols = _opportunity_symbols(snapshot, spec)

        if excluded_symbol:
            symbols = [symbol for symbol in symbols if symbol != excluded_symbol]

        if spec.family == "random_entry_control":
            symbols = [symbol for symbol in symbols if _round_trip_cost_bps(symbol, "high") <= 100.0]
            rng.shuffle(symbols)
            symbols = symbols[:1]

        for symbol in symbols:
            entry_at = _timed_entry(series, symbol, stamp, spec)
            if spec.family in {"rolling_momentum_vwap", "compression_breakout", "random_entry_control"}:
                entry_at = stamp
            if entry_at is None:
                continue
            entry_at += timedelta(minutes=extra_entry_delay_minutes)
            if entry_at >= end:
                continue
            if busy_until.get(symbol, datetime.min.replace(tzinfo=timezone.utc)) > entry_at:
                continue
            trade = _trade(
                series,
                symbol=symbol,
                opportunity_at=stamp,
                entry_at=entry_at,
                spec=spec,
                scenario=scenario,
            )
            if trade is None or trade.exit_at > end:
                continue
            trades.append(trade)
            busy_until[symbol] = trade.exit_at
    return trades


def _profit_factor(returns: Sequence[float]) -> float | None:
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    if losses <= 0:
        return None
    return gains / losses


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= max(1.0 + float(value), 1e-9)
        peak = max(peak, equity)
        maximum = max(maximum, 1.0 - equity / peak)
    return maximum


def _concentration(values: Sequence[str]) -> dict[str, Any]:
    if not values:
        return {"max_share": 0.0, "hhi": 0.0, "counts": {}}
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    n = len(values)
    shares = [count / n for count in counts.values()]
    return {
        "max_share": max(shares),
        "hhi": sum(share * share for share in shares),
        "counts": dict(sorted(counts.items())),
    }


def summarize_trades(trades: Sequence[Trade], *, seed: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: (trade.entry_at, trade.symbol))
    returns = [trade.net_return for trade in ordered]
    daily_pairs = [(trade.entry_at, trade.net_return) for trade in ordered]
    daily = _daily_means(daily_pairs)
    block_expectancies: list[float] = []
    if ordered:
        start_day = ordered[0].entry_at.date()
        groups: dict[int, list[float]] = defaultdict(list)
        for trade in ordered:
            bucket = min((trade.entry_at.date() - start_day).days // 10, 2)
            groups[bucket].append(trade.net_return)
        block_expectancies = [fmean(groups[index]) if groups[index] else 0.0 for index in range(3)]

    tail = sorted(returns)
    tail_count = max(1, int(ceil(len(tail) * 0.05))) if tail else 0
    weekday = [trade.net_return for trade in ordered if trade.entry_at.weekday() < 5]
    weekend = [trade.net_return for trade in ordered if trade.entry_at.weekday() >= 5]

    return {
        "trade_count": len(ordered),
        "expectancy_per_trade": fmean(returns) if returns else 0.0,
        "median_trade_return": median(returns) if returns else 0.0,
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        "profit_factor": _profit_factor(returns),
        "max_drawdown": _max_drawdown(returns),
        "tail_loss_05": fmean(tail[:tail_count]) if tail_count else None,
        "average_mae": fmean([trade.mae for trade in ordered]) if ordered else 0.0,
        "average_mfe": fmean([trade.mfe for trade in ordered]) if ordered else 0.0,
        "average_holding_minutes": fmean([trade.hold_minutes for trade in ordered]) if ordered else 0.0,
        "return_per_hour": (
            (fmean(returns) / fmean([trade.hold_minutes for trade in ordered]) * 60.0)
            if ordered else 0.0
        ),
        "symbol_concentration": _concentration([trade.symbol for trade in ordered]),
        "utc_hour_concentration": _concentration([str(trade.entry_at.hour) for trade in ordered]),
        "weekday_expectancy": fmean(weekday) if weekday else None,
        "weekend_expectancy": fmean(weekend) if weekend else None,
        "independent_day_blocks": len(daily),
        "daily_mean_bootstrap_ci": _bootstrap_ci(daily, seed=seed),
        "dependence_adjusted_null": _moving_block_null_pvalue(daily, replicates=2000, seed=seed + 1),
        "ten_day_block_expectancies": block_expectancies,
        "positive_ten_day_block_fraction": (
            sum(value > 0 for value in block_expectancies) / len(block_expectancies)
            if block_expectancies else 0.0
        ),
    }


def _feature_gate_map(feature_validation: Mapping[str, Any]) -> dict[tuple[str, int], bool]:
    return {
        (str(row["feature"]), int(row["horizon_minutes"])): bool(row["multiplicity_rejected"])
        for row in feature_validation.get("tests", [])
    }


def candidate_experiments(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    feature_validation: Mapping[str, Any] | None,
    apply_multiplicity: bool,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    p_values: list[float] = []
    gate_map = _feature_gate_map(feature_validation or {})

    for index, spec in enumerate(candidate_specs()):
        trades = simulate_candidate(
            series,
            spec,
            start=start,
            end=end,
            scenario="high",
            random_seed=61000 + index,
        )
        summary = summarize_trades(trades, seed=62000 + index)
        feature_gate = bool(gate_map.get((str(spec.source_feature), spec.hold_minutes), False))
        row = {
            "candidate": spec.to_dict(),
            "configuration_id": f"{spec.candidate_id}_{spec.hold_minutes}",
            "cost_scenario": "high",
            "summary": summary,
            "feature_gate_passed": feature_gate,
            "multiplicity_rejected": False,
            "eligible_for_holdout": False,
            "complexity_penalty": spec.complexity_score * 0.00001,
            "selection_score": float(summary["expectancy_per_trade"]) - spec.complexity_score * 0.00001,
        }
        rows.append(row)
        p_values.append(float(summary["dependence_adjusted_null"]["p_value"]))

    multiplicity = None
    if apply_multiplicity:
        state = benjamini_yekutieli(p_values, alpha=0.05)
        rejected = set(state.rejected_indices)
        for index, row in enumerate(rows):
            summary = row["summary"]
            row["multiplicity_rejected"] = index in rejected
            row["eligible_for_holdout"] = bool(
                row["feature_gate_passed"]
                and index in rejected
                and int(summary["trade_count"]) >= 30
                and int(summary["independent_day_blocks"]) >= 20
                and float(summary["expectancy_per_trade"]) > 0
                and float(summary["positive_ten_day_block_fraction"]) >= 2.0 / 3.0
                and (summary["profit_factor"] is None or float(summary["profit_factor"]) > 1.0)
            )
        multiplicity = state.to_dict()

    benchmarks: list[dict[str, Any]] = []
    for index, spec in enumerate(benchmark_specs()):
        trades = simulate_candidate(
            series,
            spec,
            start=start,
            end=end,
            scenario="high",
            random_seed=63000 + index,
        )
        benchmarks.append(
            {
                "candidate": spec.to_dict(),
                "configuration_id": f"{spec.candidate_id}_{spec.hold_minutes}",
                "cost_scenario": "high",
                "summary": summarize_trades(trades, seed=64000 + index),
                "promotional": False,
            }
        )

    buy_hold: dict[str, Any] = {}
    for symbol in UNIVERSE:
        first = _close(series, symbol, start)
        last = _close(series, symbol, end)
        gross = _ret(first, last)
        high_cost = _round_trip_cost_bps(symbol, "high") / 10000.0
        buy_hold[symbol] = {
            "gross_return": gross,
            "stress_cost_proxy": high_cost,
            "net_proxy": (gross - high_cost) if gross is not None else None,
        }

    eligible = sorted(
        (row for row in rows if row["eligible_for_holdout"]),
        key=lambda row: (float(row["selection_score"]), row["configuration_id"]),
        reverse=True,
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "confirmatory_candidates": rows,
        "candidate_count": len(rows),
        "multiplicity": multiplicity,
        "benchmarks": {
            "no_trade": {"return": 0.0, "trade_count": 0},
            "buy_and_hold": buy_hold,
            "strategy_controls": benchmarks,
            "block_randomized_null": "dependence_adjusted moving-block centered null on UTC-day aggregates",
        },
        "selected_validation_candidate": eligible[0] if eligible else None,
    }


def _spec_from_row(row: Mapping[str, Any]) -> CandidateSpec:
    payload = row["candidate"]
    return CandidateSpec(
        candidate_id=str(payload["candidate_id"]),
        family=str(payload["family"]),
        source_feature=payload.get("source_feature"),
        timing=str(payload["timing"]),
        hold_minutes=int(payload["hold_minutes"]),
        complexity_score=int(payload["complexity_score"]),
        confirmatory=bool(payload.get("confirmatory", True)),
    )


def holdout_evaluation(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    validation_winner: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if validation_winner is None:
        return {
            "opened": False,
            "reason": "no validation candidate satisfied the frozen feature + candidate gates",
            "start": HOLDOUT_START.isoformat(),
            "end": HOLDOUT_END.isoformat(),
        }

    spec = _spec_from_row(validation_winner)
    scenarios: dict[str, Any] = {}
    for index, scenario in enumerate(("low", "base", "high")):
        trades = simulate_candidate(
            series,
            spec,
            start=HOLDOUT_START,
            end=HOLDOUT_END,
            scenario=scenario,
            random_seed=65000 + index,
        )
        scenarios[scenario] = summarize_trades(trades, seed=66000 + index)

    high = scenarios["high"]
    holdout_passed = bool(
        int(high["trade_count"]) >= 20
        and int(high["independent_day_blocks"]) >= 15
        and float(high["expectancy_per_trade"]) > 0
        and float(high["daily_mean_bootstrap_ci"]["p05"]) > 0
        and float(high["positive_ten_day_block_fraction"]) >= 2.0 / 3.0
        and float(high["symbol_concentration"]["max_share"]) <= 0.60
        and (high["profit_factor"] is None or float(high["profit_factor"]) > 1.0)
    )
    return {
        "opened": True,
        "candidate": spec.to_dict(),
        "configuration_id": f"{spec.candidate_id}_{spec.hold_minutes}",
        "start": HOLDOUT_START.isoformat(),
        "end": HOLDOUT_END.isoformat(),
        "scenarios": scenarios,
        "passed": holdout_passed,
        "post_holdout_tuning_permitted": False,
    }


def robustness_report(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    validation_winner: Mapping[str, Any] | None,
    holdout: Mapping[str, Any],
) -> dict[str, Any]:
    if validation_winner is None or not bool(holdout.get("opened")):
        return {"performed": False, "reason": "no candidate reached the untouched holdout"}

    spec = _spec_from_row(validation_winner)
    base_trades = simulate_candidate(
        series, spec, start=HOLDOUT_START, end=HOLDOUT_END, scenario="high", random_seed=67001
    )
    base_summary = summarize_trades(base_trades, seed=67002)
    concentration = base_summary["symbol_concentration"]["counts"]
    best_symbol = None
    if concentration:
        symbol_returns: dict[str, list[float]] = defaultdict(list)
        for trade in base_trades:
            symbol_returns[trade.symbol].append(trade.net_return)
        best_symbol = max(
            symbol_returns,
            key=lambda symbol: fmean(symbol_returns[symbol]) if symbol_returns[symbol] else float("-inf"),
        )

    delayed = simulate_candidate(
        series,
        spec,
        start=HOLDOUT_START,
        end=HOLDOUT_END,
        scenario="high",
        random_seed=67003,
        extra_entry_delay_minutes=5,
    )
    removed = simulate_candidate(
        series,
        spec,
        start=HOLDOUT_START,
        end=HOLDOUT_END,
        scenario="high",
        random_seed=67004,
        excluded_symbol=best_symbol,
    ) if best_symbol else []

    stress = {
        "baseline_high_cost": base_summary,
        "entry_delayed_5m": summarize_trades(delayed, seed=67005),
        "best_symbol_removed": {
            "removed_symbol": best_symbol,
            "summary": summarize_trades(removed, seed=67006),
        },
    }
    return {
        "performed": True,
        "tests": stress,
        "graceful_degradation": bool(
            float(stress["entry_delayed_5m"]["expectancy_per_trade"]) > -0.001
            and (
                not best_symbol
                or float(stress["best_symbol_removed"]["summary"]["expectancy_per_trade"]) > -0.001
            )
        ),
        "note": "Robustness is diagnostic only after holdout; failure cannot be repaired by retuning this version.",
    }


def _coverage(series: Mapping[str, Mapping[datetime, Mapping[str, Any]]]) -> dict[str, Any]:
    expected = int(
        ((HOLDOUT_END - (DEVELOPMENT_START - timedelta(hours=8))).total_seconds() / 60)
        / BAR_MINUTES
    )
    rows: dict[str, Any] = {}
    for symbol in UNIVERSE:
        count = len(series.get(symbol, {}))
        rows[symbol] = {
            "bars": count,
            "expected_grid_bars": expected,
            "coverage_fraction": count / expected if expected else 0.0,
        }
    return rows


def _strategy_specification(
    validation_winner: Mapping[str, Any] | None,
    holdout: Mapping[str, Any],
) -> dict[str, Any] | None:
    if validation_winner is None or not bool(holdout.get("passed")):
        return None
    spec = _spec_from_row(validation_winner)
    return {
        "strategy_version_id": f"CRYPTO-GRAEN-V5-{spec.candidate_id}-{spec.hold_minutes}",
        "status": "research survivor; not production-authorized",
        "universe_rule": "dynamic RHEN crypto universe constrained by the v5 market-quality and cost hurdle; validation used the frozen six-symbol universe",
        "feature_definitions": {
            "source_feature": spec.source_feature,
            "market_residual": "asset return minus leave-one-out equal-weight return of the other frozen eligible assets",
            "activity_gate": "at least 6/12 nonzero-trade five-minute bars, at least 12 trades, positive volume in prior hour",
            "cost_hurdle": "absolute source move must exceed frozen stressed round-trip spread plus two-sided slippage",
        },
        "signal_logic": spec.to_dict(),
        "position_sizing_inputs": ["RHEN risk budget", "stressed expected cost", "effective stop distance"],
        "risk_requirements": ["long/flat only", "RHEN existing exposure limits", "no new entry on market-quality failure"],
        "execution_assumptions": COSTS_BPS,
        "telemetry_requirements": [
            "opportunity timestamp and cross-sectional rank",
            "source feature and market factor state",
            "activity gate state",
            "pre-entry quote/depth/cost state",
            "NOSTRA and ADS snapshots as non-authoritative evidence",
            "entry/exit reference and fill",
            "MAE/MFE and thesis state",
        ],
        "expected_failure_modes": [
            "factor-wide BTC shock",
            "spread/slippage widening",
            "cross-sectional dispersion breakdown",
            "symbol concentration",
            "regime shift",
            "bar activity dominated by quote-midpoint bars",
        ],
        "promotion_requirements": [
            "VELUM replay pass",
            "crypto shadow pass",
            "paper execution pass",
            "production-readiness review",
            "explicit live promotion with CRYPTO_EXECUTION_ENABLED remaining false until that point",
        ],
    }


def run_crypto_research_v5(
    *,
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    series = build_series(bars_by_symbol)
    coverage = _coverage(series)

    minimum_coverage = min(
        (float(state["coverage_fraction"]) for state in coverage.values()),
        default=0.0,
    )
    if minimum_coverage < 0.35:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "registry_version": REGISTRY_VERSION,
            "research_state": "DEVELOPMENT CONTINUES",
            "status": "INSUFFICIENT_CORPUS",
            "reason": "at least one frozen-universe symbol has less than 35% five-minute bar coverage",
            "coverage": coverage,
            "execution_authority": False,
            "live_configuration_changed": False,
        }

    development_features = feature_research(
        series,
        start=DEVELOPMENT_START + timedelta(hours=8),
        end=VALIDATION_START,
        apply_multiplicity=False,
    )
    validation_features = feature_research(
        series,
        start=VALIDATION_START,
        end=HOLDOUT_START,
        apply_multiplicity=True,
    )
    development_candidates = candidate_experiments(
        series,
        start=DEVELOPMENT_START + timedelta(hours=8),
        end=VALIDATION_START,
        feature_validation=None,
        apply_multiplicity=False,
    )
    validation_candidates = candidate_experiments(
        series,
        start=VALIDATION_START,
        end=HOLDOUT_START,
        feature_validation=validation_features,
        apply_multiplicity=True,
    )

    winner = validation_candidates["selected_validation_candidate"]
    holdout = holdout_evaluation(series, winner)
    robustness = robustness_report(series, winner, holdout)

    feature_survivors = [
        {
            "feature": row["feature"],
            "horizon_minutes": row["horizon_minutes"],
            "mean_daily_spearman_ic": row["mean_daily_spearman_ic"],
        }
        for row in validation_features["tests"]
        if row["multiplicity_rejected"]
    ]
    candidate_survivors = [
        row["configuration_id"]
        for row in validation_candidates["confirmatory_candidates"]
        if row["eligible_for_holdout"]
    ]

    state = "DEVELOPMENT CONTINUES"
    if winner is not None and bool(holdout.get("passed")):
        state = "VALIDATION CANDIDATE"

    final_spec = _strategy_specification(winner, holdout)
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "registry_version": REGISTRY_VERSION,
        "status": "COMPLETE",
        "research_state": state,
        "execution_authority": False,
        "broker_orders_possible": False,
        "live_configuration_changed": False,
        "crypto_execution_required_state": False,
        "corpus": {
            "development": [DEVELOPMENT_START.isoformat(), VALIDATION_START.isoformat()],
            "validation": [VALIDATION_START.isoformat(), HOLDOUT_START.isoformat()],
            "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()],
            "bar_timeframe": "5Min",
            "observation_cadence_minutes": OBSERVATION_MINUTES,
            "coverage": coverage,
        },
        "cost_model": {
            "source": "frozen pre-archive RHEN live quote telemetry",
            "costs_bps": COSTS_BPS,
            "selection_scenario": "high",
        },
        "feature_research": {
            "development": development_features,
            "validation": validation_features,
            "validation_survivors": feature_survivors,
        },
        "candidate_experiments": {
            "development": development_candidates,
            "validation": validation_candidates,
            "validation_survivors": candidate_survivors,
        },
        "walk_forward_analysis": {
            "design": "chronological development then frozen validation; UTC-day dependence blocks",
            "passed_candidate_count": len(candidate_survivors),
        },
        "holdout_evaluation": holdout,
        "robustness_report": robustness,
        "final_strategy_specification": final_spec,
        "research_decision": state,
    }
