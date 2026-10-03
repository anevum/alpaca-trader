from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Mapping, Sequence

UTC = timezone.utc

METHODOLOGY_VERSION = "graen-triangular-arbitrage-v14-r2d"
CAMPAIGN_ID = "v14-r2d-alpaca-triangular-arbitrage"
FAMILY = "alpaca_single_venue_triangular_arbitrage"

# Alpaca documents this exact triangle in its crypto pricing examples.
PAIRS = ("BTC/USD", "ETH/BTC", "ETH/USD")
QUOTE_SCREEN_START = datetime(2026, 10, 3, 21, 0, tzinfo=UTC)
QUOTE_SCREEN_END = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)

SNAPSHOT_INTERVAL_MS = 250
MAX_QUOTE_AGE_MS = 250
MIN_MATCHED_SNAPSHOTS = 200
MIN_TAKER_OPPORTUNITIES = 5
MIN_TOP_OF_BOOK_CAPACITY_USD = 10.0
MIN_MAX_NET_EDGE_BPS = 5.0
MIN_POSITIVE_QUARTER_SHARE = 0.50

# Lowest-volume Alpaca tier: 15bp maker / 25bp taker per trade. The mixed
# scenario uses a 20bp per-leg blend. A three-leg cycle therefore carries
# approximately 45/60/75bp before any extra adverse selection.
LEG_FEE_RATES = {
    "maker_45bp": 0.0015,
    "mixed_60bp": 0.0020,
    "taker_75bp": 0.0025,
}


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r2d.triangular-arbitrage.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source_reference": {
            "alpaca_docs": "https://docs.alpaca.markets/us/docs/crypto-pricing-data",
            "documented_triangle": list(PAIRS),
            "research_reference": (
                "Finance Research Letters 73 (2025) 106508: "
                "Wish or reality? On the exploitability of triangular arbitrage "
                "in cryptocurrency markets"
            ),
        },
        "pairs": list(PAIRS),
        "quote_screen_start": QUOTE_SCREEN_START.isoformat(),
        "quote_screen_end": QUOTE_SCREEN_END.isoformat(),
        "snapshot_interval_ms": SNAPSHOT_INTERVAL_MS,
        "max_quote_age_ms": MAX_QUOTE_AGE_MS,
        "execution_convention": (
            "evaluate both USD->BTC->ETH->USD and USD->ETH->BTC->USD using "
            "executable top-of-book bid/ask quotes; no midpoint substitution"
        ),
        "leg_fee_rates": dict(LEG_FEE_RATES),
        "oos_gate": {
            "minimum_matched_snapshots": MIN_MATCHED_SNAPSHOTS,
            "minimum_taker_profitable_snapshots": MIN_TAKER_OPPORTUNITIES,
            "minimum_top_of_book_capacity_usd": MIN_TOP_OF_BOOK_CAPACITY_USD,
            "minimum_max_taker_net_edge_bps": MIN_MAX_NET_EDGE_BPS,
            "positive_time_quarter_share_gte": MIN_POSITIVE_QUARTER_SHARE,
        },
        "next_stage_if_pass": "TRIANGULAR_ARBITRAGE_LIVE_QUOTE_SHADOW_ONLY",
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


def normalize_quotes(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, float | datetime]]:
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
        if bid <= 0 or ask <= bid or bid_size <= 0 or ask_size <= 0:
            continue
        clean.append(
            {
                "timestamp": stamp,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
            }
        )
    clean.sort(key=lambda row: row["timestamp"])
    return clean


def _bucket_anchors(
    rows: Sequence[Mapping[str, float | datetime]],
) -> list[dict[str, float | datetime]]:
    latest: dict[int, dict[str, float | datetime]] = {}
    for row in rows:
        stamp = row["timestamp"]
        assert isinstance(stamp, datetime)
        bucket = int(stamp.timestamp() * 1000.0) // SNAPSHOT_INTERVAL_MS
        latest[bucket] = dict(row)
    return [latest[key] for key in sorted(latest)]


