from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from ..velum_core import bootstrap_trade_distribution
from .crypto_edge_discovery import (
    _corpus_bounds,
    _expectancy,
    _folds,
    _moving_block_null_pvalue,
    _positive_fraction,
    _returns,
    _slice_bars,
    _tail_loss,
    _time_of_week_stability,
)
from .crypto_edge_discovery_v3 import V3CandidateSpec, _run as _run_v3
from .dependence import diagnose_cross_candidate, diagnose_path
from .multiplicity import benjamini_yekutieli


METHODOLOGY_VERSION = "graen-crypto-edge-discovery-v4"
GENERATION_SOURCE = "graen-crypto-edge-discovery-v3:NO_EDGE:ETH_SYMBOL_DIAGNOSTIC"
V3_EVALUATION_START = datetime(2026, 7, 2, 1, 53, tzinfo=timezone.utc)
DEFAULT_ALPHA = 0.05

LOW_SPREAD_BPS = {"ETH/USD": Decimal("3")}
BASE_SPREAD_BPS = {"ETH/USD": Decimal("4")}
HIGH_SPREAD_BPS = {"ETH/USD": Decimal("5")}
LOW_SLIPPAGE_BPS = {"ETH/USD": Decimal("1")}
BASE_SLIPPAGE_BPS = {"ETH/USD": Decimal("2")}
HIGH_SLIPPAGE_BPS = {"ETH/USD": Decimal("5")}


def candidate_specs_v4() -> tuple[V3CandidateSpec, ...]:
    return (
        V3CandidateSpec(
            "V3_VNT_6H_ETH",
            "volatility_normalized_trend",
            {"z_threshold": 1.0, "momentum_window": 20},
            Decimal("0.0080"),
            Decimal("0.0140"),
            360,
            0,
        ),
        V3CandidateSpec(
            "V3_VNT_8H_ETH",
            "volatility_normalized_trend",
            {"z_threshold": 1.25, "momentum_window": 30},
            Decimal("0.0100"),
            Decimal("0.0180"),
            480,
            0,
        ),
        V3CandidateSpec(
            "V3_RS_6H_ETH",
            "relative_strength_impulse",
            {
                "relative_window": 20,
                "relative_threshold": "0.0015",
                "z_threshold": 1.0,
            },
            Decimal("0.0080"),
            Decimal("0.0140"),
            360,
            0,
        ),
        V3CandidateSpec(
            "V3_RS_8H_ETH",
            "relative_strength_impulse",
            {
                "relative_window": 30,
                "relative_threshold": "0.0025",
                "z_threshold": 1.25,
            },
            Decimal("0.0100"),
            Decimal("0.0180"),
            480,
            0,
        ),
        V3CandidateSpec(
            "ETH_VNT_12H_Z125_W45",
            "volatility_normalized_trend",
            {"z_threshold": 1.25, "momentum_window": 45},
            Decimal("0.0120"),
            Decimal("0.0240"),
            720,
            2,
        ),
        V3CandidateSpec(
            "ETH_RS_12H_W45_T0035",
            "relative_strength_impulse",
            {
                "relative_window": 45,
                "relative_threshold": "0.0035",
                "z_threshold": 1.25,
            },
            Decimal("0.0120"),
            Decimal("0.0240"),
            720,
            2,
        ),
    )


