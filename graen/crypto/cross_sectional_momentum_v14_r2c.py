from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-cross-sectional-momentum-v14-r2c"
CAMPAIGN_ID = "v14-r2c-alpaca-cross-sectional-momentum"
FAMILY = "alpaca_cross_sectional_crypto_momentum"

# Long-history subset of Alpaca's published tutorial basket. MATIC/POL and
# ALGO are excluded from this preflight because they do not provide enough
# common Alpaca US daily history for the pre-registered chronological split.
UNIVERSE = (
    "BTC/USD",
    "ETH/USD",
    "DOGE/USD",
    "SHIB/USD",
    "AVAX/USD",
    "LINK/USD",
    "SOL/USD",
)
BAR_SCREEN_START = datetime(2025, 1, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)
MOMENTUM_LOOKBACK_DAYS = 7
DEVELOPMENT_SHARE = 0.60

# A switch from one crypto to another requires a sell and a buy. The decisive
# scenario charges 25bp per leg = 50bp per switch.
SWITCH_COSTS = {
    "maker_switch_30bp": 0.0030,
    "mixed_switch_40bp": 0.0040,
    "taker_switch_50bp": 0.0050,
}
MIN_OOS_DAYS = 120
MIN_SWITCHES = 4


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2c.cross-sectional-momentum.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source_reference": {
            "repository": "alpacahq/plug-and-play-strategies",
            "path": "Websocket_Momentum_Trading/websocket_momentum.ipynb",
            "reference_commit": "3ea21811a3fbf4ebed6f24f8e865e41d326cb269",
            "published_rule": "long the highest-ranked crypto by seven-day momentum",
        },
        "universe": list(UNIVERSE),
        "universe_selection": (
            "currently tradable USD pairs overlapping Alpaca's published basket "
            "with sufficient common daily history; no symbol selected by outcomes"
        ),
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "momentum_lookback_days": MOMENTUM_LOOKBACK_DAYS,
        "execution_convention": (
            "rank using closes through day t-1; hold selected asset from day t open "
            "to day t+1 open; long-only top-1"
        ),
        "switch_costs": dict(SWITCH_COSTS),
        "oos_gate": {
            "taker_switch_50bp_total_return_gt": 0.0,
            "taker_switch_50bp_sharpe_gt": 0.0,
            "minimum_oos_days": MIN_OOS_DAYS,
            "minimum_switches": MIN_SWITCHES,
            "positive_quarter_share_gte": 0.50,
        },
        "next_stage_if_pass": "CROSS_SECTIONAL_MOMENTUM_SHADOW_ONLY",
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_bars(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[datetime, dict[str, float]]]:
    out: dict[str, dict[datetime, dict[str, float]]] = {}
    for symbol in UNIVERSE:
        daily: dict[datetime, dict[str, float]] = {}
        for row in bars_by_symbol.get(symbol, []):
            raw = row.get("t", row.get("timestamp"))
            if raw is None:
                continue
            try:
                stamp = _stamp(raw)
                open_px = float(row.get("o", row.get("open")))
                close = float(row.get("c", row.get("close")))
            except (TypeError, ValueError):
                continue
            if open_px <= 0 or close <= 0:
                continue
            day = datetime(stamp.year, stamp.month, stamp.day, tzinfo=UTC)
            daily[day] = {"open": open_px, "close": close}
        out[symbol] = daily
    return out


def _aligned_days(
    normalized: Mapping[str, Mapping[datetime, Mapping[str, float]]],
) -> list[datetime]:
    day_sets = [set(normalized.get(symbol, {})) for symbol in UNIVERSE]
    if not day_sets:
        return []
    return sorted(set.intersection(*day_sets))


def _daily_strategy(
    normalized: Mapping[str, Mapping[datetime, Mapping[str, float]]],
    days: Sequence[datetime],
    *,
    switch_cost: float,
) -> tuple[list[float], list[str], int]:
    returns: list[float] = []
    selections: list[str] = []
    switches = 0
    previous: str | None = None

    # Signal uses only closes through i-1. Position is entered at i open and
    # exited/rebalanced at i+1 open.
    for i in range(MOMENTUM_LOOKBACK_DAYS + 1, len(days) - 1):
        signal_day = days[i - 1]
        base_day = days[i - 1 - MOMENTUM_LOOKBACK_DAYS]
        entry_day = days[i]
        exit_day = days[i + 1]

        scores = {}
        for symbol in UNIVERSE:
            latest = float(normalized[symbol][signal_day]["close"])
            base = float(normalized[symbol][base_day]["close"])
            scores[symbol] = latest / base - 1.0
        selected = max(UNIVERSE, key=lambda symbol: (scores[symbol], symbol))

        entry = float(normalized[selected][entry_day]["open"])
        exit_px = float(normalized[selected][exit_day]["open"])
        value = exit_px / entry - 1.0
        if previous != selected:
            value -= switch_cost
            switches += 1
        returns.append(value)
        selections.append(selected)
        previous = selected
    return returns, selections, switches


