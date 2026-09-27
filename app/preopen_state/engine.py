from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import source_catalog, symbol_tier
from .client import AlpacaReadOnlyMarketData
from .config import PreOpenSettings
from .features import (
    build_symbol_features,
    flatten_numeric_features,
    parse_bar_time,
    summarize_state,
)
from .model import LogisticArtifact

NY = ZoneInfo("America/New_York")
CHECKPOINTS = ("08:00", "08:30", "09:00", "09:15", "09:25", "09:29")
OUTCOME_DUE = {5: "09:36", 30: "10:01", 60: "10:31", 120: "11:31"}


def checkpoint_time(trade_date, label: str) -> datetime:
    hour, minute = (int(value) for value in label.split(":"))
    return datetime.combine(trade_date, time(hour, minute), tzinfo=NY)


class PreOpenStateEngine:
    def __init__(self, settings: PreOpenSettings):
        self.settings = settings
        self.data = AlpacaReadOnlyMarketData(settings)
        self.model: LogisticArtifact | None = None
        self.model_error: str | None = None
        if settings.preopen_model_json:
            try:
                self.model = LogisticArtifact.from_json(settings.preopen_model_json)
            except Exception as exc:
                self.model_error = str(exc)

    async def snapshot(
        self,
        *,
        checkpoint: str,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if checkpoint not in CHECKPOINTS:
            raise ValueError("unsupported pre-open checkpoint")
        now = (observed_at or datetime.now(NY)).astimezone(NY)
        cutoff = checkpoint_time(now.date(), checkpoint)
        if cutoff > now + timedelta(minutes=1):
            raise ValueError("checkpoint is in the future")

        minute_start = cutoff.replace(hour=4, minute=0, second=0, microsecond=0)
        history_start = cutoff - timedelta(days=45)
        symbols = self.settings.all_symbols
        bars = await self.data.bars_many(
            symbols,
            start=minute_start,
            end=cutoff,
            timeframe="1Min",
        )
        daily = await self.data.daily_bars_many(
            symbols,
            start=history_start,
            end=cutoff,
        )

        by_symbol: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            by_symbol[symbol] = build_symbol_features(
                symbol=symbol,
                bars=bars.get(symbol, []),
                daily_bars=daily.get(symbol, []),
                cutoff=cutoff,
                tier=symbol_tier(symbol, self.settings.target_symbols),
            )

        catalog = [item.to_dict() for item in source_catalog()]
        unavailable = sum(item["tier"] == "UNAVAILABLE" for item in catalog)
        available = sum(value["status"] == "AVAILABLE" for value in by_symbol.values())
        degraded = sum(value["status"] == "DEGRADED" for value in by_symbol.values())
        missing = sum(value["status"] == "UNAVAILABLE" for value in by_symbol.values())
        data_quality = (
            "AVAILABLE"
            if missing == 0 and degraded == 0
            else ("DEGRADED" if available > 0 else "INSUFFICIENT")
        )

        flat = flatten_numeric_features(by_symbol)
        if self.model is None:
            forecast: dict[str, Any] = {
                "status": "UNTRAINED" if self.model_error is None else "MODEL_REJECTED",
                "target": None,
                "probability": None,
                "model_key": None,
                "reason": self.model_error or "No validated shadow model artifact configured.",
            }
        else:
            probability = self.model.probability(flat)
            forecast = {
                "status": "AVAILABLE" if probability is not None else "FEATURES_INCOMPLETE",
                "target": self.model.target,
                "probability": probability,
                "model_key": self.model.model_key,
                "trained_through": self.model.trained_through,
                "artifact_status": self.model.status,
                "checksum": self.model.checksum,
            }

        return {
            "schema_version": "preopen-snapshot-v1",
            "feature_set_version": "preopen-features-v1",
            "snapshot_key": f"preopen-state-v1:{now.date().isoformat()}:{checkpoint}",
            "trade_date": now.date().isoformat(),
            "checkpoint": checkpoint,
            "observed_at": now.isoformat(),
            "cutoff": cutoff.isoformat(),
            "shadow_only": True,
            "data_quality_state": data_quality,
            "source_summary": {
                "alpaca_feed": self.settings.data_feed,
                "symbols_available": available,
                "symbols_degraded": degraded,
                "symbols_missing": missing,
                "institutional_sources_unavailable": unavailable,
                "proxy_warning": (
                    "ETF proxies are labeled proxies and are not treated as equivalent "
                    "to futures, direct yields/FX, VIX/VX, auction imbalance, or order-book data."
                ),
            },
            "features": by_symbol,
            "state_summary": summarize_state(
                by_symbol,
                target_symbols=self.settings.target_symbols,
            ),
            "forecast": forecast,
            "source_catalog": catalog,
        }

    async def outcome(
        self,
        *,
        horizon_minutes: int,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if horizon_minutes not in OUTCOME_DUE:
            raise ValueError("unsupported outcome horizon")
        now = (observed_at or datetime.now(NY)).astimezone(NY)
        open_at = datetime.combine(now.date(), time(9, 30), tzinfo=NY)
        target_at = open_at + timedelta(minutes=horizon_minutes)
        if now < target_at:
            raise ValueError("outcome horizon has not completed")

        minute_bars = await self.data.bars_many(
            self.settings.target_symbols,
            start=open_at,
            end=target_at + timedelta(minutes=1),
            timeframe="1Min",
        )
        daily = await self.data.daily_bars_many(
            self.settings.target_symbols,
            start=open_at - timedelta(days=10),
            end=open_at,
        )

        results: dict[str, Any] = {}
        for symbol in self.settings.target_symbols:
            bars = []
            for bar in minute_bars.get(symbol, []):
                stamp = parse_bar_time(str(bar.get("t") or ""))
                if open_at <= stamp < target_at:
                    bars.append(bar)
            bars.sort(key=lambda item: str(item.get("t") or ""))
            prior = None
            prior_candidates = []
            for bar in daily.get(symbol, []):
                stamp = parse_bar_time(str(bar.get("t") or ""))
                if stamp.date() < now.date() and bar.get("c") is not None:
                    prior_candidates.append((stamp, float(bar["c"])))
            if prior_candidates:
                prior = sorted(prior_candidates, key=lambda item: item[0])[-1][1]

            if bars:
                open_price = float(bars[0].get("o") or bars[0].get("c"))
                end_price = float(bars[-1].get("c"))
                ret = (end_price / open_price - 1.0) * 100.0
                highs = [float(bar["h"]) for bar in bars if bar.get("h") is not None]
                lows = [float(bar["l"]) for bar in bars if bar.get("l") is not None]
                realized_range = (
                    (max(highs) / min(lows) - 1.0) * 100.0
                    if highs and lows and min(lows) > 0
                    else None
                )
                gap_return = (
                    (open_price / prior - 1.0) * 100.0 if prior else None
                )
                results[symbol] = {
                    "status": "AVAILABLE",
                    "open_price": open_price,
                    "end_price": end_price,
                    "return_from_open_pct": ret,
                    "absolute_return_pct": abs(ret),
                    "realized_range_pct": realized_range,
                    "up": int(ret > 0),
                    "gap_return_pct": gap_return,
                    "gap_up": int(gap_return > 0) if gap_return is not None else None,
                    "bar_count": len(bars),
                }
            else:
                results[symbol] = {
                    "status": "UNAVAILABLE",
                    "open_price": None,
                    "end_price": None,
                    "return_from_open_pct": None,
                    "absolute_return_pct": None,
                    "realized_range_pct": None,
                    "up": None,
                    "gap_return_pct": None,
                    "gap_up": None,
                    "bar_count": 0,
                }

        return {
            "schema_version": "preopen-outcome-v1",
            "outcome_key": f"preopen-outcome-v1:{now.date().isoformat()}:{horizon_minutes}",
            "trade_date": now.date().isoformat(),
            "horizon_minutes": horizon_minutes,
            "observed_at": now.isoformat(),
            "target_at": target_at.isoformat(),
            "shadow_only": True,
            "targets": results,
        }
