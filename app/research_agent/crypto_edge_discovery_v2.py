from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from ..crypto_layer import CryptoRollingMomentumStrategy
from ..velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from .crypto_edge_discovery import (
    CompressionBreakoutStrategy,
    CryptoResearchReplayEngine,
    RelativeStrengthImpulseStrategy,
    VolatilityNormalizedTrendStrategy,
    _corpus_bounds,
    _expectancy,
    _moving_block_null_pvalue,
    _positive_fraction,
    _returns,
    _slice_bars,
    _tail_loss,
    _time_of_week_stability,
    _folds,
)
from .dependence import diagnose_cross_candidate, diagnose_path
from .multiplicity import benjamini_yekutieli


METHODOLOGY_VERSION = "graen-crypto-edge-discovery-v2"
GENERATION_SOURCE = "graen-crypto-edge-discovery-v1:NO_EDGE:2026-09-30"
DEFAULT_ALPHA = 0.05

LOW_SPREAD_BPS = {
    "BTC/USD": Decimal("3"),
    "ETH/USD": Decimal("3"),
    "SOL/USD": Decimal("9"),
}
BASE_SPREAD_BPS = {
    "BTC/USD": Decimal("4"),
    "ETH/USD": Decimal("4"),
    "SOL/USD": Decimal("13"),
}
HIGH_SPREAD_BPS = {
    "BTC/USD": Decimal("5"),
    "ETH/USD": Decimal("5"),
    "SOL/USD": Decimal("20"),
}
LOW_SLIPPAGE_BPS = {
    "BTC/USD": Decimal("1"),
    "ETH/USD": Decimal("1"),
    "SOL/USD": Decimal("2"),
}
BASE_SLIPPAGE_BPS = {
    "BTC/USD": Decimal("2"),
    "ETH/USD": Decimal("2"),
    "SOL/USD": Decimal("3"),
}
HIGH_SLIPPAGE_BPS = {
    "BTC/USD": Decimal("5"),
    "ETH/USD": Decimal("5"),
    "SOL/USD": Decimal("7.5"),
}


@dataclass(frozen=True, slots=True)
class V2CandidateSpec:
    candidate_id: str
    family: str
    parameters: dict[str, Any]
    stop_pct: Decimal
    target_pct: Decimal
    max_hold_minutes: int
    complexity_units: int

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["stop_pct"] = str(self.stop_pct)
        row["target_pct"] = str(self.target_pct)
        return row


def candidate_specs_v2() -> tuple[V2CandidateSpec, ...]:
    return (
        V2CandidateSpec(
            "MAJOR_BENCHMARK_60",
            "rolling_momentum_vwap",
            {},
            Decimal("0.0035"),
            Decimal("0.0050"),
            60,
            0,
        ),
        V2CandidateSpec(
            "VNT_MEDIUM_Z075_W8",
            "volatility_normalized_trend",
            {"z_threshold": 0.75, "momentum_window": 8},
            Decimal("0.0050"),
            Decimal("0.0080"),
            120,
            2,
        ),
        V2CandidateSpec(
            "VNT_LONG_Z125_W12",
            "volatility_normalized_trend",
            {"z_threshold": 1.25, "momentum_window": 12},
            Decimal("0.0075"),
            Decimal("0.0120"),
            240,
            3,
        ),
        V2CandidateSpec(
            "CB_MEDIUM_R060_B004",
            "compression_breakout",
            {
                "short_vol_window": 8,
                "long_vol_window": 30,
                "compression_ratio": 0.60,
                "breakout_window": 12,
                "breakout_buffer": "0.0004",
            },
            Decimal("0.0050"),
            Decimal("0.0080"),
            120,
            3,
        ),
        V2CandidateSpec(
            "CB_LONG_R045_B007",
            "compression_breakout",
            {
                "short_vol_window": 8,
                "long_vol_window": 40,
                "compression_ratio": 0.45,
                "breakout_window": 18,
                "breakout_buffer": "0.0007",
            },
            Decimal("0.0075"),
            Decimal("0.0120"),
            240,
            4,
        ),
        V2CandidateSpec(
            "RS_MEDIUM_W8_T0010",
            "relative_strength_impulse",
            {
                "relative_window": 8,
                "relative_threshold": "0.0010",
                "z_threshold": 0.75,
            },
            Decimal("0.0050"),
            Decimal("0.0080"),
            120,
            3,
        ),
        V2CandidateSpec(
            "RS_LONG_W15_T0020",
            "relative_strength_impulse",
            {
                "relative_window": 15,
                "relative_threshold": "0.0020",
                "z_threshold": 1.0,
            },
            Decimal("0.0075"),
            Decimal("0.0120"),
            240,
            4,
        ),
    )


