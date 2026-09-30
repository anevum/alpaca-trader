from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


MANIFEST_SCHEMA_VERSION = "velum-run-manifest-v1"


def _normalize(value: Any) -> Any:
    """Convert replay inputs/results into a stable JSON-serializable form."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, set):
        return [_normalize(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("VELUM manifests cannot contain non-finite floats")
        return value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def dataset_fingerprint(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    """Hash normalized market data without depending on mapping insertion order."""
    digest = hashlib.sha256()
    for symbol in sorted((str(symbol).upper() for symbol in bars_by_symbol)):
        source = bars_by_symbol.get(symbol)
        if source is None:
            source = bars_by_symbol.get(symbol.lower(), ())
        digest.update(symbol.encode("utf-8"))
        digest.update(b"\0")
        ordered = sorted(
            (dict(bar) for bar in (source or ())),
            key=lambda bar: str(bar.get("t") or ""),
        )
        for bar in ordered:
            digest.update(canonical_json(bar).encode("utf-8"))
            digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def replay_result_fingerprint(result: Mapping[str, Any]) -> str:
    """Fingerprint the deterministic, inspectable portion of a replay result."""
    return fingerprint(
        {
            "summary": result.get("summary") or {},
            "assumptions": result.get("assumptions") or {},
            "strategy": result.get("strategy") or {},
            "sessions": result.get("sessions"),
            "trades": result.get("trades") or [],
            "equity_curve": result.get("equity_curve") or [],
        }
    )


def build_run_manifest(
    *,
    asset_class: str,
    mode: str,
    methodology_version: str,
    start: datetime,
    end: datetime,
    dataset_hash: str,
    coverage: Mapping[str, int],
    strategy_version_id: str | None,
    strategy: Mapping[str, Any],
    execution_assumptions: Mapping[str, Any],
    random_seed: int | None,
    runtime_git_commit: str | None,
    result_hash: str,
) -> dict[str, Any]:
    """Create an immutable provenance envelope for a completed VELUM run."""
    if end <= start:
        raise ValueError("VELUM manifest end must be after start")
    normalized_asset = str(asset_class).strip().lower()
    normalized_mode = str(mode).strip().upper()
    if not normalized_asset:
        raise ValueError("VELUM manifest asset_class is required")
    if not normalized_mode:
        raise ValueError("VELUM manifest mode is required")
    if not str(dataset_hash).startswith("sha256:"):
        raise ValueError("VELUM dataset fingerprint must be sha256")
    if not str(result_hash).startswith("sha256:"):
        raise ValueError("VELUM result fingerprint must be sha256")

    core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "system": "VELUM",
        "mode": normalized_mode,
        "methodology_version": methodology_version,
        "asset_class": normalized_asset,
        "range": {
            "start": start,
            "end": end,
        },
        "dataset": {
            "fingerprint": dataset_hash,
            "coverage": {
                str(symbol).upper(): int(count)
                for symbol, count in sorted(coverage.items())
            },
        },
        "strategy": {
            "version_id": strategy_version_id,
            "configuration": strategy,
        },
        "execution": execution_assumptions,
        "random_seed": random_seed,
        "runtime": {
            "repository": "anevum/alpaca-trader",
            "git_commit": runtime_git_commit,
        },
        "result_fingerprint": result_hash,
        "safety": {
            "research_only": True,
            "broker_orders_possible": False,
            "execution_authority": False,
            "promotion_authority": False,
        },
    }
    experiment_fingerprint = fingerprint(core)
    suffix = experiment_fingerprint.split(":", 1)[1][:20].upper()
    return {
        **_normalize(core),
        "experiment_fingerprint": experiment_fingerprint,
        "velum_run_id": f"VELUM-{normalized_asset.upper()}-{normalized_mode}-{suffix}",
    }
