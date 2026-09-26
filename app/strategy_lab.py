from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Callable
from typing import Any

from .config import Settings
from .replay import ReplayEngine
from .strategy import OpeningRangeVwapStrategy, RollingMomentumVwapStrategy, Signal
from .strategy_004_candidate_c import strategy_004_candidate_c_gate


@dataclass(frozen=True)
class StrategyVariant:
    name: str
    description: str
    overrides: dict[str, Any]
    entry_gate: Callable[[Signal], Any] | None = None


DEFAULT_VARIANTS: tuple[StrategyVariant, ...] = (
    StrategyVariant(
        name="production",
        description="Current live rolling momentum/VWAP parameters.",
        overrides={},
    ),
    StrategyVariant(
        name="strategy_005_candidate_d_delay_open",
        description=(
            "Frozen Candidate D: keep all production logic unchanged but "
            "delay new entries until 10:30 ET. Research only until holdout "
            "and forward-shadow validation pass."
        ),
        overrides={"entry_start_raw": "10:30"},
    ),
    StrategyVariant(
        name="strategy_004_candidate_c_controlled",
        description=(
            "Frozen Candidate C: controlled continuation. Preserve production "
            "entry floors but reject 3-bar momentum above 0.29% and trend "
            "persistence above 5/8. Research only until holdout and shadow pass."
        ),
        overrides={},
        entry_gate=strategy_004_candidate_c_gate,
    ),
    StrategyVariant(
        name="fast_2_6",
        description="Faster 2/6 momentum response with a shorter hold and slightly smaller target.",
        overrides={
            "fast_window": 2,
            "slow_window": 6,
            "min_momentum_pct": Decimal("0.0004"),
            "min_vwap_edge_pct": Decimal("0"),
            "stop_pct": Decimal("0.0035"),
            "target_pct": Decimal("0.0045"),
            "max_hold_minutes": 10,
        },
    ),
    StrategyVariant(
        name="balanced_4_10",
        description="Moderately slower 4/10 trend filter with wider stop/target and 20-minute hold.",
        overrides={
            "fast_window": 4,
            "slow_window": 10,
            "min_momentum_pct": Decimal("0.0006"),
            "min_vwap_edge_pct": Decimal("0.0002"),
            "stop_pct": Decimal("0.0040"),
            "target_pct": Decimal("0.0060"),
            "max_hold_minutes": 20,
        },
    ),
    StrategyVariant(
        name="selective_3_10",
        description="Higher momentum/VWAP threshold intended to trade less often but demand stronger setups.",
        overrides={
            "fast_window": 3,
            "slow_window": 10,
            "min_momentum_pct": Decimal("0.0008"),
            "min_vwap_edge_pct": Decimal("0.0005"),
            "stop_pct": Decimal("0.0035"),
            "target_pct": Decimal("0.0060"),
            "max_hold_minutes": 15,
        },
    ),
    StrategyVariant(
        name="slow_5_15",
        description="Slower 5/15 trend filter with longer hold and wider stop/target.",
        overrides={
            "fast_window": 5,
            "slow_window": 15,
            "min_momentum_pct": Decimal("0.0007"),
            "min_vwap_edge_pct": Decimal("0.0003"),
            "stop_pct": Decimal("0.0045"),
            "target_pct": Decimal("0.0070"),
            "max_hold_minutes": 25,
        },
    ),
    StrategyVariant(
        name="strategy_004_vwap_edge",
        description=(
            "Candidate A (rejected in pre-September-25 fixed-universe "
            "validation): stronger 0.65%-0.80% VWAP edge with two "
            "independent confirmations. Retained for reproducibility."
        ),
        overrides={
            "min_vwap_edge_pct": Decimal("0.0065"),
            "max_vwap_extension_pct": Decimal("0.0080"),
            "min_confirmations": 2,
            "min_quality_score": Decimal("80"),
        },
    ),
    StrategyVariant(
        name="strategy_004_followthrough",
        description=(
            "Candidate B: preserve the production thresholds but require the "
            "core momentum/VWAP/trend setup to remain valid for two "
            "consecutive completed bars before entry."
        ),
        overrides={
            "signal_persistence_bars": 2,
            "min_quality_score": Decimal("80"),
        },
    ),
)


