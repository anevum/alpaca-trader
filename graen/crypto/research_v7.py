from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from .research_v5 import COSTS_BPS


METHODOLOGY_VERSION = "graen-crypto-native-v7-leadlag-v1"
STRATEGY_VERSION_ID = "CRYPTO-LEADLAG-001"
COST_SNAPSHOT_VERSION = "graen-crypto-cost-snapshot-v5"

BAR_MINUTES = 5
HOLD_MINUTES = 30
COOLDOWN_MINUTES = 60
LEADER_VOL_LOOKBACK_HOURS = 24
BETA_LOOKBACK_HOURS = 168
LEADER_IMPULSE_Z_THRESHOLD = 2.0
FOLLOWER_UNDERREACTION_SIGMA = 1.0

LEADER_UNIVERSE = ("BTC/USD", "ETH/USD")
FOLLOWER_UNIVERSE = ("ETH/USD", "SOL/USD")
CONTEXT_UNIVERSE = ("BTC/USD", "ETH/USD", "SOL/USD")
MIN_VOL_OBSERVATIONS = 144
MIN_BETA_OBSERVATIONS = 1000


@dataclass(frozen=True, slots=True)
class LeadLagOpportunity:
    leader: str
    follower: str
    opportunity_at: datetime
    direction: int
    leader_return_5: float
    leader_z: float
    beta: float
    follower_return_5: float
    residual: float
    residual_sigma: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["opportunity_at"] = self.opportunity_at.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class LeadLagTrade:
    candidate_id: str
    leader: str
    follower: str
    direction: int
    opportunity_at: datetime
    entry_at: datetime
    exit_at: datetime
    entry_reference: float
    exit_reference: float
    net_return: float
    scenario: str
    delayed_minutes: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("opportunity_at", "entry_at", "exit_at"):
            payload[key] = payload[key].isoformat()
        return payload


