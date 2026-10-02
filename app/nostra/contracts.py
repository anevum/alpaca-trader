from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

FORECAST_METHODOLOGY_VERSION = "FORECAST-001"
SNAPSHOT_SCHEMA_VERSION = "nostra-snapshot-v1"
PROVENANCE_SCHEMA_VERSION = "nostra-provenance-v1"
OUTCOME_SCHEMA_VERSION = "nostra-outcome-v1"
EVALUATION_SCHEMA_VERSION = "nostra-evaluation-v1"

TARGET_KINDS = frozenset({"return", "direction", "regime", "volatility", "path"})
AUTHORITY_STATES = frozenset({
    "NORMAL",
    "LOW_SUPPORT",
    "OOD",
    "DATA_DEGRADED",
    "MODEL_DEGRADED",
    "ABSTAIN",
})


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _aware(value, "datetime").isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not valid NOSTRA evidence")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported NOSTRA evidence type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize evidence deterministically for IDs and provenance fingerprints."""

    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    normalized = "".join(ch for ch in prefix.lower() if ch.isalnum() or ch in "-_")
    if not normalized:
        raise ValueError("stable ID prefix cannot be empty")
    return f"{normalized}_{digest}"


def _validate_probabilities(probabilities: Mapping[str, Any]) -> dict[str, float]:
    if not probabilities:
        raise ValueError("probabilities cannot be empty")
    normalized: dict[str, float] = {}
    for key, raw in probabilities.items():
        probability = float(raw)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(f"invalid probability for {key!r}")
        normalized[str(key)] = probability
    total = sum(normalized.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("probabilities must sum to 1")
    return normalized


def build_snapshot(
    *,
    symbol: str,
    market_lane: str,
    as_of_timestamp: datetime,
    feature_set_version: str,
    raw_features: Mapping[str, Any],
    normalized_features: Mapping[str, Any] | None = None,
    market_state: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, Any] | None = None,
    source: Mapping[str, Any] | None = None,
    candidate_id: int | None = None,
    run_id: str | None = None,
    strategy_version_id: str | None = None,
    code_sha: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an immutable point-in-time snapshot contract.

    The snapshot is only what was known at as_of_timestamp. No realized outcome
    field exists in this contract by design.
    """

    symbol = symbol.strip().upper()
    market_lane = market_lane.strip().lower()
    feature_set_version = feature_set_version.strip()
    if not symbol or not market_lane or not feature_set_version:
        raise ValueError("symbol, market_lane and feature_set_version are required")
    as_of = _aware(as_of_timestamp, "as_of_timestamp")

    core = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "symbol": symbol,
        "market_lane": market_lane,
        "as_of_timestamp": as_of.isoformat(),
        "feature_set_version": feature_set_version,
        "raw_features": _jsonable(raw_features),
        "normalized_features": _jsonable(normalized_features or {}),
        "market_state": _jsonable(market_state or {}),
        "data_quality": _jsonable(data_quality or {}),
        "source": _jsonable(source or {}),
        "candidate_id": candidate_id,
        "run_id": run_id,
        "strategy_version_id": strategy_version_id,
        "code_sha": code_sha,
        "provenance": {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            **_jsonable(provenance or {}),
        },
        "research_only": True,
        "execution_authority": False,
    }
    return {"snapshot_id": stable_id("nss", core), **core}


def build_forecast(
    *,
    snapshot_id: str,
    symbol: str,
    market_lane: str,
    as_of_timestamp: datetime,
    generated_at: datetime,
    horizon_minutes: int,
    target_kind: str,
    model_id: str,
    model_version: str,
    feature_set_version: str,
    forecast_payload: Mapping[str, Any],
    calibration_version: str | None = None,
    uncertainty: Mapping[str, Any] | None = None,
    ood_score: float | None = None,
    authority_state: str = "LOW_SUPPORT",
    methodology_version: str = FORECAST_METHODOLOGY_VERSION,
    candidate_id: int | None = None,
    run_id: str | None = None,
    strategy_version_id: str | None = None,
    code_sha: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    supersedes_forecast_id: str | None = None,
) -> dict[str, Any]:
    snapshot_id = snapshot_id.strip()
    symbol = symbol.strip().upper()
    market_lane = market_lane.strip().lower()
    target_kind = target_kind.strip().lower()
    model_id = model_id.strip()
    model_version = model_version.strip()
    feature_set_version = feature_set_version.strip()
    authority_state = authority_state.strip().upper()
    methodology_version = methodology_version.strip()

    if not snapshot_id or not symbol or not market_lane:
        raise ValueError("snapshot_id, symbol and market_lane are required")
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"unsupported target_kind: {target_kind}")
    if authority_state not in AUTHORITY_STATES:
        raise ValueError(f"unsupported authority_state: {authority_state}")
    if not model_id or not model_version or not feature_set_version:
        raise ValueError("model_id, model_version and feature_set_version are required")
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")

    as_of = _aware(as_of_timestamp, "as_of_timestamp")
    generated = _aware(generated_at, "generated_at")
    if generated < as_of:
        raise ValueError("generated_at cannot precede as_of_timestamp")

    payload = _jsonable(forecast_payload)
    probabilities = payload.get("probabilities") if isinstance(payload, Mapping) else None
    if isinstance(probabilities, Mapping):
        payload = dict(payload)
        payload["probabilities"] = _validate_probabilities(probabilities)

    if ood_score is not None:
        ood_score = float(ood_score)
        if not math.isfinite(ood_score) or not 0.0 <= ood_score <= 1.0:
            raise ValueError("ood_score must be between 0 and 1")

    core = {
        "methodology_version": methodology_version,
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "market_lane": market_lane,
        "as_of_timestamp": as_of.isoformat(),
        "generated_at": generated.isoformat(),
        "horizon_minutes": int(horizon_minutes),
        "target_kind": target_kind,
        "model_id": model_id,
        "model_version": model_version,
        "feature_set_version": feature_set_version,
        "calibration_version": calibration_version,
        "forecast_payload": payload,
        "uncertainty": _jsonable(uncertainty or {}),
        "ood_score": ood_score,
        "authority_state": authority_state,
        "candidate_id": candidate_id,
        "run_id": run_id,
        "strategy_version_id": strategy_version_id,
        "code_sha": code_sha,
        "provenance": {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            **_jsonable(provenance or {}),
        },
        "supersedes_forecast_id": supersedes_forecast_id,
        "research_only": True,
        "execution_authority": False,
    }
    return {"forecast_id": stable_id("nsf", core), **core}


def build_outcome(
    *,
    forecast_id: str,
    observed_at: datetime,
    realized_payload: Mapping[str, Any],
    methodology_version: str = OUTCOME_SCHEMA_VERSION,
    data_quality: Mapping[str, Any] | None = None,
    source_outcome_id: int | None = None,
    provenance: Mapping[str, Any] | None = None,
    supersedes_outcome_id: str | None = None,
) -> dict[str, Any]:
    forecast_id = forecast_id.strip()
    if not forecast_id:
        raise ValueError("forecast_id is required")
    observed = _aware(observed_at, "observed_at")
    core = {
        "forecast_id": forecast_id,
        "observed_at": observed.isoformat(),
        "realized_payload": _jsonable(realized_payload),
        "methodology_version": methodology_version.strip(),
        "data_quality": _jsonable(data_quality or {}),
        "source_outcome_id": source_outcome_id,
        "provenance": {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            **_jsonable(provenance or {}),
        },
        "supersedes_outcome_id": supersedes_outcome_id,
        "research_only": True,
        "execution_authority": False,
    }
    return {"outcome_id": stable_id("nso", core), **core}


def build_score_record(
    *,
    forecast_id: str,
    outcome_id: str,
    metrics: Mapping[str, Any],
    scoring_version: str,
    baseline_id: str | None = None,
    baseline_version: str | None = None,
    baseline_metrics: Mapping[str, Any] | None = None,
    skill: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    forecast_id = forecast_id.strip()
    outcome_id = outcome_id.strip()
    scoring_version = scoring_version.strip()
    if not forecast_id or not outcome_id or not scoring_version:
        raise ValueError("forecast_id, outcome_id and scoring_version are required")
    core = {
        "forecast_id": forecast_id,
        "outcome_id": outcome_id,
        "scoring_version": scoring_version,
        "metrics": _jsonable(metrics),
        "baseline_id": baseline_id,
        "baseline_version": baseline_version,
        "baseline_metrics": _jsonable(baseline_metrics or {}),
        "skill": _jsonable(skill or {}),
        "provenance": {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            **_jsonable(provenance or {}),
        },
        "research_only": True,
        "execution_authority": False,
    }
    return {"score_id": stable_id("nsc", core), **core}



def build_evaluation(
    *,
    model_id: str,
    model_version: str,
    horizon_minutes: int,
    target_kind: str,
    window_start: datetime,
    window_end: datetime,
    sample_count: int,
    through_score_id: str,
    metrics: Mapping[str, Any],
    calibration: Mapping[str, Any] | None = None,
    evaluated_at: datetime | None = None,
    methodology_version: str = EVALUATION_SCHEMA_VERSION,
    code_sha: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an immutable aggregate evaluation over already-realized scores.

    The evaluation ID intentionally excludes runtime/deployment provenance and
    wall-clock evaluation time. The same evidence cut therefore remains
    idempotent across restarts while preserving the provenance of the first
    persisted record.
    """

    model_id = model_id.strip()
    model_version = model_version.strip()
    target_kind = target_kind.strip().lower()
    methodology_version = methodology_version.strip()
    through_score_id = through_score_id.strip()
    if not model_id or not model_version or not methodology_version:
        raise ValueError("model_id, model_version and methodology_version are required")
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"unsupported target_kind: {target_kind}")
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    if not through_score_id:
        raise ValueError("through_score_id is required")

    start = _aware(window_start, "window_start")
    end = _aware(window_end, "window_end")
    evaluated = _aware(evaluated_at or datetime.now(timezone.utc), "evaluated_at")
    if end < start:
        raise ValueError("window_end cannot precede window_start")
    if evaluated < end:
        raise ValueError("evaluated_at cannot precede window_end")

    identity = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "methodology_version": methodology_version,
        "model_id": model_id,
        "model_version": model_version,
        "horizon_minutes": int(horizon_minutes),
        "target_kind": target_kind,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "sample_count": int(sample_count),
        "through_score_id": through_score_id,
        "metrics": _jsonable(metrics),
        "calibration": _jsonable(calibration or {}),
    }
    return {
        "evaluation_id": stable_id("nse", identity),
        **identity,
        "evaluated_at": evaluated.isoformat(),
        "code_sha": code_sha,
        "provenance": {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            **_jsonable(provenance or {}),
        },
        "research_only": True,
        "execution_authority": False,
    }
