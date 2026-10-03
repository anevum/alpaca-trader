from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil, sqrt
from statistics import fmean, median, pstdev
from typing import Any, Mapping, Sequence

from app.research_agent.crypto_edge_discovery import _moving_block_null_pvalue
from app.research_agent.multiplicity import benjamini_yekutieli
from .activity_shock_v9 import (
    BAR_MINUTES,
    Opportunity,
    _bar,
    _f,
    _grid,
    _open,
    _return,
    _return_history,
    _fills,
    build_series,
)
from .btc_trend_pullback_v11 import (
    DEVELOPMENT_END,
    DEVELOPMENT_FOLDS,
    DEVELOPMENT_START,
    UNIVERSE,
    verify_development_corpus,
)
from .research_v5 import COSTS_BPS


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-btc-hypothesis-tournament-v13"
CAMPAIGN_ID = "btc-hypothesis-tournament-v13"
FAMILY = "btc_new_hypothesis_tournament"
DELAY_ROBUSTNESS_MINUTES = 5
MULTIPLICITY_ALPHA = 0.05


@dataclass(frozen=True, slots=True)
class BtcHypothesisSpec:
    candidate_id: str
    mechanism: str
    fast_minutes: int
    slow_minutes: int
    trigger: float
    secondary_trigger: float
    hold_minutes: int
    cooldown_minutes: int
    concentration_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class V13Trade:
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
    scenario: str
    delayed_minutes: int


def candidate_specs() -> tuple[BtcHypothesisSpec, ...]:
    return (
        BtcHypothesisSpec(
            "V13-BTC-ACREV-15-6H-A",
            "serial_dependence_reversion",
            15, 360, 0.10, 1.50, 30, 60,
        ),
        BtcHypothesisSpec(
            "V13-BTC-ACREV-30-12H-B",
            "serial_dependence_reversion",
            30, 720, 0.15, 1.75, 60, 90,
        ),
        BtcHypothesisSpec(
            "V13-BTC-FVREV-360-A",
            "fair_value_dislocation_reversion",
            360, 1440, 1.25, -0.05, 60, 120,
        ),
        BtcHypothesisSpec(
            "V13-BTC-FVREV-720-B",
            "fair_value_dislocation_reversion",
            720, 1440, 1.50, 0.00, 120, 180,
        ),
        BtcHypothesisSpec(
            "V13-BTC-ACCEL-15-60-A",
            "return_acceleration",
            15, 60, 1.25, 0.75, 30, 60,
        ),
        BtcHypothesisSpec(
            "V13-BTC-ACCEL-30-120-B",
            "return_acceleration",
            30, 120, 1.50, 1.00, 60, 90,
        ),
        BtcHypothesisSpec(
            "V13-BTC-VOV-60-720-A",
            "volatility_of_volatility_transition",
            60, 720, 1.35, 1.00, 60, 120,
        ),
        BtcHypothesisSpec(
            "V13-BTC-VOV-120-1440-B",
            "volatility_of_volatility_transition",
            120, 1440, 1.45, 1.05, 120, 180,
        ),
    )


