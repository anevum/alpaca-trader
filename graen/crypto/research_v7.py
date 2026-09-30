from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite, sqrt
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from app.research_agent.multiplicity import benjamini_yekutieli
from .research_v5 import COSTS_BPS


METHODOLOGY_VERSION = "graen-crypto-native-v7"
RESEARCH_BATCH_ID = "crypto-market-transmission-v7"
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
DELAY_ROBUSTNESS_MINUTES = 5
MIN_ACTIVITY_BARS = 6
MIN_ACTIVITY_TRADES = 12
VALIDATION_ALPHA = 0.05


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    family: str
    mode: str
    targets: tuple[str, ...]
    hold_minutes: int
    scan_minutes: int
    lookback_minutes: int
    concentration_limit: float
    leader_symbol: str | None = None
    leader_threshold: float = 0.0
    lag_gap_threshold: float = 0.0
    min_target_return: float = -1.0
    max_target_return: float = 1.0
    min_breadth_positive: int = 0
    market_threshold: float = -1.0
    require_btc_nonnegative: bool = False
    calendar_scope: str | None = None
    calendar_start_hour_utc: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["targets"] = list(self.targets)
        return payload


@dataclass(frozen=True, slots=True)
class Opportunity:
    candidate_id: str
    family: str
    symbol: str
    opportunity_at: datetime
    hold_minutes: int
    signal: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "symbol": self.symbol,
            "opportunity_at": self.opportunity_at.isoformat(),
            "hold_minutes": self.hold_minutes,
            "signal": dict(self.signal),
        }


@dataclass(frozen=True, slots=True)
class Trade:
    candidate_id: str
    family: str
    symbol: str
    opportunity_at: datetime
    entry_at: datetime
    exit_at: datetime
    net_return: float
    scenario: str
    delayed_minutes: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("opportunity_at", "entry_at", "exit_at"):
            payload[key] = payload[key].isoformat()
        return payload


def candidate_specs() -> tuple[CandidateSpec, ...]:
    rows = [
        CandidateSpec(
            "V7-LL-BTC15-ALT60",
            "cross_asset_lead_lag_response",
            "lead_lag",
            ("ETH/USD", "SOL/USD"),
            60,
            15,
            15,
            0.80,
            leader_symbol="BTC/USD",
            leader_threshold=0.0040,
            lag_gap_threshold=0.0020,
            min_target_return=-0.0015,
            max_target_return=0.0030,
            min_breadth_positive=3,
        ),
        CandidateSpec(
            "V7-LL-BTC30-ALT90",
            "cross_asset_lead_lag_response",
            "lead_lag",
            ("ETH/USD", "SOL/USD"),
            90,
            15,
            30,
            0.80,
            leader_symbol="BTC/USD",
            leader_threshold=0.0060,
            lag_gap_threshold=0.0030,
            min_target_return=-0.0020,
            max_target_return=0.0040,
            min_breadth_positive=3,
        ),
        CandidateSpec(
            "V7-LL-ETH15-SOL60",
            "cross_asset_lead_lag_response",
            "lead_lag",
            ("SOL/USD",),
            60,
            15,
            15,
            1.00,
            leader_symbol="ETH/USD",
            leader_threshold=0.0050,
            lag_gap_threshold=0.0030,
            min_target_return=-0.0015,
            max_target_return=0.0030,
            min_breadth_positive=3,
            require_btc_nonnegative=True,
        ),
        CandidateSpec(
            "V7-BREADTH60-ALT90",
            "broad_market_laggard_response",
            "breadth_laggard",
            ("ETH/USD", "SOL/USD"),
            90,
            30,
            60,
            0.80,
            lag_gap_threshold=0.0030,
            min_target_return=-0.0020,
            min_breadth_positive=4,
            market_threshold=0.0030,
        ),
        CandidateSpec(
            "V7-BREADTH120-ALT120",
            "broad_market_laggard_response",
            "breadth_laggard",
            ("ETH/USD", "SOL/USD"),
            120,
            30,
            120,
            0.80,
            lag_gap_threshold=0.0040,
            min_target_return=-0.0030,
            min_breadth_positive=4,
            market_threshold=0.0050,
        ),
    ]
    for scope in ("weekday", "weekend"):
        for start_hour in (0, 6, 12, 18):
            rows.append(
                CandidateSpec(
                    f"V7-CAL-{scope.upper()}-{start_hour:02d}",
                    "calendar_regime_drift",
                    "calendar",
                    EXECUTION_UNIVERSE,
                    120,
                    15,
                    60,
                    0.55,
                    min_target_return=-0.0030,
                    min_breadth_positive=3,
                    market_threshold=0.0,
                    calendar_scope=scope,
                    calendar_start_hour_utc=start_hour,
                )
            )
    return tuple(rows)


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
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if isfinite(result) else 0.0