def build_strategy(settings: Settings):
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
            regime_window=settings.regime_window,
            regime_min_confirmations=settings.regime_min_confirmations,
            regime_min_return_pct=settings.regime_min_return_pct,
            max_vwap_extension_pct=settings.max_vwap_extension_pct,
            signal_persistence_bars=settings.signal_persistence_bars,
            volatility_stop_enabled=settings.volatility_stop_enabled,
            volatility_stop_multiplier=settings.volatility_stop_multiplier,
            volatility_stop_lookback_bars=settings.volatility_stop_lookback_bars,
            max_dynamic_stop_pct=settings.max_dynamic_stop_pct,
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


def variant_settings(base: Settings, variant: StrategyVariant) -> Settings:
    updates = {
        "strategy_name": "rolling_momentum_vwap",
        **variant.overrides,
    }
    return base.model_copy(update=updates)


def _metric(summary: dict[str, Any], key: str) -> Decimal:
    try:
        return Decimal(str(summary.get(key) or "0"))
    except Exception:
        return Decimal("0")


def leaderboard_key(row: dict[str, Any]) -> tuple:
    summary = row["summary"]
    eligible = bool(row.get("eligible_for_ranking"))
    expectancy = _metric(summary, "expectancy_per_trade")
    profit_factor_raw = summary.get("profit_factor")
    profit_factor = (
        Decimal(str(profit_factor_raw))
        if profit_factor_raw is not None
        else (Decimal("999") if _metric(summary, "gross_loss") == 0 and _metric(summary, "gross_profit") > 0 else Decimal("0"))
    )
    return_pct = _metric(summary, "return_pct")
    drawdown = _metric(summary, "max_drawdown_pct")
    trades = int(summary.get("trades") or 0)
    return (
        int(eligible),
        int(expectancy > 0),
        min(profit_factor, Decimal("5")),
        return_pct,
        -drawdown,
        trades,
    )


def rank_results(
    results: list[dict[str, Any]],
    *,
    min_trades: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        summary = result["summary"]
        trades = int(summary.get("trades") or 0)
        row = dict(result)
        row["eligible_for_ranking"] = trades >= min_trades
        row["sample_note"] = (
            ""
            if trades >= min_trades
            else f"sample below minimum: {trades}/{min_trades} trades"
        )
        rows.append(row)

    ranked = sorted(rows, key=leaderboard_key, reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


class StrategyTournament:
    """Runs strategy variants over identical bars with no broker dependency."""

    def __init__(
        self,
        base_settings: Settings,
        variants: tuple[StrategyVariant, ...] = DEFAULT_VARIANTS,
    ):
        self.base_settings = base_settings
        self.variants = variants

    def run_bars(
        self,
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        *,
        initial_equity: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        min_trades: int = 20,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for variant in self.variants:
            settings = variant_settings(self.base_settings, variant)
            strategy = build_strategy(settings)
            replay = ReplayEngine(
                settings,
                strategy,
                entry_gate=variant.entry_gate,
            )
            result = replay.run(
                bars_by_symbol,
                initial_equity=initial_equity,
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
            )
            results.append(
                {
                    "variant": variant.name,
                    "description": variant.description,
                    "parameters": {
                        "fast_window": settings.fast_window,
                        "slow_window": settings.slow_window,
                        "min_momentum_pct": str(settings.min_momentum_pct),
                        "min_vwap_edge_pct": str(settings.min_vwap_edge_pct),
                        "max_vwap_extension_pct": str(settings.max_vwap_extension_pct),
                        "signal_persistence_bars": settings.signal_persistence_bars,
                        "min_confirmations": settings.min_confirmations,
                        "min_quality_score": str(settings.min_quality_score),
                        "stop_pct": str(settings.stop_pct),
                        "target_pct": str(settings.target_pct),
                        "max_hold_minutes": settings.max_hold_minutes,
                        "research_entry_gate": bool(variant.entry_gate),
                    },
                    "summary": result["summary"],
                    "assumptions": result["assumptions"],
                }
            )

        leaderboard = rank_results(results, min_trades=min_trades)
        return {
            "leaderboard": leaderboard,
            "min_trades_for_ranking": min_trades,
            "variant_count": len(leaderboard),
            "notes": [
                "All variants receive the same historical bars and friction assumptions.",
                "Ranking is descriptive research output, not authorization to change live parameters.",
                "Variants below the minimum trade sample remain visible but are marked ineligible.",
                "A promotion decision should also require out-of-sample and shadow validation.",
            ],
        }
