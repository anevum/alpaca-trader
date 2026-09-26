from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import get_settings
from .entry_feature_study import (\n    DEFAULT_HORIZONS,\n    EntryFeatureStudy,\n    stable_rule_scan,\n)
from .market_data import MarketDataClient
from .strategy_lab import build_strategy
from .strategy_lab_runner import fetch_period, parse_period


def parse_horizons(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        value = int(piece)
        if value <= 0:
            raise ValueError("horizons must be positive minute counts")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("at least one horizon is required")
    return tuple(values)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    strategy = build_strategy(settings)
    study = EntryFeatureStudy(settings, strategy)
    market_data = MarketDataClient(settings)

    symbols = list(
        dict.fromkeys(
            [*settings.scan_symbols, *settings.confirmation_symbols]
        )
    )

    periods: list[dict[str, Any]] = []
    for raw in args.period:
        start, end = parse_period(raw)
        bars = await fetch_period(
            market_data,
            symbols,
            start,
            end,
        )
        result = study.run(
            bars,
            spread_bps=Decimal(args.spread_bps),
            slippage_bps=Decimal(args.slippage_bps),
            horizons=args.horizons,
        )
        periods.append(
            {
                "period": raw,
                **result,
            }
        )

    return {
        "status": "research_only",
        "strategy": settings.strategy_name,
        "scan_symbols": list(settings.scan_symbols),
        "confirmation_symbols": list(settings.confirmation_symbols),
        "period_count": len(periods),
        "periods": periods,
        "promotion_authorized": False,
        "notes": [
            "This study labels entry opportunities; it does not change live strategy settings.",
            "Future bars are used only for forward-excursion labels, never decision features.",
            "Threshold selection should use development periods and be judged on untouched holdouts.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect decision-time Strategy 004 entry features and label them "
            "with future 1/3/5/10/15-minute excursion."
        )
    )
    parser.add_argument(
        "--period",
        action="append",
        required=True,
        help="START:END using YYYY-MM-DD:YYYY-MM-DD; repeat for multiple periods",
    )
    parser.add_argument(
        "--spread-bps",
        default="5",
        help="Assumed full bid/ask spread in basis points",
    )
    parser.add_argument(
        "--slippage-bps",
        default="2",
        help="Assumed slippage per side in basis points",
    )
    parser.add_argument(
        "--horizons",
        type=parse_horizons,
        default=DEFAULT_HORIZONS,
        help="Comma-separated forward horizons in minutes, default 1,3,5,10,15",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path for JSON output; stdout is always printed",
    )
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
