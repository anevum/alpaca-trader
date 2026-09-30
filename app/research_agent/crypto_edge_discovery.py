from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import ceil, sqrt
import random
from statistics import fmean
from typing import Any, Iterable, Sequence

from ..crypto_layer import CryptoRollingMomentumStrategy
from ..math_kernel import arithmetic_return, rolling_realized_volatility, volatility_normalized_momentum
from ..strategy import Signal
from ..replay import stamp
from ..velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from .dependence import diagnose_cross_candidate, diagnose_path
from .multiplicity import benjamini_yekutieli


METHODOLOGY_VERSION = "graen-crypto-edge-discovery-v1"
DEFAULT_ALPHA = 0.05
DEFAULT_BOOTSTRAP_REPLICATES = 2000


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    family: str
    parameters: dict[str, Any]
    complexity_units: int
    requires_cross_asset_context: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_id: str
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
        }


def _d(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:
        return Decimal("0")


def _stamp(bar: dict[str, Any]) -> datetime:
    raw = str(bar.get("t") or "")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _slice_bars(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    start: datetime,
    end: datetime,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for symbol, bars in bars_by_symbol.items():
        output[symbol] = [
            bar for bar in bars
            if start <= _stamp(bar) < end
        ]
    return output


def _corpus_bounds(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> tuple[datetime, datetime] | None:
    stamps = [
        _stamp(bar)
        for bars in bars_by_symbol.values()
        for bar in bars
        if bar.get("t")
    ]
    if not stamps:
        return None
    return min(stamps), max(stamps) + timedelta(minutes=1)


def _returns(trades: Iterable[dict[str, Any]]) -> list[float]:
    return [float(trade.get("return_pct") or 0.0) for trade in trades]


def _expectancy(returns: Sequence[float]) -> float:
    return fmean(returns) if returns else 0.0


def _positive_fraction(values: Sequence[float]) -> float:
    return (
        sum(value > 0 for value in values) / len(values)
        if values else 0.0
    )


def _moving_block_null_pvalue(
    returns: Sequence[float],
    *,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = 17,
) -> dict[str, Any]:
    values = tuple(float(value) for value in returns)
    n = len(values)
    observed = _expectancy(values)
    if n < 2 or observed <= 0:
        return {
            "p_value": 1.0,
            "observed_mean": observed,
            "replicates": replicates,
            "block_length": 0,
            "method": "moving_block_bootstrap_centered_null",
            "dependence_adjusted": True,
        }

    centered = tuple(value - observed for value in values)
    block_length = max(2, min(20, int(ceil(sqrt(n)))))
    starts = tuple(range(n))
    rng = random.Random(seed)
    exceed = 0
    for _ in range(replicates):
        sampled: list[float] = []
        while len(sampled) < n:
            start = rng.choice(starts)
            for offset in range(block_length):
                sampled.append(centered[(start + offset) % n])
                if len(sampled) >= n:
                    break
        if _expectancy(sampled) >= observed:
            exceed += 1
    return {
        "p_value": (exceed + 1) / (replicates + 1),
        "observed_mean": observed,
        "replicates": replicates,
        "block_length": block_length,
        "method": "moving_block_bootstrap_centered_null",
        "dependence_adjusted": True,
    }


class CryptoResearchReplayEngine(ContinuousReplayEngine):
    """Research replay quality gate without inherited strategy confirmations.

    Production config validation remains unchanged. Research families must encode
    their own cross-asset/context requirements inside their signal logic.
    """

    def _historical_market_quality(
        self,
        signal: Signal,
        visible: dict[str, list[dict[str, Any]]],
        now: datetime,
        spread_pct: Decimal,
    ) -> tuple[bool, str, dict[str, Any]]:
        if spread_pct > self.settings.max_spread_pct:
            return False, "assumed spread exceeds MAX_SPREAD_PCT", {
                "spread_pct": str(spread_pct)
            }
        bars = visible.get(signal.symbol, [])
        if not bars:
            return False, "no candidate bars", {}
        age_seconds = max(
            (now - (stamp(bars[-1]) + timedelta(minutes=1))).total_seconds(),
            0.0,
        )
        details = {
            "bar_age_seconds": round(age_seconds, 3),
            "spread_pct": str(spread_pct),
            "research_quality_gate": "bar_freshness_and_cost_only",
        }
        if age_seconds > self.settings.max_bar_age_seconds:
            return False, "candidate bar is stale", details
        return True, "research market-quality assumptions passed", details


class _ResearchStrategyBase(CryptoRollingMomentumStrategy):
    family_name = "research_crypto"

    def _metadata(
        self,
        session: list[dict[str, Any]],
        *,
        symbol: str,
        momentum_pct: Decimal,
        vwap_edge_pct: Decimal,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "market": "crypto",
            "market_lane": "crypto",
            "session_model": "24x7",
            "strategy_family": self.family_name,
            "strategy_version_id": self.strategy_version_id,
            "model_version": self.model_version,
            "calibration_version": self.calibration_version,
            "regime_version": self.regime_version,
            "execution_adapter_version": self.execution_adapter_version,
            "bar_time": str(session[-1].get("t") or ""),
            "momentum_pct": str(momentum_pct),
            "vwap_edge_pct": str(vwap_edge_pct),
            "confirmation_passes": 0,
            "confirmations": {},
            **details,
        }

    def _buy(
        self,
        session: list[dict[str, Any]],
        *,
        symbol: str,
        order_notional: Decimal,
        momentum_pct: Decimal,
        details: dict[str, Any],
        reason: str,
    ) -> Signal:
        current = _d(session[-1].get("c"))
        vwap = self._vwap(session)
        vwap_edge = (
            (current - vwap) / vwap if current > 0 and vwap > 0 else Decimal("0")
        )
        stop_pct, stop_meta = self._effective_stop_pct(session)
        metadata = self._metadata(
            session,
            symbol=symbol,
            momentum_pct=momentum_pct,
            vwap_edge_pct=vwap_edge,
            details=details,
        )
        metadata["effective_stop_pct"] = str(stop_pct)
        metadata["stop_model"] = stop_meta
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=current,
            stop_price=self._crypto_price(current * (Decimal("1") - stop_pct)),
            take_profit_price=self._crypto_price(current * (Decimal("1") + self.target_pct)),
            reason=reason,
            metadata=metadata,
        )


class VolatilityNormalizedTrendStrategy(_ResearchStrategyBase):
    family_name = "volatility_normalized_trend"

    def __init__(self, *args, z_threshold: float = 1.0, momentum_window: int = 8, **kwargs):
        super().__init__(*args, **kwargs)
        self.z_threshold = float(z_threshold)
        self.momentum_window = int(momentum_window)

    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now=None):
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position already open")
        session = self._completed_session_bars(bars, current)
        needed = max(self.feature_volatility_lookback + 1, self.momentum_window + 2)
        if len(session) < needed:
            return Signal(action="hold", symbol=symbol, reason="not enough crypto bars")
        closes = [_d(bar["c"]) for bar in session]
        z = volatility_normalized_momentum(
            closes,
            self.momentum_window,
            self.feature_volatility_lookback,
        )
        momentum = arithmetic_return(closes[-self.momentum_window - 1], closes[-1])
        if z < self.z_threshold or closes[-1] <= closes[-2]:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="volatility-normalized trend threshold not met",
                metadata={"volatility_normalized_momentum": z},
            )
        return self._buy(
            session,
            symbol=symbol,
            order_notional=order_notional,
            momentum_pct=momentum,
            details={
                "volatility_normalized_momentum": z,
                "z_threshold": self.z_threshold,
                "momentum_window": self.momentum_window,
            },
            reason="volatility-normalized crypto trend continuation",
        )


