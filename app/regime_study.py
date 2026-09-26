from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from .replay import d


REGIME_FEATURES = (
    "momentum_pct",
    "vwap_edge_pct",
    "last_bar_return_pct",
    "relative_volume_ratio",
    "volume_acceleration",
    "trend_persistence",
)


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _time_band(raw: str) -> str:
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        minutes = stamp.hour * 60 + stamp.minute
    except Exception:
        return "unknown"
    if minutes < 10 * 60 + 30:
        return "open"
    if minutes < 13 * 60 + 30:
        return "midday"
    return "afternoon"


def _forward_metrics(
    rows: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    key = str(horizon)
    labels = [
        (row.get("forward") or {}).get(key) or {}
        for row in rows
    ]
    if not labels:
        return {
            "n": 0,
            "target_before_stop_rate": 0.0,
            "stop_before_target_rate": 0.0,
            "mean_mfe_pct": "0",
            "mean_mae_pct": "0",
            "mean_close_return_pct": "0",
        }

    targets = sum(bool(item.get("target_before_stop")) for item in labels)
    stops = sum(bool(item.get("stop_before_target")) for item in labels)
    return {
        "n": len(labels),
        "target_before_stop_rate": targets / len(labels),
        "stop_before_target_rate": stops / len(labels),
        "mean_mfe_pct": str(
            _mean([d(item.get("mfe_pct")) for item in labels])
        ),
        "mean_mae_pct": str(
            _mean([d(item.get("mae_pct")) for item in labels])
        ),
        "mean_close_return_pct": str(
            _mean([d(item.get("close_return_pct")) for item in labels])
        ),
    }


def _feature_value(row: dict[str, Any], feature: str) -> Decimal:
    try:
        return Decimal(str((row.get("features") or {}).get(feature, "0")))
    except Exception:
        return Decimal("0")


def _quartile_rows(
    rows: list[dict[str, Any]],
    feature: str,
    *,
    horizon: int,
) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: _feature_value(row, feature))
    if len(ordered) < 8:
        return []

    output: list[dict[str, Any]] = []
    for quartile in range(4):
        start = len(ordered) * quartile // 4
        end = len(ordered) * (quartile + 1) // 4
        bucket = ordered[start:end]
        if not bucket:
            continue
        values = [_feature_value(row, feature) for row in bucket]
        output.append(
            {
                "quartile": quartile + 1,
                "feature_min": str(min(values)),
                "feature_max": str(max(values)),
                **_forward_metrics(bucket, horizon=horizon),
            }
        )
    return output


def _describe_rows(
    rows: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    payload = _forward_metrics(rows, horizon=horizon)
    payload["feature_quartiles"] = {
        feature: summary
        for feature in REGIME_FEATURES
        if (
            summary := _quartile_rows(
                rows,
                feature,
                horizon=horizon,
            )
        )
    }
    return payload


def regime_conditioned_summary(
    period_results: list[dict[str, Any]],
    *,
    horizon: int = 15,
    min_group_n: int = 8,
) -> dict[str, Any]:
    """Describe entry behavior by predeclared market-regime and time bands.

    This function never selects or promotes a strategy. It is intended to show
    whether entry-feature relationships are stable within observable market
    contexts before any regime-conditioned Candidate D is proposed.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if min_group_n <= 0:
        raise ValueError("min_group_n must be positive")

    periods: dict[str, Any] = {}
    pooled: list[dict[str, Any]] = []

    for index, result in enumerate(period_results):
        label = str(
            result.get("period")
            or result.get("label")
            or f"period_{index + 1}"
        )
        rows = [
            row
            for row in (result.get("observations") or [])
            if row.get("quality_allowed")
        ]
        pooled.extend(rows)

        agreement: dict[str, list[dict[str, Any]]] = defaultdict(list)
        time_bands: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            regime = row.get("market_regime") or {}
            agreement[str(regime.get("agreement_band") or "unknown")].append(row)
            time_bands[_time_band(str(row.get("decision_at") or ""))].append(row)

        periods[label] = {
            "all": _describe_rows(rows, horizon=horizon),
            "agreement": {
                band: _describe_rows(bucket, horizon=horizon)
                for band, bucket in agreement.items()
                if len(bucket) >= min_group_n
            },
            "time_band": {
                band: _describe_rows(bucket, horizon=horizon)
                for band, bucket in time_bands.items()
                if len(bucket) >= min_group_n
            },
        }

    pooled_agreement: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pooled_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pooled:
        regime = row.get("market_regime") or {}
        pooled_agreement[str(regime.get("agreement_band") or "unknown")].append(row)
        pooled_time[_time_band(str(row.get("decision_at") or ""))].append(row)

    return {
        "status": "research_only",
        "horizon_minutes": horizon,
        "minimum_group_sample": min_group_n,
        "periods": periods,
        "pooled": {
            "all": _describe_rows(pooled, horizon=horizon),
            "agreement": {
                band: _describe_rows(bucket, horizon=horizon)
                for band, bucket in pooled_agreement.items()
                if len(bucket) >= min_group_n
            },
            "time_band": {
                band: _describe_rows(bucket, horizon=horizon)
                for band, bucket in pooled_time.items()
                if len(bucket) >= min_group_n
            },
        },
        "candidate_defined": False,
        "promotion_authorized": False,
        "notes": [
            "Agreement bands are predeclared: none, partial, full, unavailable.",
            "Time bands are predeclared: open before 10:30 ET, midday before 13:30 ET, afternoon thereafter.",
            "Market strength, volatility, dispersion, and VWAP distance are recorded as continuous decision-time features.",
            "No Candidate D should be frozen until the same conditional relationship appears across multiple development periods.",
        ],
    }