def align_snapshots(
    quotes_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    normalized = {
        symbol: normalize_quotes(quotes_by_symbol.get(symbol, []))
        for symbol in PAIRS
    }
    if any(not normalized[symbol] for symbol in PAIRS):
        return []

    anchors = _bucket_anchors(normalized["ETH/USD"])
    others = ("BTC/USD", "ETH/BTC")
    pointers = {symbol: 0 for symbol in others}
    latest: dict[str, Mapping[str, float | datetime]] = {}
    snapshots: list[dict[str, Any]] = []

    for anchor in anchors:
        anchor_stamp = anchor["timestamp"]
        assert isinstance(anchor_stamp, datetime)
        valid = True
        selected: dict[str, Mapping[str, float | datetime]] = {
            "ETH/USD": anchor
        }
        for symbol in others:
            rows = normalized[symbol]
            index = pointers[symbol]
            while index < len(rows):
                stamp = rows[index]["timestamp"]
                assert isinstance(stamp, datetime)
                if stamp > anchor_stamp:
                    break
                latest[symbol] = rows[index]
                index += 1
            pointers[symbol] = index
            row = latest.get(symbol)
            if row is None:
                valid = False
                break
            stamp = row["timestamp"]
            assert isinstance(stamp, datetime)
            age_ms = (anchor_stamp - stamp).total_seconds() * 1000.0
            if age_ms < 0 or age_ms > MAX_QUOTE_AGE_MS:
                valid = False
                break
            selected[symbol] = row
        if not valid:
            continue
        snapshots.append(
            {
                "timestamp": anchor_stamp,
                "quotes": {symbol: dict(selected[symbol]) for symbol in PAIRS},
            }
        )
    return snapshots


def _route_metrics(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    quotes = snapshot["quotes"]
    btcusd = quotes["BTC/USD"]
    ethbtc = quotes["ETH/BTC"]
    ethusd = quotes["ETH/USD"]

    # USD -> BTC -> ETH -> USD.
    route_a_gross = (
        float(ethusd["bid"])
        / (float(btcusd["ask"]) * float(ethbtc["ask"]))
    )
    route_a_capacity = min(
        float(btcusd["ask"]) * float(btcusd["ask_size"]),
        float(btcusd["ask"]) * float(ethbtc["ask"]) * float(ethbtc["ask_size"]),
        float(btcusd["ask"]) * float(ethbtc["ask"]) * float(ethusd["bid_size"]),
    )

    # USD -> ETH -> BTC -> USD.
    route_b_gross = (
        float(ethbtc["bid"]) * float(btcusd["bid"]) / float(ethusd["ask"])
    )
    route_b_capacity = min(
        float(ethusd["ask"]) * float(ethusd["ask_size"]),
        float(ethusd["ask"]) * float(ethbtc["bid_size"]),
        float(btcusd["bid_size"]) * float(ethusd["ask"]) / float(ethbtc["bid"]),
    )

    return (
        {
            "route": "USD_BTC_ETH_USD",
            "gross_ratio": route_a_gross,
            "capacity_usd": route_a_capacity,
        },
        {
            "route": "USD_ETH_BTC_USD",
            "gross_ratio": route_b_gross,
            "capacity_usd": route_b_capacity,
        },
    )


def _scenario_rows(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    fee_rate: float,
) -> list[dict[str, Any]]:
    fee_multiplier = (1.0 - fee_rate) ** 3
    out: list[dict[str, Any]] = []
    for snapshot in snapshots:
        candidates = []
        for route in _route_metrics(snapshot):
            net = float(route["gross_ratio"]) * fee_multiplier - 1.0
            candidates.append(
                {
                    **route,
                    "net_edge": net,
                    "net_edge_bps": net * 10000.0,
                }
            )
        best = max(candidates, key=lambda row: float(row["net_edge"]))
        out.append(
            {
                "timestamp": snapshot["timestamp"],
                **best,
            }
        )
    return out


def _quarter_share(rows: Sequence[Mapping[str, Any]]) -> float:
    if not rows:
        return 0.0
    observed = 0
    positive = 0
    for quarter in range(4):
        start = len(rows) * quarter // 4
        end = len(rows) * (quarter + 1) // 4
        chunk = rows[start:end]
        if not chunk:
            continue
        observed += 1
        if any(
            float(row["net_edge_bps"]) > 0.0
            and float(row["capacity_usd"]) >= MIN_TOP_OF_BOOK_CAPACITY_USD
            for row in chunk
        ):
            positive += 1
    return positive / observed if observed else 0.0


def _summarize(rows: Sequence[Mapping[str, Any]], *, fee_rate: float) -> dict[str, Any]:
    qualifying = [
        row
        for row in rows
        if float(row["net_edge_bps"]) > 0.0
        and float(row["capacity_usd"]) >= MIN_TOP_OF_BOOK_CAPACITY_USD
    ]
    all_edges = [float(row["net_edge_bps"]) for row in rows]
    positive_edges = [float(row["net_edge_bps"]) for row in qualifying]
    capacities = [float(row["capacity_usd"]) for row in qualifying]
    return {
        "fee_rate_per_leg": fee_rate,
        "snapshot_count": len(rows),
        "profitable_snapshot_count": len(qualifying),
        "profitable_snapshot_share": (
            len(qualifying) / len(rows) if rows else 0.0
        ),
        "mean_net_edge_bps": mean(all_edges) if all_edges else None,
        "max_net_edge_bps": max(all_edges) if all_edges else None,
        "median_positive_net_edge_bps": median(positive_edges) if positive_edges else None,
        "max_positive_net_edge_bps": max(positive_edges) if positive_edges else None,
        "median_positive_capacity_usd": median(capacities) if capacities else None,
        "min_positive_capacity_usd": min(capacities) if capacities else None,
        "positive_time_quarter_share": _quarter_share(rows),
        "route_counts": {
            route: sum(1 for row in qualifying if row["route"] == route)
            for route in ("USD_BTC_ETH_USD", "USD_ETH_BTC_USD")
        },
    }


def evaluate_triangular_arbitrage_preflight(
    quotes_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    snapshots = align_snapshots(quotes_by_symbol)
    if len(snapshots) < MIN_MATCHED_SNAPSHOTS:
        raise ValueError(f"v14_r2d_matched_quote_corpus_too_small:{len(snapshots)}")

    scenarios: dict[str, Any] = {}
    scenario_rows: dict[str, list[dict[str, Any]]] = {}
    for name, fee_rate in LEG_FEE_RATES.items():
        rows = _scenario_rows(snapshots, fee_rate=fee_rate)
        scenario_rows[name] = rows
        scenarios[name] = _summarize(rows, fee_rate=fee_rate)

    decisive = scenarios["taker_75bp"]
    max_edge = decisive.get("max_net_edge_bps")
    survived = bool(
        int(decisive["snapshot_count"]) >= MIN_MATCHED_SNAPSHOTS
        and int(decisive["profitable_snapshot_count"]) >= MIN_TAKER_OPPORTUNITIES
        and max_edge is not None
        and float(max_edge) >= MIN_MAX_NET_EDGE_BPS
        and float(decisive["positive_time_quarter_share"])
        >= MIN_POSITIVE_QUARTER_SHARE
    )

    first = snapshots[0]["timestamp"]
    last = snapshots[-1]["timestamp"]
    assert isinstance(first, datetime) and isinstance(last, datetime)
    return {
        "schema_version": "graen.v14-r2d.triangular-arbitrage.preflight.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "manifest": campaign_manifest(),
        "data": {
            "provider": "Alpaca US historical crypto top-of-book quotes",
            "pairs": list(PAIRS),
            "raw_quote_counts": {
                symbol: len(quotes_by_symbol.get(symbol, [])) for symbol in PAIRS
            },
            "matched_snapshot_count": len(snapshots),
            "first_snapshot": first.isoformat(),
            "last_snapshot": last.isoformat(),
            "snapshot_interval_ms": SNAPSHOT_INTERVAL_MS,
            "max_quote_age_ms": MAX_QUOTE_AGE_MS,
        },
        "scenarios": scenarios,
        "broker_feasibility_gate": {
            "survives_to_shadow": survived,
            "requirements": campaign_manifest()["oos_gate"],
        },
        "interpretation": (
            "V14_R2D_SURVIVES_TO_TRIANGULAR_ARBITRAGE_SHADOW"
            if survived
            else "V14_R2D_BROKER_FEASIBILITY_FAIL"
        ),
        "shadow_only": survived,
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