class PullbackReclaimStrategy(_ResearchStrategyBase):
    family_name = "impulse_pullback_reclaim"

    def __init__(
        self,
        *args,
        impulse_window: int = 15,
        impulse_threshold: Decimal = Decimal("0.003"),
        reclaim_window: int = 4,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.impulse_window = int(impulse_window)
        self.impulse_threshold = _d(impulse_threshold)
        self.reclaim_window = int(reclaim_window)

    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now=None):
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position already open")
        session = self._completed_session_bars(bars, current)
        needed = max(self.impulse_window + 3, self.reclaim_window + 3)
        if len(session) < needed:
            return Signal(action="hold", symbol=symbol, reason="not enough crypto bars")
        closes = [_d(bar["c"]) for bar in session]
        impulse = arithmetic_return(
            closes[-self.impulse_window - 2],
            closes[-2],
        )
        prior_mean = _mean(closes[-self.reclaim_window - 1:-1])
        current_mean = _mean(closes[-self.reclaim_window:])
        prior = closes[-2]
        latest = closes[-1]
        pulled_back = prior <= prior_mean
        reclaimed = latest > prior and latest >= current_mean
        if impulse < self.impulse_threshold or not pulled_back or not reclaimed:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="impulse pullback reclaim conditions not met",
            )
        momentum = arithmetic_return(closes[-self.reclaim_window - 1], latest)
        return self._buy(
            session,
            symbol=symbol,
            order_notional=order_notional,
            momentum_pct=momentum,
            details={
                "impulse_return": str(impulse),
                "impulse_threshold": str(self.impulse_threshold),
                "prior_reclaim_mean": str(prior_mean),
                "current_reclaim_mean": str(current_mean),
            },
            reason="crypto impulse pullback reclaimed short mean",
        )


