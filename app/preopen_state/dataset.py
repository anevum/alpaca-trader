from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import symbol_tier
from .client import AlpacaReadOnlyMarketData
from .config import PreOpenSettings
from .features import build_symbol_features, flatten_numeric_features

NY = ZoneInfo("America/New_York")


def _dt(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NY)


def _stamp(bar: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00")).astimezone(NY)


def _prior_close(
    daily_bars: list[dict[str, Any]],
    *,
    day: date,
) -> float | None:
    candidates = [
        (_stamp(bar), float(bar["c"]))
        for bar in daily_bars
        if bar.get("c") is not None and _stamp(bar).date() < day
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _outcome(
    bars: list[dict[str, Any]],
    *,
    day: date,
    horizon_minutes: int,
) -> dict[str, float | int | None]:
    open_at = _dt(day, 9, 30)
    end_at = open_at + timedelta(minutes=horizon_minutes)
    selected = [
        bar for bar in bars
        if open_at <= _stamp(bar) < end_at
    ]
    selected.sort(key=lambda item: str(item.get("t") or ""))
    if not selected:
        return {
            "open_price": None,
            "end_price": None,
            "return_from_open_pct": None,
            "absolute_return_pct": None,
            "realized_range_pct": None,
            "up": None,
            "bar_count": 0,
        }
    open_price = float(selected[0].get("o") or selected[0].get("c"))
    end_price = float(selected[-1]["c"])
    ret = (end_price / open_price - 1.0) * 100.0
    highs = [float(bar["h"]) for bar in selected if bar.get("h") is not None]
    lows = [float(bar["l"]) for bar in selected if bar.get("l") is not None]
    realized_range = (
        (max(highs) / min(lows) - 1.0) * 100.0
        if highs and lows and min(lows) > 0
        else None
    )
    return {
        "open_price": open_price,
        "end_price": end_price,
        "return_from_open_pct": ret,
        "absolute_return_pct": abs(ret),
        "realized_range_pct": realized_range,
        "up": int(ret > 0),
        "bar_count": len(selected),
    }


async def build_dataset(
    settings: PreOpenSettings,
    *,
    start: date,
    end: date,
    checkpoint: str = "09:25",
    horizons: tuple[int, ...] = (5, 30, 60, 120),
) -> list[dict[str, Any]]:
    """Build point-in-time daily rows using only information available by checkpoint.

    The trading-day set is inferred from target-symbol daily bars. No feature reads
    regular-session bars after the checkpoint. Forward bars are attached only as
    explicit outcomes.
    """
    if end < start:
        raise ValueError("end must not be before start")
    client = AlpacaReadOnlyMarketData(settings)
    symbols = settings.all_symbols
    minute_start = _dt(start, 4, 0)
    minute_end = _dt(end + timedelta(days=1), 0, 0)
    daily_start = _dt(start - timedelta(days=45), 0, 0)

    minute = await client.bars_many(
        symbols,
        start=minute_start,
        end=minute_end,
        timeframe="1Min",
    )
    daily = await client.daily_bars_many(
        symbols,
        start=daily_start,
        end=minute_end,
    )

    target = settings.target_symbols[0]
    trading_days = sorted({
        _stamp(bar).date()
        for bar in daily.get(target, [])
        if start <= _stamp(bar).date() <= end
    })

    hour, minute_value = (int(item) for item in checkpoint.split(":"))
    rows: list[dict[str, Any]] = []
    for day in trading_days:
        cutoff = _dt(day, hour, minute_value)
        by_symbol: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            symbol_minute = [
                bar for bar in minute.get(symbol, [])
                if _stamp(bar).date() == day and _stamp(bar) < cutoff
            ]
            by_symbol[symbol] = build_symbol_features(
                symbol=symbol,
                bars=symbol_minute,
                daily_bars=daily.get(symbol, []),
                cutoff=cutoff,
                tier=symbol_tier(symbol, settings.target_symbols),
            )

        row: dict[str, Any] = {
            "trade_date": day.isoformat(),
            "checkpoint": checkpoint,
            "feature_set_version": "preopen-features-v1",
        }
        row.update(flatten_numeric_features(by_symbol))

        for symbol in settings.target_symbols:
            day_bars = [
                bar for bar in minute.get(symbol, [])
                if _stamp(bar).date() == day
            ]
            regular = [
                bar for bar in day_bars
                if _dt(day, 9, 30) <= _stamp(bar) < _dt(day, 16, 0)
            ]
            regular.sort(key=lambda item: str(item.get("t") or ""))
            prior = _prior_close(daily.get(symbol, []), day=day)
            actual_open = (
                float(regular[0].get("o") or regular[0].get("c"))
                if regular
                else None
            )
            open_gap = (
                (actual_open / prior - 1.0) * 100.0
                if actual_open is not None and prior
                else None
            )
            row[f"{symbol}.open_gap_return_pct"] = open_gap
            row[f"{symbol}.open_gap_up"] = (
                int(open_gap > 0) if open_gap is not None else None
            )

            for horizon in horizons:
                result = _outcome(day_bars, day=day, horizon_minutes=horizon)
                prefix = f"{symbol}.open_to_{horizon}m"
                row[f"{prefix}_return_pct"] = result["return_from_open_pct"]
                row[f"{prefix}_absolute_return_pct"] = result["absolute_return_pct"]
                row[f"{prefix}_realized_range_pct"] = result["realized_range_pct"]
                row[f"{prefix}_up"] = result["up"]
                row[f"{prefix}_bar_count"] = result["bar_count"]
        rows.append(row)
    return rows


def chronological_split(
    rows: list[dict[str, Any]],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    embargo_sessions: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0 <= validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train + validation fractions must leave holdout data")
    if embargo_sessions < 0:
        raise ValueError("embargo_sessions must be non-negative")

    ordered = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    n = len(ordered)
    train_end = max(1, int(n * train_fraction))
    validation_end = max(
        train_end + embargo_sessions + 1,
        int(n * (train_fraction + validation_fraction)),
    )
    validation_start = train_end + embargo_sessions
    holdout_start = validation_end + embargo_sessions

    train = ordered[:train_end]
    validation = ordered[validation_start:validation_end]
    holdout = ordered[holdout_start:]
    if not train or not validation or not holdout:
        raise ValueError("dataset is too small for requested chronological split and embargo")
    return train, validation, holdout