def _bar_end(row: Mapping[str, Any]) -> datetime:
    return _stamp(row.get("t")) + timedelta(minutes=BAR_MINUTES)


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, dict[datetime, dict[str, Any]]]:
    lower = _aware(start, "start") - timedelta(hours=8)
    upper = _aware(end, "end") + timedelta(hours=3)
    output: dict[str, dict[datetime, dict[str, Any]]] = {}
    for symbol in CONTEXT_UNIVERSE:
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


def _returns(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    end: datetime,
    minutes: int,
) -> dict[str, float]:
    output: dict[str, float] = {}
    for symbol in CONTEXT_UNIVERSE:
        value = _return(series, symbol, end, minutes)
        if value is not None:
            output[symbol] = value
    return output


def _activity_ok(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> bool:
    rows: list[Mapping[str, Any]] = []
    for offset in range(0, 60, BAR_MINUTES):
        row = _bar(series, symbol, end - timedelta(minutes=offset))
        if row is None:
            return False
        rows.append(row)
    nonzero = sum(_f(row.get("v")) > 0 and _f(row.get("n")) > 0 for row in rows)
    trades = sum(int(_f(row.get("n"))) for row in rows)
    return nonzero >= MIN_ACTIVITY_BARS and trades >= MIN_ACTIVITY_TRADES


def _grid(start: datetime, end: datetime, step_minutes: int) -> Iterable[datetime]:
    current = _aware(start, "start")
    end = _aware(end, "end")
    while current < end:
        yield current
        current += timedelta(minutes=step_minutes)


def _market_state(values: Mapping[str, float]) -> dict[str, Any]:
    available = list(values.values())
    return {
        "breadth_positive": sum(value > 0 for value in available),
        "median_return": median(available) if available else 0.0,
        "mean_return": fmean(available) if available else 0.0,
        "symbol_count": len(available),
    }


def _calendar_day_matches(scope: str | None, stamp: datetime) -> bool:
    if scope == "weekday":
        return stamp.weekday() < 5
    if scope == "weekend":
        return stamp.weekday() >= 5
    return False


def _opportunities_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: CandidateSpec,
    stamp: datetime,
) -> list[Opportunity]:
    values = _returns(series, stamp, spec.lookback_minutes)
    if len(values) < 4:
        return []
    market = _market_state(values)
    if int(market["breadth_positive"]) < spec.min_breadth_positive:
        return []

    if spec.mode == "lead_lag":
        leader = values.get(str(spec.leader_symbol))
        if leader is None or leader < spec.leader_threshold:
            return []
        if spec.require_btc_nonnegative and values.get("BTC/USD", -1.0) < 0:
            return []
        candidates: list[tuple[float, str, float]] = []
        for target in spec.targets:
            target_return = values.get(target)
            if target_return is None or not _activity_ok(series, target, stamp):
                continue
            gap = leader - target_return
            if (
                gap >= spec.lag_gap_threshold
                and spec.min_target_return <= target_return <= spec.max_target_return
            ):
                candidates.append((gap, target, target_return))
        if not candidates:
            return []
        gap, target, target_return = max(candidates, key=lambda item: (item[0], item[1]))
        return [
            Opportunity(
                spec.candidate_id,
                spec.family,
                target,
                stamp,
                spec.hold_minutes,
                {
                    "mode": spec.mode,
                    "leader_symbol": spec.leader_symbol,
                    "leader_return": leader,
                    "target_return": target_return,
                    "lead_lag_gap": gap,
                    **market,
                },
            )
        ]

    if spec.mode == "breadth_laggard":
        if float(market["median_return"]) < spec.market_threshold:
            return []
        candidates: list[tuple[float, str, float]] = []
        for target in spec.targets:
            target_return = values.get(target)
            if target_return is None or target_return < spec.min_target_return:
                continue
            if not _activity_ok(series, target, stamp):
                continue
            gap = float(market["median_return"]) - target_return
            if gap >= spec.lag_gap_threshold:
                candidates.append((gap, target, target_return))
        if not candidates:
            return []
        gap, target, target_return = max(candidates, key=lambda item: (item[0], item[1]))
        return [
            Opportunity(
                spec.candidate_id,
                spec.family,
                target,
                stamp,
                spec.hold_minutes,
                {
                    "mode": spec.mode,
                    "target_return": target_return,
                    "median_market_return": market["median_return"],
                    "lag_gap": gap,
                    **market,
                },
            )
        ]

    if spec.mode == "calendar":
        if (
            stamp.minute != 0
            or stamp.hour != spec.calendar_start_hour_utc
            or not _calendar_day_matches(spec.calendar_scope, stamp)
            or float(market["median_return"]) < spec.market_threshold
        ):
            return []
        output: list[Opportunity] = []
        for target in spec.targets:
            target_return = values.get(target)
            if (
                target_return is None
                or target_return < spec.min_target_return
                or not _activity_ok(series, target, stamp)
            ):
                continue
            output.append(
                Opportunity(
                    spec.candidate_id,
                    spec.family,
                    target,
                    stamp,
                    spec.hold_minutes,
                    {
                        "mode": spec.mode,
                        "calendar_scope": spec.calendar_scope,
                        "calendar_start_hour_utc": spec.calendar_start_hour_utc,
                        "target_return": target_return,
                        **market,
                    },
                )
            )
        return output

    raise ValueError(f"unsupported candidate mode: {spec.mode}")


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: CandidateSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    output: list[Opportunity] = []
    for stamp in _grid(start, end, spec.scan_minutes):
        output.extend(_opportunities_at(series, spec, stamp))
    return output