def _cost_payload(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


def _settings_for_spec(settings: Any, spec: V2CandidateSpec) -> Any:
    return settings.model_copy(
        update={
            "stop_pct": spec.stop_pct,
            "target_pct": spec.target_pct,
            "max_hold_minutes": spec.max_hold_minutes,
        }
    )


def _strategy_kwargs(settings: Any, spec: V2CandidateSpec) -> dict[str, Any]:
    return {
        "fast_window": settings.fast_window,
        "slow_window": settings.slow_window,
        "min_momentum_pct": settings.min_momentum_pct,
        "min_vwap_edge_pct": settings.min_vwap_edge_pct,
        "stop_pct": settings.stop_pct,
        "target_pct": settings.target_pct,
        "entry_start": settings.entry_start,
        "entry_cutoff": settings.entry_cutoff,
        "confirmation_symbols": settings.confirmation_symbols,
        "min_confirmations": settings.min_confirmations,
        "regime_window": settings.regime_window,
        "regime_min_confirmations": settings.regime_min_confirmations,
        "regime_min_return_pct": settings.regime_min_return_pct,
        "max_vwap_extension_pct": settings.max_vwap_extension_pct,
        "volatility_stop_enabled": settings.volatility_stop_enabled,
        "volatility_stop_multiplier": settings.volatility_stop_multiplier,
        "volatility_stop_lookback_bars": settings.volatility_stop_lookback_bars,
        "max_dynamic_stop_pct": max(
            settings.max_dynamic_stop_pct,
            spec.stop_pct,
        ),
        "strategy_version_id": f"CRYPTO-RESEARCH-EDGEV2-{spec.candidate_id}",
        "model_version": f"crypto-edge-v2-{spec.candidate_id.lower()}",
        "calibration_version": "crypto-calibration-unvalidated-v1",
        "calibration_promoted": False,
        "regime_version": "nostra-crypto-regime-v1",
        "execution_adapter_version": "velum-replay-only",
        "feature_volatility_lookback": settings.crypto_volatility_lookback_bars,
    }


def _build_strategy(spec: V2CandidateSpec, settings: Any) -> tuple[Any, Any]:
    candidate_settings = _settings_for_spec(settings, spec)
    kwargs = _strategy_kwargs(candidate_settings, spec)
    if spec.family == "rolling_momentum_vwap":
        return candidate_settings, CryptoRollingMomentumStrategy(**kwargs)
    if spec.family == "volatility_normalized_trend":
        return candidate_settings, VolatilityNormalizedTrendStrategy(
            **kwargs,
            **spec.parameters,
        )
    if spec.family == "compression_breakout":
        params = dict(spec.parameters)
        params["breakout_buffer"] = Decimal(str(params["breakout_buffer"]))
        return candidate_settings, CompressionBreakoutStrategy(
            **kwargs,
            **params,
        )
    if spec.family == "relative_strength_impulse":
        params = dict(spec.parameters)
        params["relative_threshold"] = Decimal(str(params["relative_threshold"]))
        return candidate_settings, RelativeStrengthImpulseStrategy(
            **kwargs,
            **params,
        )
    raise ValueError(f"unsupported v2 crypto edge family: {spec.family}")


def _engine(spec: V2CandidateSpec, settings: Any):
    candidate_settings, strategy = _build_strategy(spec, settings)
    engine_cls = (
        ContinuousReplayEngine
        if spec.family == "rolling_momentum_vwap"
        else CryptoResearchReplayEngine
    )
    return engine_cls(candidate_settings, strategy)


def _run(
    spec: V2CandidateSpec,
    settings: Any,
    bars: dict[str, list[dict[str, Any]]],
    *,
    initial_equity: Decimal,
    spread_bps: Mapping[str, Decimal],
    slippage_bps: Mapping[str, Decimal],
) -> dict[str, Any]:
    return _engine(spec, settings).run(
        bars,
        initial_equity=initial_equity,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
    )


def run_crypto_edge_discovery_v2(
    *,
    settings: Any,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    initial_equity: Decimal,
    min_train_days: int = 10,
    validation_days: int = 5,
    holdout_days: int = 5,
    minimum_validation_trades: int = 20,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    majors = {"BTC/USD", "ETH/USD", "SOL/USD"}
    bars = {
        symbol: rows
        for symbol, rows in bars_by_symbol.items()
        if symbol.upper() in majors
    }
    bounds = _corpus_bounds(bars)
    if bounds is None:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "no majors crypto historical bars",
            "promotion_authority": False,
        }

    start, end = bounds
    corpus_days = (end - start).total_seconds() / 86400.0
    minimum_days = min_train_days + validation_days * 2 + holdout_days
    if corpus_days < minimum_days:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "historical corpus is too short for v2 frozen contract",
            "corpus_days": corpus_days,
            "minimum_days": minimum_days,
            "promotion_authority": False,
        }

    folds, holdout_start = _folds(
        start=start,
        end=end,
        min_train_days=min_train_days,
        validation_days=validation_days,
        holdout_days=holdout_days,
    )
    if len(folds) < 2:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "fewer than two v2 validation folds",
            "promotion_authority": False,
        }

    specs = candidate_specs_v2()
    candidate_rows: list[dict[str, Any]] = []
    fold_paths: list[list[float]] = []
    p_values: list[float] = []

    for candidate_index, spec in enumerate(specs):
        validation_returns: list[float] = []
        fold_expectancies: list[float] = []
        fold_rows: list[dict[str, Any]] = []
        for fold in folds:
            validation_bars = _slice_bars(
                bars,
                fold.validation_start,
                fold.validation_end,
            )
            run = _run(
                spec,
                settings,
                validation_bars,
                initial_equity=initial_equity,
                spread_bps=HIGH_SPREAD_BPS,
                slippage_bps=HIGH_SLIPPAGE_BPS,
            )
            returns = _returns(run["trades"])
            expectancy = _expectancy(returns)
            validation_returns.extend(returns)
            fold_expectancies.append(expectancy)
            fold_rows.append({
                "fold_id": fold.fold_id,
                "validation_start": fold.validation_start.isoformat(),
                "validation_end": fold.validation_end.isoformat(),
                "trade_count": len(returns),
                "expectancy_return": expectancy,
                "return_pct": run["summary"]["return_pct"],
                "profit_factor": run["summary"]["profit_factor"],
                "max_drawdown_pct": run["summary"]["max_drawdown_pct"],
            })

        p_state = _moving_block_null_pvalue(
            validation_returns,
            seed=2000 + candidate_index,
        )
        dependence = diagnose_path([
            max(-1.0, min(1.0, value))
            for value in validation_returns
        ]).to_dict()
        expectancy = _expectancy(validation_returns)
        complexity_penalty = spec.complexity_units * 0.00001
        row = {
            "candidate": spec.to_dict(),
            "validation": {
                "trade_count": len(validation_returns),
                "expectancy_return": expectancy,
                "positive_fold_fraction": _positive_fraction(fold_expectancies),
                "fold_expectancies": fold_expectancies,
                "folds": fold_rows,
            },
            "null_test": p_state,
            "dependence": dependence,
            "complexity_penalty": complexity_penalty,
            "selection_score": expectancy - complexity_penalty,
            "multiplicity_rejected": False,
            "eligible_for_holdout": False,
        }
        candidate_rows.append(row)
        fold_paths.append(fold_expectancies)
        p_values.append(float(p_state["p_value"]))

    multiplicity = benjamini_yekutieli(p_values, alpha=alpha)
    rejected = set(multiplicity.rejected_indices)
    for index, row in enumerate(candidate_rows):
        validation = row["validation"]
        row["multiplicity_rejected"] = index in rejected
        row["eligible_for_holdout"] = bool(
            index in rejected
            and validation["trade_count"] >= minimum_validation_trades
            and validation["expectancy_return"] > 0
            and validation["positive_fold_fraction"] >= (2.0 / 3.0)
        )

    eligible = [
        row for row in candidate_rows
        if row["eligible_for_holdout"]
    ]
    eligible.sort(
        key=lambda row: (
            float(row["selection_score"]),
            float(row["validation"]["expectancy_return"]),
            row["candidate"]["candidate_id"],
        ),
        reverse=True,
    )
    winner = eligible[0] if eligible else None
    cross_dependence = diagnose_cross_candidate(fold_paths).to_dict()

    holdout = None
    holdout_passed = False
    if winner is not None:
        spec = next(
            item for item in specs
            if item.candidate_id == winner["candidate"]["candidate_id"]
        )
        holdout_bars = _slice_bars(bars, holdout_start, end)
        scenarios = {
            "low": (LOW_SPREAD_BPS, LOW_SLIPPAGE_BPS),
            "base": (BASE_SPREAD_BPS, BASE_SLIPPAGE_BPS),
            "high": (HIGH_SPREAD_BPS, HIGH_SLIPPAGE_BPS),
        }
        scenario_rows: dict[str, Any] = {}
        high_trades: list[dict[str, Any]] = []
        for label, (spread, slippage) in scenarios.items():
            run = _run(
                spec,
                settings,
                holdout_bars,
                initial_equity=initial_equity,
                spread_bps=spread,
                slippage_bps=slippage,
            )
            returns = _returns(run["trades"])
            scenario_rows[label] = {
                "summary": run["summary"],
                "assumptions": run["assumptions"],
                "expectancy_return": _expectancy(returns),
                "trade_count": len(returns),
                "tail_loss_05": _tail_loss(returns),
                "time_of_week_stability": _time_of_week_stability(run["trades"]),
            }
            if label == "high":
                high_trades = run["trades"]
        bootstrap = bootstrap_trade_distribution(
            high_trades,
            paths=1000,
            seed=29901,
        )
        high = scenario_rows["high"]
        holdout_passed = bool(
            high["trade_count"] >= minimum_validation_trades
            and high["expectancy_return"] > 0
            and bootstrap["p05_net_pnl"] > 0
        )
        holdout = {
            "candidate_id": spec.candidate_id,
            "start": holdout_start.isoformat(),
            "end": end.isoformat(),
            "cost_scenarios": scenario_rows,
            "high_cost_bootstrap": bootstrap,
            "passed": holdout_passed,
        }

    no_lookahead = all(
        fold.train_end <= fold.validation_start
        and fold.validation_end <= holdout_start
        for fold in folds
    )
    status = (
        "HOLDOUT_PASS"
        if holdout_passed
        else "VALIDATION_EDGE_FOUND"
        if winner is not None
        else "NO_EDGE"
    )
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "generation_source": GENERATION_SOURCE,
        "status": status,
        "market_lane": "crypto",
        "research_only": True,
        "promotion_authority": False,
        "live_configuration_changed": False,
        "benchmark_strategy_version_id": settings.crypto_strategy_version_id,
        "universe_policy": {
            "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
            "reason": "v1 plus live spread telemetry showed majors have materially lower execution friction",
            "v1_wide_spread_alts_excluded": True,
        },
        "cost_model": {
            "source": "frozen live quote spread telemetry snapshot before v2 evaluation",
            "low_spread_bps": _cost_payload(LOW_SPREAD_BPS),
            "base_spread_bps": _cost_payload(BASE_SPREAD_BPS),
            "high_spread_bps": _cost_payload(HIGH_SPREAD_BPS),
            "low_slippage_bps_per_side": _cost_payload(LOW_SLIPPAGE_BPS),
            "base_slippage_bps_per_side": _cost_payload(BASE_SLIPPAGE_BPS),
            "high_slippage_bps_per_side": _cost_payload(HIGH_SLIPPAGE_BPS),
            "high_spread_max_bps": str(max(HIGH_SPREAD_BPS.values())),
            "high_slippage_max_bps_per_side": str(max(HIGH_SLIPPAGE_BPS.values())),
        },
        "corpus": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": corpus_days,
            "symbols": sorted(bars),
            "bar_counts": {
                symbol: len(rows)
                for symbol, rows in sorted(bars.items())
            },
            "nonoverlap_with_v1_evaluation": end <= datetime(
                2026, 8, 31, 1, 53, tzinfo=timezone.utc
            ),
        },
        "contract": {
            "candidate_set_frozen_before_validation": True,
            "candidate_generation_informed_by_v1": True,
            "v2_evaluation_corpus_must_not_overlap_v1": True,
            "selection_population": "validation_folds_only",
            "holdout_opened_only_after_multiplicity_adjusted_selection": True,
            "cost_selection_rule": "symbol_specific_high_cost_only",
            "minimum_validation_trades": minimum_validation_trades,
            "alpha": alpha,
            "complexity_penalty_per_unit": 0.00001,
        },
        "folds": [fold.to_dict() for fold in folds],
        "holdout_range": {
            "start": holdout_start.isoformat(),
            "end": end.isoformat(),
        },
        "candidates": candidate_rows,
        "multiplicity": multiplicity.to_dict(),
        "cross_candidate_dependence": cross_dependence,
        "selected_candidate": winner["candidate"] if winner else None,
        "selected_validation": winner["validation"] if winner else None,
        "holdout": holdout,
        "walk_forward_passed": winner is not None,
        "holdout_passed": holdout_passed,
        "dependence_adjusted": True,
        "dependence_method": "moving_block_bootstrap + BY arbitrary-dependence correction",
        "multiplicity_adjusted": True,
        "multiplicity_method": "benjamini_yekutieli",
        "no_lookahead_verified": no_lookahead,
        "winner_selected": winner is not None,
    }
