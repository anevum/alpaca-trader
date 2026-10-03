from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-passive-scalping-v14-r2b"
CAMPAIGN_ID = "v14-r2b-alpaca-passive-scalping"
FAMILY = "alpaca_passive_range_scalping"
UNIVERSE = ("BTC/USD",)
BAR_SCREEN_START = datetime(2026, 9, 1, tzinfo=UTC)
BAR_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)
DEVELOPMENT_SHARE = 0.60

LOOKBACK_MINUTES = (5, 10, 15)
OFFSET_BPS = (10, 20)
MAX_HOLD_MINUTES = (5, 10)
ROUND_TRIP_COSTS = {
    "maker_base_30bp": 0.0030,
    "mixed_stress_40bp": 0.0040,
    "taker_stress_50bp": 0.0050,
}
MIN_DEVELOPMENT_TRADES = 20
MIN_OOS_TRADES = 15


@dataclass(frozen=True, slots=True)
class Config:
    lookback_minutes: int
    offset_bps: int
    max_hold_minutes: int

    @property
    def config_id(self) -> str:
        return f"scalp-{self.lookback_minutes}m-{self.offset_bps}bp-{self.max_hold_minutes}m"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "lookback_minutes": self.lookback_minutes,
            "offset_bps": self.offset_bps,
            "max_hold_minutes": self.max_hold_minutes,
        }


def candidate_configs() -> tuple[Config, ...]:
    return tuple(
        Config(lookback, offset, hold)
        for lookback in LOOKBACK_MINUTES
        for offset in OFFSET_BPS
        for hold in MAX_HOLD_MINUTES
    )


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2b.passive-scalping.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source_reference": {
            "repository": "alpacahq/plug-and-play-strategies",
            "path": "Scalping/scalping.py",
            "reference_commit": "3ea21811a3fbf4ebed6f24f8e865e41d326cb269",
            "adaptation": (
                "preserves rolling-range passive limit concept but removes live order side "
                "effects and evaluates fills chronologically on Alpaca minute bars"
            ),
        },
        "bar_screen_start": BAR_SCREEN_START.isoformat(),
        "bar_screen_end": BAR_SCREEN_END.isoformat(),
        "candidate_configs": [row.to_dict() for row in candidate_configs()],
        "round_trip_costs": dict(ROUND_TRIP_COSTS),
        "selection": "development-only selection by 50bp net return; frozen before OOS",
        "fill_model": {
            "entry": "buy limit fills only if a subsequent minute low reaches the frozen buy price",
            "exit": "sell limit fills only if a subsequent minute high reaches the frozen sell price",
            "unfilled_entry": "no trade",
            "unfilled_exit": "forced exit at horizon close with the same full round-trip cost hurdle",
        },
        "oos_gate": {
            "taker_stress_50bp_total_return_gt": 0.0,
            "taker_stress_50bp_sharpe_gt": 0.0,
            "minimum_trades": MIN_OOS_TRADES,
            "positive_quarter_share_gte": 0.50,
        },
        "next_stage_if_pass": "PASSIVE_SCALPING_SHADOW_ONLY",
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


def normalize_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for row in rows:
        raw = row.get("t", row.get("timestamp"))
        if raw is None:
            continue
        try:
            stamp = _stamp(raw)
            o = float(row.get("o", row.get("open")))
            h = float(row.get("h", row.get("high")))
            l = float(row.get("l", row.get("low")))
            c = float(row.get("c", row.get("close")))
        except (TypeError, ValueError):
            continue
        if stamp in seen or min(o, h, l, c) <= 0:
            continue
        seen.add(stamp)
        result.append({"timestamp": stamp, "open": o, "high": h, "low": l, "close": c})
    result.sort(key=lambda row: row["timestamp"])
    return result


def _trade_returns(
    bars: Sequence[Mapping[str, Any]],
    config: Config,
    *,
    round_trip_cost: float,
) -> list[float]:
    returns: list[float] = []
    i = config.lookback_minutes
    offset = config.offset_bps / 10000.0
    while i + config.max_hold_minutes < len(bars):
        history = bars[i - config.lookback_minutes : i]
        max_high = max(float(row["high"]) for row in history)
        min_low = min(float(row["low"]) for row in history)
        buy_limit = min_low * (1.0 + offset)
        sell_limit = max_high * (1.0 - offset)
        if sell_limit <= buy_limit:
            i += 1
            continue

        # The original Alpaca example only participates if the expected range
        # exceeds fees. Here the strictest 50bp hurdle controls selection.
        gross_range = sell_limit / buy_limit - 1.0
        if gross_range <= round_trip_cost:
            i += 1
            continue

        entry_index = None
        for j in range(i, min(i + config.max_hold_minutes, len(bars))):
            if float(bars[j]["low"]) <= buy_limit:
                entry_index = j
                break
        if entry_index is None:
            i += 1
            continue

        exit_index = min(entry_index + config.max_hold_minutes, len(bars) - 1)
        exit_price = float(bars[exit_index]["close"])
        passive_exit = False
        for j in range(entry_index + 1, exit_index + 1):
            if float(bars[j]["high"]) >= sell_limit:
                exit_index = j
                exit_price = sell_limit
                passive_exit = True
                break

        gross = exit_price / buy_limit - 1.0
        returns.append(gross - round_trip_cost)
        i = exit_index + 1
    return returns


