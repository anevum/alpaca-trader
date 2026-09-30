from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .crypto_layer import CryptoRollingMomentumStrategy
from .math_kernel import volatility_normalized_momentum
from .strategy import Signal
from .velum_core import ContinuousReplayEngine


METHODOLOGY_VERSION = "velum-crypto-challengers-v1"


class CryptoRegimeLayerStrategy(CryptoRollingMomentumStrategy):
    """Challenger C: B plus a crypto-native NOSTRA regime gate."""

    def evaluate(self, *args, **kwargs) -> Signal:
        signal = super().evaluate(*args, **kwargs)
        if signal.action != "buy":
            return signal
        regime = str(((signal.metadata or {}).get("nostra") or {}).get("regime") or "")
        if regime in {"LOW_ACTIVITY", "WIDE_SPREAD"}:
            return Signal(
                action="hold",
                symbol=signal.symbol,
                reason=f"NOSTRA crypto regime gate: {regime}",
                metadata=signal.metadata,
            )
        return signal


class StructuralCryptoMomentumStrategy(CryptoRollingMomentumStrategy):
    """Challenger D: volatility-normalized crypto momentum without VWAP entry gating."""

    structural_z_threshold = 1.0

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
        now: datetime | None = None,
    ) -> Signal:
        current = now or datetime.now(timezone.utc)
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position already open")
        session = self._completed_session_bars(bars, current)
        if len(session) < max(self.slow_window + 1, self.feature_volatility_lookback + 1):
            return Signal(action="hold", symbol=symbol, reason="not enough continuous crypto bars")
        closes = [Decimal(str(bar["c"])) for bar in session]
        z = volatility_normalized_momentum(
            closes,
            self.fast_window,
            self.feature_volatility_lookback,
        )
        if z < self.structural_z_threshold:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="volatility-normalized momentum below structural threshold",
                metadata={
                    "market": "crypto",
                    "market_lane": "crypto",
                    "strategy_family": "structural_crypto_momentum",
                    "volatility_normalized_momentum": z,
                    "bar_time": str(session[-1].get("t") or ""),
                },
            )
        price = Decimal(str(session[-1]["c"]))
        stop_pct, stop_meta = self._effective_stop_pct(session)
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=price,
            stop_price=price * (Decimal("1") - stop_pct),
            take_profit_price=price * (Decimal("1") + self.target_pct),
            reason="structural volatility-normalized crypto momentum",
            metadata={
                "market": "crypto",
                "market_lane": "crypto",
                "strategy_family": "structural_crypto_momentum",
                "strategy_version_id": self.strategy_version_id,
                "model_version": "crypto-structural-model-v1",
                "calibration_version": self.calibration_version,
                "regime_version": self.regime_version,
                "volatility_normalized_momentum": z,
                "volatility_stop": stop_meta,
                "bar_time": str(session[-1].get("t") or ""),
            },
        )


def _strategy(settings: Any, cls=CryptoRollingMomentumStrategy):
    return cls(
        fast_window=settings.fast_window,
        slow_window=settings.slow_window,
        min_momentum_pct=settings.min_momentum_pct,
        min_vwap_edge_pct=settings.min_vwap_edge_pct,
        stop_pct=settings.stop_pct,
        target_pct=settings.target_pct,
        entry_start=settings.entry_start,
        entry_cutoff=settings.entry_cutoff,
        confirmation_symbols=settings.confirmation_symbols,
        min_confirmations=min(settings.min_confirmations, len(settings.confirmation_symbols)),
        regime_window=settings.regime_window,
        regime_min_confirmations=min(settings.regime_min_confirmations, len(settings.confirmation_symbols)),
        regime_min_return_pct=settings.regime_min_return_pct,
        max_vwap_extension_pct=settings.max_vwap_extension_pct,
        volatility_stop_enabled=settings.volatility_stop_enabled,
        volatility_stop_multiplier=settings.volatility_stop_multiplier,
        volatility_stop_lookback_bars=settings.volatility_stop_lookback_bars,
        max_dynamic_stop_pct=settings.max_dynamic_stop_pct,
        strategy_version_id=settings.crypto_strategy_version_id,
        model_version=settings.crypto_model_version,
        calibration_version=settings.crypto_calibration_version,
        calibration_promoted=settings.crypto_calibration_promoted,
        regime_version=settings.crypto_regime_version,
        execution_adapter_version=settings.crypto_execution_adapter_version,
        feature_volatility_lookback=settings.crypto_volatility_lookback_bars,
    )


