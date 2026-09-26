from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MANIFEST_PATH = Path("research/residual-downshock-rebound-v2.1.json")
CHECKSUM_EXCLUDED = {"manifest_checksum_sha256", "source_commit_sha"}


def canonical_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(payload))
    freeze = dict(clone.get("freeze") or {})
    for key in CHECKSUM_EXCLUDED:
        freeze.pop(key, None)
    clone["freeze"] = freeze
    return clone


def manifest_checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_manifest_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_manifest(path: str | Path = MANIFEST_PATH, *, verify: bool = True) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_manifest(payload)
    if verify:
        expected = str(payload["freeze"]["manifest_checksum_sha256"])
        actual = manifest_checksum(payload)
        if actual != expected:
            raise ValueError(f"manifest checksum mismatch: expected {expected}, got {actual}")
    return payload


def validate_manifest(m: dict[str, Any]) -> None:
    if m.get("experiment", {}).get("semantic_id") != "residual-downshock-rebound-v2.1":
        raise ValueError("wrong experiment")
    if m["parameter_grid"]["configuration_count"] != 6:
        raise ValueError("configuration count must be 6")
    if m["horizons"]["primary"] != 30:
        raise ValueError("primary horizon must remain 30 minutes")
    if m["entry"]["same_bar_close_execution_prohibited"] is not True:
        raise ValueError("same-bar execution must remain prohibited")
    roles = [w["role"] for w in m["data"]["windows"]]
    if "development" not in roles or "validation" not in roles or "holdout" not in roles:
        raise ValueError("stage windows incomplete")
    if any(w["access"] != "locked" for w in m["data"]["windows"] if w["role"] in {"validation", "holdout", "quarantine"}):
        raise ValueError("later stages must be locked in the frozen manifest")
    if m["stage_access"]["quarantine"] != "never accessible under v2.1":
        raise ValueError("quarantine must remain inaccessible")


def expand_parameter_grid(m: dict[str, Any]) -> list[dict[str, Any]]:
    grid = m["parameter_grid"]["tunable_parameters"]
    out = []
    idx = 1
    for z in grid["residual_z_threshold"]:
        for floor in grid["absolute_residual_floor"]:
            out.append({
                "configuration_id": f"RDR21-{idx:02d}",
                "residual_z_threshold": float(z),
                "absolute_residual_floor": float(floor),
            })
            idx += 1
    if len(out) != m["parameter_grid"]["configuration_count"]:
        raise ValueError("grid expansion does not match frozen configuration_count")
    return out