def candidate_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "candidate_id": "LEADER_IMPULSE_CONTROL",
            "family": "leader_impulse_only",
            "confirmatory": False,
            "hold_minutes": HOLD_MINUTES,
            "role": "matched_control",
        },
        {
            "candidate_id": STRATEGY_VERSION_ID,
            "family": "cross_asset_lead_lag_underreaction",
            "confirmatory": True,
            "hold_minutes": HOLD_MINUTES,
            "role": "primary_candidate",
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
    lower = start - timedelta(hours=BETA_LOOKBACK_HOURS, minutes=BAR_MINUTES)
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


def _ret(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return right / left - 1.0


def _paired_returns(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    left_symbol: str,
    right_symbol: str,
    *,
    end: datetime,
    hours: int,
) -> tuple[list[float], list[float]]:
    left: list[float] = []
    right: list[float] = []
    cursor = end - timedelta(hours=hours) + timedelta(minutes=BAR_MINUTES)
    while cursor <= end:
        prior = cursor - timedelta(minutes=BAR_MINUTES)
        left_return = _ret(_close(series, left_symbol, prior), _close(series, left_symbol, cursor))
        right_return = _ret(_close(series, right_symbol, prior), _close(series, right_symbol, cursor))
        if left_return is not None and right_return is not None:
            left.append(left_return)
            right.append(right_return)
        cursor += timedelta(minutes=BAR_MINUTES)
    return left, right


def _beta_and_residual_sigma(
    leader_returns: Sequence[float],
    follower_returns: Sequence[float],
) -> tuple[float, float] | None:
    if len(leader_returns) != len(follower_returns) or len(leader_returns) < MIN_BETA_OBSERVATIONS:
        return None
    leader_mean = fmean(leader_returns)
    follower_mean = fmean(follower_returns)
    variance = sum((value - leader_mean) ** 2 for value in leader_returns)
    if variance <= 1e-18:
        return None
    covariance = sum(
        (leader - leader_mean) * (follower - follower_mean)
        for leader, follower in zip(leader_returns, follower_returns)
    )
    beta = covariance / variance
    residuals = [
        follower - beta * leader
        for leader, follower in zip(leader_returns, follower_returns)
    ]
    sigma = pstdev(residuals) if len(residuals) > 1 else 0.0
    if sigma <= 1e-12:
        return None
    return beta, sigma


def _leader_impulse(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    leader: str,
    end: datetime,
) -> tuple[float, float] | None:
    current_return = _ret(
        _close(series, leader, end - timedelta(minutes=BAR_MINUTES)),
        _close(series, leader, end),
    )
    if current_return is None or current_return == 0:
        return None
    history, _ = _paired_returns(
        series,
        leader,
        leader,
        end=end - timedelta(minutes=BAR_MINUTES),
        hours=LEADER_VOL_LOOKBACK_HOURS,
    )
    if len(history) < MIN_VOL_OBSERVATIONS:
        return None
    sigma = pstdev(history)
    if sigma <= 1e-12:
        return None
    z = current_return / sigma
    if abs(z) < LEADER_IMPULSE_Z_THRESHOLD:
        return None
    return current_return, z


def opportunity_state(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    leader: str,
    follower: str,
    end: datetime,
) -> LeadLagOpportunity | None:
    if leader not in LEADER_UNIVERSE or follower not in FOLLOWER_UNIVERSE or leader == follower:
        return None
    impulse = _leader_impulse(series, leader, end)
    if impulse is None:
        return None
    leader_return, leader_z = impulse
    direction = 1 if leader_return > 0 else -1

    leader_history, follower_history = _paired_returns(
        series,
        leader,
        follower,
        end=end - timedelta(minutes=BAR_MINUTES),
        hours=BETA_LOOKBACK_HOURS,
    )
    beta_state = _beta_and_residual_sigma(leader_history, follower_history)
    if beta_state is None:
        return None
    beta, residual_sigma = beta_state
    follower_return = _ret(
        _close(series, follower, end - timedelta(minutes=BAR_MINUTES)),
        _close(series, follower, end),
    )
    if follower_return is None:
        return None
    residual = follower_return - beta * leader_return
    signed_residual = direction * residual
    if signed_residual > -FOLLOWER_UNDERREACTION_SIGMA * residual_sigma:
        return None
    return LeadLagOpportunity(
        leader=leader,
        follower=follower,
        opportunity_at=end,
        direction=direction,
        leader_return_5=leader_return,
        leader_z=leader_z,
        beta=beta,
        follower_return_5=follower_return,
        residual=residual,
        residual_sigma=residual_sigma,
    )


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> list[LeadLagOpportunity]:
    opportunities: list[LeadLagOpportunity] = []
    cooldown_until: dict[str, datetime] = {}
    cursor = start
    while cursor < end:
        for leader in LEADER_UNIVERSE:
            for follower in FOLLOWER_UNIVERSE:
                if leader == follower:
                    continue
                if cooldown_until.get(follower, datetime.min.replace(tzinfo=timezone.utc)) > cursor:
                    continue
                opportunity = opportunity_state(series, leader=leader, follower=follower, end=cursor)
                if opportunity is None:
                    continue
                opportunities.append(opportunity)
                cooldown_until[follower] = cursor + timedelta(minutes=COOLDOWN_MINUTES)
        cursor += timedelta(minutes=BAR_MINUTES)
    return opportunities


def round_trip_cost_bps(symbol: str, scenario: str = "high") -> float:
    state = COSTS_BPS[symbol]
    return float(state["spread"][scenario]) + 2.0 * float(state["slippage"][scenario])


def _fill_prices(symbol: str, entry: float, exit: float, direction: int, scenario: str) -> tuple[float, float]:
    state = COSTS_BPS[symbol]
    half_spread = float(state["spread"][scenario]) / 20000.0
    slippage = float(state["slippage"][scenario]) / 10000.0
    if direction > 0:
        return (
            entry * (1.0 + half_spread + slippage),
            exit * (1.0 - half_spread - slippage),
        )
    return (
        entry * (1.0 - half_spread - slippage),
        exit * (1.0 + half_spread + slippage),
    )


def _make_trade(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunity: LeadLagOpportunity,
    *,
    scenario: str,
    extra_entry_delay_minutes: int = 0,
    candidate_id: str = STRATEGY_VERSION_ID,
) -> LeadLagTrade | None:
    entry_at = opportunity.opportunity_at + timedelta(minutes=extra_entry_delay_minutes)
    exit_at = entry_at + timedelta(minutes=HOLD_MINUTES)
    entry_reference = _open(series, opportunity.follower, entry_at + timedelta(minutes=BAR_MINUTES))
    exit_reference = _open(series, opportunity.follower, exit_at + timedelta(minutes=BAR_MINUTES))
    if entry_reference is None or exit_reference is None:
        return None
    entry_fill, exit_fill = _fill_prices(
        opportunity.follower,
        entry_reference,
        exit_reference,
        opportunity.direction,
        scenario,
    )
    if entry_fill <= 0 or exit_fill <= 0:
        return None
    net_return = (
        exit_fill / entry_fill - 1.0
        if opportunity.direction > 0
        else entry_fill / exit_fill - 1.0
    )
    return LeadLagTrade(
        candidate_id=candidate_id,
        leader=opportunity.leader,
        follower=opportunity.follower,
        direction=opportunity.direction,
        opportunity_at=opportunity.opportunity_at,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_reference=entry_reference,
        exit_reference=exit_reference,
        net_return=net_return,
        scenario=scenario,
        delayed_minutes=extra_entry_delay_minutes,
    )


def simulate_candidate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunities: Sequence[LeadLagOpportunity],
    *,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    extra_entry_delay_minutes: int = 0,
) -> list[LeadLagTrade]:
    trades: list[LeadLagTrade] = []
    busy_until: dict[str, datetime] = {}
    for opportunity in sorted(opportunities, key=lambda row: (row.opportunity_at, row.follower, row.leader)):
        if not (start <= opportunity.opportunity_at < end):
            continue
        if busy_until.get(opportunity.follower, datetime.min.replace(tzinfo=timezone.utc)) > opportunity.opportunity_at:
            continue
        trade = _make_trade(
            series,
            opportunity,
            scenario=scenario,
            extra_entry_delay_minutes=extra_entry_delay_minutes,
        )
        if trade is None or trade.exit_at > end:
            continue
        trades.append(trade)
        busy_until[opportunity.follower] = max(
            opportunity.opportunity_at + timedelta(minutes=COOLDOWN_MINUTES),
            trade.exit_at,
        )
    return trades


def _daily_means(trades: Sequence[LeadLagTrade]) -> list[float]:
    groups: dict[date, list[float]] = defaultdict(list)
    for trade in trades:
        groups[trade.entry_at.date()].append(trade.net_return)
    return [fmean(groups[key]) for key in sorted(groups)]


def _profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return gains / losses if losses > 0 else None


def _concentration(values: Sequence[str]) -> dict[str, Any]:
    if not values:
        return {"max_share": 0.0, "counts": {}}
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    total = len(values)
    return {"max_share": max(counts.values()) / total, "counts": dict(sorted(counts.items()))}


def summarize_trades(trades: Sequence[LeadLagTrade], *, seed: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda row: (row.entry_at, row.follower, row.leader))
    values = [trade.net_return for trade in ordered]
    daily = _daily_means(ordered)
    return {
        "trade_count": len(ordered),
        "expectancy_per_trade": fmean(values) if values else 0.0,
        "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "profit_factor": _profit_factor(values),
        "independent_day_blocks": len(daily),
        "dependence_adjusted_null": _moving_block_null_pvalue(daily, replicates=2000, seed=seed),
        "symbol_concentration": _concentration([trade.follower for trade in ordered]),
        "leader_concentration": _concentration([trade.leader for trade in ordered]),
    }


def evaluate_period(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    scenario: str = "high",
) -> dict[str, Any]:
    opportunities = collect_opportunities(series, start=start, end=end)
    primary = simulate_candidate(series, opportunities, start=start, end=end, scenario=scenario)
    delayed = simulate_candidate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
        extra_entry_delay_minutes=BAR_MINUTES,
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "primary": summarize_trades(primary, seed=78101),
        "one_bar_delay_robustness": summarize_trades(delayed, seed=78102),
        "sample_opportunities": [row.to_dict() for row in opportunities[:3]],
    }