def challenger_settings(
    base: Any,
    equity_source: Any | None = None,
) -> dict[str, tuple[Any, type[CryptoRollingMomentumStrategy]]]:
    equity = equity_source or base
    transferred = base.model_copy(update={
        "fast_window": equity.fast_window,
        "slow_window": equity.slow_window,
        "min_momentum_pct": equity.min_momentum_pct,
        "min_vwap_edge_pct": equity.min_vwap_edge_pct,
        "regime_window": equity.regime_window,
        "regime_min_return_pct": equity.regime_min_return_pct,
        "max_vwap_extension_pct": equity.max_vwap_extension_pct,
        "volatility_stop_enabled": equity.volatility_stop_enabled,
        "volatility_stop_multiplier": equity.volatility_stop_multiplier,
        "volatility_stop_lookback_bars": equity.volatility_stop_lookback_bars,
        "max_dynamic_stop_pct": equity.max_dynamic_stop_pct,
    })
    return {
        "A_FULL_TRANSFER": (transferred, CryptoRollingMomentumStrategy),
        "B_PARAMETER_ADAPTATION": (base, CryptoRollingMomentumStrategy),
        "C_CRYPTO_REGIME_LAYER": (base, CryptoRegimeLayerStrategy),
        "D_STRUCTURAL_CRYPTO_MODEL": (base, StructuralCryptoMomentumStrategy),
    }


def run_crypto_challengers(
    *,
    settings: Any,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    initial_equity: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    equity_settings: Any | None = None,
) -> dict[str, Any]:
    scenarios = {
        "low": (spread_bps * Decimal("0.5"), slippage_bps * Decimal("0.5")),
        "base": (spread_bps, slippage_bps),
        "high": (spread_bps * Decimal("2"), slippage_bps * Decimal("2")),
    }
    results: dict[str, Any] = {}
    for name, (candidate_settings, cls) in challenger_settings(settings, equity_settings).items():
        strategy = _strategy(candidate_settings, cls)
        engine = ContinuousReplayEngine(candidate_settings, strategy)
        costs = {}
        for scenario, (spread, slippage) in scenarios.items():
            run = engine.run(
                bars_by_symbol,
                initial_equity=initial_equity,
                spread_bps=spread,
                slippage_bps=slippage,
            )
            costs[scenario] = {
                "summary": run["summary"],
                "assumptions": run["assumptions"],
            }
        results[name] = {
            "strategy_family": (
                "structural_crypto_momentum"
                if name == "D_STRUCTURAL_CRYPTO_MODEL"
                else "rolling_momentum_vwap"
            ),
            "normalization_population": (
                "equity_transfer" if name == "A_FULL_TRANSFER" else "crypto_only"
            ),
            "calibration_population": (
                "equity_transfer" if name == "A_FULL_TRANSFER" else "crypto_only"
            ),
            "regime_layer": name == "C_CRYPTO_REGIME_LAYER",
            "cost_scenarios": costs,
        }
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "market_lane": "crypto",
        "challengers": results,
        "no_lookahead_required": True,
        "parameters_frozen_before_evaluation": True,
        "train_validation_test_contract": "expanding_walk_forward_required_for_promotion; rolling replay is engineering evidence only",
        "dependence_inference": "use existing RHEN dependence controls before promotion",
        "multiplicity_inference": "use existing RHEN multiplicity controls before promotion",
        "complexity_penalty_required": True,
        "winner_selected": False,
        "promotion_authority": False,
    }