class CompressionBreakoutStrategy(_ResearchStrategyBase):
    family_name = "compression_breakout"

    def __init__(
        self,
        *args,
        short_vol_window: int = 8,
        long_vol_window: int = 30,
        compression_ratio: float = 0.60,
        breakout_window: int = 12,
        breakout_buffer: Decimal = Decimal("0.0004"),
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.short_vol_window = int(short_vol_window)
        self.long_vol_window = int(long_vol_window)
        self.compression_ratio = float(compression_ratio)
        self.breakout_window = int(breakout_window)
        self.breakout_buffer = _d(breakout_buffer)

    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now=None):
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position already open")
        session = self._completed_session_bars(bars, current)
        needed = max(self.long_vol_window + 2, self.breakout_window + 2)
        if len(session) < needed:
            return Signal(action="hold", symbol=symbol, reason="not enough crypto bars")
        closes = [_d(bar["c"]) for bar in session]
        pre = closes[:-1]
        short_vol = rolling_realized_volatility(pre, self.short_vol_window)
        long_vol = rolling_realized_volatility(pre, self.long_vol_window)
        prior_high = max(_d(bar["h"]) for bar in session[-self.breakout_window - 1:-1])
        latest = closes[-1]
        threshold = prior_high * (Decimal("1") + self.breakout_buffer)
        compressed = long_vol > 0 and short_vol <= long_vol * self.compression_ratio
        breakout = latest > threshold
        if not compressed or not breakout:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="compression breakout conditions not met",
            )
        momentum = arithmetic_return(closes[-self.breakout_window - 1], latest)
        return self._buy(
            session,
            symbol=symbol,
            order_notional=order_notional,
            momentum_pct=momentum,
            details={
                "short_realized_volatility": short_vol,
                "long_realized_volatility": long_vol,
                "compression_ratio": self.compression_ratio,
                "prior_breakout_high": str(prior_high),
                "breakout_buffer": str(self.breakout_buffer),
            },
            reason="crypto volatility compression expanded through prior range",
        )