def five_minute_bin_end(minute_of_day: int) -> int | None:
    start = 9 * 60 + 30
    end = 16 * 60
    if minute_of_day < start or minute_of_day >= end:
        return None
    offset = minute_of_day - start
    return start + ((offset // 5) + 1) * 5


def aggregate_five_minute(bars: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for bar in bars:
        minute = int(bar["minute_of_day"])
        bucket = five_minute_bin_end(minute)
        if bucket is None:
            continue
        buckets.setdefault(bucket, []).append(bar)
    out: dict[int, dict[str, float]] = {}
    for bucket, rows in sorted(buckets.items()):
        rows = sorted(rows, key=lambda r: int(r["minute_of_day"]))
        out[bucket] = {
            "open": float(rows[0]["open"]),
            "high": max(float(r["high"]) for r in rows),
            "low": min(float(r["low"]) for r in rows),
            "close": float(rows[-1]["close"]),
            "volume": sum(float(r.get("volume", 0)) for r in rows),
            "raw_minute_count": len(rows),
        }
    return out


def synchronized_returns(
    stock: dict[int, dict[str, float]],
    market: dict[int, dict[str, float]],
    sector: dict[int, dict[str, float]],
) -> list[dict[str, float]]:
    common = sorted(set(stock) & set(market) & set(sector))
    out = []
    for t in common:
        prior = t - 5
        if prior not in stock or prior not in market or prior not in sector:
            continue
        out.append({
            "t": t,
            "stock_return": stock[t]["close"] / stock[prior]["close"] - 1.0,
            "market_return": market[t]["close"] / market[prior]["close"] - 1.0,
            "sector_return": sector[t]["close"] / sector[prior]["close"] - 1.0,
        })
    return out


@dataclass(frozen=True)
class ResidualModel:
    alpha: float
    beta_market: float
    beta_sector_excess: float


def fit_residual_model(rows: list[dict[str, float]], *, variance_floor: float = 1e-10) -> ResidualModel | None:
    if len(rows) < 3:
        return None
    y = [r["stock_return"] for r in rows]
    x1 = [r["market_return"] for r in rows]
    x2 = [r["sector_return"] - r["market_return"] for r in rows]
    # Deterministic 3x3 normal-equation solve with intercept.
    n = float(len(rows))
    a = [
        [n, sum(x1), sum(x2)],
        [sum(x1), sum(v*v for v in x1), sum(p*q for p, q in zip(x1, x2))],
        [sum(x2), sum(p*q for p, q in zip(x1, x2)), sum(v*v for v in x2)],
    ]
    b = [sum(y), sum(p*q for p, q in zip(x1, y)), sum(p*q for p, q in zip(x2, y))]
    # Gaussian elimination.
    aug = [row[:] + [rhs] for row, rhs in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < variance_floor:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        aug[col] = [v/div for v in aug[col]]
        for r in range(3):
            if r == col:
                continue
            factor = aug[r][col]
            aug[r] = [v - factor*w for v, w in zip(aug[r], aug[col])]
    return ResidualModel(aug[0][3], aug[1][3], aug[2][3])


def residual(row: dict[str, float], model: ResidualModel) -> float:
    return row["stock_return"] - (
        model.alpha
        + model.beta_market * row["market_return"]
        + model.beta_sector_excess * (row["sector_return"] - row["market_return"])
    )


def median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    if not n:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0


def robust_z(value: float, history: list[float], *, min_scale: float = 1e-6) -> float | None:
    if not history:
        return None
    center = median(history)
    mad = median([abs(x - center) for x in history])
    scale = max(1.4826 * mad, min_scale)
    return (value - center) / scale


def is_downshock(resid: float, z: float, *, residual_floor: float, z_threshold: float) -> bool:
    return resid <= -abs(residual_floor) and z <= -abs(z_threshold)


def apply_round_trip_cost(entry: float, exit_price: float, *, full_spread_bps: float, slippage_bps_per_side: float) -> float:
    half_spread = full_spread_bps / 2.0 / 10000.0
    slip = slippage_bps_per_side / 10000.0
    buy = entry * (1.0 + half_spread + slip)
    sell = exit_price * (1.0 - half_spread - slip)
    return sell / buy - 1.0


def suppress_overlaps(events: list[dict[str, Any]], cooldown_minutes: int = 30) -> list[dict[str, Any]]:
    kept = []
    last_by_symbol: dict[str, int] = {}
    for event in sorted(events, key=lambda e: (e["decision_minute"], e["symbol"])):
        sym = str(event["symbol"])
        minute = int(event["decision_minute"])
        if sym in last_by_symbol and minute - last_by_symbol[sym] < cooldown_minutes:
            continue
        kept.append(event)
        last_by_symbol[sym] = minute
    return kept


def stage_allowed(m: dict[str, Any], stage: str, prior: dict[str, Any] | None = None) -> bool:
    if stage == "development":
        return True
    if stage == "validation":
        return bool(prior and prior.get("stage") == "development" and prior.get("verdict") == "PASS" and prior.get("manifest_checksum") == m["freeze"]["manifest_checksum_sha256"] and prior.get("selected_configuration_id"))
    if stage == "holdout":
        return bool(prior and prior.get("stage") == "validation" and prior.get("verdict") == "PASS" and prior.get("manifest_checksum") == m["freeze"]["manifest_checksum_sha256"] and prior.get("selected_configuration_id"))
    return False


def data_quality_pass(report: dict[str, Any], m: dict[str, Any]) -> bool:
    g = m["data_quality_gate"]
    return (
        report.get("pagination_complete") is True
        and float(report.get("expected_session_representation", 0)) >= g["expected_session_representation_required"]
        and float(report.get("synchronization_completeness", 0)) >= g["minimum_synchronized_five_minute_ratio_per_symbol_window"]
        and float(report.get("market_benchmark_completeness", 0)) >= g["minimum_market_benchmark_five_minute_ratio"]
        and float(report.get("sector_benchmark_completeness", 0)) >= g["minimum_sector_benchmark_five_minute_ratio"]
    )


def deterministic_control_choice(candidates: Iterable[str], *, seed: int, key: str) -> str | None:
    values = sorted(set(candidates))
    if not values:
        return None
    scored = []
    for value in values:
        digest = hashlib.sha256(f"{seed}|{key}|{value}".encode()).hexdigest()
        scored.append((digest, value))
    return min(scored)[1]


def clustered_bootstrap_mean(events: list[dict[str, Any]], *, seed: int, resamples: int = 1000) -> list[float]:
    by_day: dict[str, list[float]] = {}
    for e in events:
        by_day.setdefault(str(e["session"]), []).append(float(e["value"]))
    days = sorted(by_day)
    if not days:
        return []
    rng = random.Random(seed)
    out = []
    for _ in range(resamples):
        sample_days = [rng.choice(days) for _ in days]
        vals = [v for d in sample_days for v in by_day[d]]
        out.append(sum(vals) / len(vals))
    return out