def _cost_payload(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


def _run(
    spec: V3CandidateSpec,
    settings: Any,
    bars: dict[str, list[dict[str, Any]]],
    *,
    initial_equity: Decimal,
    spread_bps: Mapping[str, Decimal],
    slippage_bps: Mapping[str, Decimal],
):
    return _run_v3(
        spec,
        settings,
        bars,
        initial_equity=initial_equity,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
    )


def run_crypto_edge_discovery_v4(
    *,
    settings: Any,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    initial_equity: Decimal,
    min_train_days: int = 10,
    validation_days: int = 5,
    holdout_days: int = 10,
    minimum_validation_trades: int = 12,
    minimum_holdout_trades: int = 8,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    required = {"BTC/USD", "ETH/USD"}
    bars = {
        symbol: rows
        for symbol, rows in bars_by_symbol.items()
        if symbol.upper() in required
    }
    if "ETH/USD" not in bars or "BTC/USD" not in bars:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "ETH execution bars and BTC context bars are both required",
            "promotion_authority": False,
        }

    bounds = _corpus_bounds({"ETH/USD": bars["ETH/USD"]})
    if bounds is None:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "no ETH historical bars",
            "promotion_authority": False,
        }
    start, end = bounds
    if end > V3_EVALUATION_START:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "CORPUS_OVERLAP_INVALID",
            "reason": "v4 execution bars overlap the v3 evaluation corpus",
            "corpus_start": start.isoformat(),
            "corpus_end": end.isoformat(),
            "v3_evaluation_start": V3_EVALUATION_START.isoformat(),
            "promotion_authority": False,
            "live_configuration_changed": False,
        }

    corpus_days = (end - start).total_seconds() / 86400.0
    minimum_days = min_train_days + validation_days * 2 + holdout_days
    if corpus_days < minimum_days:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "historical corpus is too short for v4 frozen contract",
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
            "reason": "fewer than two v4 validation folds",
            "promotion_authority": False,
        }

    specs = candidate_specs_v4()
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
                "by_symbol": run["summary"]["by_symbol"],
            })

        p_state = _moving_block_null_pvalue(
            validation_returns,
            seed=4000 + candidate_index,
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

    eligible = [row for row in candidate_rows if row["eligible_for_holdout"]]
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
            seed=49901,
        )
        high = scenario_rows["high"]
        holdout_passed = bool(
            high["trade_count"] >= minimum_holdout_trades
            and high["expectancy_return"] > 0
            and bootstrap["p05_net_pnl"] > 0
        )
        holdout = {
            "candidate_id": spec.candidate_id,
            "start": holdout_start.isoformat(),
            "end": end.isoformat(),
            "minimum_holdout_trades": minimum_holdout_trades,
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
            "execution_symbols": ["ETH/USD"],
            "context_symbols": ["BTC/USD"],
            "reason": "v3 by-symbol validation showed ETH positive across four slower candidates while BTC was negative across all four",
        },
        "cost_model": {
            "source": "same frozen pre-v2 live quote cost model; ETH execution only",
            "low_spread_bps": _cost_payload(LOW_SPREAD_BPS),
            "base_spread_bps": _cost_payload(BASE_SPREAD_BPS),
            "high_spread_bps": _cost_payload(HIGH_SPREAD_BPS),
            "low_slippage_bps_per_side": _cost_payload(LOW_SLIPPAGE_BPS),
            "base_slippage_bps_per_side": _cost_payload(BASE_SLIPPAGE_BPS),
            "high_slippage_bps_per_side": _cost_payload(HIGH_SLIPPAGE_BPS),
            "high_spread_max_bps": "5",
            "high_slippage_max_bps_per_side": "5",
        },
        "corpus": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": corpus_days,
            "execution_bar_count": len(bars["ETH/USD"]),
            "context_bar_count": len(bars["BTC/USD"]),
            "nonoverlap_with_v3_evaluation": end <= V3_EVALUATION_START,
        },
        "contract": {
            "candidate_set_frozen_before_validation": True,
            "candidate_generation_informed_by_v3_symbol_diagnostic": True,
            "v4_evaluation_corpus_must_not_overlap_v3": True,
            "selection_population": "validation_folds_only",
            "holdout_opened_only_after_multiplicity_adjusted_selection": True,
            "cost_selection_rule": "ETH_high_cost_only",
            "minimum_validation_trades": minimum_validation_trades,
            "minimum_holdout_trades": minimum_holdout_trades,
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