class RelativeStrengthImpulseStrategy(_ResearchStrategyBase):
    family_name = "relative_strength_impulse"

    def __init__(
        self,
        *args,
        relative_window: int = 12,
        relative_threshold: Decimal = Decimal("0.0015"),
        z_threshold: float = 0.75,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.relative_window = int(relative_window)
        self.relative_threshold = _d(relative_threshold)
        self.z_threshold = float(z_threshold)

    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now=None):
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position already open")
        session = self._completed_session_bars(bars, current)
        needed = max(self.relative_window + 2, self.feature_volatility_lookback + 1)
        if len(session) < needed:
            return Signal(action="hold", symbol=symbol, reason="not enough crypto bars")
        closes = [_d(bar["c"]) for bar in session]
        own = arithmetic_return(closes[-self.relative_window - 1], closes[-1])
        contexts: list[Decimal] = []
        for context_symbol, context_bars in confirmation_bars.items():
            if context_symbol.upper() == symbol.upper():
                continue
            visible = self._completed_session_bars(context_bars, current)
            if len(visible) <= self.relative_window:
                continue
            context_closes = [_d(bar["c"]) for bar in visible]
            contexts.append(
                arithmetic_return(
                    context_closes[-self.relative_window - 1],
                    context_closes[-1],
                )
            )
        if not contexts:
            return Signal(action="hold", symbol=symbol, reason="no cross-asset crypto context")
        benchmark = _mean(contexts)
        relative = own - benchmark
        z = volatility_normalized_momentum(
            closes,
            self.relative_window,
            self.feature_volatility_lookback,
        )
        if relative < self.relative_threshold or z < self.z_threshold:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="relative-strength impulse threshold not met",
            )
        return self._buy(
            session,
            symbol=symbol,
            order_notional=order_notional,
            momentum_pct=own,
            details={
                "relative_return": str(relative),
                "benchmark_return": str(benchmark),
                "relative_threshold": str(self.relative_threshold),
                "volatility_normalized_momentum": z,
                "z_threshold": self.z_threshold,
            },
            reason="crypto relative-strength impulse versus market context",
        )


def candidate_specs() -> tuple[CandidateSpec, ...]:
    return (
        CandidateSpec(
            "BENCHMARK_RMVWAP",
            "rolling_momentum_vwap",
            {},
            0,
        ),
        CandidateSpec(
            "VNT_Z075_W8",
            "volatility_normalized_trend",
            {"z_threshold": 0.75, "momentum_window": 8},
            1,
        ),
        CandidateSpec(
            "VNT_Z125_W12",
            "volatility_normalized_trend",
            {"z_threshold": 1.25, "momentum_window": 12},
            1,
        ),
        CandidateSpec(
            "PBR_I12_T0025",
            "impulse_pullback_reclaim",
            {"impulse_window": 12, "impulse_threshold": "0.0025", "reclaim_window": 4},
            2,
        ),
        CandidateSpec(
            "PBR_I20_T0040",
            "impulse_pullback_reclaim",
            {"impulse_window": 20, "impulse_threshold": "0.0040", "reclaim_window": 5},
            2,
        ),
        CandidateSpec(
            "CB_R060_B004",
            "compression_breakout",
            {
                "short_vol_window": 8,
                "long_vol_window": 30,
                "compression_ratio": 0.60,
                "breakout_window": 12,
                "breakout_buffer": "0.0004",
            },
            2,
        ),
        CandidateSpec(
            "CB_R045_B007",
            "compression_breakout",
            {
                "short_vol_window": 8,
                "long_vol_window": 40,
                "compression_ratio": 0.45,
                "breakout_window": 18,
                "breakout_buffer": "0.0007",
            },
            2,
        ),
        CandidateSpec(
            "RSI_W8_T0010",
            "relative_strength_impulse",
            {"relative_window": 8, "relative_threshold": "0.0010", "z_threshold": 0.75},
            2,
            True,
        ),
        CandidateSpec(
            "RSI_W15_T0020",
            "relative_strength_impulse",
            {"relative_window": 15, "relative_threshold": "0.0020", "z_threshold": 1.0},
            2,
            True,
        ),
    )


def _base_strategy_kwargs(settings: Any, *, candidate_id: str) -> dict[str, Any]:
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
        "max_dynamic_stop_pct": settings.max_dynamic_stop_pct,
        "strategy_version_id": f"CRYPTO-RESEARCH-EDGEV1-{candidate_id}",
        "model_version": f"crypto-edge-v1-{candidate_id.lower()}",
        "calibration_version": "crypto-calibration-unvalidated-v1",
        "calibration_promoted": False,
        "regime_version": "nostra-crypto-regime-v1",
        "execution_adapter_version": "velum-replay-only",
        "feature_volatility_lookback": settings.crypto_volatility_lookback_bars,
    }


