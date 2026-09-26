from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from math import sqrt
from typing import Any

from .config import Settings
from .entry_feature_study import forward_excursion
from .replay import BPS, ReplayEngine, d, price, stamp


NY_OPEN = time(9, 30)
OPENING_RANGE_END = time(9, 45)
OPENING_RETEST_CUTOFF = time(12, 0)
DEFAULT_HORIZON = 15
DEFAULT_EVENT_COOLDOWN_MINUTES = 15

FAMILY_NAMES = (
    "controlled_continuation",
    "pullback_reclaim",
    "compression_breakout",
    "relative_strength_impulse",
    "opening_breakout_retest",
)


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _simple_return(new: Decimal, old: Decimal) -> Decimal:
    if old <= 0:
        return Decimal("0")
    return (new - old) / old


def _session_vwap(bars: list[dict[str, Any]]) -> Decimal:
    total_volume = Decimal("0")
    weighted = Decimal("0")
    closes: list[Decimal] = []
    for bar in bars:
        close = price(bar)
        closes.append(close)
        volume = d(bar.get("v"))
        bar_vwap = d(bar.get("vw")) or close
        if volume > 0:
            total_volume += volume
            weighted += bar_vwap * volume
    if total_volume > 0:
        return weighted / total_volume
    return _mean(closes)


def _relative_volume(bars: list[dict[str, Any]], lookback: int = 20) -> Decimal:
    sample = bars[-(lookback + 1):]
    if len(sample) < 2:
        return Decimal("1")
    latest = d(sample[-1].get("v"))
    history = [
        d(bar.get("v"))
        for bar in sample[:-1]
        if d(bar.get("v")) > 0
    ]
    average = _mean(history)
    if latest <= 0 or average <= 0:
        return Decimal("1")
    return latest / average


def _trend_persistence(closes: list[Decimal], transitions: int = 8) -> Decimal:
    if len(closes) < 2:
        return Decimal("0")
    sample = closes[-(transitions + 1):]
    if len(sample) < 2:
        return Decimal("0")
    positive = sum(
        1
        for previous, current in zip(sample, sample[1:])
        if current > previous
    )
    return Decimal(positive) / Decimal(len(sample) - 1)


def _range_pct(bars: list[dict[str, Any]], reference: Decimal) -> Decimal:
    if not bars or reference <= 0:
        return Decimal("0")
    high = max(d(bar.get("h")) for bar in bars)
    low = min(d(bar.get("l")) for bar in bars)
    if high <= 0 or low <= 0:
        return Decimal("0")
    return (high - low) / reference


def _close_location(bar: dict[str, Any]) -> Decimal:
    high = d(bar.get("h"))
    low = d(bar.get("l"))
    close = price(bar)
    span = high - low
    if span <= 0:
        return Decimal("0")
    return (close - low) / span


def _volume_acceleration(bars: list[dict[str, Any]]) -> Decimal:
    if len(bars) < 2:
        return Decimal("0")
    latest = d(bars[-1].get("v"))
    prior = [
        d(bar.get("v"))
        for bar in bars[max(0, len(bars) - 4):-1]
        if d(bar.get("v")) > 0
    ]
    baseline = _mean(prior)
    if latest <= 0 or baseline <= 0:
        return Decimal("0")
    return latest / baseline


def _return_over(closes: list[Decimal], bars_back: int) -> Decimal:
    if len(closes) <= bars_back:
        return Decimal("0")
    return _simple_return(closes[-1], closes[-(bars_back + 1)])


