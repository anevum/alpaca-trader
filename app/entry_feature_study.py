from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from .config import Settings
from .opportunity import score_opportunity
from .replay import BPS, ReplayEngine, d, price, stamp
from .strategy import Signal


DEFAULT_HORIZONS = (1, 3, 5, 10, 15)
FEATURES_FOR_QUARTILES = (
    "quality_score",
    "momentum_pct",
    "vwap_edge_pct",
    "relative_volume_ratio",
    "last_bar_return_pct",
    "prior_bar_return_pct",
    "path_efficiency",
    "latest_share_of_net_move",
    "fast_slow_gap_pct",
    "recent_range_pct",
    "volume_acceleration",
    "green_fraction",
    "close_location_value",
    "confirmation_passes",
    "bar_age_seconds",
)


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _return(new: Decimal, old: Decimal) -> Decimal:
    if old <= 0:
        return Decimal("0")
    return (new - old) / old


def decision_path_features(
    bars: list[dict[str, Any]],
    *,
    fast_window: int,
    slow_window: int,
) -> dict[str, str]:
    """Features available strictly at the decision point.

    The function never reads future bars. It describes whether the recent path
    is directional, choppy, accelerating, stretched, or closing strongly.
    """
    if len(bars) < 2:
        return {}

    closes = [price(bar) for bar in bars]
    returns = [
        _return(closes[index], closes[index - 1])
        for index in range(1, len(closes))
    ]
    window_size = min(max(fast_window, 3), len(returns))
    recent_returns = returns[-window_size:]
    anchor = closes[-(window_size + 1)]
    net_return = _return(closes[-1], anchor)
    gross_path = sum((abs(value) for value in recent_returns), Decimal("0"))
    path_efficiency = (
        net_return / gross_path
        if gross_path > 0
        else Decimal("0")
    )

    current = bars[-1]
    current_close = closes[-1]
    current_open = d(current.get("o"))
    current_high = d(current.get("h"))
    current_low = d(current.get("l"))
    bar_range = current_high - current_low
    close_location = (
        (current_close - current_low) / bar_range
        if bar_range > 0
        else Decimal("0")
    )

    fast_count = min(max(fast_window, 1), len(closes))
    slow_count = min(max(slow_window, fast_count), len(closes))
    fast_average = _mean(closes[-fast_count:])
    slow_average = _mean(closes[-slow_count:])
    fast_slow_gap = (
        (fast_average - slow_average) / current_close
        if current_close > 0
        else Decimal("0")
    )

    range_bars = bars[-min(max(fast_window, 3), len(bars)):]
    recent_high = max(d(bar.get("h")) for bar in range_bars)
    recent_low = min(d(bar.get("l")) for bar in range_bars)
    recent_range_pct = (
        (recent_high - recent_low) / current_close
        if current_close > 0
        else Decimal("0")
    )

    previous_volumes = [
        d(bar.get("v"))
        for bar in bars[max(0, len(bars) - 4):-1]
        if d(bar.get("v")) > 0
    ]
    current_volume = d(current.get("v"))
    previous_volume_mean = _mean(previous_volumes)
    volume_acceleration = (
        current_volume / previous_volume_mean
        if previous_volume_mean > 0
        else Decimal("0")
    )

    positive_returns = sum(1 for value in recent_returns if value > 0)
    green_fraction = (
        Decimal(positive_returns) / Decimal(len(recent_returns))
        if recent_returns
        else Decimal("0")
    )

    last_bar_return = returns[-1]
    prior_bar_return = returns[-2] if len(returns) >= 2 else Decimal("0")
    latest_share = (
        last_bar_return / net_return
        if net_return > 0
        else Decimal("0")
    )
    current_body_return = _return(current_close, current_open)

    return {
        "last_bar_return_pct": str(last_bar_return),
        "prior_bar_return_pct": str(prior_bar_return),
        "net_path_return_pct": str(net_return),
        "gross_path_return_pct": str(gross_path),
        "path_efficiency": str(path_efficiency),
        "latest_share_of_net_move": str(latest_share),
        "fast_slow_gap_pct": str(fast_slow_gap),
        "recent_range_pct": str(recent_range_pct),
        "volume_acceleration": str(volume_acceleration),
        "green_fraction": str(green_fraction),
        "current_body_return_pct": str(current_body_return),
        "close_location_value": str(close_location),
    }