def _build_strategy(spec: CandidateSpec, settings: Any) -> tuple[Any, Any]:
    kwargs = _base_strategy_kwargs(settings, candidate_id=spec.candidate_id)
    if spec.family == "rolling_momentum_vwap":
        return settings, CryptoRollingMomentumStrategy(**kwargs)

    research_settings = settings
    kwargs = _base_strategy_kwargs(research_settings, candidate_id=spec.candidate_id)
    if spec.family == "volatility_normalized_trend":
        strategy = VolatilityNormalizedTrendStrategy(**kwargs, **spec.parameters)
    elif spec.family == "impulse_pullback_reclaim":
        params = dict(spec.parameters)
        params["impulse_threshold"] = _d(params["impulse_threshold"])
        strategy = PullbackReclaimStrategy(**kwargs, **params)
    elif spec.family == "compression_breakout":
        params = dict(spec.parameters)
        params["breakout_buffer"] = _d(params["breakout_buffer"])
        strategy = CompressionBreakoutStrategy(**kwargs, **params)
    elif spec.family == "relative_strength_impulse":
        params = dict(spec.parameters)
        params["relative_threshold"] = _d(params["relative_threshold"])
        strategy = RelativeStrengthImpulseStrategy(**kwargs, **params)
    else:
        raise ValueError(f"unsupported crypto edge family: {spec.family}")
    return research_settings, strategy


def _folds(
    *,
    start: datetime,
    end: datetime,
    min_train_days: int,
    validation_days: int,
    holdout_days: int,
) -> tuple[tuple[WalkForwardFold, ...], datetime]:
    holdout_start = end - timedelta(days=holdout_days)
    cursor = start + timedelta(days=min_train_days)
    folds: list[WalkForwardFold] = []
    index = 1
    while cursor + timedelta(days=validation_days) <= holdout_start:
        validation_end = cursor + timedelta(days=validation_days)
        folds.append(
            WalkForwardFold(
                fold_id=f"WF-{index:02d}",
                train_start=start,
                train_end=cursor,
                validation_start=cursor,
                validation_end=validation_end,
            )
        )
        cursor = validation_end
        index += 1
    return tuple(folds), holdout_start


def _run_high_cost(
    spec: CandidateSpec,
    settings: Any,
    bars: dict[str, list[dict[str, Any]]],
    *,
    initial_equity: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, Any]:
    candidate_settings, strategy = _build_strategy(spec, settings)
    engine_cls = (
        ContinuousReplayEngine
        if spec.family == "rolling_momentum_vwap"
        else CryptoResearchReplayEngine
    )
    engine = engine_cls(candidate_settings, strategy)
    return engine.run(
        bars,
        initial_equity=initial_equity,
        spread_bps=spread_bps * Decimal("2"),
        slippage_bps=slippage_bps * Decimal("2"),
    )


