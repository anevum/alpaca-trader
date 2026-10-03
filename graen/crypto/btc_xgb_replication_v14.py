from __future__ import annotations

"""GRAEN V14-R1 literature-replication preflight for cost-aware BTC XGBoost.

This module intentionally separates a cheap broker-compatibility screen from a
claim of exact paper reproduction. The source paper does not publish a complete
machine-readable feature specification or public code repository, so this
compiler MUST NOT label its result an exact reproduction and MUST NOT authorize
promotion or live execution.

The preflight asks a narrower question before ANEVUM spends substantially more
compute on the full replication ladder:

    Does a chronologically trained XGBoost implementation using the documented
    feature families and cost-aware long-only decision rule retain any useful
    out-of-sample signal on Alpaca BTC/USD after realistic Alpaca costs?

A negative result kills V14-R1 early. A positive result only justifies the
original-data/code replication step. It does not skip DEVELOPMENT, VELUM,
VALIDATION, HOLDOUT, or human live-trading authorization.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, isfinite, log, sqrt
from typing import Any, Mapping, Sequence


UTC = timezone.utc
METHODOLOGY_VERSION = "graen-btc-xgb-replication-v14-r1"
CAMPAIGN_ID = "v14-r1-cost-aware-xgb-btc"
FAMILY = "external_literature_replication_cost_aware_xgboost"
UNIVERSE = ("BTC/USD",)

SOURCE_TITLE = (
    "Machine Learning-Based Bitcoin Trading Under Transaction Costs: "
    "Evidence From Walk-Forward Forecasting"
)
SOURCE_AUTHORS = ("Andrei Bysik", "Robert Slepaczuk")
SOURCE_YEAR = 2026
SOURCE_URL = "https://arxiv.org/abs/2606.00060"
SOURCE_REPLICATION_STATUS = "CLOSEST_METHOD_PREFLIGHT_NOT_EXACT_REPLICATION"

# This is deliberately broker-facing data, not the source paper's Binance
# futures corpus. Alpaca data is available from 2021; keep the boundary frozen.
ALPACA_SCREEN_START = datetime(2021, 1, 1, tzinfo=UTC)
ALPACA_SCREEN_END = datetime(2026, 10, 1, tzinfo=UTC)

TRAIN_MONTHS = 12
VALIDATION_MONTHS = 3
TEST_MONTHS = 3
STEP_MONTHS = 3
LAMBDA_COST = 2.0

# Per-unit turnover. "paper" matches the paper's 10bp proxy. Alpaca Tier-1
# taker fee is 25bp before spread/slippage, so 30bp is the pre-registered base
# broker screen and 40bp is the stress case.
COST_SCENARIOS = {
    "paper_10bp": 0.0010,
    "alpaca_fee_only_25bp": 0.0025,
    "alpaca_base_30bp": 0.0030,
    "alpaca_stress_40bp": 0.0040,
}

# Pre-registered, deliberately small proxy grid inside the paper's published
# XGBoost search region. The paper used Optuna; therefore this screen cannot be
# called an exact replication. Keeping the grid small prevents an expensive
# 27-fold/50-trial research run before broker economics are known to survive.
XGB_SCREEN_CONFIGS = (
    {
        "max_depth": 2,
        "learning_rate": 0.010,
        "n_estimators": 1000,
        "min_child_weight": 10,
        "subsample": 0.70,
        "colsample_bytree": 0.70,
        "reg_alpha": 0.001,
        "reg_lambda": 5.0,
    },
    {
        "max_depth": 3,
        "learning_rate": 0.010,
        "n_estimators": 1250,
        "min_child_weight": 20,
        "subsample": 0.80,
        "colsample_bytree": 0.80,
        "reg_alpha": 0.003,
        "reg_lambda": 10.0,
    },
    {
        "max_depth": 3,
        "learning_rate": 0.020,
        "n_estimators": 1000,
        "min_child_weight": 30,
        "subsample": 0.75,
        "colsample_bytree": 0.85,
        "reg_alpha": 0.010,
        "reg_lambda": 20.0,
    },
    {
        "max_depth": 4,
        "learning_rate": 0.015,
        "n_estimators": 1500,
        "min_child_weight": 40,
        "subsample": 0.90,
        "colsample_bytree": 0.90,
        "reg_alpha": 0.030,
        "reg_lambda": 35.0,
    },
)


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    train_start: datetime
    train_end: datetime
    validation_end: datetime
    test_end: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "test_end": self.test_end.isoformat(),
        }


def campaign_manifest() -> dict[str, Any]:
    return {
        "schema_version": "graen.v14-r1.manifest.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source": {
            "title": SOURCE_TITLE,
            "authors": list(SOURCE_AUTHORS),
            "year": SOURCE_YEAR,
            "url": SOURCE_URL,
            "evidence_class": "C_GENUINE_HISTORICAL_OOS_NOT_LIVE_PROFITABILITY",
        },
        "replication_status": SOURCE_REPLICATION_STATUS,
        "known_source_limitations": [
            "paper states Binance USD-margined futures history begins in 2017-12; provenance requires verification",
            "complete 15-variable OHLCV base feature definitions are not published in a machine-readable specification",
            "author code is described as available on request rather than in a public repository",
            "paper uses Optuna; this preflight uses a pre-registered bounded proxy grid",
        ],
        "screen": {
            "market": "ALPACA_BTC_USD_SPOT",
            "start": ALPACA_SCREEN_START.isoformat(),
            "end": ALPACA_SCREEN_END.isoformat(),
            "timeframe": "1Hour",
            "train_months": TRAIN_MONTHS,
            "validation_months": VALIDATION_MONTHS,
            "test_months": TEST_MONTHS,
            "step_months": STEP_MONTHS,
            "target": "next_hour_log_return",
            "positioning": "long_or_cash",
            "cost_aware_lambda": LAMBDA_COST,
            "cost_scenarios": dict(COST_SCENARIOS),
        },
        "authority": {
            "research_only": True,
            "promotion_eligible": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "live_execution_authorized": False,
        },
    }


def _stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _num(row: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(number):
            return number
    return default


def _normalize_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float | datetime]]:
    by_stamp: dict[datetime, dict[str, float | datetime]] = {}
    for row in rows:
        raw_stamp = row.get("t", row.get("timestamp"))
        if raw_stamp is None:
            continue
        try:
            stamp = _stamp(raw_stamp)
        except Exception:
            continue
        close = _num(row, "c", "close")
        if close <= 0:
            continue
        open_px = _num(row, "o", "open", default=close)
        high = _num(row, "h", "high", default=max(open_px, close))
        low = _num(row, "l", "low", default=min(open_px, close))
        volume = max(_num(row, "v", "volume"), 0.0)
        trades = max(_num(row, "n", "trade_count"), 0.0)
        vwap = _num(row, "vw", "vwap", default=close)
        by_stamp[stamp] = {
            "timestamp": stamp,
            "open": open_px,
            "high": max(high, open_px, close),
            "low": min(low, open_px, close),
            "close": close,
            "volume": volume,
            "trade_count": trades,
            "vwap": vwap if vwap > 0 else close,
        }
    return [by_stamp[key] for key in sorted(by_stamp)]


def _add_months(value: datetime, months: int) -> datetime:
    month0 = value.month - 1 + months
    year = value.year + month0 // 12
    month = month0 % 12 + 1
    # All campaign boundaries are first-of-month UTC, so day clamping is not
    # needed and would only create accidental degrees of freedom.
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def walk_forward_folds(start: datetime, end: datetime) -> tuple[Fold, ...]:
    folds: list[Fold] = []
    cursor = start
    index = 0
    while True:
        train_end = _add_months(cursor, TRAIN_MONTHS)
        validation_end = _add_months(train_end, VALIDATION_MONTHS)
        test_end = _add_months(validation_end, TEST_MONTHS)
        if test_end > end:
            break
        folds.append(Fold(index, cursor, train_end, validation_end, test_end))
        cursor = _add_months(cursor, STEP_MONTHS)
        index += 1
    return tuple(folds)


def cost_aware_positions(
    forecasts: Sequence[float],
    *,
    cost_per_turnover: float,
    lambda_cost: float = LAMBDA_COST,
    initial_position: int = 0,
) -> list[int]:
    position = 1 if initial_position else 0
    output: list[int] = []
    for raw in forecasts:
        forecast = float(raw)
        desired = 1 if forecast > 0.0 else 0
        change = abs(desired - position)
        threshold = lambda_cost * cost_per_turnover * change
        if change and abs(forecast) > threshold:
            position = desired
        output.append(position)
    return output


def _rolling_mean(values: Any, window: int) -> Any:
    import numpy as np

    arr = np.asarray(values, dtype=float)
    out = np.full(arr.shape, np.nan)
    if window <= 0 or arr.size < window:
        return out

    finite = np.isfinite(arr)
    values_filled = np.where(finite, arr, 0.0)
    csum = np.cumsum(np.insert(values_filled, 0, 0.0))
    counts = np.cumsum(np.insert(finite.astype(int), 0, 0))
    rolling_sum = csum[window:] - csum[:-window]
    rolling_count = counts[window:] - counts[:-window]
    target = out[window - 1 :]
    complete = rolling_count == window
    target[complete] = rolling_sum[complete] / window
    return out


def _rolling_std(values: Any, window: int) -> Any:
    import numpy as np

    arr = np.asarray(values, dtype=float)
    mean = _rolling_mean(arr, window)
    mean_sq = _rolling_mean(arr * arr, window)
    variance = np.maximum(mean_sq - mean * mean, 0.0)
    return np.sqrt(variance)


def _ema(values: Any, span: int) -> Any:
    import numpy as np

    arr = np.asarray(values, dtype=float)
    out = np.full(arr.shape, np.nan)
    if arr.size == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    current = arr[0]
    out[0] = current
    for index in range(1, arr.size):
        current = alpha * arr[index] + (1.0 - alpha) * current
        out[index] = current
    return out


def _rsi(close: Any, window: int) -> Any:
    import numpy as np

    diff = np.diff(close, prepend=np.nan)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = _rolling_mean(gains, window)
    avg_loss = _rolling_mean(losses, window)
    rs = np.divide(
        avg_gain,
        avg_loss,
        out=np.full_like(avg_gain, np.nan),
        where=avg_loss > 0,
    )
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high: Any, low: Any, close: Any, window: int) -> Any:
    import numpy as np

    previous = np.roll(close, 1)
    previous[0] = np.nan
    true_range = np.maximum(
        high - low,
        np.maximum(np.abs(high - previous), np.abs(low - previous)),
    )
    return _rolling_mean(true_range, window)


def _obv(volume: Any, close: Any) -> Any:
    import numpy as np

    direction = np.sign(np.diff(close, prepend=close[0]))
    return np.cumsum(direction * volume)


def _slope(values: Any, window: int) -> Any:
    import numpy as np

    arr = np.asarray(values, dtype=float)
    out = np.full(arr.shape, np.nan)
    if arr.size < window:
        return out
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    denom = float((x_centered * x_centered).sum())
    for index in range(window - 1, arr.size):
        y = arr[index - window + 1 : index + 1]
        if not np.isfinite(y).all():
            continue
        out[index] = float((x_centered * (y - y.mean())).sum() / denom)
    return out


def _mfi(high: Any, low: Any, close: Any, volume: Any, window: int) -> Any:
    import numpy as np

    typical = (high + low + close) / 3.0
    raw_flow = typical * volume
    delta = np.diff(typical, prepend=np.nan)
    positive = np.where(delta > 0, raw_flow, 0.0)
    negative = np.where(delta < 0, raw_flow, 0.0)
    pos = _rolling_mean(positive, window) * window
    neg = _rolling_mean(negative, window) * window
    ratio = np.divide(
        pos,
        neg,
        out=np.full_like(pos, np.nan),
        where=neg > 0,
    )
    return 100.0 - 100.0 / (1.0 + ratio)


def _lag_return(close: Any, lag: int) -> Any:
    import numpy as np

    out = np.full(close.shape, np.nan)
    if close.size > lag:
        out[lag:] = np.log(close[lag:] / close[:-lag])
    return out


def feature_bank(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    import numpy as np

    normalized = _normalize_bars(rows)
    if not normalized:
        return {"timestamps": [], "target": np.array([]), "base": {}, "ta_groups": {}}

    timestamps = [row["timestamp"] for row in normalized]
    open_px = np.asarray([row["open"] for row in normalized], dtype=float)
    high = np.asarray([row["high"] for row in normalized], dtype=float)
    low = np.asarray([row["low"] for row in normalized], dtype=float)
    close = np.asarray([row["close"] for row in normalized], dtype=float)
    volume = np.asarray([row["volume"] for row in normalized], dtype=float)
    trades = np.asarray([row["trade_count"] for row in normalized], dtype=float)
    vwap = np.asarray([row["vwap"] for row in normalized], dtype=float)

    ret1 = _lag_return(close, 1)
    target = np.roll(ret1, -1)
    target[-1] = np.nan

    log_volume = np.log1p(volume)
    log_trades = np.log1p(trades)
    base = {
        "ret_1h": ret1,
        "ret_2h": _lag_return(close, 2),
        "ret_3h": _lag_return(close, 3),
        "ret_6h": _lag_return(close, 6),
        "ret_12h": _lag_return(close, 12),
        "ret_24h": _lag_return(close, 24),
        "range_frac": (high - low) / close,
        "body_frac": (close - open_px) / open_px,
        "close_vwap_frac": (close - vwap) / vwap,
        "log_volume": log_volume,
        "volume_delta_1h": np.diff(log_volume, prepend=np.nan),
        "trade_count_delta_1h": np.diff(log_trades, prepend=np.nan),
        "vwap_ret_1h": _lag_return(vwap, 1),
        "realized_vol_24h": _rolling_std(ret1, 24),
        "realized_vol_168h": _rolling_std(ret1, 168),
    }

    windows = (3, 6, 12, 24, 48, 72, 168, 336)
    ta_groups: dict[str, dict[str, Any]] = {
        "rsi": {},
        "roc": {},
        "sma_distance": {},
        "macd_norm": {},
        "macd_hist": {},
        "atr_ratio": {},
        "rolling_std": {},
        "bollinger_position": {},
        "obv_slope": {},
        "mfi": {},
    }
    obv = _obv(volume, close)

    for window in windows:
        if window >= close.size:
            continue
        sma = _rolling_mean(close, window)
        std = _rolling_std(close, window)
        ta_groups["rsi"][f"rsi_{window}"] = _rsi(close, window) / 100.0
        ta_groups["roc"][f"roc_{window}"] = _lag_return(close, window)
        ta_groups["sma_distance"][f"sma_dist_{window}"] = (close - sma) / sma
        ta_groups["atr_ratio"][f"atr_ratio_{window}"] = _atr(high, low, close, window) / close
        ta_groups["rolling_std"][f"rolling_std_{window}"] = _rolling_std(ret1, window)
        ta_groups["bollinger_position"][f"bollinger_pos_{window}"] = np.divide(
            close - sma,
            2.0 * std,
            out=np.full_like(close, np.nan),
            where=std > 0,
        )
        ta_groups["obv_slope"][f"obv_slope_{window}"] = _slope(obv, window)
        ta_groups["mfi"][f"mfi_{window}"] = _mfi(high, low, close, volume, window) / 100.0

    for fast, slow in ((6, 24), (12, 26), (24, 72), (48, 168)):
        fast_ema = _ema(close, fast)
        slow_ema = _ema(close, slow)
        macd = fast_ema - slow_ema
        signal = _ema(macd, 9)
        ta_groups["macd_norm"][f"macd_{fast}_{slow}"] = macd / close
        ta_groups["macd_hist"][f"macd_hist_{fast}_{slow}"] = (macd - signal) / close

    return {
        "timestamps": timestamps,
        "target": target,
        "close": close,
        "base": base,
        "ta_groups": ta_groups,
    }


def _rankdata(values: Any) -> Any:
    import numpy as np

    arr = np.asarray(values, dtype=float)
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(arr.size, dtype=float)
    ranks[order] = np.arange(arr.size, dtype=float)
    return ranks


def _abs_spearman(x: Any, y: Any) -> float:
    import numpy as np

    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 24:
        return 0.0
    rx = _rankdata(x[mask])
    ry = _rankdata(y[mask])
    sx = float(rx.std())
    sy = float(ry.std())
    if sx <= 0 or sy <= 0:
        return 0.0
    return abs(float(np.corrcoef(rx, ry)[0, 1]))


def select_ta_features(bank: Mapping[str, Any], train_mask: Any) -> tuple[str, ...]:
    import numpy as np

    target = np.asarray(bank["target"], dtype=float)
    indices = np.flatnonzero(train_mask & np.isfinite(target))
    if indices.size < 96:
        raise ValueError("v14_training_sample_too_small_for_feature_selection")
    blocks = [block for block in np.array_split(indices, 4) if block.size >= 24]
    selected: list[str] = []
    for group_name in sorted(bank["ta_groups"]):
        candidates = bank["ta_groups"][group_name]
        if not candidates:
            continue
        names = sorted(candidates)
        rank_totals = {name: 0.0 for name in names}
        for block in blocks:
            scored = sorted(
                (
                    (_abs_spearman(np.asarray(candidates[name])[block], target[block]), name)
                    for name in names
                ),
                key=lambda item: (-item[0], item[1]),
            )
            for rank, (_, name) in enumerate(scored, start=1):
                rank_totals[name] += rank
        selected.append(min(names, key=lambda name: (rank_totals[name], name)))
    if len(selected) != 10:
        raise ValueError(f"v14_expected_10_ta_groups_got_{len(selected)}")
    return tuple(selected)


def _matrix(bank: Mapping[str, Any], selected_ta: Sequence[str]) -> tuple[Any, tuple[str, ...]]:
    import numpy as np

    names = list(sorted(bank["base"]))
    columns = [np.asarray(bank["base"][name], dtype=float) for name in names]
    lookup: dict[str, Any] = {}
    for group in bank["ta_groups"].values():
        lookup.update(group)
    for name in selected_ta:
        names.append(name)
        columns.append(np.asarray(lookup[name], dtype=float))
    return np.column_stack(columns), tuple(names)


def _time_mask(timestamps: Sequence[datetime], start: datetime, end: datetime) -> Any:
    import numpy as np

    return np.asarray([start <= stamp < end for stamp in timestamps], dtype=bool)


def _valid_rows(matrix: Any, target: Any, mask: Any) -> Any:
    import numpy as np

    return mask & np.isfinite(target) & np.isfinite(matrix).all(axis=1)


def _fit_predict_fold(
    matrix: Any,
    target: Any,
    train_mask: Any,
    validation_mask: Any,
    test_mask: Any,
    *,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    import numpy as np
    from xgboost import XGBRegressor

    train = _valid_rows(matrix, target, train_mask)
    validation = _valid_rows(matrix, target, validation_mask)
    test = _valid_rows(matrix, target, test_mask)
    if int(train.sum()) < 2000 or int(validation.sum()) < 500 or int(test.sum()) < 500:
        raise ValueError(
            "v14_fold_insufficient_rows:"
            f"train={int(train.sum())}:validation={int(validation.sum())}:test={int(test.sum())}"
        )

    best_config: dict[str, Any] | None = None
    best_mse: float | None = None
    for index, config in enumerate(XGB_SCREEN_CONFIGS):
        model = XGBRegressor(
            objective="reg:squarederror",
            tree_method="hist",
            random_state=seed + index,
            n_jobs=2,
            verbosity=0,
            **config,
        )
        model.fit(matrix[train], target[train])
        prediction = model.predict(matrix[validation])
        mse = float(np.mean((prediction - target[validation]) ** 2))
        if best_mse is None or mse < best_mse:
            best_mse = mse
            best_config = dict(config)

    if best_config is None:
        raise RuntimeError("v14_no_xgb_configuration_selected")

    refit = train | validation
    model = XGBRegressor(
        objective="reg:squarederror",
        tree_method="hist",
        random_state=seed + 1000,
        n_jobs=2,
        verbosity=0,
        **best_config,
    )
    model.fit(matrix[refit], target[refit])
    forecasts = model.predict(matrix[test])
    return forecasts, {
        "best_config": best_config,
        "validation_mse": best_mse,
        "train_rows": int(train.sum()),
        "validation_rows": int(validation.sum()),
        "test_rows": int(test.sum()),
        "test_mask": test,
    }


def _performance(returns: Sequence[float]) -> dict[str, Any]:
    import numpy as np

    values = np.asarray(list(returns), dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {
            "observations": 0,
            "total_return": 0.0,
            "annualized_compounded_return": 0.0,
            "annualized_volatility": 0.0,
            "sharpe": None,
            "max_drawdown": 0.0,
        }
    safe = np.maximum(values, -0.999999)
    equity = np.cumprod(1.0 + safe)
    total = float(equity[-1] - 1.0)
    years = max(values.size / 8760.0, 1.0 / 8760.0)
    annualized = float((max(equity[-1], 1e-12) ** (1.0 / years)) - 1.0)
    volatility = float(np.std(values, ddof=1) * sqrt(8760.0)) if values.size > 1 else 0.0
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    sharpe = mean / std * sqrt(8760.0) if std > 0 else None
    peaks = np.maximum.accumulate(equity)
    drawdowns = equity / peaks - 1.0
    return {
        "observations": int(values.size),
        "total_return": total,
        "annualized_compounded_return": annualized,
        "annualized_volatility": volatility,
        "sharpe": float(sharpe) if sharpe is not None else None,
        "max_drawdown": float(drawdowns.min(initial=0.0)),
    }


def _strategy_returns(
    forecasts: Sequence[float],
    realized_log_returns: Sequence[float],
    *,
    cost: float,
) -> tuple[list[float], dict[str, int]]:
    import numpy as np

    forecast_arr = np.asarray(forecasts, dtype=float)
    realized = np.asarray(realized_log_returns, dtype=float)
    positions = cost_aware_positions(forecast_arr, cost_per_turnover=cost)
    previous = 0
    net: list[float] = []
    entries = 0
    exits = 0
    transitions = 0
    for position, log_return in zip(positions, realized, strict=True):
        turnover = abs(position - previous)
        if position > previous:
            entries += 1
        elif position < previous:
            exits += 1
        transitions += turnover
        simple_return = exp(float(log_return)) - 1.0
        net.append(position * simple_return - turnover * cost)
        previous = position
    return net, {
        "entries": entries,
        "exits": exits,
        "turnover_units": transitions,
    }


def evaluate_alpaca_transfer_screen(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = 141400,
) -> dict[str, Any]:
    import numpy as np

    bank = feature_bank(rows)
    timestamps: list[datetime] = list(bank["timestamps"])
    target = np.asarray(bank["target"], dtype=float)
    if len(timestamps) < 12000:
        raise ValueError(f"v14_alpaca_hourly_corpus_too_small:{len(timestamps)}")

    folds = walk_forward_folds(ALPACA_SCREEN_START, ALPACA_SCREEN_END)
    fold_results: list[dict[str, Any]] = []
    aggregate: dict[str, list[float]] = {name: [] for name in COST_SCENARIOS}
    aggregate_buy_hold: list[float] = []

    for fold in folds:
        train_mask = _time_mask(timestamps, fold.train_start, fold.train_end)
        validation_mask = _time_mask(timestamps, fold.train_end, fold.validation_end)
        test_mask = _time_mask(timestamps, fold.validation_end, fold.test_end)
        selected_ta = select_ta_features(bank, train_mask)
        matrix, feature_names = _matrix(bank, selected_ta)
        forecasts, fit = _fit_predict_fold(
            matrix,
            target,
            train_mask,
            validation_mask,
            test_mask,
            seed=seed + fold.index * 100,
        )
        test_rows = fit.pop("test_mask")
        realized = target[test_rows]
        aggregate_buy_hold.extend((np.exp(realized) - 1.0).tolist())

        scenario_results: dict[str, Any] = {}
        for name, cost in COST_SCENARIOS.items():
            net, activity = _strategy_returns(
                forecasts,
                realized,
                cost=cost,
            )
            aggregate[name].extend(net)
            scenario_results[name] = {
                **_performance(net),
                **activity,
                "cost_per_turnover": cost,
                "decision_threshold_for_position_change": LAMBDA_COST * cost,
            }

        fold_results.append({
            "fold": fold.to_dict(),
            "selected_ta_features": list(selected_ta),
            "feature_count": len(feature_names),
            "feature_names": list(feature_names),
            "fit": fit,
            "scenarios": scenario_results,
            "buy_and_hold": _performance((np.exp(realized) - 1.0).tolist()),
        })

    aggregate_results = {
        name: {
            **_performance(values),
            "cost_per_turnover": COST_SCENARIOS[name],
        }
        for name, values in aggregate.items()
    }
    base_positive_folds = sum(
        1
        for row in fold_results
        if float(row["scenarios"]["alpaca_base_30bp"]["total_return"]) > 0.0
    )
    paper = aggregate_results["paper_10bp"]
    alpaca = aggregate_results["alpaca_base_30bp"]
    positive_share = base_positive_folds / len(fold_results) if fold_results else 0.0
    paper_sharpe = paper.get("sharpe")
    alpaca_sharpe = alpaca.get("sharpe")
    worth_full_replication = bool(
        len(fold_results) >= 8
        and paper_sharpe is not None
        and float(paper_sharpe) > 0.50
        and alpaca_sharpe is not None
        and float(alpaca_sharpe) > 0.0
        and float(alpaca["total_return"]) > 0.0
        and positive_share > 0.50
    )

    return {
        "schema_version": "graen.v14-r1.alpaca-transfer-screen.v1",
        "methodology_version": METHODOLOGY_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "family": FAMILY,
        "source_manifest": campaign_manifest(),
        "data": {
            "market": "BTC/USD",
            "provider": "Alpaca US crypto feed",
            "timeframe": "1Hour",
            "normalized_bar_count": len(timestamps),
            "first_bar": timestamps[0].isoformat() if timestamps else None,
            "last_bar": timestamps[-1].isoformat() if timestamps else None,
            "requested_start": ALPACA_SCREEN_START.isoformat(),
            "requested_end": ALPACA_SCREEN_END.isoformat(),
        },
        "walk_forward": {
            "fold_count": len(fold_results),
            "train_months": TRAIN_MONTHS,
            "validation_months": VALIDATION_MONTHS,
            "test_months": TEST_MONTHS,
            "step_months": STEP_MONTHS,
            "folds": fold_results,
        },
        "aggregate": aggregate_results,
        "buy_and_hold": _performance(aggregate_buy_hold),
        "broker_feasibility_gate": {
            "worth_full_original_replication": worth_full_replication,
            "alpaca_base_positive_fold_count": base_positive_folds,
            "alpaca_base_positive_fold_share": positive_share,
            "requirements": {
                "minimum_folds": 8,
                "paper_10bp_sharpe_gt": 0.50,
                "alpaca_base_30bp_sharpe_gt": 0.0,
                "alpaca_base_30bp_total_return_gt": 0.0,
                "alpaca_base_positive_fold_share_gt": 0.50,
            },
        },
        "interpretation": (
            "BROKER_FEASIBILITY_SURVIVES_RUN_FULL_ORIGINAL_REPLICATION"
            if worth_full_replication
            else "BROKER_FEASIBILITY_FAIL_DO_NOT_PROMOTE"
        ),
        "original_replication_complete": False,
        "methodology_verification_complete": False,
        "development_gate_opened": False,
        "velum_opened": False,
        "validation_opened": False,
        "holdout_opened": False,
        "promotion_eligible": False,
        "research_only": True,
        "execution_authority": False,
        "broker_orders_possible": False,
        "crypto_execution_enabled": False,
        "live_execution_authorized": False,
    }