def forward_excursion(
    session_bars: list[dict[str, Any]],
    *,
    decision_bar_time,
    entry_price: Decimal,
    stop_pct: Decimal,
    target_pct: Decimal,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> dict[str, dict[str, Any]]:
    """Measure future price potential without using the production exit engine.

    This is an entry-quality diagnostic. Stop is checked before target within a
    one-minute bar because intrabar ordering is unknown.
    """
    future = [
        bar
        for bar in session_bars
        if stamp(bar) > decision_bar_time
    ]
    output: dict[str, dict[str, Any]] = {}
    if entry_price <= 0:
        return output

    stop_price = entry_price * (Decimal("1") - stop_pct)
    target_price = entry_price * (Decimal("1") + target_pct)

    for horizon in horizons:
        sample = future[:horizon]
        if not sample:
            output[str(horizon)] = {
                "bars": 0,
                "mfe_pct": "0",
                "mae_pct": "0",
                "close_return_pct": "0",
                "target_before_stop": False,
                "stop_before_target": False,
                "first_barrier": "none",
            }
            continue

        highs = [d(bar.get("h")) for bar in sample if d(bar.get("h")) > 0]
        lows = [d(bar.get("l")) for bar in sample if d(bar.get("l")) > 0]
        final_close = price(sample[-1])

        raw_mfe = (
            max((_return(value, entry_price) for value in highs), default=Decimal("0"))
        )
        raw_mae = (
            min((_return(value, entry_price) for value in lows), default=Decimal("0"))
        )
        mfe = max(Decimal("0"), raw_mfe)
        mae = min(Decimal("0"), raw_mae)

        first_barrier = "none"
        for bar in sample:
            low = d(bar.get("l"))
            high = d(bar.get("h"))
            if low > 0 and low <= stop_price:
                first_barrier = "stop"
                break
            if high > 0 and high >= target_price:
                first_barrier = "target"
                break

        output[str(horizon)] = {
            "bars": len(sample),
            "mfe_pct": str(mfe),
            "mae_pct": str(mae),
            "close_return_pct": str(_return(final_close, entry_price)),
            "target_before_stop": first_barrier == "target",
            "stop_before_target": first_barrier == "stop",
            "first_barrier": first_barrier,
        }

    return output


def _numeric_feature(row: dict[str, Any], feature: str) -> Decimal:
    try:
        return Decimal(str((row.get("features") or {}).get(feature, "0")))
    except Exception:
        return Decimal("0")


def _quartile_summary(
    rows: list[dict[str, Any]],
    *,
    feature: str,
    horizon: int,
) -> list[dict[str, Any]]:
    usable = [
        row for row in rows
        if feature in (row.get("features") or {})
    ]
    usable.sort(key=lambda row: _numeric_feature(row, feature))
    if len(usable) < 4:
        return []

    horizon_key = str(horizon)
    result: list[dict[str, Any]] = []
    for quartile in range(4):
        start = len(usable) * quartile // 4
        end = len(usable) * (quartile + 1) // 4
        bucket = usable[start:end]
        if not bucket:
            continue

        values = [_numeric_feature(row, feature) for row in bucket]
        forward = [
            (row.get("forward") or {}).get(horizon_key) or {}
            for row in bucket
        ]
        mfes = [d(item.get("mfe_pct")) for item in forward]
        maes = [d(item.get("mae_pct")) for item in forward]
        targets = sum(bool(item.get("target_before_stop")) for item in forward)
        stops = sum(bool(item.get("stop_before_target")) for item in forward)

        result.append(
            {
                "quartile": quartile + 1,
                "n": len(bucket),
                "min_feature": str(min(values)),
                "max_feature": str(max(values)),
                "mean_feature": str(_mean(values)),
                "mean_mfe_pct": str(_mean(mfes)),
                "mean_mae_pct": str(_mean(maes)),
                "target_before_stop_rate": targets / len(bucket),
                "stop_before_target_rate": stops / len(bucket),
            }
        )
    return result


class EntryFeatureStudy:
    """Research-only decision-time feature and forward-excursion collector."""

    def __init__(self, settings: Settings, strategy: Any):
        self.settings = settings
        self.strategy = strategy
        self.replay = ReplayEngine(settings, strategy)

    def run(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    ) -> dict[str, Any]:
        if spread_bps < 0 or slippage_bps < 0:
            raise ValueError("spread_bps and slippage_bps cannot be negative")
        if not horizons or any(value <= 0 for value in horizons):
            raise ValueError("horizons must contain positive minute counts")

        sessions = self.replay._session_bar_map(bars_by_symbol)
        observations: list[dict[str, Any]] = []
        spread_pct = spread_bps / BPS

        for session_day in sorted(sessions):
            session = sessions[session_day]
            timeline = sorted(
                {
                    stamp(bar)
                    for symbol in self.settings.scan_symbols
                    for bar in session.get(symbol, [])
                }
            )
            if not timeline:
                continue

            visible: dict[str, list[dict[str, Any]]] = {
                symbol: []
                for symbol in (
                    set(self.settings.scan_symbols)
                    | set(self.settings.confirmation_symbols)
                )
            }
            index = {symbol: 0 for symbol in visible}

            for bar_time in timeline:
                now = bar_time + timedelta(minutes=1)
                for symbol in visible:
                    source = session.get(symbol, [])
                    cursor = index[symbol]
                    while cursor < len(source) and stamp(source[cursor]) <= bar_time:
                        visible[symbol].append(source[cursor])
                        cursor += 1
                    index[symbol] = cursor

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

                for symbol in self.settings.scan_symbols:
                    signal: Signal = self.strategy.evaluate(
                        bars=visible.get(symbol, []),
                        confirmation_bars=confirmation_bars,
                        symbol=symbol,
                        has_position=False,
                        order_notional=self.settings.order_notional,
                        now=now,
                    )
                    if signal.action != "buy":
                        continue

                    market_allowed, market_reason, market_quality = (
                        self.replay._historical_market_quality(
                            signal,
                            visible,
                            now,
                            spread_pct,
                        )
                    )

                    metadata = dict(signal.metadata or {})
                    quality_score = Decimal("0")
                    ranking: dict[str, Any] = {}
                    if market_allowed:
                        ranking = score_opportunity(
                            self.settings,
                            signal,
                            visible.get(symbol, []),
                            market_quality,
                        )
                        quality_score = d(ranking.get("score"))
                        metadata["market_quality"] = market_quality
                        metadata["quality_score"] = ranking.get("score")
                        metadata["quality_components"] = ranking.get("components")
                        metadata["relative_volume_ratio"] = ranking.get(
                            "relative_volume_ratio"
                        )
                        metadata["trend_persistence"] = ranking.get(
                            "trend_persistence"
                        )

                    quality_allowed = (
                        market_allowed
                        and quality_score >= self.settings.min_quality_score
                    )
                    signal.metadata = metadata

                    reference = signal.reference_price
                    simulated_entry = self.replay._fill(
                        reference,
                        "buy",
                        spread_bps,
                        slippage_bps,
                    )
                    effective_stop_pct = d(
                        metadata.get(
                            "effective_stop_pct",
                            self.settings.stop_pct,
                        )
                    )
                    if effective_stop_pct <= 0:
                        effective_stop_pct = self.settings.stop_pct

                    features = decision_path_features(
                        visible.get(symbol, []),
                        fast_window=self.settings.fast_window,
                        slow_window=self.settings.slow_window,
                    )
                    features.update(
                        {
                            "quality_score": str(quality_score),
                            "momentum_pct": str(d(metadata.get("momentum_pct"))),
                            "vwap_edge_pct": str(d(metadata.get("vwap_edge_pct"))),
                            "relative_volume_ratio": str(
                                d(metadata.get("relative_volume_ratio"))
                            ),
                            "trend_persistence": str(
                                d(metadata.get("trend_persistence"))
                            ),
                            "confirmation_passes": str(
                                int(metadata.get("confirmation_passes", 0) or 0)
                            ),
                            "regime_passes": str(
                                int(metadata.get("regime_passes", 0) or 0)
                            ),
                            "bar_age_seconds": str(
                                d(market_quality.get("bar_age_seconds"))
                            ),
                            "spread_pct": str(spread_pct),
                        }
                    )

                    observations.append(
                        {
                            "session": session_day.isoformat(),
                            "symbol": symbol,
                            "decision_at": now.isoformat(),
                            "decision_bar_time": bar_time.isoformat(),
                            "reference_price": str(reference),
                            "simulated_entry_price": str(simulated_entry),
                            "market_quality_allowed": market_allowed,
                            "market_quality_reason": market_reason,
                            "quality_allowed": quality_allowed,
                            "features": features,
                            "effective_stop_pct": str(effective_stop_pct),
                            "target_pct": str(self.settings.target_pct),
                            "forward": forward_excursion(
                                session.get(symbol, []),
                                decision_bar_time=bar_time,
                                entry_price=simulated_entry,
                                stop_pct=effective_stop_pct,
                                target_pct=self.settings.target_pct,
                                horizons=horizons,
                            ),
                        }
                    )

        eligible = [
            row for row in observations
            if row["quality_allowed"]
        ]
        horizon_summary: dict[str, Any] = {}
        for horizon in horizons:
            key = str(horizon)
            forward = [
                (row.get("forward") or {}).get(key) or {}
                for row in eligible
            ]
            mfes = [d(item.get("mfe_pct")) for item in forward]
            maes = [d(item.get("mae_pct")) for item in forward]
            targets = sum(bool(item.get("target_before_stop")) for item in forward)
            stops = sum(bool(item.get("stop_before_target")) for item in forward)
            horizon_summary[key] = {
                "n": len(forward),
                "mean_mfe_pct": str(_mean(mfes)),
                "mean_mae_pct": str(_mean(maes)),
                "target_before_stop_rate": (
                    targets / len(forward) if forward else 0.0
                ),
                "stop_before_target_rate": (
                    stops / len(forward) if forward else 0.0
                ),
            }

        quartiles: dict[str, Any] = {}
        primary_horizon = 5 if 5 in horizons else horizons[-1]
        for feature in FEATURES_FOR_QUARTILES:
            summary = _quartile_summary(
                eligible,
                feature=feature,
                horizon=primary_horizon,
            )
            if summary:
                quartiles[feature] = summary

        by_session: dict[str, int] = defaultdict(int)
        for row in eligible:
            by_session[row["session"]] += 1

        return {
            "status": "research_only",
            "purpose": (
                "Measure whether decision-time entry features predict forward "
                "excursion before modifying live thresholds, exits, or capital."
            ),
            "assumptions": {
                "spread_bps": str(spread_bps),
                "slippage_bps_per_side": str(slippage_bps),
                "stop_before_target_within_same_bar": True,
                "production_exit_engine_ignored": True,
                "position_caps_ignored": True,
                "correlation_blocks_ignored": True,
                "cooldowns_ignored": True,
                "future_data_used_only_for_labels": True,
            },
            "horizons_minutes": list(horizons),
            "strategy_buy_observations": len(observations),
            "quality_eligible_observations": len(eligible),
            "eligible_by_session": dict(by_session),
            "forward_summary": horizon_summary,
            "quartile_horizon_minutes": primary_horizon,
            "feature_quartiles": quartiles,
            "observations": observations,
        }



def _feature_quantiles(
    rows: list[dict[str, Any]],
    feature: str,
) -> list[Decimal]:
    values = sorted(
        {
            _numeric_feature(row, feature)
            for row in rows
            if feature in (row.get("features") or {})
        }
    )
    if len(values) < 4:
        return []
    indexes = {
        int((len(values) - 1) * fraction)
        for fraction in (0.25, 0.50, 0.75)
    }
    return [values[index] for index in sorted(indexes)]


def _condition_pass(
    row: dict[str, Any],
    condition: dict[str, Any],
) -> bool:
    value = _numeric_feature(row, str(condition["feature"]))
    threshold = Decimal(str(condition["threshold"]))
    if condition["operator"] == ">=":
        return value >= threshold
    return value <= threshold


def _barrier_metrics(
    rows: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    key = str(horizon)
    labeled = [
        (row.get("forward") or {}).get(key) or {}
        for row in rows
    ]
    if not labeled:
        return {
            "n": 0,
            "target_before_stop_rate": 0.0,
            "stop_before_target_rate": 0.0,
            "mean_mfe_pct": "0",
            "mean_mae_pct": "0",
            "mean_close_return_pct": "0",
        }

    targets = sum(bool(item.get("target_before_stop")) for item in labeled)
    stops = sum(bool(item.get("stop_before_target")) for item in labeled)
    mfes = [d(item.get("mfe_pct")) for item in labeled]
    maes = [d(item.get("mae_pct")) for item in labeled]
    closes = [d(item.get("close_return_pct")) for item in labeled]
    return {
        "n": len(labeled),
        "target_before_stop_rate": targets / len(labeled),
        "stop_before_target_rate": stops / len(labeled),
        "mean_mfe_pct": str(_mean(mfes)),
        "mean_mae_pct": str(_mean(maes)),
        "mean_close_return_pct": str(_mean(closes)),
    }


def stable_rule_scan(
    period_results: list[dict[str, Any]],
    *,
    horizon: int = 15,
    min_per_period: int = 15,
    top_n: int = 25,
) -> dict[str, Any]:
    """Find low-complexity entry filters that improve every development period.

    Thresholds are limited to pooled quartiles and rule complexity to one or two
    conditions. A rule is retained only when every supplied development period
    has enough observations, target-before-stop is no worse than baseline,
    stop-before-target is no worse than baseline, and the target-minus-stop
    balance improves. This is a discovery aid, not an automatic promotion step.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if min_per_period <= 0:
        raise ValueError("min_per_period must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    periods: list[dict[str, Any]] = []
    pooled: list[dict[str, Any]] = []
    for index, result in enumerate(period_results):
        label = str(
            result.get("period")
            or result.get("label")
            or f"period_{index + 1}"
        )
        rows = [
            row
            for row in (result.get("observations") or [])
            if row.get("quality_allowed")
        ]
        baseline = _barrier_metrics(rows, horizon=horizon)
        periods.append(
            {
                "label": label,
                "rows": rows,
                "baseline": baseline,
            }
        )
        pooled.extend(rows)

    if len(periods) < 2:
        return {
            "status": "insufficient_periods",
            "horizon_minutes": horizon,
            "minimum_sample_per_period": min_per_period,
            "rules": [],
            "reason": "stable rule scan requires at least two development periods",
        }

    conditions: list[dict[str, Any]] = []
    for feature in FEATURES_FOR_QUARTILES:
        for threshold in _feature_quantiles(pooled, feature):
            for operator in (">=", "<="):
                condition = {
                    "feature": feature,
                    "operator": operator,
                    "threshold": str(threshold),
                }
                if condition not in conditions:
                    conditions.append(condition)

    candidates: list[list[dict[str, Any]]] = [
        [condition] for condition in conditions
    ]
    for left_index, left in enumerate(conditions):
        for right in conditions[left_index + 1:]:
            if left["feature"] == right["feature"]:
                continue
            candidates.append([left, right])

    accepted: list[dict[str, Any]] = []
    for rule in candidates:
        by_period: dict[str, Any] = {}
        minimum_improvement: float | None = None
        minimum_support = 1.0
        valid = True

        for period in periods:
            selected = [
                row
                for row in period["rows"]
                if all(_condition_pass(row, condition) for condition in rule)
            ]
            metrics = _barrier_metrics(selected, horizon=horizon)
            baseline = period["baseline"]
            by_period[period["label"]] = metrics

            if metrics["n"] < min_per_period:
                valid = False
                break

            target_rate = float(metrics["target_before_stop_rate"])
            stop_rate = float(metrics["stop_before_target_rate"])
            base_target = float(baseline["target_before_stop_rate"])
            base_stop = float(baseline["stop_before_target_rate"])

            improvement = (
                (target_rate - stop_rate)
                - (base_target - base_stop)
            )
            support = (
                metrics["n"] / baseline["n"]
                if baseline["n"]
                else 0.0
            )
            minimum_support = min(minimum_support, support)
            minimum_improvement = (
                improvement
                if minimum_improvement is None
                else min(minimum_improvement, improvement)
            )

            if (
                target_rate < base_target
                or stop_rate > base_stop
                or improvement <= 0
            ):
                valid = False
                break

        if valid and minimum_improvement is not None:
            accepted.append(
                {
                    "conditions": rule,
                    "complexity": len(rule),
                    "minimum_balance_improvement": minimum_improvement,
                    "minimum_support_rate": minimum_support,
                    "periods": by_period,
                }
            )

    accepted.sort(
        key=lambda item: (
            item["minimum_balance_improvement"],
            item["minimum_support_rate"],
            -item["complexity"],
        ),
        reverse=True,
    )

    return {
        "status": "research_only",
        "horizon_minutes": horizon,
        "minimum_sample_per_period": min_per_period,
        "development_periods": {
            period["label"]: period["baseline"]
            for period in periods
        },
        "condition_count": len(conditions),
        "tested_rule_count": len(candidates),
        "passing_rule_count": len(accepted),
        "rules": accepted[:top_n],
        "candidate_frozen": False,
        "promotion_authorized": False,
        "notes": [
            "Thresholds come only from pooled development-period quartiles.",
            "Rules contain at most two conditions.",
            "Every retained rule improves target-minus-stop balance in every development period.",
            "A retained rule still requires full replay, a frozen untouched holdout, and forward shadow validation.",
        ],
    }