def run_crypto_edge_discovery(
    *,
    settings: Any,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    initial_equity: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    min_train_days: int = 10,
    validation_days: int = 5,
    holdout_days: int = 5,
    minimum_validation_trades: int = 20,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    bounds = _corpus_bounds(bars_by_symbol)
    if bounds is None:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "no crypto historical bars",
            "promotion_authority": False,
        }
    start, end = bounds
    minimum_days = min_train_days + validation_days * 2 + holdout_days
    corpus_days = (end - start).total_seconds() / 86400.0
    if corpus_days < minimum_days:
        return {
            "methodology_version": METHODOLOGY_VERSION,
            "status": "INSUFFICIENT_CORPUS",
            "reason": "historical corpus is too short for frozen walk-forward contract",
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
            "reason": "fewer than two validation folds",
            "promotion_authority": False,
        }

    specs = candidate_specs()
    candidate_rows: list[dict[str, Any]] = []
    fold_paths: list[list[float]] = []
    p_values: list[float] = []

    for candidate_index, spec in enumerate(specs):
        all_validation_returns: list[float] = []
        fold_expectancies: list[float] = []
        fold_rows: list[dict[str, Any]] = []
        for fold in folds:
            validation_bars = _slice_bars(
                bars_by_symbol,
                fold.validation_start,
                fold.validation_end,
            )
            run = _run_high_cost(
                spec,
                settings,
                validation_bars,
                initial_equity=initial_equity,
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
            )
            returns = _returns(run["trades"])
            expectancy = _expectancy(returns)
            all_validation_returns.extend(returns)
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
            all_validation_returns,
            seed=1000 + candidate_index,
        )
        bounded = [max(-1.0, min(1.0, value)) for value in all_validation_returns]
        dependence = diagnose_path(bounded).to_dict()
        raw_expectancy = _expectancy(all_validation_returns)
        complexity_penalty = spec.complexity_units * 0.00001
        score = raw_expectancy - complexity_penalty
        row = {
            "candidate": spec.to_dict(),
            "validation": {
                "trade_count": len(all_validation_returns),
                "expectancy_return": raw_expectancy,
                "positive_fold_fraction": _positive_fraction(fold_expectancies),
                "fold_expectancies": fold_expectancies,
                "folds": fold_rows,
            },
            "null_test": p_state,
            "dependence": dependence,
            "complexity_penalty": complexity_penalty,
            "selection_score": score,
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

    cross_dependence = diagnose_cross_candidate(fold_paths).to_dict()
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

    holdout: dict[str, Any] | None = None
    holdout_passed = False
    if winner is not None:
        spec = next(
            item for item in specs
            if item.candidate_id == winner["candidate"]["candidate_id"]
        )
        holdout_bars = _slice_bars(bars_by_symbol, holdout_start, end)
        cost_scenarios = {
            "low": (spread_bps * Decimal("0.5"), slippage_bps * Decimal("0.5")),
            "base": (spread_bps, slippage_bps),
            "high": (spread_bps * Decimal("2"), slippage_bps * Decimal("2")),
        }
        scenario_rows: dict[str, Any] = {}
        high_returns: list[float] = []
        high_trades: list[dict[str, Any]] = []
        for label, (spread, slippage) in cost_scenarios.items():
            candidate_settings, strategy = _build_strategy(spec, settings)
            engine_cls = (
                ContinuousReplayEngine
                if spec.family == "rolling_momentum_vwap"
                else CryptoResearchReplayEngine
            )
            run = engine_cls(candidate_settings, strategy).run(
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
            }
            if label == "high":
                high_returns = returns
                high_trades = run["trades"]
        bootstrap = bootstrap_trade_distribution(
            high_trades,
            paths=1000,
            seed=9901,
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
    walk_forward_passed = winner is not None
    status = (
        "HOLDOUT_PASS"
        if holdout_passed
        else "VALIDATION_EDGE_FOUND"
        if winner is not None
        else "NO_EDGE"
    )

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "status": status,
        "market_lane": "crypto",
        "research_only": True,
        "promotion_authority": False,
        "live_configuration_changed": False,
        "benchmark_strategy_version_id": settings.crypto_strategy_version_id,
        "corpus": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": corpus_days,
            "symbols": sorted(bars_by_symbol),
            "bar_counts": {
                symbol: len(bars) for symbol, bars in sorted(bars_by_symbol.items())
            },
        },
        "contract": {
            "candidate_set_frozen_before_validation": True,
            "training_role": "expanding_prior_context; no parameter fitting in v1",
            "selection_population": "validation_folds_only",
            "holdout_opened_only_after_multiplicity_adjusted_selection": True,
            "cost_selection_rule": "high_cost_only",
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
        "walk_forward_passed": walk_forward_passed,
        "holdout_passed": holdout_passed,
        "dependence_adjusted": True,
        "dependence_method": "moving_block_bootstrap_per_candidate + BY under arbitrary cross-candidate dependence",
        "multiplicity_adjusted": True,
        "multiplicity_method": "benjamini_yekutieli",
        "no_lookahead_verified": no_lookahead,
        "winner_selected": winner is not None,
    }
