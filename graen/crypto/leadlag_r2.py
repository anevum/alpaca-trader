from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from statistics import fmean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from .research_v5 import COSTS_BPS


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-crypto-leadlag-r2"
HYPOTHESIS_ID = "CRYPTO-LEADLAG-001"
CANDIDATE_ID = "CRYPTO-LEADLAG-001-R2"
PRESPEC_REVISION = 2
REVISION_REASON = (
    "Pre-data operational-feasibility restriction: RHEN's current Alpaca crypto lane "
    "is long-only, so negative-leader impulses that would require shorting are excluded "
    "before the Dec-2025/Jan-2026 corpus is inspected."
)

LEADERS = ("BTC/USD", "ETH/USD")
FOLLOWERS_BY_LEADER = {
    "BTC/USD": ("ETH/USD", "SOL/USD"),
    "ETH/USD": ("SOL/USD",),
}
UNIVERSE = ("BTC/USD", "ETH/USD", "SOL/USD")
BAR_MINUTES = 5
LEADER_VOL_LOOKBACK_HOURS = 24
BETA_LOOKBACK_HOURS = 168
LEADER_IMPULSE_Z_THRESHOLD = 2.0
FOLLOWER_UNDERREACTION_Z_THRESHOLD = 1.0
HOLD_MINUTES = 30
COOLDOWN_MINUTES = 60
DELAY_ROBUSTNESS_MINUTES = 5
HIGH_COST_SCENARIO = "high"


@dataclass(frozen=True, slots=True)
class LeadLagOpportunity:
    leader: str
    follower: str
    opportunity_at: datetime
    leader_return_5: float
    leader_z: float
    beta: float
    follower_return_5: float
    residual: float
    residual_sigma: float
    residual_z: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["opportunity_at"] = self.opportunity_at.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class LeadLagTrade:
    leader: str
    follower: str
    opportunity_at: datetime
    entry_at: datetime
    exit_at: datetime
    entry_reference: float
    exit_reference: float
    net_return: float
    scenario: str
    delayed_minutes: int
    leader_z: float
    residual_z: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("opportunity_at", "entry_at", "exit_at"):
            payload[key] = self.__getattribute__(key).isoformat()
        return payload


def research_specification() -> dict[str, Any]:
    return {
        "schema_version": "graen.crypto_leadlag.prespec.v2",
        "hypothesis_id": HYPOTHESIS_ID,
        "candidate_id": CANDIDATE_ID,
        "revision": PRESPEC_REVISION,
        "revision_reason": REVISION_REASON,
        "methodology_version": METHODOLOGY_VERSION,
        "family": "cross_asset_lead_lag_underreaction",
        "market_lane": "crypto",
        "bar_interval_minutes": BAR_MINUTES,
        "leaders": list(LEADERS),
        "followers_by_leader": {
            key: list(value) for key, value in FOLLOWERS_BY_LEADER.items()
        },
        "side_contract": "LONG_ONLY_POSITIVE_LEADER_IMPULSES",
        "leader_impulse_z_threshold": LEADER_IMPULSE_Z_THRESHOLD,
        "leader_volatility_lookback_hours": LEADER_VOL_LOOKBACK_HOURS,
        "rolling_beta_lookback_hours": BETA_LOOKBACK_HOURS,
        "follower_underreaction_z_threshold": FOLLOWER_UNDERREACTION_Z_THRESHOLD,
        "hold_minutes": HOLD_MINUTES,
        "cooldown_minutes_per_follower": COOLDOWN_MINUTES,
        "one_bar_delay_robustness_minutes": DELAY_ROBUSTNESS_MINUTES,
        "entry_reference": "next completed 5-minute bar open after signal",
        "cost_model": "graen-crypto-cost-snapshot-v5",
        "primary_cost_scenario": HIGH_COST_SCENARIO,
        "control": "positive leader impulse without follower-underreaction filter",
        "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT"],
        "holdout_opened_only_after_validation_pass": True,
        "development_gate": {
            "min_trades": 20,
            "expectancy_must_be_positive": True,
        },
        "validation_gate": {
            "min_trades": 30,
            "min_independent_day_blocks": 20,
            "dependence_adjusted_p_max": 0.05,
            "profit_factor_min": 1.0,
            "expectancy_must_be_positive": True,
            "symbol_concentration_max_share": 0.70,
            "one_bar_delay_expectancy_must_be_positive": True,
        },
        "holdout_gate": {
            "min_trades": 20,
            "min_independent_day_blocks": 15,
            "dependence_adjusted_p_max": 0.05,
            "profit_factor_min": 1.0,
            "expectancy_must_be_positive": True,
            "symbol_concentration_max_share": 0.60,
            "one_bar_delay_expectancy_must_be_positive": True,
        },
        "multiplicity": {
            "confirmatory_candidate_count": 1,
            "method": "not_required_single_confirmatory_candidate",
        },
        "authority": {
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
        },
    }


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