def _candidate_passed(development: Mapping[str, Any], validation: Mapping[str, Any]) -> bool:
    dev = development["primary"]
    val = validation["primary"]
    delayed = validation["one_bar_delay_robustness"]
    return bool(
        int(dev["trade_count"]) >= 20
        and float(dev["expectancy_per_trade"]) > 0
        and int(val["trade_count"]) >= 30
        and int(val["independent_day_blocks"]) >= 20
        and float(val["expectancy_per_trade"]) > 0
        and float(val["dependence_adjusted_null"]["p_value"]) <= 0.05
        and (val["profit_factor"] is None or float(val["profit_factor"]) > 1.0)
        and float(val["symbol_concentration"]["max_share"]) <= 0.70
        and float(delayed["expectancy_per_trade"]) > 0
    )


def _holdout_passed(high: Mapping[str, Any]) -> bool:
    primary = high["primary"]
    delayed = high["one_bar_delay_robustness"]
    return bool(
        int(primary["trade_count"]) >= 20
        and int(primary["independent_day_blocks"]) >= 15
        and float(primary["expectancy_per_trade"]) > 0
        and float(primary["dependence_adjusted_null"]["p_value"]) <= 0.05
        and (primary["profit_factor"] is None or float(primary["profit_factor"]) > 1.0)
        and float(primary["symbol_concentration"]["max_share"]) <= 0.60
        and float(delayed["expectancy_per_trade"]) > 0
    )


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
        development_start, validation_start, holdout_start, holdout_end
    )
    _verify_uninspected_corpus(
        start=development_start,
        end=holdout_end,
        corpus_provenance_verified=corpus_provenance_verified,
        previously_inspected_ranges=previously_inspected_ranges,
    )
    series = build_series(bars_by_symbol, start=development_start, end=holdout_end)
    development = evaluate_period(series, start=development_start, end=validation_start, scenario="high")
    validation = evaluate_period(series, start=validation_start, end=holdout_start, scenario="high")
    validation_passed = _candidate_passed(development, validation)
    if validation_passed:
        holdout_scenarios = {
            scenario: evaluate_period(series, start=holdout_start, end=holdout_end, scenario=scenario)
            for scenario in ("low", "base", "high")
        }
        holdout = {
            "opened": True,
            "start": holdout_start.isoformat(),
            "end": holdout_end.isoformat(),
            "scenarios": holdout_scenarios,
            "passed": _holdout_passed(holdout_scenarios["high"]),
        }
    else:
        holdout = {
            "opened": False,
            "start": holdout_start.isoformat(),
            "end": holdout_end.isoformat(),
            "reason": "validation candidate did not satisfy frozen v7 gates",
        }
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
        "broker_orders_possible": False,
        "risk_or_sizing_authority": False,
        "production_state_changed": False,
        "candidate_family": list(candidate_specs()),
        "leader_universe": list(LEADER_UNIVERSE),
        "follower_universe": list(FOLLOWER_UNIVERSE),
        "frozen_parameters": {
            "bar_minutes": BAR_MINUTES,
            "hold_minutes": HOLD_MINUTES,
            "cooldown_minutes": COOLDOWN_MINUTES,
            "leader_volatility_lookback_hours": LEADER_VOL_LOOKBACK_HOURS,
            "beta_lookback_hours": BETA_LOOKBACK_HOURS,
            "leader_impulse_z_threshold": LEADER_IMPULSE_Z_THRESHOLD,
            "follower_underreaction_sigma": FOLLOWER_UNDERREACTION_SIGMA,
            "cost_snapshot_version": COST_SNAPSHOT_VERSION,
        },
        "corpus_contract": {
            "provenance_verified": corpus_provenance_verified,
            "prior_range_count": len(previously_inspected_ranges),
            "overlap_allowed": False,
            "holdout_opened_only_after_validation_gate": True,
        },
        "development": development,
        "validation": validation,
        "validation_passed": validation_passed,
        "holdout": holdout,
    }
