from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from math import ceil, isfinite, sqrt
from statistics import fmean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from app.research_agent.multiplicity import benjamini_yekutieli
from .research_v5 import COSTS_BPS


METHODOLOGY_VERSION = "graen-crypto-native-v6"
STRATEGY_VERSION_ID = "CRYPTO-RESIDUAL-RECLAIM-001"
FEATURE_SET_VERSION = "crypto-residual-reclaim-v6-snapshot-v1"
COST_SNAPSHOT_VERSION = "graen-crypto-cost-snapshot-v5"

CONTEXT_UNIVERSE = (
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "XRP/USD",
    "AVAX/USD",
    "LINK/USD",
)
EXECUTION_UNIVERSE = ("BTC/USD", "ETH/USD", "SOL/USD")

BAR_MINUTES = 5
SHOCK_LOOKBACK_MINUTES = 15
RESIDUAL_VOL_LOOKBACK_MINUTES = 360
RECLAIM_WINDOW_MINUTES = 15
HOLD_MINUTES = 120
FEATURE_HORIZONS = (90, 120, 180)
FEATURE_OBSERVATION_MINUTES = 60
STRATEGY_SCAN_MINUTES = 5
MIN_RESIDUAL_HISTORY = 36
SHOCK_SIGMA_MULTIPLE = 1.5
COST_HURDLE_MULTIPLE = 2.0


@dataclass(frozen=True, slots=True)
class Opportunity:
    symbol: str
    opportunity_at: datetime
    residual_15: float
    residual_sigma: float
    threshold: float
    shock_multiple: float
    snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["opportunity_at"] = self.opportunity_at.isoformat()
        return payload


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
    reclaim_delay_minutes: int
    scenario: str
    opportunity_residual: float
    opportunity_threshold: float
    nostra_snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("opportunity_at", "entry_at", "exit_at"):
            payload[key] = payload[key].isoformat()
        return payload


def candidate_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "candidate_id": "CRR_IMMEDIATE_CONTROL",
            "family": "controlled_residual_reversal",
            "timing": "immediate",
            "hold_minutes": HOLD_MINUTES,
            "confirmatory": False,
            "role": "timing_control",
        },
        {
            "candidate_id": STRATEGY_VERSION_ID,
            "family": "controlled_residual_reversal",
            "timing": "reclaim",
            "hold_minutes": HOLD_MINUTES,
            "confirmatory": True,
            "role": "primary_v6_candidate",
        },
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


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


def _ret(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return right / left - 1.0


def _bar_end(bar: Mapping[str, Any]) -> datetime:
    return _stamp(bar.get("t")) + timedelta(minutes=BAR_MINUTES)


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, dict[datetime, dict[str, Any]]]:
    start = _aware(start, "start")
    end = _aware(end, "end")
    if end <= start:
        raise ValueError("end must follow start")
    lower = start - timedelta(minutes=RESIDUAL_VOL_LOOKBACK_MINUTES + 120)
    upper = end + timedelta(minutes=HOLD_MINUTES + 2 * BAR_MINUTES)

    output: dict[str, dict[datetime, dict[str, Any]]] = {}
    for symbol in CONTEXT_UNIVERSE:
        series: dict[datetime, dict[str, Any]] = {}
        for raw in bars_by_symbol.get(symbol, ()):
            try:
                bar_end = _bar_end(raw)
            except Exception:
                continue
            if lower <= bar_end <= upper:
                series[bar_end] = dict(raw)
        output[symbol] = series
    return output


