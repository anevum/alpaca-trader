from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import get_settings
from .entry_feature_study import EntryFeatureStudy
from .entry_exit_surface import (
    consistent_surface_cells,
    run_exit_surface,
)
from .market_data import MarketDataClient
from .regime_feature_study import (
    DEFAULT_REGIME_REFERENCES,
    build_regime_report,
)
from .strategy_lab import build_strategy
from .strategy_lab_runner import fetch_period, parse_period


def parse_symbols(raw: str) -> tuple[str, ...]:
    result: list[str] = []
    for piece in raw.split(","):
        symbol = piece.strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    if len(result) < 2:
        raise ValueError("at least two regime reference symbols are required")
    return tuple(result)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = apply_strategy_005_entry_profile(get_settings())
    strategy = build_strategy(settings)
    entry_study = EntryFeatureStudy(settings, strategy)
    market_data = MarketDataClient(settings)

    symbols = list(
        dict.fromkeys(
            [
                *settings.scan_symbols,
                *settings.confirmation_symbols,
                *args.regime_references,
            ]
        )
    )

    periods: list[dict[str, Any]] = []
    exit_surfaces: list[dict[str, Any]] = []
    for raw in args.period:
        start, end = parse_period(raw)
        bars = await fetch_period(
            market_data,
            symbols,
            start,
            end,
        )
        entry_result = entry_study.run(
            bars,
            spread_bps=Decimal(args.spread_bps),
            slippage_bps=Decimal(args.slippage_bps),
        )
        report = build_regime_report(
            entry_result,
            bars,
            reference_symbols=args.regime_references,
            window=args.regime_window,
            interaction_horizon=args.interaction_horizon,
            min_regime_sample=args.min_regime_sample,
        )
        periods.append(
            {
                "period": raw,
                "range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                **report,
            }
        )

    return {
        "status": "research_only",
        "strategy": settings.strategy_name,
        "research_profile": strategy_005_profile_manifest(),
        "research_profile_hash": strategy_005_profile_hash(),
        "scan_symbols": list(settings.scan_symbols),
        "entry_confirmation_symbols": list(settings.confirmation_symbols),
        "regime_reference_symbols": list(args.regime_references),
        "confirmation_reference_separation_explicit": True,
        "periods": periods,
        "exit_surface_consistency": consistent_surface_cells(exit_surfaces),
        "candidate_frozen": False,
        "promotion_authorized": False,
        "scalable_capital_merge_allowed": False,
        "notes": [
            "This phase performs regime-conditioned diagnosis only.",
            "Entry-study settings are locked by a named profile instead of inheriting ambient deployment variables.",
            "No Strategy 005 entry rule is selected automatically.",
            "No Railway variables, live strategy parameters, or exposure limits are changed.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Strategy 005 research: condition entry-feature behavior on "
            "coarse SPY/QQQ/SMH market regimes."
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
        "--regime-references",
        type=parse_symbols,
        default=DEFAULT_REGIME_REFERENCES,
        help="Comma-separated broad-market references; default SPY,QQQ,SMH",
    )
    parser.add_argument(
        "--regime-window",
        type=int,
        default=5,
        help="Completed one-minute bars used for reference return state",
    )
    parser.add_argument(
        "--interaction-horizon",
        type=int,
        default=15,
        help="Forward horizon used for within-regime feature interaction screens",
    )
    parser.add_argument(
        "--min-regime-sample",
        type=int,
        default=20,
        help="Minimum eligible observations required for a regime interaction screen",
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
