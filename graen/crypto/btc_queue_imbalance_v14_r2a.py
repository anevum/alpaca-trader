from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-btc-queue-imbalance-v14-r2a"
CAMPAIGN_ID = "v14-r2a-alpaca-queue-imbalance"
FAMILY = "alpaca_top_of_book_queue_imbalance"
UNIVERSE = ("BTC/USD",)
QUOTE_SCREEN_START = datetime(2026, 9, 28, tzinfo=UTC)
QUOTE_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

BUCKET_SECONDS = 15
DEVELOPMENT_SHARE = 0.60
MIN_DEVELOPMENT_BUCKETS = 1200
MIN_OOS_BUCKETS = 800
MAX_SPREAD_BPS = 12.0

# Frozen before corpus access. The threshold/horizon grid is deliberately small.
IMBALANCE_THRESHOLDS = (0.25, 0.40, 0.55)
HOLD_SECONDS = (30, 60, 120)

# Round-trip cost assumptions. 30bp approximates maker/maker at the lowest
# Alpaca tier, 50bp approximates taker/taker before additional adverse selection.
ROUND_TRIP_COSTS = {
    "maker_base_30bp": 0.0030,
    "mixed_stress_40bp": 0.0040,
    "taker_base_50bp": 0.0050,
}


@dataclass(frozen=True, slots=True)
class Config:
    imbalance_threshold: float
    hold_seconds: int

    @property
    def config_id(self) -> str:
        return f"qi-{self.imbalance_threshold:.2f}-{self.hold_seconds}s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "imbalance_threshold": self.imbalance_threshold,
            "hold_seconds": self.hold_seconds,
        }


def candidate_configs() -> tuple[Config, ...]:
    return tuple(
        Config(threshold, hold)
        for threshold in IMBALANCE_THRESHOLDS
        for hold in HOLD_SECONDS
    )


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2a.queue-imbalance.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "universe": list(UNIVERSE),
        "quote_screen_start": QUOTE_SCREEN_START.isoformat(),
        "quote_screen_end": QUOTE_SCREEN_END.isoformat(),
        "signal": (
            "top-of-book queue imbalance=(bid_size-ask_size)/(bid_size+ask_size); "
            "long/cash only"
        ),
        "bucket_seconds": BUCKET_SECONDS,
        "development_share": DEVELOPMENT_SHARE,
        "max_spread_bps": MAX_SPREAD_BPS,
        "candidate_configs": [row.to_dict() for row in candidate_configs()],
        "round_trip_costs": dict(ROUND_TRIP_COSTS),
        "selection": (
            "choose one configuration on the chronological development partition "
            "by 50bp net return, then freeze it before OOS evaluation"
        ),
        "oos_gate": {
            "taker_base_50bp_total_return_gt": 0.0,
            "taker_base_50bp_sharpe_gt": 0.0,
            "minimum_trades": 20,
            "positive_quarter_share_gte": 0.50,
        },
        "next_stage_if_pass": "LIVE_L2_SHADOW_CONFIRMATION_ONLY",
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }


def _stamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def normalize_quotes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float | datetime]]:
    clean: list[dict[str, float | datetime]] = []
    for row in rows:
        raw_stamp = row.get("t", row.get("timestamp"))
        if raw_stamp is None:
            continue
        try:
            stamp = _stamp(raw_stamp)
            bid = float(row.get("bp", row.get("bid_price")))
            ask = float(row.get("ap", row.get("ask_price")))
            bid_size = float(row.get("bs", row.get("bid_size")))
            ask_size = float(row.get("as", row.get("ask_size")))
        except (TypeError, ValueError):
            continue
        if bid <= 0 or ask <= bid or bid_size < 0 or ask_size < 0:
            continue
        depth = bid_size + ask_size
        if depth <= 0:
            continue
        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10000.0
        clean.append({
            "timestamp": stamp,
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "mid": mid,
            "spread_bps": spread_bps,
            "imbalance": (bid_size - ask_size) / depth,
        })
    clean.sort(key=lambda row: row["timestamp"])
    return clean


def bucket_quotes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float | datetime]]:
    normalized = normalize_quotes(rows)
    if not normalized:
        return []
    latest_by_bucket: dict[int, dict[str, float | datetime]] = {}
    for row in normalized:
        stamp = row["timestamp"]
        assert isinstance(stamp, datetime)
        bucket = int(stamp.timestamp()) // BUCKET_SECONDS
        latest_by_bucket[bucket] = dict(row)
    return [latest_by_bucket[key] for key in sorted(latest_by_bucket)]