def _bar(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> Mapping[str, Any] | None:
    return series.get(symbol, {}).get(end)


def _close(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> float | None:
    row = _bar(series, symbol, end)
    value = _f(row.get("c")) if row else 0.0
    return value if value > 0 else None


def _open(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> float | None:
    row = _bar(series, symbol, end)
    value = _f(row.get("o")) if row else 0.0
    return value if value > 0 else None


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
    nonzero = sum(_f(row.get("n")) > 0 and _f(row.get("v")) > 0 for row in rows)
    trade_count = sum(int(_f(row.get("n"))) for row in rows)
    volume = sum(_f(row.get("v")) for row in rows)
    return {
        "ok": nonzero >= 6 and trade_count >= 12 and volume > 0,
        "nonzero_trade_bars": nonzero,
        "trade_count": trade_count,
        "volume": volume,
    }


def _market_returns(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    end: datetime,
    minutes: int,
) -> dict[str, float]:
    output: dict[str, float] = {}
    for symbol in CONTEXT_UNIVERSE:
        value = _ret(
            _close(series, symbol, end - timedelta(minutes=minutes)),
            _close(series, symbol, end),
        )
        if value is not None:
            output[symbol] = value
    return output


def _leave_one_out(values: Mapping[str, float], symbol: str) -> float | None:
    peers = [value for name, value in values.items() if name != symbol]
    return fmean(peers) if len(peers) >= 3 else None


def residual_snapshot(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    end: datetime,
) -> dict[str, dict[str, Any]]:
    r5 = _market_returns(series, end, 5)
    r15 = _market_returns(series, end, 15)
    r60 = _market_returns(series, end, 60)
    if len(r15) < 4:
        return {}

    residuals: dict[str, float] = {}
    for symbol, own in r15.items():
        factor = _leave_one_out(r15, symbol)
        if factor is not None:
            residuals[symbol] = own - factor

    dispersion = pstdev(residuals.values()) if len(residuals) > 1 else 0.0
    btc15 = r15.get("BTC/USD", 0.0)
    btc60 = r60.get("BTC/USD", 0.0)
    output: dict[str, dict[str, Any]] = {}

    for symbol, residual in residuals.items():
        activity = _activity_state(series, symbol, end)
        returns_180 = _window_returns(series, symbol, end, 180)
        rows_60 = _window_bars(series, symbol, end, 60)
        rows_360 = _window_bars(series, symbol, end, 360)
        realized_vol = pstdev(returns_180) * sqrt(12.0) if len(returns_180) > 1 else 0.0
        volume_60 = sum(_f(row.get("v")) for row in rows_60)
        volume_360 = sum(_f(row.get("v")) for row in rows_360)
        baseline_hour = volume_360 / 6.0 if volume_360 > 0 else 0.0
        activity_ratio = volume_60 / baseline_hour - 1.0 if baseline_hour > 0 else 0.0
        output[symbol] = {
            "return_5": r5.get(symbol, 0.0),
            "return_15": r15.get(symbol, 0.0),
            "return_60": r60.get(symbol, 0.0),
            "market_factor_15": r15[symbol] - residual,
            "residual_15": residual,
            "cross_sectional_dispersion": dispersion,
            "btc_return_15": btc15,
            "btc_return_60": btc60,
            "realized_volatility_60_equiv": realized_vol,
            "activity_ratio_60_vs_360": activity_ratio,
            "activity_ok": bool(activity["ok"]),
            "trade_count_60": int(activity["trade_count"]),
            "nonzero_trade_bars_60": int(activity["nonzero_trade_bars"]),
        }
    return output


def _residual_history(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> list[float]:
    values: list[float] = []
    start = end - timedelta(minutes=RESIDUAL_VOL_LOOKBACK_MINUTES)
    current = start
    latest_allowed = end - timedelta(minutes=BAR_MINUTES)
    while current <= latest_allowed:
        row = residual_snapshot(series, current).get(symbol)
        if row is not None:
            values.append(float(row["residual_15"]))
        current += timedelta(minutes=BAR_MINUTES)
    return values


def round_trip_cost_bps(symbol: str, scenario: str = "high") -> float:
    state = COSTS_BPS[symbol]
    return float(state["spread"][scenario]) + 2.0 * float(state["slippage"][scenario])


def shock_threshold(symbol: str, residual_sigma: float) -> float:
    volatility_hurdle = SHOCK_SIGMA_MULTIPLE * max(float(residual_sigma), 0.0)
    cost_hurdle = COST_HURDLE_MULTIPLE * round_trip_cost_bps(symbol, "high") / 10000.0
    return max(volatility_hurdle, cost_hurdle)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}_{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def build_nostra_snapshot(
    *,
    symbol: str,
    as_of_timestamp: datetime,
    state: Mapping[str, Any],
    residual_sigma: float,
    threshold: float,
) -> dict[str, Any]:
    as_of = _aware(as_of_timestamp, "as_of_timestamp")
    residual = float(state["residual_15"])
    cost_return = round_trip_cost_bps(symbol, "high") / 10000.0
    raw_features = {
        "return_5": float(state.get("return_5") or 0.0),
        "return_15": float(state.get("return_15") or 0.0),
        "return_60": float(state.get("return_60") or 0.0),
        "market_factor_15": float(state.get("market_factor_15") or 0.0),
        "residual_15": residual,
        "residual_sigma_360": float(residual_sigma),
        "cross_sectional_dispersion": float(state.get("cross_sectional_dispersion") or 0.0),
        "btc_return_15": float(state.get("btc_return_15") or 0.0),
        "btc_return_60": float(state.get("btc_return_60") or 0.0),
        "realized_volatility_60_equiv": float(state.get("realized_volatility_60_equiv") or 0.0),
        "activity_ratio_60_vs_360": float(state.get("activity_ratio_60_vs_360") or 0.0),
        "stressed_round_trip_cost_bps": round_trip_cost_bps(symbol, "high"),
        "shock_threshold": float(threshold),
    }
    normalized = {
        "residual_z": residual / residual_sigma if residual_sigma > 1e-12 else 0.0,
        "shock_to_cost": abs(residual) / cost_return if cost_return > 0 else 0.0,
        "shock_to_threshold": abs(residual) / threshold if threshold > 0 else 0.0,
    }
    base = {
        "symbol": symbol,
        "market_lane": "crypto",
        "as_of_timestamp": as_of.isoformat(),
        "feature_set_version": FEATURE_SET_VERSION,
        "raw_features": raw_features,
        "normalized_features": normalized,
        "market_state": {
            "btc_direction_15": "up" if raw_features["btc_return_15"] > 0 else "down" if raw_features["btc_return_15"] < 0 else "flat",
            "btc_direction_60": "up" if raw_features["btc_return_60"] > 0 else "down" if raw_features["btc_return_60"] < 0 else "flat",
            "cross_sectional_dispersion": raw_features["cross_sectional_dispersion"],
        },
        "data_quality": {
            "activity_ok": bool(state.get("activity_ok")),
            "trade_count_60": int(state.get("trade_count_60") or 0),
            "nonzero_trade_bars_60": int(state.get("nonzero_trade_bars_60") or 0),
        },
        "source": {
            "system": "GRAEN",
            "methodology_version": METHODOLOGY_VERSION,
            "strategy_version_id": STRATEGY_VERSION_ID,
            "cost_snapshot_version": COST_SNAPSHOT_VERSION,
            "canonical_nostra_ledger_pending": True,
        },
        "research_only": True,
        "execution_authority": False,
    }
    return {"snapshot_id": _stable_id("nostra-shadow", base), **base}


def opportunity_state(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> Opportunity | None:
    if symbol not in EXECUTION_UNIVERSE:
        return None
    state = residual_snapshot(series, end).get(symbol)
    if not state or not bool(state.get("activity_ok")):
        return None
    residual = float(state["residual_15"])
    if residual >= 0:
        return None
    cost_floor = COST_HURDLE_MULTIPLE * round_trip_cost_bps(symbol, "high") / 10000.0
    if abs(residual) <= cost_floor:
        return None
    history = _residual_history(series, symbol, end)
    if len(history) < MIN_RESIDUAL_HISTORY:
        return None
    sigma = pstdev(history) if len(history) > 1 else 0.0
    threshold = shock_threshold(symbol, sigma)
    if abs(residual) <= threshold:
        return None
    snapshot = build_nostra_snapshot(
        symbol=symbol,
        as_of_timestamp=end,
        state=state,
        residual_sigma=sigma,
        threshold=threshold,
    )
    return Opportunity(
        symbol=symbol,
        opportunity_at=end,
        residual_15=residual,
        residual_sigma=sigma,
        threshold=threshold,
        shock_multiple=abs(residual) / threshold if threshold > 0 else 0.0,
        snapshot=snapshot,
    )


def _grid(start: datetime, end: datetime, step_minutes: int) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(minutes=step_minutes)


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    suppressed_until: dict[str, datetime] = {}
    for stamp in _grid(start, end, STRATEGY_SCAN_MINUTES):
        for symbol in EXECUTION_UNIVERSE:
            if suppressed_until.get(symbol, datetime.min.replace(tzinfo=timezone.utc)) > stamp:
                continue
            opportunity = opportunity_state(series, symbol, stamp)
            if opportunity is None:
                continue
            opportunities.append(opportunity)
            suppressed_until[symbol] = stamp + timedelta(minutes=RECLAIM_WINDOW_MINUTES)
    return opportunities


def reclaim_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunity: Opportunity,
) -> datetime | None:
    for offset in range(BAR_MINUTES, RECLAIM_WINDOW_MINUTES + 1, BAR_MINUTES):
        current_at = opportunity.opportunity_at + timedelta(minutes=offset)
        current_state = residual_snapshot(series, current_at).get(opportunity.symbol)
        previous_close = _close(series, opportunity.symbol, current_at - timedelta(minutes=BAR_MINUTES))
        current_close = _close(series, opportunity.symbol, current_at)
        if current_state is None or previous_close is None or current_close is None:
            continue
        if not bool(current_state.get("activity_ok")):
            continue
        residual_improved = float(current_state["residual_15"]) > opportunity.residual_15
        positive_bar = current_close > previous_close
        if residual_improved and positive_bar:
            return current_at
    return None


def _fill_prices(symbol: str, entry: float, exit: float, scenario: str) -> tuple[float, float]:
    state = COSTS_BPS[symbol]
    half_spread = float(state["spread"][scenario]) / 20000.0
    slippage = float(state["slippage"][scenario]) / 10000.0
    return (
        entry * (1.0 + half_spread + slippage),
        exit * (1.0 - half_spread - slippage),
    )


def _make_trade(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunity: Opportunity,
    *,
    entry_at: datetime,
    candidate_id: str,
    scenario: str,
    extra_entry_delay_minutes: int = 0,
) -> Trade | None:
    entry_at = entry_at + timedelta(minutes=extra_entry_delay_minutes)
    exit_at = entry_at + timedelta(minutes=HOLD_MINUTES)
    entry_reference = _open(series, opportunity.symbol, entry_at + timedelta(minutes=BAR_MINUTES))
    exit_reference = _open(series, opportunity.symbol, exit_at + timedelta(minutes=BAR_MINUTES))
    if entry_reference is None or exit_reference is None:
        return None
    entry_fill, exit_fill = _fill_prices(opportunity.symbol, entry_reference, exit_reference, scenario)
    if entry_fill <= 0 or exit_fill <= 0:
        return None

    highs: list[float] = []
    lows: list[float] = []
    current = entry_at + timedelta(minutes=BAR_MINUTES)
    while current <= exit_at:
        row = _bar(series, opportunity.symbol, current)
        if row is not None:
            highs.append(_f(row.get("h")))
            lows.append(_f(row.get("l")))
        current += timedelta(minutes=BAR_MINUTES)

    return Trade(
        candidate_id=candidate_id,
        symbol=opportunity.symbol,
        opportunity_at=opportunity.opportunity_at,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_reference=entry_reference,
        exit_reference=exit_reference,
        net_return=exit_fill / entry_fill - 1.0,
        mae=min((value / entry_reference - 1.0 for value in lows if value > 0), default=0.0),
        mfe=max((value / entry_reference - 1.0 for value in highs if value > 0), default=0.0),
        reclaim_delay_minutes=int((entry_at - opportunity.opportunity_at).total_seconds() // 60),
        scenario=scenario,
        opportunity_residual=opportunity.residual_15,
        opportunity_threshold=opportunity.threshold,
        nostra_snapshot_id=str(opportunity.snapshot["snapshot_id"]),
    )


def simulate_candidate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunities: Sequence[Opportunity],
    *,
    timing: str,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    extra_entry_delay_minutes: int = 0,
) -> list[Trade]:
    if timing not in {"immediate", "reclaim"}:
        raise ValueError("timing must be immediate or reclaim")
    candidate_id = "CRR_IMMEDIATE_CONTROL" if timing == "immediate" else STRATEGY_VERSION_ID
    busy_until: dict[str, datetime] = {}
    trades: list[Trade] = []

    for opportunity in sorted(opportunities, key=lambda row: (row.opportunity_at, row.symbol)):
        if not (start <= opportunity.opportunity_at < end):
            continue
        if busy_until.get(opportunity.symbol, datetime.min.replace(tzinfo=timezone.utc)) > opportunity.opportunity_at:
            continue
        entry_at = opportunity.opportunity_at if timing == "immediate" else reclaim_at(series, opportunity)
        if entry_at is None:
            continue
        delayed_entry = entry_at + timedelta(minutes=extra_entry_delay_minutes)
        if delayed_entry >= end:
            continue
        trade = _make_trade(
            series,
            opportunity,
            entry_at=entry_at,
            candidate_id=candidate_id,
            scenario=scenario,
            extra_entry_delay_minutes=extra_entry_delay_minutes,
        )
        if trade is None or trade.exit_at > end:
            continue
        trades.append(trade)
        busy_until[opportunity.symbol] = trade.exit_at
    return trades


def _rank(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        stop = cursor + 1
        while stop < len(indexed) and indexed[stop][1] == indexed[cursor][1]:
            stop += 1
        average = (cursor + 1 + stop) / 2.0
        for index in range(cursor, stop):
            result[indexed[index][0]] = average
        cursor = stop
    return result


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    a = _rank(left)
    b = _rank(right)
    am = fmean(a)
    bm = fmean(b)
    numerator = sum((x - am) * (y - bm) for x, y in zip(a, b))
    ass = sum((x - am) ** 2 for x in a)
    bss = sum((y - bm) ** 2 for y in b)
    if ass <= 0 or bss <= 0:
        return None
    return numerator / sqrt(ass * bss)


def _daily_means(values: Sequence[tuple[datetime, float]]) -> list[float]:
    groups: dict[date, list[float]] = defaultdict(list)
    for stamp, value in values:
        groups[stamp.date()].append(float(value))
    return [fmean(groups[key]) for key in sorted(groups)]


def feature_research(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    apply_multiplicity: bool,
) -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    p_values: list[float] = []
    for horizon in FEATURE_HORIZONS:
        hourly_ics: list[tuple[datetime, float]] = []
        for stamp in _grid(start, end, FEATURE_OBSERVATION_MINUTES):
            snapshot = residual_snapshot(series, stamp)
            signal: list[float] = []
            future: list[float] = []
            for symbol, state in snapshot.items():
                forward = _ret(
                    _close(series, symbol, stamp),
                    _close(series, symbol, stamp + timedelta(minutes=horizon)),
                )
                if forward is None:
                    continue
                signal.append(-float(state["residual_15"]))
                future.append(float(forward))
            if len(signal) < 4:
                continue
            ic = _spearman(signal, future)
            if ic is not None:
                hourly_ics.append((stamp, ic))
        daily = _daily_means(hourly_ics)
        null = _moving_block_null_pvalue(
            daily,
            replicates=2000,
            seed=76000 + horizon,
        )
        tests.append({
            "feature": "negative_market_residual_15",
            "horizon_minutes": horizon,
            "hourly_cross_sections": len(hourly_ics),
            "independent_day_blocks": len(daily),
            "mean_daily_spearman_ic": fmean(daily) if daily else 0.0,
            "median_daily_ic": median(daily) if daily else 0.0,
            "positive_day_fraction": sum(value > 0 for value in daily) / len(daily) if daily else 0.0,
            "null_test": null,
            "multiplicity_rejected": False,
        })
        p_values.append(float(null["p_value"]))

    multiplicity = None
    if apply_multiplicity:
        state = benjamini_yekutieli(p_values, alpha=0.05)
        rejected = set(state.rejected_indices)
        for index, row in enumerate(tests):
            row["multiplicity_rejected"] = index in rejected
        multiplicity = state.to_dict()
    return {
        "feature_family": ["negative_market_residual_15"],
        "horizons_minutes": list(FEATURE_HORIZONS),
        "test_count": len(tests),
        "tests": tests,
        "multiplicity": multiplicity,
    }


def _profit_factor(returns: Sequence[float]) -> float | None:
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return gains / losses if losses > 0 else None


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= max(1.0 + value, 1e-9)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _concentration(values: Sequence[str]) -> dict[str, Any]:
    if not values:
        return {"max_share": 0.0, "counts": {}}
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    total = len(values)
    return {
        "max_share": max(counts.values()) / total,
        "counts": dict(sorted(counts.items())),
    }


def summarize_trades(trades: Sequence[Trade], *, seed: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: (trade.entry_at, trade.symbol))
    returns = [trade.net_return for trade in ordered]
    daily = _daily_means([(trade.entry_at, trade.net_return) for trade in ordered])
    return {
        "trade_count": len(ordered),
        "expectancy_per_trade": fmean(returns) if returns else 0.0,
        "median_trade_return": median(returns) if returns else 0.0,
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        "profit_factor": _profit_factor(returns),
        "max_drawdown": _max_drawdown(returns),
        "average_mae": fmean([trade.mae for trade in ordered]) if ordered else 0.0,
        "average_mfe": fmean([trade.mfe for trade in ordered]) if ordered else 0.0,
        "average_reclaim_delay_minutes": fmean([trade.reclaim_delay_minutes for trade in ordered]) if ordered else 0.0,
        "independent_day_blocks": len(daily),
        "dependence_adjusted_null": _moving_block_null_pvalue(
            daily,
            replicates=2000,
            seed=seed,
        ),
        "symbol_concentration": _concentration([trade.symbol for trade in ordered]),
    }


def _feature_120_passed(feature_validation: Mapping[str, Any]) -> bool:
    return any(
        int(row.get("horizon_minutes") or 0) == HOLD_MINUTES
        and bool(row.get("multiplicity_rejected"))
        and float(row.get("mean_daily_spearman_ic") or 0.0) > 0
        for row in feature_validation.get("tests", [])
    )


def evaluate_period(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    scenario: str = "high",
) -> dict[str, Any]:
    opportunities = collect_opportunities(series, start=start, end=end)
    immediate = simulate_candidate(
        series,
        opportunities,
        timing="immediate",
        start=start,
        end=end,
        scenario=scenario,
    )
    reclaim = simulate_candidate(
        series,
        opportunities,
        timing="reclaim",
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = simulate_candidate(
        series,
        opportunities,
        timing="reclaim",
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=BAR_MINUTES,
    )
    immediate_summary = summarize_trades(immediate, seed=77101)
    reclaim_summary = summarize_trades(reclaim, seed=77102)
    delayed_summary = summarize_trades(delayed, seed=77103)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "nostra_shadow_snapshot_count": len(opportunities),
        "nostra_shadow_sample": [item.snapshot for item in opportunities[:3]],
        "immediate_control": immediate_summary,
        "primary_reclaim": reclaim_summary,
        "one_bar_delay_robustness": delayed_summary,
        "timing_lift": float(reclaim_summary["expectancy_per_trade"]) - float(immediate_summary["expectancy_per_trade"]),
    }


def _candidate_passed(
    *,
    development: Mapping[str, Any],
    validation: Mapping[str, Any],
    feature_validation: Mapping[str, Any],
) -> bool:
    dev = development["primary_reclaim"]
    val = validation["primary_reclaim"]
    delayed = validation["one_bar_delay_robustness"]
    p_value = float(val["dependence_adjusted_null"]["p_value"])
    return bool(
        _feature_120_passed(feature_validation)
        and int(dev["trade_count"]) >= 20
        and float(dev["expectancy_per_trade"]) > 0
        and int(val["trade_count"]) >= 30
        and int(val["independent_day_blocks"]) >= 20
        and float(val["expectancy_per_trade"]) > 0
        and p_value <= 0.05
        and (val["profit_factor"] is None or float(val["profit_factor"]) > 1.0)
        and float(val["symbol_concentration"]["max_share"]) <= 0.70
        and float(delayed["expectancy_per_trade"]) > 0
    )


def _holdout(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    open_holdout: bool,
) -> dict[str, Any]:
    if not open_holdout:
        return {
            "opened": False,
            "reason": "validation candidate did not satisfy frozen v6 gates",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    scenarios = {
        scenario: evaluate_period(series, start=start, end=end, scenario=scenario)
        for scenario in ("low", "base", "high")
    }
    high = scenarios["high"]["primary_reclaim"]
    high_delay = scenarios["high"]["one_bar_delay_robustness"]
    passed = bool(
        int(high["trade_count"]) >= 20
        and int(high["independent_day_blocks"]) >= 15
        and float(high["expectancy_per_trade"]) > 0
        and float(high["dependence_adjusted_null"]["p_value"]) <= 0.05
        and (high["profit_factor"] is None or float(high["profit_factor"]) > 1.0)
        and float(high["symbol_concentration"]["max_share"]) <= 0.60
        and float(high_delay["expectancy_per_trade"]) > 0
    )
    return {
        "opened": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scenarios": scenarios,
        "passed": passed,
    }


def _validate_ranges(
    development_start: datetime,
    validation_start: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
) -> tuple[datetime, datetime, datetime, datetime]:
    values = tuple(
        _aware(value, name)
        for value, name in (
            (development_start, "development_start"),
            (validation_start, "validation_start"),
            (holdout_start, "holdout_start"),
            (holdout_end, "holdout_end"),
        )
    )
    if not (values[0] < values[1] < values[2] < values[3]):
        raise ValueError("research ranges must be strictly chronological and non-overlapping")
    return values


def _verify_uninspected_corpus(
    *,
    start: datetime,
    end: datetime,
    corpus_provenance_verified: bool,
    previously_inspected_ranges: Sequence[Mapping[str, Any]],
) -> None:
    if not corpus_provenance_verified:
        raise ValueError("v6 corpus provenance must be explicitly verified before inspection")
    for row in previously_inspected_ranges:
        prior_start = _stamp(row.get("start"))
        prior_end = _stamp(row.get("end"))
        if prior_end <= prior_start:
            raise ValueError("previously inspected corpus range is invalid")
        if start < prior_end and prior_start < end:
            label = str(row.get("id") or row.get("methodology") or "prior research")
            raise ValueError(f"v6 corpus overlaps previously inspected range: {label}")


def run_crypto_research_v6(
    *,
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    development_start: datetime,
    validation_start: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
    corpus_provenance_verified: bool = False,
    previously_inspected_ranges: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    development_start, validation_start, holdout_start, holdout_end = _validate_ranges(
        development_start,
        validation_start,
        holdout_start,
        holdout_end,
    )
    _verify_uninspected_corpus(
        start=development_start,
        end=holdout_end,
        corpus_provenance_verified=corpus_provenance_verified,
        previously_inspected_ranges=previously_inspected_ranges,
    )
    series = build_series(
        bars_by_symbol,
        start=development_start,
        end=holdout_end,
    )
    development_features = feature_research(
        series,
        start=development_start,
        end=validation_start,
        apply_multiplicity=False,
    )
    validation_features = feature_research(
        series,
        start=validation_start,
        end=holdout_start,
        apply_multiplicity=True,
    )
    development = evaluate_period(
        series,
        start=development_start,
        end=validation_start,
        scenario="high",
    )
    validation = evaluate_period(
        series,
        start=validation_start,
        end=holdout_start,
        scenario="high",
    )
    validation_passed = _candidate_passed(
        development=development,
        validation=validation,
        feature_validation=validation_features,
    )
    holdout = _holdout(
        series,
        start=holdout_start,
        end=holdout_end,
        open_holdout=validation_passed,
    )
    status = (
        "HOLDOUT_PASS"
        if holdout.get("passed") is True
        else "VALIDATION_EDGE_FOUND"
        if validation_passed
        else "DEVELOPMENT_CONTINUES"
    )
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "strategy_version_id": STRATEGY_VERSION_ID,
        "status": status,
        "market_lane": "crypto",
        "research_only": True,
        "execution_authority": False,
        "crypto_execution_required_state": False,
        "candidate_family": list(candidate_specs()),
        "context_universe": list(CONTEXT_UNIVERSE),
        "execution_universe": list(EXECUTION_UNIVERSE),
        "corpus_contract": {
            "provenance_verified": corpus_provenance_verified,
            "prior_range_count": len(previously_inspected_ranges),
            "overlap_allowed": False,
            "holdout_opened_only_after_validation_gate": True,
        },
        "frozen_parameters": {
            "shock_lookback_minutes": SHOCK_LOOKBACK_MINUTES,
            "residual_vol_lookback_minutes": RESIDUAL_VOL_LOOKBACK_MINUTES,
            "shock_sigma_multiple": SHOCK_SIGMA_MULTIPLE,
            "cost_hurdle_multiple": COST_HURDLE_MULTIPLE,
            "reclaim_window_minutes": RECLAIM_WINDOW_MINUTES,
            "hold_minutes": HOLD_MINUTES,
            "feature_horizons": list(FEATURE_HORIZONS),
            "cost_snapshot_version": COST_SNAPSHOT_VERSION,
        },
        "development_feature_research": development_features,
        "validation_feature_research": validation_features,
        "development": development,
        "validation": validation,
        "validation_passed": validation_passed,
        "holdout": holdout,
        "nostra": {
            "mode": "shadow_snapshot_only",
            "feature_set_version": FEATURE_SET_VERSION,
            "forecast_gate_applied": False,
            "execution_authority": False,
            "upgrade_path": "replace shadow snapshot persistence with canonical NOSTRA ledger after FWD-002-006 merges; then compare unfiltered CRR against forecast-filtered CRR",
        },
        "production_state_changed": False,
    }