def _performance(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "trade_count": 0,
            "total_return": 0.0,
            "mean_trade_return": None,
            "win_rate": None,
            "sharpe": None,
        }
    compounded = 1.0
    for value in values:
        compounded *= 1.0 + value
    avg = mean(values)
    sigma = pstdev(values)
    sharpe = avg / sigma * sqrt(len(values)) if sigma > 0 else None
    return {
        "trade_count": len(values),
        "total_return": compounded - 1.0,
        "mean_trade_return": avg,
        "win_rate": sum(1 for value in values if value > 0.0) / len(values),
        "sharpe": sharpe,
    }


def _evaluate(bars: Sequence[Mapping[str, Any]], config: Config) -> dict[str, Any]:
    scenarios = {}
    for name, cost in ROUND_TRIP_COSTS.items():
        values = _trade_returns(bars, config, round_trip_cost=cost)
        scenarios[name] = {**_performance(values), "round_trip_cost": cost}
    return {"config": config.to_dict(), "scenarios": scenarios}


def _positive_quarter_share(
    bars: Sequence[Mapping[str, Any]],
    config: Config,
    *,
    cost: float,
) -> float:
    positive = 0
    observed = 0
    for q in range(4):
        start = len(bars) * q // 4
        end = len(bars) * (q + 1) // 4
        chunk = bars[start:end]
        values = _trade_returns(chunk, config, round_trip_cost=cost)
        if not values:
            continue
        observed += 1
        if float(_performance(values)["total_return"]) > 0:
            positive += 1
    return positive / observed if observed else 0.0


def evaluate_passive_scalping_preflight(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    bars = normalize_bars(rows)
    if len(bars) < 3000:
        raise ValueError(f"v14_r2b_bar_corpus_too_small:{len(bars)}")
    split = int(len(bars) * DEVELOPMENT_SHARE)
    development = bars[:split]
    oos = bars[split:]

    development_results = [_evaluate(development, cfg) for cfg in candidate_configs()]
    eligible = [
        row for row in development_results
        if int(row["scenarios"]["taker_stress_50bp"]["trade_count"]) >= MIN_DEVELOPMENT_TRADES
    ]
    if not eligible:
        return {
            "schema_version": "graen.v14-r2b.passive-scalping.preflight.v1",
            "methodology_version": METHODOLOGY_VERSION,
            "campaign_id": CAMPAIGN_ID,
            "family": FAMILY,
            "manifest": campaign_manifest(),
            "development": {
                "bar_count": len(development),
                "candidate_results": development_results,
                "selected_config": None,
            },
            "oos": None,
            "broker_feasibility_gate": {"survives_to_shadow": False},
            "interpretation": "V14_R2B_NO_DEVELOPMENT_LIQUIDITY_SURVIVOR",
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "live_execution_authorized": False,
        }

    selected_row = max(
        eligible,
        key=lambda row: (
            float(row["scenarios"]["taker_stress_50bp"]["total_return"]),
            float(row["scenarios"]["mixed_stress_40bp"]["total_return"]),
            -int(row["config"]["lookback_minutes"]),
            -int(row["config"]["max_hold_minutes"]),
        ),
    )
    selected = Config(
        int(selected_row["config"]["lookback_minutes"]),
        int(selected_row["config"]["offset_bps"]),
        int(selected_row["config"]["max_hold_minutes"]),
    )
    oos_result = _evaluate(oos, selected)
    decisive = oos_result["scenarios"]["taker_stress_50bp"]
    quarters = _positive_quarter_share(
        oos,
        selected,
        cost=ROUND_TRIP_COSTS["taker_stress_50bp"],
    )
    sharpe = decisive.get("sharpe")
    survived = bool(
        int(decisive["trade_count"]) >= MIN_OOS_TRADES
        and float(decisive["total_return"]) > 0.0
        and sharpe is not None
        and float(sharpe) > 0.0
        and quarters >= 0.50
    )
    return {
        "schema_version": "graen.v14-r2b.passive-scalping.preflight.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "manifest": campaign_manifest(),
        "data": {
            "market": "BTC/USD",
            "provider": "Alpaca US crypto minute bars",
            "bar_count": len(bars),
            "first_bar": bars[0]["timestamp"].isoformat(),
            "last_bar": bars[-1]["timestamp"].isoformat(),
        },
        "development": {
            "bar_count": len(development),
            "candidate_results": development_results,
            "selected_config": selected.to_dict(),
        },
        "oos": {
            "bar_count": len(oos),
            **oos_result,
            "taker_stress_50bp_positive_quarter_share": quarters,
        },
        "broker_feasibility_gate": {
            "survives_to_shadow": survived,
            "requirements": campaign_manifest()["oos_gate"],
        },
        "interpretation": (
            "V14_R2B_SURVIVES_TO_PASSIVE_SCALPING_SHADOW"
            if survived
            else "V14_R2B_BROKER_FEASIBILITY_FAIL"
        ),
        "shadow_only": survived,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