def _trade_returns(
    buckets: Sequence[Mapping[str, Any]],
    config: Config,
    *,
    round_trip_cost: float,
) -> list[float]:
    returns: list[float] = []
    if len(buckets) < 2:
        return returns
    horizon_buckets = max(1, config.hold_seconds // BUCKET_SECONDS)
    index = 0
    while index + horizon_buckets < len(buckets):
        row = buckets[index]
        imbalance = float(row["imbalance"])
        spread_bps = float(row["spread_bps"])
        if imbalance < config.imbalance_threshold or spread_bps > MAX_SPREAD_BPS:
            index += 1
            continue
        exit_index = index + horizon_buckets
        entry_ask = float(row["ask"])
        exit_bid = float(buckets[exit_index]["bid"])
        gross = exit_bid / entry_ask - 1.0
        returns.append(gross - round_trip_cost)
        index = exit_index + 1
    return returns


def _performance(returns: Sequence[float]) -> dict[str, Any]:
    if not returns:
        return {
            "trade_count": 0,
            "total_return": 0.0,
            "mean_trade_return": None,
            "win_rate": None,
            "sharpe": None,
        }
    compounded = 1.0
    for value in returns:
        compounded *= 1.0 + value
    avg = mean(returns)
    sigma = pstdev(returns)
    sharpe = avg / sigma * sqrt(len(returns)) if sigma > 0 else None
    return {
        "trade_count": len(returns),
        "total_return": compounded - 1.0,
        "mean_trade_return": avg,
        "win_rate": sum(1 for value in returns if value > 0.0) / len(returns),
        "sharpe": sharpe,
    }


def _evaluate_config(
    buckets: Sequence[Mapping[str, Any]],
    config: Config,
) -> dict[str, Any]:
    scenarios = {}
    for name, cost in ROUND_TRIP_COSTS.items():
        values = _trade_returns(buckets, config, round_trip_cost=cost)
        scenarios[name] = {
            **_performance(values),
            "round_trip_cost": cost,
        }
    return {
        "config": config.to_dict(),
        "scenarios": scenarios,
    }


def _positive_quarter_share(
    buckets: Sequence[Mapping[str, Any]],
    config: Config,
    *,
    cost: float,
) -> float:
    if len(buckets) < 4:
        return 0.0
    positive = 0
    observed = 0
    for quarter in range(4):
        start = len(buckets) * quarter // 4
        end = len(buckets) * (quarter + 1) // 4
        chunk = buckets[start:end]
        values = _trade_returns(chunk, config, round_trip_cost=cost)
        if not values:
            continue
        observed += 1
        if float(_performance(values)["total_return"]) > 0.0:
            positive += 1
    return positive / observed if observed else 0.0


def evaluate_queue_imbalance_preflight(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    buckets = bucket_quotes(rows)
    if len(buckets) < MIN_DEVELOPMENT_BUCKETS + MIN_OOS_BUCKETS:
        raise ValueError(f"v14_r2a_quote_corpus_too_small:{len(buckets)}")

    split = int(len(buckets) * DEVELOPMENT_SHARE)
    development = buckets[:split]
    oos = buckets[split:]
    if len(development) < MIN_DEVELOPMENT_BUCKETS or len(oos) < MIN_OOS_BUCKETS:
        raise ValueError(
            "v14_r2a_partition_too_small:"
            f"development={len(development)}:oos={len(oos)}"
        )

    development_results = [
        _evaluate_config(development, config) for config in candidate_configs()
    ]
    selected_row = max(
        development_results,
        key=lambda row: (
            float(
                row["scenarios"]["taker_base_50bp"]["total_return"]
            ),
            float(
                row["scenarios"]["mixed_stress_40bp"]["total_return"]
            ),
            -float(row["config"]["imbalance_threshold"]),
            -int(row["config"]["hold_seconds"]),
        ),
    )
    selected = Config(
        imbalance_threshold=float(selected_row["config"]["imbalance_threshold"]),
        hold_seconds=int(selected_row["config"]["hold_seconds"]),
    )
    oos_result = _evaluate_config(oos, selected)
    decisive = oos_result["scenarios"]["taker_base_50bp"]
    positive_quarter_share = _positive_quarter_share(
        oos,
        selected,
        cost=ROUND_TRIP_COSTS["taker_base_50bp"],
    )
    sharpe = decisive.get("sharpe")
    survived = bool(
        int(decisive["trade_count"]) >= 20
        and float(decisive["total_return"]) > 0.0
        and sharpe is not None
        and float(sharpe) > 0.0
        and positive_quarter_share >= 0.50
    )

    first = buckets[0]["timestamp"]
    last = buckets[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    return {
        "schema_version": "graen.v14-r2a.queue-imbalance.preflight.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "manifest": campaign_manifest(),
        "data": {
            "market": "BTC/USD",
            "provider": "Alpaca US crypto quotes",
            "raw_quote_count": len(rows),
            "bucket_count": len(buckets),
            "bucket_seconds": BUCKET_SECONDS,
            "first_quote": first.isoformat(),
            "last_quote": last.isoformat(),
        },
        "development": {
            "bucket_count": len(development),
            "candidate_results": development_results,
            "selected_config": selected.to_dict(),
        },
        "oos": {
            "bucket_count": len(oos),
            **oos_result,
            "taker_base_50bp_positive_quarter_share": positive_quarter_share,
        },
        "broker_feasibility_gate": {
            "survives_to_live_l2_shadow": survived,
            "requirements": {
                "taker_base_50bp_total_return_gt": 0.0,
                "taker_base_50bp_sharpe_gt": 0.0,
                "minimum_trades": 20,
                "positive_quarter_share_gte": 0.50,
            },
        },
        "interpretation": (
            "V14_R2A_SURVIVES_TO_LIVE_L2_SHADOW"
            if survived
            else "V14_R2A_BROKER_FEASIBILITY_FAIL"
        ),
        "shadow_only": True,
        "development_opened": False,
        "validation_opened": False,
        "holdout_opened": False,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