def hypothesis_registry() -> tuple[dict[str, Any], ...]:
    common_cost = {
        "source": "graen-crypto-cost-snapshot-v5",
        "selection_scenario": "high",
        "btc_spread_bps": COSTS_BPS["BTC/USD"]["spread"],
        "btc_slippage_bps_per_side": COSTS_BPS["BTC/USD"]["slippage"],
        "delayed_entry_minutes": DELAY_ROBUSTNESS_MINUTES,
    }
    descriptions = {
        "serial_dependence_reversion": {
            "mechanism_hypothesis": (
                "BTC short-horizon losses should mean-revert only when the recent "
                "5-minute return process is measurably negatively autocorrelated."
            ),
            "why_it_could_exist": (
                "Temporary inventory or liquidity pressure can create alternating short-horizon "
                "returns; conditioning on negative serial dependence separates that state from "
                "unconditional dip buying."
            ),
            "observable_inputs": [
                "BTC/USD 5-minute OHLCV bars", "lag-1 autocorrelation of 5-minute returns",
                "volatility-normalized 15/30-minute return", "current bar VWAP and close location",
            ],
            "entry_condition": (
                "Lag-1 return autocorrelation is below the frozen negative threshold, the latest "
                "15/30-minute return is a sufficiently negative volatility-normalized shock, and "
                "the completed signal bar closes positive and at/above its VWAP."
            ),
            "exit_logic": "Fixed 30/60-minute thesis horizon; no adaptive target or stop.",
            "invalidation_condition": (
                "Fails stressed-cost expectancy/PF, one-bar delay, multiplicity, sample, or "
                "temporal-fold gates."
            ),
        },
        "fair_value_dislocation_reversion": {
            "mechanism_hypothesis": (
                "A large downside displacement from a trailing transaction-weighted fair-value "
                "anchor can revert when the local return process is non-persistent."
            ),
            "why_it_could_exist": (
                "A volume-weighted anchor approximates where recent BTC turnover occurred; "
                "temporary displacement may close after forced or one-sided flow subsides."
            ),
            "observable_inputs": [
                "BTC/USD 5-minute close, volume and bar VWAP", "6/12-hour rolling volume-weighted anchor",
                "24-hour 5-minute realized volatility", "lag-1 return autocorrelation",
            ],
            "entry_condition": (
                "Close is sufficiently far below the causal rolling volume-weighted anchor after "
                "volatility normalization, lag-1 autocorrelation is at/below the frozen ceiling, "
                "and the completed signal bar closes positive and at/above bar VWAP."
            ),
            "exit_logic": "Fixed 60/120-minute thesis horizon.",
            "invalidation_condition": (
                "No robust net reversion after stressed costs, delay, multiplicity, and temporal folds."
            ),
        },
        "return_acceleration": {
            "mechanism_hypothesis": (
                "The change in return velocity, rather than return level, can identify an emerging "
                "BTC move before a conventional trend threshold is reached."
            ),
            "why_it_could_exist": (
                "Order-flow imbalance can first appear as a positive second difference in returns; "
                "requiring the slower move not to be already extended avoids simply retesting momentum."
            ),
            "observable_inputs": [
                "BTC/USD 5-minute returns", "current versus prior 15/30-minute return",
                "60/120-minute normalized return", "current bar VWAP",
            ],
            "entry_condition": (
                "Volatility-normalized return acceleration exceeds the frozen threshold, current "
                "fast return is positive, the slower return is not already extended, and the signal "
                "bar closes positive at/above VWAP."
            ),
            "exit_logic": "Fixed 30/60-minute thesis horizon.",
            "invalidation_condition": (
                "Acceleration does not retain positive stressed-cost and delayed-entry expectancy "
                "across at least five of seven temporal folds with multiplicity control."
            ),
        },
        "volatility_of_volatility_transition": {
            "mechanism_hypothesis": (
                "A discrete transition from ordinary to elevated short-horizon volatility can create "
                "brief directional persistence when the transition occurs with positive signed return."
            ),
            "why_it_could_exist": (
                "Information arrival and participation shocks can move BTC from one volatility state "
                "to another; the transition itself may matter more than static high or low volatility."
            ),
            "observable_inputs": [
                "BTC/USD 5-minute returns", "current and prior 1/2-hour realized volatility",
                "12/24-hour realized-volatility baseline", "signed half-window return", "bar VWAP",
            ],
            "entry_condition": (
                "Short volatility rises above the frozen multiple of long volatility after the prior "
                "short-vol window was at/below its frozen ceiling, with positive signed return and a "
                "positive signal bar at/above VWAP."
            ),
            "exit_logic": "Fixed 60/120-minute thesis horizon.",
            "invalidation_condition": (
                "Transition-state continuation is not positive after stressed cost, delay, "
                "multiplicity, and temporal-consistency gates."
            ),
        },
    }
    rows: list[dict[str, Any]] = []
    for spec in candidate_specs():
        rows.append({
            **spec.to_dict(),
            **descriptions[spec.mechanism],
            "expected_holding_horizon_minutes": spec.hold_minutes,
            "transaction_cost_assumptions": common_cost,
        })
    return tuple(rows)