def build_series(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[datetime, dict[str, Any]]]:
    output: dict[str, dict[datetime, dict[str, Any]]] = {}
    for symbol in UNIVERSE:
        rows: dict[datetime, dict[str, Any]] = {}
        for raw in bars_by_symbol.get(symbol, ()):
            try:
                rows[_bar_end(raw)] = dict(raw)
            except Exception:
                continue
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


def _return_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    end: datetime,
) -> float | None:
    previous = _close(series, symbol, end - timedelta(minutes=BAR_MINUTES))
    current = _close(series, symbol, end)
    if previous is None or current is None or previous <= 0:
        return None
    return current / previous - 1.0


def _grid(start: datetime, end: datetime) -> Iterable[datetime]:
    current = _aware(start, "start")
    limit = _aware(end, "end")
    while current < limit:
        yield current
        current += timedelta(minutes=BAR_MINUTES)


def _return_history(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    symbol: str,
    *,
    end: datetime,
    lookback_hours: int,
) -> list[float]:
    values: list[float] = []
    current = end - timedelta(hours=lookback_hours) + timedelta(minutes=BAR_MINUTES)
    latest = end - timedelta(minutes=BAR_MINUTES)
    while current <= latest:
        value = _return_at(series, symbol, current)
        if value is not None:
            values.append(value)
        current += timedelta(minutes=BAR_MINUTES)
    return values


def _paired_history(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    leader: str,
    follower: str,
    *,
    end: datetime,
    lookback_hours: int,
) -> tuple[list[float], list[float]]:
    leaders: list[float] = []
    followers: list[float] = []
    current = end - timedelta(hours=lookback_hours) + timedelta(minutes=BAR_MINUTES)
    latest = end - timedelta(minutes=BAR_MINUTES)
    while current <= latest:
        left = _return_at(series, leader, current)
        right = _return_at(series, follower, current)
        if left is not None and right is not None:
            leaders.append(left)
            followers.append(right)
        current += timedelta(minutes=BAR_MINUTES)
    return leaders, followers


def _beta(leader_returns: Sequence[float], follower_returns: Sequence[float]) -> float | None:
    if len(leader_returns) != len(follower_returns) or len(leader_returns) < 100:
        return None
    lm = fmean(leader_returns)
    fm = fmean(follower_returns)
    variance = sum((value - lm) ** 2 for value in leader_returns)
    if variance <= 1e-18:
        return None
    covariance = sum(
        (left - lm) * (right - fm)
        for left, right in zip(leader_returns, follower_returns)
    )
    return covariance / variance