def _fills(symbol: str, entry: float, exit: float, scenario: str) -> tuple[float, float]:
    costs = COSTS_BPS[symbol]
    half_spread = float(costs["spread"][scenario]) / 20000.0
    slippage = float(costs["slippage"][scenario]) / 10000.0
    return (
        entry * (1.0 + half_spread + slippage),
        exit * (1.0 - half_spread - slippage),
    )


def simulate_candidate(
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
        if not (start <= opportunity.opportunity_at < end):
            continue
        if busy_until.get(opportunity.symbol, datetime.min.replace(tzinfo=timezone.utc)) > opportunity.opportunity_at:
            continue
        entry_at = opportunity.opportunity_at + timedelta(minutes=extra_entry_delay_minutes)
        exit_at = entry_at + timedelta(minutes=opportunity.hold_minutes)
        if exit_at > end:
            continue
        entry_reference = _open(series, opportunity.symbol, entry_at + timedelta(minutes=BAR_MINUTES))
        exit_reference = _open(series, opportunity.symbol, exit_at + timedelta(minutes=BAR_MINUTES))
        if entry_reference is None or exit_reference is None:
            continue
        entry_fill, exit_fill = _fills(opportunity.symbol, entry_reference, exit_reference, scenario)
        if entry_fill <= 0 or exit_fill <= 0:
            continue
        output.append(
            Trade(
                opportunity.candidate_id,
                opportunity.family,
                opportunity.symbol,
                opportunity.opportunity_at,
                entry_at,
                exit_at,
                exit_fill / entry_fill - 1.0,
                scenario,
                extra_entry_delay_minutes,
            )
        )
        busy_until[opportunity.symbol] = exit_at
    return output


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


def _daily_means(trades: Sequence[Trade]) -> list[float]:
    groups: dict[date, list[float]] = defaultdict(list)
    for trade in trades:
        groups[trade.entry_at.date()].append(trade.net_return)
    return [fmean(groups[key]) for key in sorted(groups)]


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
    ordered = sorted(trades, key=lambda row: (row.entry_at, row.symbol))
    values = [row.net_return for row in ordered]
    daily = _daily_means(ordered)
    duration_days = max((end - start).total_seconds() / 86400.0, 1.0)
    return {
        "trade_count": len(ordered),
        "trades_per_day": len(ordered) / duration_days,
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
        "symbol_concentration": _concentration(ordered),
    }


def evaluate_candidate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: CandidateSpec,
    *,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    opportunities = collect_opportunities(series, spec, start=start, end=end)
    primary = simulate_candidate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = simulate_candidate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )
    return {
        "candidate": spec.to_dict(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": summarize(delayed, start=start, end=end, seed=seed + 1),
        "opportunity_sample": [row.to_dict() for row in opportunities[:3]],
    }


def _development_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 20:
        reasons.append("development_trade_count_below_20")
    if int(primary["independent_day_blocks"]) < 10:
        reasons.append("development_independent_days_below_10")
    if float(primary["trades_per_day"]) < 0.35:
        reasons.append("development_frequency_below_0.35_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_expectancy_nonpositive")
    pf = primary.get("profit_factor")
    if pf is not None and float(pf) <= 1.0:
        reasons.append("development_profit_factor_not_above_one")
    return not reasons, reasons


def _validation_gate(
    spec: CandidateSpec,
    development: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    multiplicity_rejected: bool,
) -> tuple[bool, list[str]]:
    dev_passed, dev_reasons = _development_gate(development)
    primary = validation["primary"]
    delayed = validation["one_bar_delay"]
    reasons = list(dev_reasons)
    if not dev_passed:
        reasons.append("development_gate_failed")
    if int(primary["trade_count"]) < 12:
        reasons.append("validation_trade_count_below_12")
    if int(primary["independent_day_blocks"]) < 8:
        reasons.append("validation_independent_days_below_8")
    if float(primary["trades_per_day"]) < 0.35:
        reasons.append("validation_frequency_below_0.35_per_day")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("validation_expectancy_nonpositive")
    if not multiplicity_rejected:
        reasons.append("validation_multiplicity_gate_failed")
    if float(primary["dependence_adjusted_null"]["p_value"]) > VALIDATION_ALPHA:
        reasons.append("validation_dependence_p_above_0.05")
    pf = primary.get("profit_factor")
    if pf is not None and float(pf) <= 1.0:
        reasons.append("validation_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("one_bar_delay_expectancy_nonpositive")
    if float(primary["symbol_concentration"]["max_share"]) > spec.concentration_limit:
        reasons.append("symbol_concentration_above_limit")
    return not reasons, sorted(set(reasons))


def _holdout_gate(
    spec: CandidateSpec,
    scenarios: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    high = scenarios["high"]["primary"]
    high_delay = scenarios["high"]["one_bar_delay"]
    reasons: list[str] = []
    if int(high["trade_count"]) < 8:
        reasons.append("holdout_trade_count_below_8")
    if int(high["independent_day_blocks"]) < 6:
        reasons.append("holdout_independent_days_below_6")
    if float(high["trades_per_day"]) < 0.30:
        reasons.append("holdout_frequency_below_0.30_per_day")
    if float(high["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_high_cost_expectancy_nonpositive")
    if float(high["dependence_adjusted_null"]["p_value"]) > VALIDATION_ALPHA:
        reasons.append("holdout_dependence_p_above_0.05")
    pf = high.get("profit_factor")
    if pf is not None and float(pf) <= 1.0:
        reasons.append("holdout_profit_factor_not_above_one")
    if float(high_delay["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_delay_expectancy_nonpositive")
    if float(high["symbol_concentration"]["max_share"]) > spec.concentration_limit:
        reasons.append("holdout_symbol_concentration_above_limit")
    for scenario in ("low", "base"):
        if float(scenarios[scenario]["primary"]["expectancy_per_trade"]) <= 0:
            reasons.append(f"holdout_{scenario}_cost_expectancy_nonpositive")
    return not reasons, reasons


def _validate_ranges(
    development_start: datetime,
    validation_start: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
) -> tuple[datetime, datetime, datetime, datetime]:
    values = (
        _aware(development_start, "development_start"),
        _aware(validation_start, "validation_start"),
        _aware(holdout_start, "holdout_start"),
        _aware(holdout_end, "holdout_end"),
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
        raise ValueError("v7 corpus provenance must be explicitly verified before inspection")
    for row in previously_inspected_ranges:
        prior_start = _stamp(row.get("start"))
        prior_end = _stamp(row.get("end"))
        if prior_end <= prior_start:
            raise ValueError("previously inspected corpus range is invalid")
        if start < prior_end and prior_start < end:
            label = str(row.get("id") or row.get("methodology") or "prior research")
            raise ValueError(f"v7 corpus overlaps previously inspected range: {label}")


def run_crypto_research_v7(
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
    specs = candidate_specs()
    development: dict[str, dict[str, Any]] = {}
    validation: dict[str, dict[str, Any]] = {}
    development_gates: list[dict[str, Any]] = []
    development_survivors: list[tuple[int, CandidateSpec]] = []

    for index, spec in enumerate(specs):
        development_result = evaluate_candidate(
            series,
            spec,
            start=development_start,
            end=validation_start,
            scenario="high",
            seed=78000 + index * 10,
        )
        development[spec.candidate_id] = development_result
        passed, reasons = _development_gate(development_result)
        development_gates.append({
            "candidate_id": spec.candidate_id,
            "family": spec.family,
            "passed": passed,
            "reasons": reasons,
        })
        if passed:
            development_survivors.append((index, spec))

    validation_order: list[tuple[int, CandidateSpec]] = []
    p_values: list[float] = []
    for index, spec in development_survivors:
        validation_result = evaluate_candidate(
            series,
            spec,
            start=validation_start,
            end=holdout_start,
            scenario="high",
            seed=79000 + index * 10,
        )
        validation[spec.candidate_id] = validation_result
        validation_order.append((index, spec))
        p_values.append(
            float(validation_result["primary"]["dependence_adjusted_null"]["p_value"])
        )

    multiplicity = benjamini_yekutieli(p_values, alpha=VALIDATION_ALPHA)
    rejected_positions = set(multiplicity.rejected_indices)
    survivors: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []

    survivor_ids = {spec.candidate_id for _, spec in development_survivors}
    for index, spec in enumerate(specs):
        if spec.candidate_id not in survivor_ids:
            _, reasons = _development_gate(development[spec.candidate_id])
            gate_rows.append({
                "candidate_id": spec.candidate_id,
                "family": spec.family,
                "passed": False,
                "reasons": sorted(set([*reasons, "validation_not_opened"])),
                "multiplicity_rejected": False,
                "selection_score": None,
            })

    for position, (index, spec) in enumerate(validation_order):
        passed, reasons = _validation_gate(
            spec,
            development[spec.candidate_id],
            validation[spec.candidate_id],
            multiplicity_rejected=position in rejected_positions,
        )
        primary = validation[spec.candidate_id]["primary"]
        score = float(primary["expectancy_per_trade"]) * sqrt(max(int(primary["trade_count"]), 1))
        row = {
            "candidate_id": spec.candidate_id,
            "family": spec.family,
            "passed": passed,
            "reasons": reasons,
            "multiplicity_rejected": position in rejected_positions,
            "selection_score": score,
        }
        gate_rows.append(row)
        if passed:
            survivors.append(row)

    gate_rows.sort(key=lambda row: next(
        idx for idx, spec in enumerate(specs) if spec.candidate_id == row["candidate_id"]
    ))

    selected = (
        max(survivors, key=lambda row: (float(row["selection_score"]), str(row["candidate_id"])))
        if survivors
        else None
    )
    holdout: dict[str, Any]
    decision = "CONTINUE_RESEARCH"
    status = "NO_VALIDATION_SURVIVOR"
    selected_candidate = None

    if selected is None:
        holdout = {
            "opened": False,
            "reason": "no candidate passed frozen development and validation gates",
            "start": holdout_start.isoformat(),
            "end": holdout_end.isoformat(),
        }
    else:
        selected_candidate = next(
            spec for spec in specs if spec.candidate_id == selected["candidate_id"]
        )
        scenarios = {
            scenario: evaluate_candidate(
                series,
                selected_candidate,
                start=holdout_start,
                end=holdout_end,
                scenario=scenario,
                seed=80000 + offset * 20,
            )
            for offset, scenario in enumerate(("low", "base", "high"))
        }
        passed, reasons = _holdout_gate(selected_candidate, scenarios)
        holdout = {
            "opened": True,
            "candidate_id": selected_candidate.candidate_id,
            "selection_rule": "max_validation_expectancy_times_sqrt_trade_count",
            "start": holdout_start.isoformat(),
            "end": holdout_end.isoformat(),
            "scenarios": scenarios,
            "passed": passed,
            "reasons": reasons,
        }
        if passed:
            status = "HOLDOUT_PASS"
            decision = "PROMOTE_TO_VELUM"
        else:
            status = "HOLDOUT_FAIL"

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "research_batch_id": RESEARCH_BATCH_ID,
        "status": status,
        "decision": decision,
        "market_lane": "crypto",
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "risk_or_sizing_authority": False,
        "production_promotion_authority": False,
        "model_invoked": False,
        "production_state_changed": False,
        "search_space_exhausted": False,
        "context_universe": list(CONTEXT_UNIVERSE),
        "execution_universe": list(EXECUTION_UNIVERSE),
        "candidate_registry": [spec.to_dict() for spec in specs],
        "candidate_family_count": len({spec.family for spec in specs}),
        "candidate_count": len(specs),
        "corpus_contract": {
            "provenance_verified": corpus_provenance_verified,
            "prior_range_count": len(previously_inspected_ranges),
            "overlap_allowed": False,
            "development": [development_start.isoformat(), validation_start.isoformat()],
            "validation": [validation_start.isoformat(), holdout_start.isoformat()],
            "holdout": [holdout_start.isoformat(), holdout_end.isoformat()],
            "holdout_opened_only_after_validation_gate": True,
        },
        "validation_multiplicity": multiplicity.to_dict(),
        "development": development,
        "development_gates": development_gates,
        "development_survivors": [spec.candidate_id for _, spec in development_survivors],
        "validation": validation,
        "validation_gates": gate_rows,
        "validation_survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate": selected_candidate.to_dict() if selected_candidate else None,
        "holdout": holdout,
        "next_action": (
            "VELUM_REPLAY"
            if decision == "PROMOTE_TO_VELUM"
            else "DESIGN_NEXT_FROZEN_RESEARCH_BATCH"
        ),
    }