def _market_context(
    confirmation_bars: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    five_minute_returns: list[Decimal] = []
    ten_minute_vols: list[Decimal] = []

    for bars in confirmation_bars.values():
        if len(bars) < 6:
            continue
        closes = [price(bar) for bar in bars]
        five_minute_returns.append(_return_over(closes, 5))

        recent = closes[-11:]
        one_minute = [
            abs(_simple_return(current, previous))
            for previous, current in zip(recent, recent[1:])
            if previous > 0
        ]
        if one_minute:
            ten_minute_vols.append(_mean(one_minute))

    market_return_5 = _mean(five_minute_returns)
    market_abs_return_1 = _mean(ten_minute_vols)

    if market_return_5 >= Decimal("0.001"):
        trend_regime = "up"
    elif market_return_5 <= Decimal("-0.001"):
        trend_regime = "down"
    else:
        trend_regime = "flat"

    if market_abs_return_1 >= Decimal("0.0015"):
        volatility_regime = "high"
    elif market_abs_return_1 <= Decimal("0.0006"):
        volatility_regime = "low"
    else:
        volatility_regime = "normal"

    return {
        "market_return_5": str(market_return_5),
        "market_abs_return_1": str(market_abs_return_1),
        "trend_regime": trend_regime,
        "volatility_regime": volatility_regime,
    }


def _time_bucket(now: datetime) -> str:
    current = now.time()
    if current < time(11, 0):
        return "open"
    if current < time(14, 0):
        return "midday"
    return "late"


def candidate_features(
    bars: list[dict[str, Any]],
    confirmation_bars: dict[str, list[dict[str, Any]]],
    *,
    now: datetime,
) -> dict[str, Any]:
    if len(bars) < 21:
        return {}

    closes = [price(bar) for bar in bars]
    current = closes[-1]
    previous = closes[-2]
    if current <= 0 or previous <= 0:
        return {}

    current_bar = bars[-1]
    session_vwap = _session_vwap(bars)
    fast3 = _mean(closes[-3:])
    fast5 = _mean(closes[-5:])
    slow8 = _mean(closes[-8:])
    slow15 = _mean(closes[-15:])
    previous_fast3 = _mean(closes[-4:-1])

    prior5 = bars[-6:-1]
    prior20 = bars[-21:-1]
    prior8 = bars[-9:-1]
    prior5_high = max(d(bar.get("h")) for bar in prior5)
    prior8_high = max(d(bar.get("h")) for bar in prior8)

    last_return = _simple_return(current, previous)
    prior_return = _simple_return(previous, closes[-3])
    return_3 = _return_over(closes, 3)
    return_5 = _return_over(closes, 5)
    return_10 = _return_over(closes, 10)
    vwap_edge = (
        (current - session_vwap) / session_vwap
        if session_vwap > 0
        else Decimal("0")
    )
    pullback_depth = (
        (prior8_high - previous) / prior8_high
        if prior8_high > 0
        else Decimal("0")
    )

    context = _market_context(confirmation_bars)
    market_return_5 = d(context["market_return_5"])

    return {
        "current_close": str(current),
        "previous_close": str(previous),
        "session_vwap": str(session_vwap),
        "vwap_edge_pct": str(vwap_edge),
        "fast3": str(fast3),
        "fast5": str(fast5),
        "slow8": str(slow8),
        "slow15": str(slow15),
        "previous_fast3": str(previous_fast3),
        "last_bar_return_pct": str(last_return),
        "prior_bar_return_pct": str(prior_return),
        "return_3_pct": str(return_3),
        "return_5_pct": str(return_5),
        "return_10_pct": str(return_10),
        "relative_strength_5_pct": str(return_5 - market_return_5),
        "relative_volume_ratio": str(_relative_volume(bars)),
        "trend_persistence": str(_trend_persistence(closes)),
        "range_5_pct": str(_range_pct(prior5, current)),
        "range_20_pct": str(_range_pct(prior20, current)),
        "prior5_high": str(prior5_high),
        "prior8_high": str(prior8_high),
        "pullback_depth_pct": str(pullback_depth),
        "close_location_value": str(_close_location(current_bar)),
        "volume_acceleration": str(_volume_acceleration(bars)),
        "current_body_return_pct": str(
            _simple_return(current, d(current_bar.get("o")))
        ),
        "time_bucket": _time_bucket(now),
        **context,
    }


def _opening_breakout_retest(
    bars: list[dict[str, Any]],
    *,
    now: datetime,
    features: dict[str, Any],
) -> dict[str, Any] | None:
    if not (OPENING_RANGE_END <= now.time() <= OPENING_RETEST_CUTOFF):
        return None

    opening = [
        bar
        for bar in bars
        if NY_OPEN <= stamp(bar).time() < OPENING_RANGE_END
    ]
    if len(opening) < 10:
        return None

    opening_high = max(d(bar.get("h")) for bar in opening)
    opening_low = min(d(bar.get("l")) for bar in opening)
    if opening_high <= 0 or opening_low <= 0:
        return None

    opening_range_pct = (opening_high - opening_low) / opening_low
    if opening_range_pct > Decimal("0.02"):
        return None

    post_opening = [
        bar for bar in bars
        if stamp(bar).time() >= OPENING_RANGE_END
    ]
    breakout_index: int | None = None
    for index, bar in enumerate(post_opening[:-1]):
        if price(bar) > opening_high:
            breakout_index = index
            break

    if breakout_index is None:
        return None

    bars_since_breakout = len(post_opening) - 1 - breakout_index
    if not (2 <= bars_since_breakout <= 20):
        return None

    between = post_opening[breakout_index:-1]
    max_high = max(
        (d(bar.get("h")) for bar in between),
        default=opening_high,
    )
    pre_retest_excursion = (
        (max_high - opening_high) / opening_high
        if opening_high > 0
        else Decimal("0")
    )
    if pre_retest_excursion > Decimal("0.006"):
        return None

    current = post_opening[-1]
    current_low = d(current.get("l"))
    current_close = price(current)
    last_return = d(features.get("last_bar_return_pct"))
    market_return = d(features.get("market_return_5"))

    if not (
        current_low <= opening_high * Decimal("1.0015")
        and current_close > opening_high
        and last_return > 0
        and market_return >= Decimal("-0.001")
    ):
        return None

    return {
        "opening_high": str(opening_high),
        "opening_low": str(opening_low),
        "opening_range_pct": str(opening_range_pct),
        "bars_since_breakout": bars_since_breakout,
        "pre_retest_excursion_pct": str(pre_retest_excursion),
    }


def detect_families(
    bars: list[dict[str, Any]],
    confirmation_bars: dict[str, list[dict[str, Any]]],
    *,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Return research setup families observed using only completed bars."""
    features = candidate_features(
        bars,
        confirmation_bars,
        now=now,
    )
    if not features:
        return {}

    current = d(features["current_close"])
    vwap = d(features["session_vwap"])
    vwap_edge = d(features["vwap_edge_pct"])
    fast3 = d(features["fast3"])
    fast5 = d(features["fast5"])
    slow8 = d(features["slow8"])
    slow15 = d(features["slow15"])
    previous = d(features["previous_close"])
    previous_fast3 = d(features["previous_fast3"])
    last_return = d(features["last_bar_return_pct"])
    prior_return = d(features["prior_bar_return_pct"])
    return_3 = d(features["return_3_pct"])
    return_5 = d(features["return_5_pct"])
    return_10 = d(features["return_10_pct"])
    relative_strength = d(features["relative_strength_5_pct"])
    relative_volume = d(features["relative_volume_ratio"])
    persistence = d(features["trend_persistence"])
    range_5 = d(features["range_5_pct"])
    range_20 = d(features["range_20_pct"])
    prior5_high = d(features["prior5_high"])
    pullback_depth = d(features["pullback_depth_pct"])
    close_location = d(features["close_location_value"])
    volume_acceleration = d(features["volume_acceleration"])
    body_return = d(features["current_body_return_pct"])
    market_return = d(features["market_return_5"])

    output: dict[str, dict[str, Any]] = {}

    if (
        fast3 > slow8
        and current > vwap
        and Decimal("0.0005") <= return_3 <= Decimal("0.0030")
        and Decimal("0") < last_return <= Decimal("0.0018")
        and persistence <= Decimal("0.75")
        and Decimal("0") <= vwap_edge <= Decimal("0.006")
        and market_return >= Decimal("-0.001")
    ):
        output["controlled_continuation"] = {
            "reason": "moderate positive path without mature-momentum chase"
        }

    if (
        fast5 > slow15
        and current > vwap
        and return_10 >= Decimal("0.001")
        and prior_return <= 0
        and last_return > 0
        and previous <= previous_fast3
        and current > fast3
        and Decimal("0.001") <= pullback_depth <= Decimal("0.006")
        and relative_strength >= 0
        and market_return >= Decimal("-0.0015")
    ):
        output["pullback_reclaim"] = {
            "reason": "uptrend pullback followed by fast-average reclaim"
        }

    compressed = (
        range_20 > 0
        and range_5 <= Decimal("0.0045")
        and range_5 <= range_20 * Decimal("0.65")
    )
    if (
        compressed
        and current > prior5_high
        and current > vwap
        and Decimal("0") <= vwap_edge <= Decimal("0.006")
        and volume_acceleration >= Decimal("1.25")
        and close_location >= Decimal("0.75")
        and body_return > 0
        and market_return >= Decimal("-0.0015")
    ):
        output["compression_breakout"] = {
            "reason": "range compression released with volume and strong close"
        }

    if (
        return_5 >= Decimal("0.002")
        and relative_strength >= Decimal("0.0015")
        and Decimal("0.0015") <= return_3 <= Decimal("0.006")
        and last_return > 0
        and relative_volume >= Decimal("1.25")
        and close_location >= Decimal("0.70")
        and Decimal("0") <= vwap_edge <= Decimal("0.008")
        and market_return >= Decimal("-0.0015")
    ):
        output["relative_strength_impulse"] = {
            "reason": "candidate is materially outperforming market context with participation"
        }

    retest = _opening_breakout_retest(
        bars,
        now=now,
        features=features,
    )
    if retest is not None:
        output["opening_breakout_retest"] = {
            "reason": "opening-range breakout retested without excessive pre-retest excursion",
            **retest,
        }

    for payload in output.values():
        payload["features"] = dict(features)
    return output


def _modeled_return_pct(
    forward: dict[str, Any],
    *,
    target_pct: Decimal,
    stop_pct: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
) -> Decimal:
    exit_friction = (spread_bps / Decimal("2") + slippage_bps) / BPS
    barrier = str(forward.get("first_barrier") or "none")

    if barrier == "target":
        gross_exit = Decimal("1") + target_pct
    elif barrier == "stop":
        gross_exit = Decimal("1") - stop_pct
    else:
        gross_exit = Decimal("1") + d(forward.get("close_return_pct"))

    return gross_exit * (Decimal("1") - exit_friction) - Decimal("1")


def _profit_factor(returns: list[Decimal]) -> Decimal | None:
    gross_profit = sum((value for value in returns if value > 0), Decimal("0"))
    gross_loss = abs(sum((value for value in returns if value < 0), Decimal("0")))
    if gross_loss <= 0:
        return None if gross_profit <= 0 else Decimal("999")
    return gross_profit / gross_loss


def summarize_events(
    events: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    if not events:
        return {
            "events": 0,
            "wins": 0,
            "losses": 0,
            "expectancy_pct": "0",
            "profit_factor": None,
            "target_before_stop_rate": 0.0,
            "stop_before_target_rate": 0.0,
            "mean_mfe_pct": "0",
            "mean_mae_pct": "0",
            "session_count": 0,
            "session_expectancy_lower_95_pct": None,
        }

    key = str(horizon)
    returns = [d(event["modeled_return_pct"]) for event in events]
    labels = [(event.get("forward") or {}).get(key) or {} for event in events]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    targets = sum(bool(label.get("target_before_stop")) for label in labels)
    stops = sum(bool(label.get("stop_before_target")) for label in labels)
    mfes = [d(label.get("mfe_pct")) for label in labels]
    maes = [d(label.get("mae_pct")) for label in labels]

    by_session: dict[str, list[Decimal]] = defaultdict(list)
    for event, value in zip(events, returns):
        by_session[str(event["session"])].append(value)

    session_means = [_mean(values) for values in by_session.values()]
    lower_95: Decimal | None = None
    if len(session_means) >= 2:
        center = _mean(session_means)
        variance = sum(
            ((value - center) ** 2 for value in session_means),
            Decimal("0"),
        ) / Decimal(len(session_means) - 1)
        standard_error = Decimal(str(sqrt(float(variance)))) / Decimal(
            str(sqrt(len(session_means)))
        )
        lower_95 = center - Decimal("1.96") * standard_error

    pf = _profit_factor(returns)
    return {
        "events": len(events),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(events),
        "expectancy_pct": str(_mean(returns)),
        "profit_factor": None if pf is None else str(pf),
        "target_before_stop_rate": targets / len(events),
        "stop_before_target_rate": stops / len(events),
        "mean_mfe_pct": str(_mean(mfes)),
        "mean_mae_pct": str(_mean(maes)),
        "session_count": len(session_means),
        "session_expectancy_lower_95_pct": (
            None if lower_95 is None else str(lower_95)
        ),
    }


class EdgeDiscoveryStudy:
    """Broker-isolated research over setup families independent of live signals."""

    def __init__(
        self,
        settings: Settings,
        candidate_symbols: tuple[str, ...],
        *,
        event_cooldown_minutes: int = DEFAULT_EVENT_COOLDOWN_MINUTES,
    ):
        self.settings = settings
        self.candidate_symbols = tuple(
            dict.fromkeys(symbol.upper() for symbol in candidate_symbols if symbol)
        )
        self.event_cooldown_minutes = max(event_cooldown_minutes, 1)

    def run(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        horizon: int = DEFAULT_HORIZON,
    ) -> dict[str, Any]:
        if spread_bps < 0 or slippage_bps < 0:
            raise ValueError("friction assumptions cannot be negative")
        if horizon <= 0:
            raise ValueError("horizon must be positive")

        sessions = ReplayEngine._session_bar_map(bars_by_symbol)
        events: list[dict[str, Any]] = []
        cooldown: dict[tuple[str, str], datetime] = {}

        for session_day in sorted(sessions):
            session = sessions[session_day]
            timeline = sorted(
                {
                    stamp(bar)
                    for symbol in self.candidate_symbols
                    for bar in session.get(symbol, [])
                }
            )
            if not timeline:
                continue

            symbols = set(self.candidate_symbols) | set(
                self.settings.confirmation_symbols
            )
            visible = {symbol: [] for symbol in symbols}
            indexes = {symbol: 0 for symbol in symbols}

            for bar_time in timeline:
                now = bar_time + timedelta(minutes=1)
                for symbol in visible:
                    source = session.get(symbol, [])
                    cursor = indexes[symbol]
                    while cursor < len(source) and stamp(source[cursor]) <= bar_time:
                        visible[symbol].append(source[cursor])
                        cursor += 1
                    indexes[symbol] = cursor

                if not (
                    self.settings.entry_start
                    <= now.time()
                    <= self.settings.entry_cutoff
                ):
                    continue

                confirmation_bars = {
                    symbol: visible.get(symbol, [])
                    for symbol in self.settings.confirmation_symbols
                }

                for symbol in self.candidate_symbols:
                    candidate_bars = visible.get(symbol, [])
                    families = detect_families(
                        candidate_bars,
                        confirmation_bars,
                        now=now,
                    )
                    if not families:
                        continue

                    reference = price(candidate_bars[-1])
                    if reference <= 0:
                        continue
                    entry_fill = ReplayEngine._fill(
                        reference,
                        "buy",
                        spread_bps,
                        slippage_bps,
                    )
                    forward = forward_excursion(
                        session.get(symbol, []),
                        decision_bar_time=bar_time,
                        entry_price=entry_fill,
                        stop_pct=self.settings.stop_pct,
                        target_pct=self.settings.target_pct,
                        horizons=(horizon,),
                    )
                    label = forward.get(str(horizon), {})
                    if not label or int(label.get("bars") or 0) == 0:
                        continue

                    for family, payload in families.items():
                        key = (family, symbol)
                        previous_event = cooldown.get(key)
                        if previous_event is not None:
                            elapsed = (now - previous_event).total_seconds() / 60
                            if elapsed < self.event_cooldown_minutes:
                                continue

                        modeled = _modeled_return_pct(
                            label,
                            target_pct=self.settings.target_pct,
                            stop_pct=self.settings.stop_pct,
                            spread_bps=spread_bps,
                            slippage_bps=slippage_bps,
                        )
                        events.append(
                            {
                                "session": session_day.isoformat(),
                                "symbol": symbol,
                                "family": family,
                                "decision_at": now.isoformat(),
                                "decision_bar_time": bar_time.isoformat(),
                                "reference_price": str(reference),
                                "entry_fill": str(entry_fill),
                                "modeled_return_pct": str(modeled),
                                "features": payload.get("features", {}),
                                "family_details": {
                                    key: value
                                    for key, value in payload.items()
                                    if key != "features"
                                },
                                "forward": forward,
                            }
                        )
                        cooldown[key] = now

        by_family = {
            family: summarize_events(
                [event for event in events if event["family"] == family],
                horizon=horizon,
            )
            for family in FAMILY_NAMES
        }

        by_family_regime: dict[str, dict[str, Any]] = {}
        for family in FAMILY_NAMES:
            family_events = [
                event for event in events if event["family"] == family
            ]
            buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for event in family_events:
                features = event.get("features") or {}
                label = (
                    f'{features.get("trend_regime", "unknown")}/'
                    f'{features.get("volatility_regime", "unknown")}/'
                    f'{features.get("time_bucket", "unknown")}'
                )
                buckets[label].append(event)
            by_family_regime[family] = {
                label: summarize_events(bucket, horizon=horizon)
                for label, bucket in buckets.items()
            }

        return {
            "status": "research_only",
            "candidate_symbols": list(self.candidate_symbols),
            "confirmation_symbols": list(self.settings.confirmation_symbols),
            "family_names": list(FAMILY_NAMES),
            "horizon_minutes": horizon,
            "event_cooldown_minutes": self.event_cooldown_minutes,
            "assumptions": {
                "spread_bps": str(spread_bps),
                "slippage_bps_per_side": str(slippage_bps),
                "target_pct": str(self.settings.target_pct),
                "stop_pct": str(self.settings.stop_pct),
                "stop_first_when_same_bar_touches_both": True,
                "production_signal_required": False,
                "broker_orders_possible": False,
            },
            "event_count": len(events),
            "summary_by_family": by_family,
            "summary_by_family_regime": by_family_regime,
            "events": events,
        }


def aggregate_family_periods(
    period_results: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for family in FAMILY_NAMES:
        family_events: list[dict[str, Any]] = []
        period_expectancies: list[Decimal] = []
        positive_periods = 0
        periods_with_events = 0

        for period in period_results:
            events = [
                event
                for event in (period.get("events") or [])
                if event.get("family") == family
            ]
            if not events:
                continue
            periods_with_events += 1
            summary = summarize_events(events, horizon=horizon)
            expectancy = d(summary.get("expectancy_pct"))
            period_expectancies.append(expectancy)
            if expectancy > 0:
                positive_periods += 1
            family_events.extend(events)

        aggregate = summarize_events(family_events, horizon=horizon)
        aggregate.update(
            {
                "periods_with_events": periods_with_events,
                "positive_periods": positive_periods,
                "worst_period_expectancy_pct": (
                    str(min(period_expectancies))
                    if period_expectancies
                    else "0"
                ),
            }
        )
        output[family] = aggregate
    return output


def robust_edge_gate(
    scenario_results: list[dict[str, Any]],
    *,
    min_events: int = 60,
    min_positive_periods: int = 2,
    min_profit_factor: Decimal = Decimal("1.20"),
) -> dict[str, Any]:
    """Require a family to survive every configured cost scenario."""
    families: dict[str, Any] = {}

    for family in FAMILY_NAMES:
        scenario_checks: list[dict[str, Any]] = []
        for scenario in scenario_results:
            aggregate = (scenario.get("aggregate_by_family") or {}).get(
                family,
                {},
            )
            events = int(aggregate.get("events") or 0)
            expectancy = d(aggregate.get("expectancy_pct"))
            pf_raw = aggregate.get("profit_factor")
            profit_factor = (
                d(pf_raw) if pf_raw is not None else Decimal("0")
            )
            positive_periods = int(aggregate.get("positive_periods") or 0)
            lower_raw = aggregate.get("session_expectancy_lower_95_pct")
            lower_95 = d(lower_raw) if lower_raw is not None else None

            passed = (
                events >= min_events
                and expectancy > 0
                and profit_factor >= min_profit_factor
                and positive_periods >= min_positive_periods
            )
            scenario_checks.append(
                {
                    "scenario": scenario.get("scenario"),
                    "passed": passed,
                    "events": events,
                    "expectancy_pct": str(expectancy),
                    "profit_factor": str(profit_factor),
                    "positive_periods": positive_periods,
                    "session_expectancy_lower_95_pct": (
                        None if lower_95 is None else str(lower_95)
                    ),
                }
            )

        robust = bool(scenario_checks) and all(
            check["passed"] for check in scenario_checks
        )
        families[family] = {
            "robust_historical_edge": robust,
            "scenario_checks": scenario_checks,
        }

    passing = [
        family
        for family, payload in families.items()
        if payload["robust_historical_edge"]
    ]
    return {
        "status": "research_only",
        "criteria": {
            "minimum_events_per_scenario": min_events,
            "minimum_positive_periods_per_scenario": min_positive_periods,
            "minimum_profit_factor": str(min_profit_factor),
            "positive_expectancy_required": True,
            "must_pass_every_cost_scenario": True,
        },
        "families": families,
        "passing_families": passing,
        "promotion_authorized": False,
        "next_step": (
            "Freeze a passing family before untouched holdout replay; "
            "if none pass, reject the slate and design a new structural hypothesis."
        ),
    }
