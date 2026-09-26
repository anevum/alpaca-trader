from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import get_settings
from .edge_corpus import (
    CorpusManifest,
    fetch_or_load_window,
    load_manifest,
    manifest_sha256,
    windows_for_roles,
)
from .edge_discovery import (
    EdgeDiscoveryStudy,
    aggregate_family_periods,
)
from .edge_discovery_runner import DEFAULT_COST_SCENARIOS, parse_cost_scenario
from .edge_elimination import (
    development_elimination,
    holdout_elimination,
    research_outcome,
    validation_elimination,
)
from .market_data import MarketDataClient


MIN_SHARED_PANEL_RATIO = Decimal("0.80")


def _shared_panel(
    payloads: list[dict[str, Any]],
    manifest: CorpusManifest,
    *,
    required_panel: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    panel = tuple(required_panel or manifest.candidate_symbols)
    eligible_sets = [
        set((payload.get("coverage") or {}).get("eligible_candidate_symbols") or [])
        for payload in payloads
    ]
    if not eligible_sets:
        return ()
    shared = set(panel)
    for eligible in eligible_sets:
        shared &= eligible
    return tuple(symbol for symbol in panel if symbol in shared)


def _panel_integrity(
    panel: tuple[str, ...],
    expected: tuple[str, ...],
) -> dict[str, Any]:
    ratio = (
        Decimal(len(panel)) / Decimal(len(expected))
        if expected else Decimal("0")
    )
    return {
        "expected_symbols": list(expected),
        "shared_symbols": list(panel),
        "expected_count": len(expected),
        "shared_count": len(panel),
        "shared_ratio": str(ratio),
        "passed": ratio >= MIN_SHARED_PANEL_RATIO,
        "minimum_shared_ratio": str(MIN_SHARED_PANEL_RATIO),
    }


def _bars_for_panel(
    payload: dict[str, Any],
    panel: tuple[str, ...],
    confirmation_symbols: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    bars = payload.get("bars") or {}
    symbols = list(dict.fromkeys([*panel, *confirmation_symbols]))
    return {
        symbol: list(bars.get(symbol, []))
        for symbol in symbols
    }


def _scenario_stage(
    payloads: list[dict[str, Any]],
    *,
    settings,
    panel: tuple[str, ...],
    confirmation_symbols: tuple[str, ...],
    scenarios: list[tuple[str, Decimal, Decimal]],
    horizon: int,
    event_cooldown_minutes: int,
) -> list[dict[str, Any]]:
    scenario_results = []
    for name, spread_bps, slippage_bps in scenarios:
        periods = []
        for payload in payloads:
            bars = _bars_for_panel(
                payload,
                panel,
                confirmation_symbols,
            )
            result = EdgeDiscoveryStudy(
                settings,
                panel,
                event_cooldown_minutes=event_cooldown_minutes,
            ).run(
                bars,
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
                horizon=horizon,
            )
            window = payload["window"]
            periods.append(
                {
                    "period": window["id"],
                    "range": {
                        "start": window["start"],
                        "end": window["end"],
                    },
                    "role": window["role"],
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
                    horizon=horizon,
                ),
            }
        )
    return scenario_results


async def _load_role(
    market_data: MarketDataClient,
    manifest: CorpusManifest,
    role: str,
    *,
    cache_dir: str,
    refresh: bool,
) -> list[dict[str, Any]]:
    payloads = []
    for window in windows_for_roles(manifest, {role}):
        payloads.append(
            await fetch_or_load_window(
                market_data,
                manifest,
                window,
                cache_dir=cache_dir,
                refresh=refresh,
            )
        )
    return payloads


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    manifest = load_manifest(args.manifest)
    if settings.data_feed != manifest.data_feed:
        raise ValueError(
            f"runtime DATA_FEED={settings.data_feed} does not match "
            f"corpus feed {manifest.data_feed}"
        )
    if settings.bar_timeframe != manifest.timeframe:
        raise ValueError(
            f"runtime BAR_TIMEFRAME={settings.bar_timeframe} does not match "
            f"corpus timeframe {manifest.timeframe}"
        )

    scenarios = args.cost_scenario or list(DEFAULT_COST_SCENARIOS)
    market_data = MarketDataClient(settings)

    development_payloads = await _load_role(
        market_data,
        manifest,
        "development",
        cache_dir=args.cache_dir,
        refresh=args.refresh,
    )
    development_panel = _shared_panel(
        development_payloads,
        manifest,
    )
    development_integrity = _panel_integrity(
        development_panel,
        manifest.candidate_symbols,
    )
    if not development_integrity["passed"]:
        raise RuntimeError(
            "development corpus shared-symbol coverage is below the "
            f"{MIN_SHARED_PANEL_RATIO} minimum"
        )

    development_scenarios = _scenario_stage(
        development_payloads,
        settings=settings,
        panel=development_panel,
        confirmation_symbols=manifest.confirmation_symbols,
        scenarios=scenarios,
        horizon=args.horizon,
        event_cooldown_minutes=args.event_cooldown_minutes,
    )
    development_gate = development_elimination(
        development_scenarios,
    )

    report: dict[str, Any] = {
        "status": "research_only",
        "corpus": {
            "version": manifest.version,
            "manifest_sha256": manifest_sha256(manifest),
            "manifest_path": args.manifest,
            "data_feed": manifest.data_feed,
            "timeframe": manifest.timeframe,
            "candidate_panel_count": len(manifest.candidate_symbols),
            "confirmation_symbols": list(manifest.confirmation_symbols),
        },
        "research_settings": {
            "horizon_minutes": args.horizon,
            "event_cooldown_minutes": args.event_cooldown_minutes,
            "cost_scenarios": [
                {
                    "name": name,
                    "spread_bps": str(spread),
                    "slippage_bps_per_side": str(slippage),
                }
                for name, spread, slippage in scenarios
            ],
        },
        "development": {
            "panel_integrity": development_integrity,
            "windows": [
                {
                    **payload["window"],
                    "coverage": payload["coverage"],
                    "cache": payload.get("cache"),
                }
                for payload in development_payloads
            ],
            "scenario_results": development_scenarios,
            "elimination": development_gate,
        },
        "validation": None,
        "holdout": None,
        "live_configuration_changed": False,
        "promotion_authorized": False,
        "capital_scaling_allowed": False,
    }

    if not development_gate["survivors"]:
        report["outcome"] = research_outcome(development_gate)
        return report

    frozen_families = list(development_gate["survivors"])
    report["frozen_after_development"] = {
        "families": frozen_families,
        "manifest_sha256": manifest_sha256(manifest),
        "panel": list(development_panel),
        "rule": (
            "No family thresholds, symbol membership, horizon, cost scenarios "
            "or stage gates may change after this point for this corpus version."
        ),
    }

    validation_payloads = await _load_role(
        market_data,
        manifest,
        "validation",
        cache_dir=args.cache_dir,
        refresh=args.refresh,
    )
    validation_panel = _shared_panel(
        validation_payloads,
        manifest,
        required_panel=development_panel,
    )
    validation_integrity = _panel_integrity(
        validation_panel,
        development_panel,
    )
    if not validation_integrity["passed"]:
        raise RuntimeError(
            "validation corpus does not preserve at least 80% of the frozen "
            "development panel"
        )

    validation_scenarios = _scenario_stage(
        validation_payloads,
        settings=settings,
        panel=validation_panel,
        confirmation_symbols=manifest.confirmation_symbols,
        scenarios=scenarios,
        horizon=args.horizon,
        event_cooldown_minutes=args.event_cooldown_minutes,
    )
    validation_gate = validation_elimination(
        validation_scenarios,
        frozen_families=frozen_families,
    )
    report["validation"] = {
        "panel_integrity": validation_integrity,
        "windows": [
            {
                **payload["window"],
                "coverage": payload["coverage"],
                "cache": payload.get("cache"),
            }
            for payload in validation_payloads
        ],
        "scenario_results": validation_scenarios,
        "elimination": validation_gate,
    }

    if not validation_gate["survivors"]:
        report["outcome"] = research_outcome(
            development_gate,
            validation_gate,
        )
        return report

    validation_survivors = list(validation_gate["survivors"])
    report["frozen_before_holdout"] = {
        "families": validation_survivors,
        "manifest_sha256": manifest_sha256(manifest),
        "panel": list(validation_panel),
        "holdout_opened": True,
        "rule": "Holdout results may reject a family but may not tune it.",
    }

    holdout_payloads = await _load_role(
        market_data,
        manifest,
        "holdout",
        cache_dir=args.cache_dir,
        refresh=args.refresh,
    )
    holdout_panel = _shared_panel(
        holdout_payloads,
        manifest,
        required_panel=validation_panel,
    )
    holdout_integrity = _panel_integrity(
        holdout_panel,
        validation_panel,
    )
    if not holdout_integrity["passed"]:
        raise RuntimeError(
            "holdout corpus does not preserve at least 80% of the frozen "
            "validation panel"
        )

    holdout_scenarios = _scenario_stage(
        holdout_payloads,
        settings=settings,
        panel=holdout_panel,
        confirmation_symbols=manifest.confirmation_symbols,
        scenarios=scenarios,
        horizon=args.horizon,
        event_cooldown_minutes=args.event_cooldown_minutes,
    )
    holdout_gate = holdout_elimination(
        holdout_scenarios,
        frozen_families=validation_survivors,
    )
    report["holdout"] = {
        "panel_integrity": holdout_integrity,
        "windows": [
            {
                **payload["window"],
                "coverage": payload["coverage"],
                "cache": payload.get("cache"),
            }
            for payload in holdout_payloads
        ],
        "scenario_results": holdout_scenarios,
        "elimination": holdout_gate,
    }
    report["outcome"] = research_outcome(
        development_gate,
        validation_gate,
        holdout_gate,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the versioned ANEVUM historical edge corpus with staged "
            "development, validation and unopened-until-needed holdout gates."
        )
    )
    parser.add_argument(
        "--manifest",
        default="research/edge-corpus-v1.json",
    )
    parser.add_argument(
        "--cache-dir",
        default=".edge_corpus",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch cached historical windows.",
    )
    parser.add_argument("--horizon", type=int, default=15)
    parser.add_argument("--event-cooldown-minutes", type=int, default=15)
    parser.add_argument(
        "--cost-scenario",
        action="append",
        type=parse_cost_scenario,
        default=[],
    )
    parser.add_argument("--output", default="edge-corpus-report.json")
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