def _residual_sigma(
    leader_returns: Sequence[float],
    follower_returns: Sequence[float],
    beta: float,
) -> float:
    residuals = [
        follower - beta * leader
        for leader, follower in zip(leader_returns, follower_returns)
    ]
    return pstdev(residuals) if len(residuals) > 1 else 0.0


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    leader: str,
    follower: str,
    stamp: datetime,
) -> LeadLagOpportunity | None:
    leader_return = _return_at(series, leader, stamp)
    follower_return = _return_at(series, follower, stamp)
    if leader_return is None or follower_return is None or leader_return <= 0:
        return None

    vol_history = _return_history(
        series,
        leader,
        end=stamp,
        lookback_hours=LEADER_VOL_LOOKBACK_HOURS,
    )
    if len(vol_history) < (LEADER_VOL_LOOKBACK_HOURS * 60 // BAR_MINUTES) * 0.80:
        return None
    leader_sigma = pstdev(vol_history) if len(vol_history) > 1 else 0.0
    if leader_sigma <= 1e-12:
        return None
    leader_z = leader_return / leader_sigma
    if leader_z < LEADER_IMPULSE_Z_THRESHOLD:
        return None

    leader_history, follower_history = _paired_history(
        series,
        leader,
        follower,
        end=stamp,
        lookback_hours=BETA_LOOKBACK_HOURS,
    )
    required = int((BETA_LOOKBACK_HOURS * 60 // BAR_MINUTES) * 0.80)
    if len(leader_history) < required:
        return None
    beta = _beta(leader_history, follower_history)
    if beta is None:
        return None
    sigma = _residual_sigma(leader_history, follower_history, beta)
    if sigma <= 1e-12:
        return None

    residual = follower_return - beta * leader_return
    residual_z = residual / sigma
    if residual_z > -FOLLOWER_UNDERREACTION_Z_THRESHOLD:
        return None

    return LeadLagOpportunity(
        leader=leader,
        follower=follower,
        opportunity_at=stamp,
        leader_return_5=leader_return,
        leader_z=leader_z,
        beta=beta,
        follower_return_5=follower_return,
        residual=residual,
        residual_sigma=sigma,
        residual_z=residual_z,
    )


def _leader_impulse_control_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> list[LeadLagOpportunity]:
    output: list[LeadLagOpportunity] = []
    cooldown: dict[str, datetime] = {}
    for stamp in _grid(start, end):
        choices: dict[str, LeadLagOpportunity] = {}
        for leader in LEADERS:
            leader_return = _return_at(series, leader, stamp)
            if leader_return is None or leader_return <= 0:
                continue
            history = _return_history(
                series,
                leader,
                end=stamp,
                lookback_hours=LEADER_VOL_LOOKBACK_HOURS,
            )
            if len(history) < (LEADER_VOL_LOOKBACK_HOURS * 60 // BAR_MINUTES) * 0.80:
                continue
            sigma = pstdev(history) if len(history) > 1 else 0.0
            if sigma <= 1e-12:
                continue
            leader_z = leader_return / sigma
            if leader_z < LEADER_IMPULSE_Z_THRESHOLD:
                continue
            for follower in FOLLOWERS_BY_LEADER[leader]:
                if cooldown.get(follower, datetime.min.replace(tzinfo=UTC)) > stamp:
                    continue
                follower_return = _return_at(series, follower, stamp)
                if follower_return is None:
                    continue
                candidate = LeadLagOpportunity(
                    leader=leader,
                    follower=follower,
                    opportunity_at=stamp,
                    leader_return_5=leader_return,
                    leader_z=leader_z,
                    beta=0.0,
                    follower_return_5=follower_return,
                    residual=0.0,
                    residual_sigma=0.0,
                    residual_z=0.0,
                )
                current = choices.get(follower)
                if current is None or candidate.leader_z > current.leader_z:
                    choices[follower] = candidate
        for follower, candidate in sorted(choices.items()):
            output.append(candidate)
            cooldown[follower] = stamp + timedelta(minutes=COOLDOWN_MINUTES)
    return output


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
) -> list[LeadLagOpportunity]:
    output: list[LeadLagOpportunity] = []
    cooldown: dict[str, datetime] = {}
    for stamp in _grid(start, end):
        choices: dict[str, LeadLagOpportunity] = {}
        for leader in LEADERS:
            for follower in FOLLOWERS_BY_LEADER[leader]:
                if cooldown.get(follower, datetime.min.replace(tzinfo=UTC)) > stamp:
                    continue
                candidate = opportunity_at(series, leader, follower, stamp)
                if candidate is None:
                    continue
                current = choices.get(follower)
                if current is None or (
                    candidate.leader_z,
                    -candidate.residual_z,
                    candidate.leader,
                ) > (
                    current.leader_z,
                    -current.residual_z,
                    current.leader,
                ):
                    choices[follower] = candidate
        for follower, candidate in sorted(choices.items()):
            output.append(candidate)
            cooldown[follower] = stamp + timedelta(minutes=COOLDOWN_MINUTES)
    return output


def _fills(symbol: str, entry: float, exit: float, scenario: str) -> tuple[float, float]:
    state = COSTS_BPS[symbol]
    half_spread = float(state["spread"][scenario]) / 20000.0
    slippage = float(state["slippage"][scenario]) / 10000.0
    return (
        entry * (1.0 + half_spread + slippage),
        exit * (1.0 - half_spread - slippage),
    )


def simulate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunities: Sequence[LeadLagOpportunity],
    *,
    start: datetime,
    end: datetime,
    scenario: str = HIGH_COST_SCENARIO,
    delay_minutes: int = 0,
) -> list[LeadLagTrade]:
    output: list[LeadLagTrade] = []
    busy_until: dict[str, datetime] = {}
    for opportunity in sorted(opportunities, key=lambda row: (row.opportunity_at, row.follower)):
        if not (start <= opportunity.opportunity_at < end):
            continue
        if busy_until.get(opportunity.follower, datetime.min.replace(tzinfo=UTC)) > opportunity.opportunity_at:
            continue
        entry_at = opportunity.opportunity_at + timedelta(minutes=delay_minutes)
        exit_at = entry_at + timedelta(minutes=HOLD_MINUTES)
        if exit_at > end:
            continue
        entry_reference = _open(
            series,
            opportunity.follower,
            entry_at + timedelta(minutes=BAR_MINUTES),
        )
        exit_reference = _open(
            series,
            opportunity.follower,
            exit_at + timedelta(minutes=BAR_MINUTES),
        )
        if entry_reference is None or exit_reference is None:
            continue
        entry_fill, exit_fill = _fills(
            opportunity.follower,
            entry_reference,
            exit_reference,
            scenario,
        )
        if entry_fill <= 0 or exit_fill <= 0:
            continue
        output.append(
            LeadLagTrade(
                leader=opportunity.leader,
                follower=opportunity.follower,
                opportunity_at=opportunity.opportunity_at,
                entry_at=entry_at,
                exit_at=exit_at,
                entry_reference=entry_reference,
                exit_reference=exit_reference,
                net_return=exit_fill / entry_fill - 1.0,
                scenario=scenario,
                delayed_minutes=delay_minutes,
                leader_z=opportunity.leader_z,
                residual_z=opportunity.residual_z,
            )
        )
        busy_until[opportunity.follower] = exit_at
    return output


def _daily_means(trades: Sequence[LeadLagTrade]) -> list[float]:
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


def _concentration(trades: Sequence[LeadLagTrade]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for trade in trades:
        counts[trade.follower] += 1
    total = len(trades)
    return {
        "max_share": max(counts.values()) / total if total else 0.0,
        "counts": dict(sorted(counts.items())),
    }


def summarize(
    trades: Sequence[LeadLagTrade],
    *,
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda row: (row.entry_at, row.follower))
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
        "sample": [row.to_dict() for row in ordered[:5]],
    }


def evaluate_stage(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    scenario: str = HIGH_COST_SCENARIO,
    seed: int = 81001,
) -> dict[str, Any]:
    start = _aware(start, "start")
    end = _aware(end, "end")
    series = build_series(bars_by_symbol)
    opportunities = collect_opportunities(series, start=start, end=end)
    control_opportunities = _leader_impulse_control_opportunities(
        series,
        start=start,
        end=end,
    )
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
        delay_minutes=DELAY_ROBUSTNESS_MINUTES,
    )
    control = simulate(
        series,
        control_opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    return {
        "candidate_id": CANDIDATE_ID,
        "hypothesis_id": HYPOTHESIS_ID,
        "methodology_version": METHODOLOGY_VERSION,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "opportunity_count": len(opportunities),
        "control_opportunity_count": len(control_opportunities),
        "primary": summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": summarize(delayed, start=start, end=end, seed=seed + 1),
        "leader_impulse_only_control": summarize(control, start=start, end=end, seed=seed + 2),
        "opportunity_sample": [row.to_dict() for row in opportunities[:5]],
        "research_only": True,
        "execution_authority": False,
    }


def development_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 20:
        reasons.append("development_trade_count_below_20")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_high_cost_expectancy_nonpositive")
    return not reasons, reasons


def validation_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 30:
        reasons.append("validation_trade_count_below_30")
    if int(primary["independent_day_blocks"]) < 20:
        reasons.append("validation_independent_days_below_20")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("validation_high_cost_expectancy_nonpositive")
    if float(primary["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("validation_dependence_p_above_0.05")
    pf = primary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("validation_profit_factor_not_above_one")
    if float(primary["symbol_concentration"]["max_share"]) > 0.70:
        reasons.append("validation_symbol_concentration_above_0.70")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("validation_one_bar_delay_expectancy_nonpositive")
    return not reasons, reasons


def holdout_gate(result: Mapping[str, Any]) -> tuple[bool, list[str]]:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    reasons: list[str] = []
    if int(primary["trade_count"]) < 20:
        reasons.append("holdout_trade_count_below_20")
    if int(primary["independent_day_blocks"]) < 15:
        reasons.append("holdout_independent_days_below_15")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_high_cost_expectancy_nonpositive")
    if float(primary["dependence_adjusted_null"]["p_value"]) > 0.05:
        reasons.append("holdout_dependence_p_above_0.05")
    pf = primary.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        reasons.append("holdout_profit_factor_not_above_one")
    if float(primary["symbol_concentration"]["max_share"]) > 0.60:
        reasons.append("holdout_symbol_concentration_above_0.60")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("holdout_one_bar_delay_expectancy_nonpositive")
    return not reasons, reasons
