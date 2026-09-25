from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from app.config import get_settings
from app.market_data import MarketDataClient
from app.replay import ReplayLab
from app.strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy


def build_strategy(settings):
    if settings.strategy_name == "rolling_momentum_vwap":
        return RollingMomentumVwapStrategy(
            fast_window=settings.fast_window,
            slow_window=settings.slow_window,
            min_momentum_pct=settings.min_momentum_pct,
            min_vwap_edge_pct=settings.min_vwap_edge_pct,
            stop_pct=settings.stop_pct,
            target_pct=settings.target_pct,
            entry_start=settings.entry_start,
            entry_cutoff=settings.entry_cutoff,
            confirmation_symbols=settings.confirmation_symbols,
            min_confirmations=settings.min_confirmations,
        )
    return OpeningRangeVwapStrategy(
        opening_range_minutes=settings.opening_range_minutes,
        max_opening_range_pct=settings.max_opening_range_pct,
        max_breakout_extension_pct=settings.max_breakout_extension_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
    )


async def run(args):
    settings = get_settings()
    market_data = MarketDataClient(settings)
    lab = ReplayLab(settings, market_data, build_strategy(settings))
    result = await lab.run(
        start=args.start,
        end=args.end,
        initial_equity=Decimal(args.initial_equity),
        spread_bps=Decimal(args.spread_bps),
        slippage_bps=Decimal(args.slippage_bps),
    )

    print(json.dumps({
        "range": result["range"],
        "summary": result["summary"],
        "assumptions": result["assumptions"],
        "strategy": result["strategy"],
    }, indent=2))

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run a read-only historical replay of the current ANEVUM strategy."
    )
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--initial-equity", default="100")
    parser.add_argument("--spread-bps", default="5")
    parser.add_argument("--slippage-bps", default="2")
    parser.add_argument("--output", help="Optional full JSON result path")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