def spec_from_dict(payload: Mapping[str, Any]) -> BtcHypothesisSpec:
    return BtcHypothesisSpec(**dict(payload))


def _return_sample(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    stamp: datetime,
    minutes: int,
) -> list[float]:
    hours = max(int(ceil(minutes / 60.0)), 1)
    values = _return_history(
        series,
        "BTC/USD",
        end=stamp,
        impulse_minutes=BAR_MINUTES,
        hours=hours,
    )
    count = max(minutes // BAR_MINUTES, 2)
    return values[-count:]


def _lag1_autocorr(values: Sequence[float]) -> float | None:
    if len(values) < 24:
        return None
    left = list(values[:-1])
    right = list(values[1:])
    mean_left = fmean(left)
    mean_right = fmean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_ss = sum((a - mean_left) ** 2 for a in left)
    right_ss = sum((b - mean_right) ** 2 for b in right)
    denominator = sqrt(left_ss * right_ss)
    return numerator / denominator if denominator > 1e-18 else None


def _sigma(values: Sequence[float]) -> float | None:
    value = pstdev(values) if len(values) >= 12 else 0.0
    return value if value > 1e-12 else None


def _positive_confirmation(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    stamp: datetime,
) -> bool:
    row = _bar(series, "BTC/USD", stamp)
    if row is None:
        return False
    open_ = _f(row.get("o"))
    close = _f(row.get("c"))
    vwap = _f(row.get("vw"))
    return bool(
        open_ > 0
        and close > open_
        and (vwap <= 0 or close >= vwap)
    )


def _rolling_fair_value(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    stamp: datetime,
    minutes: int,
) -> float | None:
    weighted = 0.0
    volume = 0.0
    current = stamp - timedelta(minutes=minutes)
    while current < stamp:
        row = _bar(series, "BTC/USD", current)
        if row is not None:
            v = max(_f(row.get("v")), 0.0)
            p = _f(row.get("vw") or row.get("c"))
            if v > 0 and p > 0:
                weighted += p * v
                volume += v
        current += timedelta(minutes=BAR_MINUTES)
    return weighted / volume if volume > 0 else None


def _serial_dependence_reversion(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    history = _return_sample(series, stamp, spec.slow_minutes)
    sigma = _sigma(history)
    autocorr = _lag1_autocorr(history)
    shock = _return(series, "BTC/USD", stamp, spec.fast_minutes)
    if sigma is None or autocorr is None or shock is None:
        return None
    shock_norm = shock / (sigma * sqrt(max(spec.fast_minutes / BAR_MINUTES, 1.0)))
    if autocorr > -spec.trigger or shock_norm > -spec.secondary_trigger:
        return None
    if not _positive_confirmation(series, stamp):
        return None
    return {
        "mechanism": spec.mechanism,
        "lag1_autocorrelation": autocorr,
        "shock_return": shock,
        "shock_normalized": shock_norm,
    }


def _fair_value_dislocation_reversion(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    anchor = _rolling_fair_value(series, stamp, spec.fast_minutes)
    row = _bar(series, "BTC/USD", stamp)
    history = _return_sample(series, stamp, spec.slow_minutes)
    sigma = _sigma(history)
    autocorr = _lag1_autocorr(history)
    if anchor is None or row is None or sigma is None or autocorr is None:
        return None
    close = _f(row.get("c"))
    if close <= 0:
        return None
    distance = close / anchor - 1.0
    normalized = distance / (
        sigma * sqrt(max(spec.fast_minutes / BAR_MINUTES, 1.0))
    )
    if normalized > -spec.trigger or autocorr > spec.secondary_trigger:
        return None
    if not _positive_confirmation(series, stamp):
        return None
    return {
        "mechanism": spec.mechanism,
        "fair_value_anchor": anchor,
        "distance_from_anchor": distance,
        "normalized_distance": normalized,
        "lag1_autocorrelation": autocorr,
    }


def _return_acceleration(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    current_fast = _return(series, "BTC/USD", stamp, spec.fast_minutes)
    prior_fast = _return(
        series,
        "BTC/USD",
        stamp - timedelta(minutes=spec.fast_minutes),
        spec.fast_minutes,
    )
    slow_return = _return(series, "BTC/USD", stamp, spec.slow_minutes)
    history = _return_sample(series, stamp, max(spec.slow_minutes * 6, 360))
    sigma = _sigma(history)
    if (
        current_fast is None
        or prior_fast is None
        or slow_return is None
        or sigma is None
        or current_fast <= 0
    ):
        return None
    acceleration = current_fast - prior_fast
    acceleration_norm = acceleration / (
        sigma * sqrt(max(2.0 * spec.fast_minutes / BAR_MINUTES, 1.0))
    )
    slow_norm = slow_return / (
        sigma * sqrt(max(spec.slow_minutes / BAR_MINUTES, 1.0))
    )
    if acceleration_norm < spec.trigger or slow_norm > spec.secondary_trigger:
        return None
    if not _positive_confirmation(series, stamp):
        return None
    return {
        "mechanism": spec.mechanism,
        "current_fast_return": current_fast,
        "prior_fast_return": prior_fast,
        "return_acceleration": acceleration,
        "acceleration_normalized": acceleration_norm,
        "slow_return_normalized": slow_norm,
    }


def _volatility_of_volatility_transition(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    stamp: datetime,
) -> dict[str, Any] | None:
    current_short = _return_sample(series, stamp, spec.fast_minutes)
    prior_short = _return_sample(
        series,
        stamp - timedelta(minutes=spec.fast_minutes),
        spec.fast_minutes,
    )
    long_sample = _return_sample(series, stamp, spec.slow_minutes)
    short_sigma = _sigma(current_short)
    prior_sigma = _sigma(prior_short)
    long_sigma = _sigma(long_sample)
    signed = _return(
        series,
        "BTC/USD",
        stamp,
        max(spec.fast_minutes // 2, BAR_MINUTES),
    )
    if (
        short_sigma is None
        or prior_sigma is None
        or long_sigma is None
        or signed is None
        or signed <= 0
    ):
        return None
    ratio = short_sigma / long_sigma
    prior_ratio = prior_sigma / long_sigma
    if ratio < spec.trigger or prior_ratio > spec.secondary_trigger:
        return None
    if not _positive_confirmation(series, stamp):
        return None
    return {
        "mechanism": spec.mechanism,
        "short_volatility": short_sigma,
        "prior_short_volatility": prior_sigma,
        "long_volatility": long_sigma,
        "volatility_ratio": ratio,
        "prior_volatility_ratio": prior_ratio,
        "signed_transition_return": signed,
    }


def opportunity_at(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    symbol: str,
    stamp: datetime,
) -> Opportunity | None:
    if symbol != "BTC/USD":
        return None
    if spec.mechanism == "serial_dependence_reversion":
        signal = _serial_dependence_reversion(series, spec, stamp)
    elif spec.mechanism == "fair_value_dislocation_reversion":
        signal = _fair_value_dislocation_reversion(series, spec, stamp)
    elif spec.mechanism == "return_acceleration":
        signal = _return_acceleration(series, spec, stamp)
    elif spec.mechanism == "volatility_of_volatility_transition":
        signal = _volatility_of_volatility_transition(series, spec, stamp)
    else:
        raise ValueError(f"unsupported_v13_mechanism:{spec.mechanism}")
    if signal is None:
        return None
    return Opportunity(
        candidate_id=spec.candidate_id,
        symbol="BTC/USD",
        opportunity_at=stamp,
        hold_minutes=spec.hold_minutes,
        signal={"family": FAMILY, **signal},
    )


def collect_opportunities(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    spec: BtcHypothesisSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[Opportunity]:
    rows: list[Opportunity] = []
    cooldown_until: datetime | None = None
    for stamp in _grid(start, end):
        if cooldown_until is not None and stamp < cooldown_until:
            continue
        opportunity = opportunity_at(series, spec, "BTC/USD", stamp)
        if opportunity is None:
            continue
        rows.append(opportunity)
        cooldown_until = stamp + timedelta(minutes=spec.cooldown_minutes)
    return rows


def _simulate(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    opportunities: Sequence[Opportunity],
    *,
    start: datetime,
    end: datetime,
    scenario: str,
    extra_entry_delay_minutes: int = 0,
) -> list[V13Trade]:
    busy_until: datetime | None = None
    output: list[V13Trade] = []
    for opportunity in sorted(opportunities, key=lambda row: row.opportunity_at):
        if busy_until is not None and busy_until > opportunity.opportunity_at:
            continue
        entry_at = opportunity.opportunity_at + timedelta(minutes=extra_entry_delay_minutes)
        exit_at = entry_at + timedelta(minutes=opportunity.hold_minutes)
        if entry_at < start or exit_at > end:
            continue
        entry_bar_end = entry_at + timedelta(minutes=BAR_MINUTES)
        exit_bar_end = exit_at + timedelta(minutes=BAR_MINUTES)
        entry_reference = _open(series, "BTC/USD", entry_bar_end)
        exit_reference = _open(series, "BTC/USD", exit_bar_end)
        if entry_reference is None or exit_reference is None:
            continue
        entry_fill, exit_fill = _fills(
            "BTC/USD",
            entry_reference,
            exit_reference,
            scenario,
        )
        if entry_fill <= 0 or exit_fill <= 0:
            continue
        highs: list[float] = []
        lows: list[float] = []
        current = entry_bar_end
        while current <= exit_bar_end:
            row = _bar(series, "BTC/USD", current)
            if row is not None:
                high = _f(row.get("h"))
                low = _f(row.get("l"))
                if high > 0:
                    highs.append(high)
                if low > 0:
                    lows.append(low)
            current += timedelta(minutes=BAR_MINUTES)
        mfe = max((value / entry_reference - 1.0 for value in highs), default=0.0)
        mae = min((value / entry_reference - 1.0 for value in lows), default=0.0)
        output.append(
            V13Trade(
                candidate_id=opportunity.candidate_id,
                symbol="BTC/USD",
                opportunity_at=opportunity.opportunity_at,
                entry_at=entry_at,
                exit_at=exit_at,
                entry_reference=entry_reference,
                exit_reference=exit_reference,
                net_return=exit_fill / entry_fill - 1.0,
                mae=mae,
                mfe=mfe,
                scenario=scenario,
                delayed_minutes=extra_entry_delay_minutes,
            )
        )
        busy_until = exit_at
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


def _summarize(
    trades: Sequence[V13Trade],
    *,
    start: datetime,
    end: datetime,
    seed: int,
) -> dict[str, Any]:
    values = [row.net_return for row in trades]
    groups: dict[date, list[float]] = defaultdict(list)
    for trade in trades:
        groups[trade.entry_at.date()].append(trade.net_return)
    daily = [fmean(groups[key]) for key in sorted(groups)]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    flats = [value for value in values if value == 0]
    duration_days = max((end - start).total_seconds() / 86400.0, 1.0)
    return {
        "trade_count": len(trades),
        "trades_per_day": len(trades) / duration_days,
        "independent_day_blocks": len(daily),
        "expectancy_per_trade": fmean(values) if values else 0.0,
        "median_trade_return": median(values) if values else 0.0,
        "win_rate": len(wins) / len(values) if values else 0.0,
        "profit_factor": _profit_factor(values),
        "max_drawdown": _max_drawdown(values),
        "dependence_adjusted_null": _moving_block_null_pvalue(
            daily,
            replicates=2000,
            seed=seed,
        ),
        "symbol_concentration": {
            "max_share": 1.0 if trades else 0.0,
            "counts": {"BTC/USD": len(trades)} if trades else {},
        },
        "win_loss_distribution": {
            "wins": len(wins),
            "losses": len(losses),
            "flat": len(flats),
            "average_win": fmean(wins) if wins else None,
            "average_loss": fmean(losses) if losses else None,
            "best_trade": max(values) if values else None,
            "worst_trade": min(values) if values else None,
        },
        "mfe_mae": {
            "available": True,
            "mean_mfe": fmean([row.mfe for row in trades]) if trades else None,
            "mean_mae": fmean([row.mae for row in trades]) if trades else None,
            "median_mfe": median([row.mfe for row in trades]) if trades else None,
            "median_mae": median([row.mae for row in trades]) if trades else None,
        },
    }


def evaluate_candidate_from_series(
    series: Mapping[str, Mapping[datetime, Mapping[str, Any]]],
    *,
    spec: BtcHypothesisSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    opportunities = collect_opportunities(series, spec, start=start, end=end)
    primary = _simulate(
        series,
        opportunities,
        start=start,
        end=end,
        scenario=scenario,
    )
    delayed = _simulate(
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
        "mechanism": spec.mechanism,
        "symbol": "BTC/USD",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_scenario": scenario,
        "cost_assumptions": {
            "spread_bps": COSTS_BPS["BTC/USD"]["spread"][scenario],
            "slippage_bps_per_side": COSTS_BPS["BTC/USD"]["slippage"][scenario],
        },
        "opportunity_count": len(opportunities),
        "primary": _summarize(primary, start=start, end=end, seed=seed),
        "one_bar_delay": _summarize(delayed, start=start, end=end, seed=seed + 1),
        "opportunity_sample": [row.to_dict() for row in opportunities[:5]],
    }


def evaluate_candidate(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    spec: BtcHypothesisSpec,
    start: datetime,
    end: datetime,
    scenario: str = "high",
    seed: int,
) -> dict[str, Any]:
    warmup_hours = max(int(ceil(spec.slow_minutes / 60.0)) + 2, 26)
    series = build_series(
        bars_by_symbol,
        start=start,
        end=end,
        warmup_hours=warmup_hours,
    )
    return evaluate_candidate_from_series(
        series,
        spec=spec,
        start=start,
        end=end,
        scenario=scenario,
        seed=seed,
    )


def _fold_pass(result: Mapping[str, Any]) -> bool:
    primary = result["primary"]
    delayed = result["one_bar_delay"]
    pf = primary.get("profit_factor")
    return bool(
        int(primary["trade_count"]) >= 6
        and int(primary["independent_day_blocks"]) >= 5
        and float(primary["expectancy_per_trade"]) > 0
        and pf is not None
        and float(pf) > 1.0
        and float(delayed["expectancy_per_trade"]) > 0
    )


def _economic_gate(
    aggregate_high: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
) -> list[str]:
    primary = aggregate_high["primary"]
    delayed = aggregate_high["one_bar_delay"]
    pf = primary.get("profit_factor")
    fold_passes = sum(1 for row in folds if bool(row.get("passed")))
    reasons: list[str] = []
    if int(primary["trade_count"]) < 60:
        reasons.append("development_trade_count_below_60")
    if int(primary["independent_day_blocks"]) < 40:
        reasons.append("development_independent_days_below_40")
    if float(primary["expectancy_per_trade"]) <= 0:
        reasons.append("development_expectancy_nonpositive")
    if pf is None or float(pf) <= 1.0:
        reasons.append("development_profit_factor_not_above_one")
    if float(delayed["expectancy_per_trade"]) <= 0:
        reasons.append("development_delay_expectancy_nonpositive")
    if fold_passes < 5:
        reasons.append("development_positive_temporal_folds_below_5_of_7")
    return reasons


def evaluate_development(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    series = build_series(
        bars_by_symbol,
        start=DEVELOPMENT_START,
        end=DEVELOPMENT_END,
        warmup_hours=26,
    )
    provisional: dict[str, Any] = {}
    p_values: list[float] = []
    specs = candidate_specs()

    for index, spec in enumerate(specs):
        high = evaluate_candidate_from_series(
            series,
            spec=spec,
            start=DEVELOPMENT_START,
            end=DEVELOPMENT_END,
            scenario="high",
            seed=131000 + index * 100,
        )
        base = evaluate_candidate_from_series(
            series,
            spec=spec,
            start=DEVELOPMENT_START,
            end=DEVELOPMENT_END,
            scenario="base",
            seed=131010 + index * 100,
        )
        folds: list[dict[str, Any]] = []
        for fold_index, (label, start, end) in enumerate(DEVELOPMENT_FOLDS):
            result = evaluate_candidate_from_series(
                series,
                spec=spec,
                start=start,
                end=end,
                scenario="high",
                seed=132000 + index * 100 + fold_index * 10,
            )
            folds.append({
                "label": label,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "passed": _fold_pass(result),
                "result": result,
            })
        primary = high["primary"]
        p_value = float(
            (primary.get("dependence_adjusted_null") or {}).get("p_value", 1.0)
        )
        p_values.append(p_value)
        provisional[spec.candidate_id] = {
            "aggregate_high": high,
            "aggregate_base": base,
            "folds": folds,
            "positive_temporal_folds": sum(1 for row in folds if row["passed"]),
            "economic_reasons": _economic_gate(high, folds),
            "cost_sensitivity": {
                "base_expectancy_per_trade": base["primary"]["expectancy_per_trade"],
                "high_expectancy_per_trade": high["primary"]["expectancy_per_trade"],
                "expectancy_erosion_base_to_high": (
                    float(base["primary"]["expectancy_per_trade"])
                    - float(high["primary"]["expectancy_per_trade"])
                ),
                "high_cost_positive": float(high["primary"]["expectancy_per_trade"]) > 0,
            },
            "dependence_adjusted_p_value": p_value,
        }

    multiplicity = benjamini_yekutieli(p_values, alpha=MULTIPLICITY_ALPHA)
    rejected = set(multiplicity.rejected_indices)
    results: dict[str, Any] = {}
    survivors: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        row = provisional[spec.candidate_id]
        reasons = list(row["economic_reasons"])
        statistical_survival = index in rejected
        if not statistical_survival:
            reasons.append("development_multiplicity_gate_failed")
        passed = not reasons
        high_primary = row["aggregate_high"]["primary"]
        fold_passes = int(row["positive_temporal_folds"])
        selection_score = (
            float(high_primary["expectancy_per_trade"])
            * sqrt(max(int(high_primary["trade_count"]), 1))
            * (fold_passes / len(DEVELOPMENT_FOLDS))
        )
        results[spec.candidate_id] = {
            **row,
            "passed": passed,
            "reasons": reasons,
            "statistical_survival": statistical_survival,
            "economic_survival": not row["economic_reasons"],
            "selection_score": selection_score,
        }
        if passed:
            survivors.append({
                "candidate_id": spec.candidate_id,
                "mechanism": spec.mechanism,
                "selection_score": selection_score,
            })

    selected = (
        max(survivors, key=lambda row: (row["selection_score"], row["candidate_id"]))
        if survivors else None
    )
    selected_id = str(selected["candidate_id"]) if selected else None
    selected_spec = next(
        (spec for spec in specs if spec.candidate_id == selected_id),
        None,
    )
    return {
        "stage": "HISTORICAL_DEVELOPMENT",
        "evidence_role": "DEVELOPMENT_ONLY",
        "adaptive_lineage": (
            "V6-V12 historical BTC/crypto research is already inspected; V13 uses it only "
            "to define new mechanisms and cannot treat the archive as independent confirmation."
        ),
        "independent_historical_validation": False,
        "independent_historical_holdout": False,
        "fresh_confirmation_required": "FORWARD_VALIDATION_THEN_SEALED_HOLDOUT",
        "holdout_opened": False,
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "symbol": "BTC/USD",
        "candidate_count": len(specs),
        "mechanisms": sorted({spec.mechanism for spec in specs}),
        "fold_count": len(DEVELOPMENT_FOLDS),
        "multiplicity": multiplicity.to_dict(),
        "results": results,
        "survivors": [row["candidate_id"] for row in survivors],
        "selected_candidate_id": selected_id,
        "selected_candidate_spec": selected_spec.to_dict() if selected_spec else None,
        "development_start": DEVELOPMENT_START.isoformat(),
        "development_end": DEVELOPMENT_END.isoformat(),
        "authority": {
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "production_promotion_authority": False,
            "crypto_execution_enabled": False,
        },
    }


def holdout_may_open(*, validation_passed: bool, velum_passed: bool) -> bool:
    """Fail-closed V13 stage guard. HOLDOUT is inaccessible until both prior gates pass."""
    return bool(validation_passed and velum_passed)
