from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite, log1p, sqrt
from statistics import fmean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from .research_v5 import COSTS_BPS


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-crypto-activity-shock-v9"
FAMILY = "activity_confirmed_momentum_continuation"
UNIVERSE = ("BTC/USD", "ETH/USD", "SOL/USD")
BAR_MINUTES = 5
DELAY_ROBUSTNESS_MINUTES = 5


@dataclass(frozen=True, slots=True)
class ActivityShockSpec:
    candidate_id: str
    impulse_minutes: int
    activity_lookback_hours: int
    return_z_threshold: float
    activity_z_threshold: float
    hold_minutes: int
    cooldown_minutes: int
    concentration_limit: float = 0.70

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Opportunity:
    candidate_id: str
    symbol: str
    opportunity_at: datetime
    hold_minutes: int
    signal: dict[str, Any]

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
    net_return: float
    scenario: str
    delayed_minutes: int


def candidate_specs() -> tuple[ActivityShockSpec, ...]:
    return (
        ActivityShockSpec("V9-ASC-10-15-A", 10, 6, 1.25, 1.00, 15, 30),
        ActivityShockSpec("V9-ASC-15-30-A", 15, 6, 1.35, 1.00, 30, 45),
        ActivityShockSpec("V9-ASC-15-30-B", 15, 12, 1.60, 0.75, 30, 45),
        ActivityShockSpec("V9-ASC-30-30-A", 30, 12, 1.40, 1.00, 30, 60),
        ActivityShockSpec("V9-ASC-30-45-B", 30, 12, 1.75, 0.75, 45, 75),
        ActivityShockSpec("V9-ASC-60-60-A", 60, 24, 1.50, 1.00, 60, 90),
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _f(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def _bar_end(row: Mapping[str, Any]) -> datetime:
    return _stamp(row.get("t")) + timedelta(minutes=BAR_MINUTES)


def verify_stage_corpus(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    min_fraction: float = 0.80,
) -> dict[str, Any]:
    start = _aware(start, "start")
    end = _aware(end, "end")
    expected = max(int((end - start).total_seconds() // (BAR_MINUTES * 60)), 1)
    counts: dict[str, int] = {}
    fractions: dict[str, float] = {}
    failures: list[str] = []
    for symbol in UNIVERSE:
        count = 0
        for row in bars_by_symbol.get(symbol, ()):
            try:
                stamp = _stamp(row.get("t"))
            except Exception:
                continue
            if start <= stamp < end:
                count += 1
        counts[symbol] = count
        fraction = count / expected
        fractions[symbol] = fraction
        if fraction < min_fraction:
            failures.append(symbol)
    report = {
        "expected_bars_per_symbol": expected,
        "counts": counts,
        "fractions": fractions,
        "minimum_fraction": min_fraction,
        "passed": not failures,
        "failed_symbols": failures,
    }
    if failures:
        raise ValueError(
            "v9_stage_corpus_incomplete:" + ",".join(sorted(failures))
        )
    return report


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    warmup_hours: int = 25,
) -> dict[str, dict[datetime, dict[str, Any]]]:
    lower = _aware(start, "start") - timedelta(hours=warmup_hours)
    upper = _aware(end, "end") + timedelta(hours=2)
    output: dict[str, dict[datetime, dict[str, Any]]] = {}
    for symbol in UNIVERSE:
        rows: dict[datetime, dict[str, Any]] = {}
        for raw in bars_by_symbol.get(symbol, ()):
            try:
                stamp = _bar_end(raw)
            except Exception:
                continue
            if lower <= stamp <= upper:
                rows[stamp] = dict(raw)
        output[symbol] = rows
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


def _return(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
    minutes: int,
) -> float | None:
    left = _close(series, symbol, end - timedelta(minutes=minutes))
    right = _close(series, symbol, end)
    if left is None or right is None or left <= 0:
        return None
    return right / left - 1.0


def _grid(start: datetime, end: datetime) -> Iterable[datetime]:
    current = _aware(start, "start")
    limit = _aware(end, "end")
    while current < limit:
        yield current
        current += timedelta(minutes=BAR_MINUTES)


def _history(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    *,
    end: datetime,
    hours: int,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    stamp = end - timedelta(hours=hours)
    while stamp < end:
        row = _bar(series, symbol, stamp)
        if row is not None:
            rows.append(row)
        stamp += timedelta(minutes=BAR_MINUTES)
    return rows


def _return_history(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    *,
    end: datetime,
    impulse_minutes: int,
    hours: int,
) -> list[float]:
    output: list[float] = []
    stamp = end - timedelta(hours=hours)
    while stamp < end:
        value = _return(series, symbol, stamp, impulse_minutes)
        if value is not None:
            output.append(value)
        stamp += timedelta(minutes=BAR_MINUTES)
    return output


def _z(value: float, history: Sequence[float]) -> float | None:
    if len(history) < 36:
        return None
    sigma = pstdev(history)
    if sigma <= 1e-12:
        return None
    return (value - fmean(history)) / sigma


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: ActivityShockSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    impulse = _return(series, symbol, stamp, spec.impulse_minutes)
    if impulse is None or impulse <= 0:
        return None

    return_hist = _return_history(
        series,
        symbol,
        end=stamp,
        impulse_minutes=spec.impulse_minutes,
        hours=spec.activity_lookback_hours,
    )
    return_z = _z(impulse, return_hist)
    if return_z is None or return_z < spec.return_z_threshold:
        return None

    rows = _history(
        series,
        symbol,
        end=stamp,
        hours=spec.activity_lookback_hours,
    )
    current = _bar(series, symbol, stamp)
    if current is None or len(rows) < 36:
        return None

    trade_value = log1p(max(_f(current.get("n")), 0.0))
    volume_value = log1p(max(_f(current.get("v")), 0.0))
    trade_history = [log1p(max(_f(row.get("n")), 0.0)) for row in rows]
    volume_history = [log1p(max(_f(row.get("v")), 0.0)) for row in rows]
    trade_z = _z(trade_value, trade_history)
    volume_z = _z(volume_value, volume_history)
    if trade_z is None or volume_z is None:
        return None

    activity_z = max(trade_z, volume_z)
    if activity_z < spec.activity_z_threshold:
        return None

    last_hour = _return(series, symbol, stamp, 60)
    if last_hour is None or last_hour <= 0:
        return None

    return Opportunity(
        candidate_id=spec.candidate_id,
        symbol=symbol,
        opportunity_at=stamp,
        hold_minutes=spec.hold_minutes,
        signal={
            "family": FAMILY,
            "impulse_return": impulse,
            "return_z": return_z,
            "trade_count_z": trade_z,
            "volume_z": volume_z,
            "activity_z": activity_z,
            "one_hour_return": last_hour,
        },
    )


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: ActivityShockSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    cooldown: dict[str, datetime] = {}
    for stamp in _grid(start, end):
        for symbol in UNIVERSE:
            if cooldown.get(symbol, datetime.min.replace(tzinfo=UTC)) > stamp:
                continue
            candidate = opportunity_at(series, spec, symbol, stamp)
            if candidate is None:
                continue
            output.append(candidate)
            cooldown[symbol] = stamp + timedelta(minutes=spec.cooldown_minutes)
    return output


def _fills(symbol: str, entry: float, exit: float, scenario: str) -> tuple[float, float]:
    costs = COSTS_BPS[symbol]
    half_spread = float(costs["spread"][scenario]) / 20000.0
    slippage = float(costs["slippage"][scenario]) / 10000.0
    return (
        entry * (1.0 + half_spread + slippage),
        exit * (1.0 - half_spread - slippage),
    )


def simulate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunities: Sequence[Opportunity],
    *,
    start: datetime,
    end: datetime,
    scenario: str,
    extra_entry_delay_minutes: int = 0,
) -> list[Trade]:
    busy_until: dict[str, datetime] = {}
    output: list[Trade] = []
    for opportunity in sorted(opportunities, key=lambda row: (row.opportunity_at, row.symbol)):
        if busy_until.get(opportunity.symbol, datetime.min.replace(tzinfo=UTC)) > opportunity.opportunity_at:
            continue
        entry_at = opportunity.opportunity_at + timedelta(minutes=extra_entry_delay_minutes)
        exit_at = entry_at + timedelta(minutes=opportunity.hold_minutes)
        if entry_at < start or exit_at > end:
            continue
        entry_reference = _open(
            series,
            opportunity.symbol,
            entry_at + timedelta(minutes=BAR_MINUTES),
        )
        exit_reference = _open(
            series,
            opportunity.symbol,
            exit_at + timedelta(minutes=BAR_MINUTES),
        )
        if entry_reference is None or exit_reference is None:
            continue
        entry_fill, exit_fill = _fills(
            opportunity.symbol,
            entry_reference,
            exit_reference,
            scenario,
        )
        if entry_fill <= 0 or exit_fill <= 0:
            continue
        output.append(
            Trade(
                candidate_id=opportunity.candidate_id,
                symbol=opportunity.symbol,
                opportunity_at=opportunity.opportunity_at,
                entry_at=entry_at,
                exit_at=exit_at,
                net_return=exit_fill / entry_fill - 1.0,
                scenario=scenario,
                delayed_minutes=extra_entry_delay_minutes,
            )
        )
        busy_until[opportunity.symbol] = exit_at
    return output


def _daily_means(trades: Sequence[Trade]) -> list[float]:
    groups: dict[date, list[float]] = defaultdict(list)
    for trade in trades:
        groups[trade.entry_at.date()].append(trade.net_return)
    return [fmean(groups[key]) for key in sorted(groups)]


def _profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return gains / losses if losses > 0 else None


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= max(1.0 + value, 1e-9)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _concentration(trades: Sequence[Trade]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for trade in trades:
        counts[trade.symbol] += 1
    total = len(trades)
    return {
        "max_share": max(counts.values()) / total if total else 0.0,
        "counts": dict(sorted(counts.items())),
    }


def summarize(
    trades: Sequence[Trade],
    *,
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    values = [row.net_return for row in trades]
    daily = _daily_means(trades)
    duration_days = max((end - start).total_seconds() / 86400.0, 1.0)
    return {
        "trade_count": len(trades),
        "trades_per_day": len(trades) / duration_days,
        "independent_day_blocks": len(daily),
        "expectancy_per_trade": fmean(values) if values else 0.0,
        "median_trade_return": median(values) if values else 0.0,
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "profit_factor": _profit_factor(values),
        "max_drawdown": _max_drawdown(values),
        "dependence_adjusted_null": _moving_block_null_pvalue(
            daily,
            replicates=2000,
            seed=seed,
        ),
        "symbol_concentration": _concentration(trades),
    }


def evaluate_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: ActivityShockSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    series = build_series(bars_by_symbol, start=start, end=end)
    opportunities = collect_opportunities(series, spec, start=start, end=end)
    primary = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )
    return {
        "candidate": spec.to_dict(),
        "family": FAMILY,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": summarize(delayed, start=start, end=end, seed=seed + 1),
        "opportunity_sample": [row.to_dict() for row in opportunities[:5]],
    }


def development_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 40:
        reasons.append("development_trade_count_below_40")
    if int(primary["independent_day_blocks"]) < 20:
        reasons.append("development_independent_days_below_20")
    if float(primary["trades_per_day"]) < 0.50:
        reasons.append("development_frequency_below_0.50_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_expectancy_nonpositive")
    profit_factor = primary.get("profit_factor")
    if profit_factor is None or float(profit_factor) <= 1.0:
        reasons.append("development_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("development_delay_expectancy_nonpositive")
    return not reasons, reasons


def validation_gate(
    spec: ActivityShockSpec,
    result: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 20:
        reasons.append("validation_trade_count_below_20")
    if int(primary["independent_day_blocks"]) < 12:
        reasons.append("validation_independent_days_below_12")
    if float(primary["trades_per_day"]) < 0.50:
        reasons.append("validation_frequency_below_0.50_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("validation_expectancy_nonpositive")
    if float(primary["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("validation_dependence_p_above_0.05")
    profit_factor = primary.get("profit_factor")
    if profit_factor is None or float(profit_factor) <= 1.0:
        reasons.append("validation_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("validation_delay_expectancy_nonpositive")
    if float(primary["symbol_concentration"]["max_share"]) > spec.concentration_limit:
        reasons.append("validation_symbol_concentration_above_limit")
    return not reasons, reasons


def holdout_gate(
    spec: ActivityShockSpec,
    scenarios: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    high = scenarios["high"]["primary"]
    high_delay = scenarios["high"]["one_bar_delay"]
    reasons: list[str] = []
    if int(high["trade_count"]) < 20:
        reasons.append("holdout_trade_count_below_20")
    if int(high["independent_day_blocks"]) < 12:
        reasons.append("holdout_independent_days_below_12")
    if float(high["trades_per_day"]) < 0.40:
        reasons.append("holdout_frequency_below_0.40_per_day")
    if float(high["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_high_cost_expectancy_nonpositive")
    if float(high["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("holdout_dependence_p_above_0.05")
    profit_factor = high.get("profit_factor")
    if profit_factor is None or float(profit_factor) <= 1.0:
        reasons.append("holdout_profit_factor_not_above_one")
    if float(high_delay["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_delay_expectancy_nonpositive")
    if float(high["symbol_concentration"]["max_share"]) > spec.concentration_limit:
        reasons.append("holdout_symbol_concentration_above_limit")
    for scenario in ("low", "base"):
        if float(scenarios[scenario]["primary"]["expectancy_per_trade"]) <= 0:
            reasons.append(f"holdout_{scenario}_cost_expectancy_nonpositive")
    return not reasons, reasons


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    survivors: list[dict[str, Any]] = []
    for index, spec in enumerate(candidate_specs()):
        result = evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=start,
            end=end,
            scenario="high",
            seed=92000 + index * 10,
        )
        passed, reasons = development_gate(result)
        primary = result["primary"]
        score = (
            float(primary["expectancy_per_trade"])
            * sqrt(max(int(primary["trade_count"]), 1))
        )
        results[spec.candidate_id] = {
            "result": result,
            "passed": passed,
            "reasons": reasons,
            "selection_score": score,
        }
        if passed:
            survivors.append({
                "candidate_id": spec.candidate_id,
                "selection_score": score,
            })
    selected = (
        max(survivors, key=lambda row: (row["selection_score"], row["candidate_id"]))
        if survivors
        else None
    )
    selected_id = str(selected["candidate_id"]) if selected else None
    selected_spec = next(
        (spec for spec in candidate_specs() if spec.candidate_id == selected_id),
        None,
    )
    return {
        "stage": "DEVELOPMENT",
        "family": FAMILY,
        "methodology_version": METHODOLOGY_VERSION,
        "candidate_count": len(candidate_specs()),
        "results": results,
        "survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": selected_spec.to_dict() if selected_spec else None,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def spec_from_dict(payload: Mapping[str, Any]) -> ActivityShockSpec:
    return ActivityShockSpec(**dict(payload))


def evaluate_validation(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    spec = spec_from_dict(candidate_spec)
    result = evaluate_candidate(
        bars_by_symbol,
        spec=spec,
        start=start,
        end=end,
        scenario="high",
        seed=seed,
    )
    passed, reasons = validation_gate(spec, result)
    return {
        "stage": "VALIDATION",
        "candidate_id": spec.candidate_id,
        "result": result,
        "passed": passed,
        "reasons": reasons,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def evaluate_holdout(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_spec: Mapping[str, Any],
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    spec = spec_from_dict(candidate_spec)
    scenarios = {
        scenario: evaluate_candidate(
            bars_by_symbol,
            spec=spec,
            start=start,
            end=end,
            scenario=scenario,
            seed=seed + index * 20,
        )
        for index, scenario in enumerate(("low", "base", "high"))
    }
    passed, reasons = holdout_gate(spec, scenarios)
    return {
        "stage": "HOLDOUT",
        "candidate_id": spec.candidate_id,
        "scenarios": scenarios,
        "passed": passed,
        "reasons": reasons,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