def _performance(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "day_count": 0,
            "total_return": 0.0,
            "mean_daily_return": None,
            "win_rate": None,
            "sharpe": None,
        }
    compounded = 1.0
    for value in values:
        compounded *= 1.0 + value
    avg = mean(values)
    sigma = pstdev(values)
    sharpe = avg / sigma * sqrt(365.0) if sigma > 0 else None
    return {
        "day_count": len(values),
        "total_return": compounded - 1.0,
        "mean_daily_return": avg,
        "win_rate": sum(1 for value in values if value > 0.0) / len(values),
        "sharpe": sharpe,
    }


def _quarter_share(
    normalized: Mapping[str, Mapping[datetime, Mapping[str, float]]],
    days: Sequence[datetime],
    *,
    switch_cost: float,
) -> float:
    observed = 0
    positive = 0
    for q in range(4):
        start = len(days) * q // 4
        end = len(days) * (q + 1) // 4
        chunk = days[start:end]
        if len(chunk) <= MOMENTUM_LOOKBACK_DAYS + 3:
            continue
        values, _, _ = _daily_strategy(
            normalized,
            chunk,
            switch_cost=switch_cost,
        )
        if not values:
            continue
        observed += 1
        if float(_performance(values)["total_return"]) > 0.0:
            positive += 1
    return positive / observed if observed else 0.0


def evaluate_cross_sectional_momentum_preflight(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    normalized = normalize_bars(bars_by_symbol)
    missing = [symbol for symbol in UNIVERSE if len(normalized.get(symbol, {})) < 180]
    if missing:
        raise ValueError("v14_r2c_insufficient_symbol_history:" + ",".join(missing))

    days = _aligned_days(normalized)
    if len(days) < 300:
        raise ValueError(f"v14_r2c_aligned_corpus_too_small:{len(days)}")

    split = int(len(days) * DEVELOPMENT_SHARE)
    development_days = days[:split]
    # Carry enough warmup history into OOS without using future OOS outcomes.
    oos_days = days[max(0, split - MOMENTUM_LOOKBACK_DAYS - 2):]
    if len(oos_days) < MIN_OOS_DAYS + MOMENTUM_LOOKBACK_DAYS + 3:
        raise ValueError(f"v14_r2c_oos_too_small:{len(oos_days)}")

    scenarios: dict[str, Any] = {}
    for name, cost in SWITCH_COSTS.items():
        values, selections, switches = _daily_strategy(
            normalized,
            oos_days,
            switch_cost=cost,
        )
        scenarios[name] = {
            **_performance(values),
            "switch_cost": cost,
            "switch_count": switches,
            "distinct_selected_assets": sorted(set(selections)),
        }

    decisive = scenarios["taker_switch_50bp"]
    quarter_share = _quarter_share(
        normalized,
        oos_days,
        switch_cost=SWITCH_COSTS["taker_switch_50bp"],
    )
    sharpe = decisive.get("sharpe")
    survived = bool(
        int(decisive["day_count"]) >= MIN_OOS_DAYS
        and int(decisive["switch_count"]) >= MIN_SWITCHES
        and float(decisive["total_return"]) > 0.0
        and sharpe is not None
        and float(sharpe) > 0.0
        and quarter_share >= 0.50
    )

    # Baselines are descriptive only; the pre-registered gate above remains
    # absolute so the benchmark does not become an after-the-fact tuning knob.
    btc_values = []
    for i in range(1, len(oos_days)):
        prev_day = oos_days[i - 1]
        day = oos_days[i]
        btc_values.append(
            float(normalized["BTC/USD"][day]["open"])
            / float(normalized["BTC/USD"][prev_day]["open"])
            - 1.0
        )

    return {
        "schema_version": "graen.v14-r2c.cross-sectional-momentum.preflight.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US crypto daily bars",
            "aligned_day_count": len(days),
            "development_day_count": len(development_days),
            "oos_day_count_with_warmup": len(oos_days),
            "first_day": days[0].isoformat(),
            "last_day": days[-1].isoformat(),
            "per_symbol_counts": {
                symbol: len(normalized[symbol]) for symbol in UNIVERSE
            },
        },
        "oos": {
            "scenarios": scenarios,
            "taker_switch_50bp_positive_quarter_share": quarter_share,
        },
        "benchmarks": {
            "btc_buy_hold_open_to_open": _performance(btc_values),
        },
        "broker_feasibility_gate": {
            "survives_to_shadow": survived,
            "requirements": campaign_manifest()["oos_gate"],
        },
        "interpretation": (
            "V14_R2C_SURVIVES_TO_CROSS_SECTIONAL_MOMENTUM_SHADOW"
            if survived
            else "V14_R2C_BROKER_FEASIBILITY_FAIL"
        ),
        "shadow_only": survived,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
