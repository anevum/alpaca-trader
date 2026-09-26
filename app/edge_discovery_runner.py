from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import get_settings
from .edge_discovery import (
    EdgeDiscoveryStudy,
    aggregate_family_periods,
    robust_edge_gate,
)
from .market_data import MarketDataClient
from .strategy_lab_runner import fetch_period, parse_period


DEFAULT_COST_SCENARIOS = (
    ("base", Decimal("5"), Decimal("2")),
    ("moderate", Decimal("8"), Decimal("3")),
    ("stress", Decimal("12"), Decimal("5")),
)


def parse_symbols(raw: str) -> tuple[str, ...]:
    values = []
    for piece in raw.split(","):
        symbol = piece.strip().upper()
        if symbol and symbol not in values:
            values.append(symbol)
    if not values:
        raise ValueError("at least one candidate symbol is required")
    return tuple(values)


def parse_cost_scenario(raw: str) -> tuple[str, Decimal, Decimal]:
    try:
        name, spread, slippage = raw.split(":", 2)
        spread_value = Decimal(spread)
        slippage_value = Decimal(slippage)
    except Exception as exc:
        raise ValueError(
            "cost scenario must be NAME:SPREAD_BPS:SLIPPAGE_BPS"
        ) from exc
    if spread_value < 0 or slippage_value < 0:
        raise ValueError("cost assumptions cannot be negative")
    return name, spread_value, slippage_value


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    market_data = MarketDataClient(settings)
    scenarios = args.cost_scenario or list(DEFAULT_COST_SCENARIOS)

    period_ranges = []
    for raw in args.period:
        start, end = parse_period(raw)
        period_ranges.append((raw, start, end))

    symbols = list(
        dict.fromkeys(
            [
                *args.candidate_symbols,
                *settings.confirmation_symbols,
            ]
        )
    )

    fetched: list[tuple[str, dict[str, list[dict[str, Any]]]]] = []
    for raw, start, end in period_ranges:
        bars = await fetch_period(
            market_data,
            symbols,
            start,
            end,
        )
        fetched.append((raw, bars))

    scenario_results = []
    for name, spread_bps, slippage_bps in scenarios:
        periods = []
        for raw, bars in fetched:
            study = EdgeDiscoveryStudy(
                settings,
                args.candidate_symbols,
                event_cooldown_minutes=args.event_cooldown_minutes,
            )
            result = study.run(
                bars,
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
                horizon=args.horizon,
            )
            periods.append(
                {
                    "period": raw,
                    **result,
                }
            )

        scenario_results.append(
            {
                "scenario": name,
                "spread_bps": str(spread_bps),
                "slippage_bps_per_side": str(slippage_bps),
                "periods": periods,
                "aggregate_by_family": aggregate_family_periods(
                    periods,
                    horizon=args.horizon,
                ),
            }
        )

    gate = robust_edge_gate(
        scenario_results,
        min_events=args.min_events,
        min_positive_periods=args.min_positive_periods,
        min_profit_factor=Decimal(args.min_profit_factor),
    )

    return {
        "status": "research_only",
        "candidate_symbols": list(args.candidate_symbols),
        "periods": list(args.period),
        "horizon_minutes": args.horizon,
        "event_cooldown_minutes": args.event_cooldown_minutes,
        "scenario_results": scenario_results,
        "robust_edge_gate": gate,
        "live_configuration_changed": False,
        "promotion_authorized": False,
        "notes": [
            "Signal families are generated independently of the production BUY signal.",
            "The same historical bars are reused across cost scenarios; only friction changes.",
            "No passing family is eligible for live use until a separate untouched holdout and forward shadow sample pass.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ANEVUM's research-only multi-family edge discovery lab."
    )
    parser.add_argument(
        "--period",
        action="append",
        required=True,
        help="START:END using YYYY-MM-DD:YYYY-MM-DD; repeat for multiple development windows.",
    )
    parser.add_argument(
        "--candidate-symbols",
        type=parse_symbols,
        default=parse_symbols("AAPL,MSFT,SMCI,TQQQ,SOXL"),
        help="Comma-separated candidate universe for the discovery run.",
    )
    parser.add_argument("--horizon", type=int, default=15)
    parser.add_argument("--event-cooldown-minutes", type=int, default=15)
    parser.add_argument("--min-events", type=int, default=60)
    parser.add_argument("--min-positive-periods", type=int, default=2)
    parser.add_argument("--min-profit-factor", default="1.20")
    parser.add_argument(
        "--cost-scenario",
        action="append",
        type=parse_cost_scenario,
        default=[],
        help="NAME:SPREAD_BPS:SLIPPAGE_BPS; repeat to override defaults.",
    )
    parser.add_argument("--output", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = asyncio.run(run(args))
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
