"""Canonical protected RHEN configuration identity and deterministic drift diff."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any

SCHEMA_VERSION = "rhen_protected_configuration.v2"
DRIFT_SCHEMA_VERSION = "iren_configuration_drift.v2"

_SETLIKE_PATH_TOKENS = {
    "symbols", "scan_symbols", "confirmation_symbols", "universe_always_include",
    "extended_equity_symbols", "extended_equity_confirmation_symbols",
}

_CLASSIFICATION_PREFIXES = (
    ("comparison.strategy", "strategy_identity"),
    ("protected.strategy", "strategy_identity"),
    ("comparison.entry", "entry_logic"),
    ("protected.entry", "entry_logic"),
    ("comparison.exit", "exit_logic"),
    ("protected.exit", "exit_logic"),
    ("comparison.market", "market_quality_filter"),
    ("protected.market", "market_quality_filter"),
    ("comparison.universe", "universe_selection"),
    ("protected.universe", "universe_selection"),
    ("protected.sizing", "position_sizing"),
    ("protected.portfolio", "portfolio_risk"),
    ("protected.risk", "portfolio_risk"),
    ("protected.execution", "execution_authority"),
    ("protected.broker", "broker_authority"),
    ("protected.session", "session_behavior"),
    ("comparison.research", "research_only"),
    ("protected.research", "research_only"),
)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def canonicalize(value: Any, *, path: str = "") -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(item, path=f"{path}.{key}" if path else str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (canonicalize(item, path=path) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, tuple):
        token = path.rsplit(".", 1)[-1].lower()
        items = [canonicalize(item, path=path) for item in value]
        return sorted(items, key=str) if token in _SETLIKE_PATH_TOKENS else items
    if isinstance(value, list):
        token = path.rsplit(".", 1)[-1].lower()
        items = [canonicalize(item, path=path) for item in value]
        return sorted(items, key=str) if token in _SETLIKE_PATH_TOKENS else items
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    return value


def _fingerprint_material(comparison: Mapping[str, Any], protected: Mapping[str, Any]) -> dict:
    return canonicalize({"comparison": comparison, "protected": protected})


def fingerprint(comparison: Mapping[str, Any], protected: Mapping[str, Any]) -> str:
    material = _fingerprint_material(comparison, protected)
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def build_protected_configuration(
    *,
    comparison: Mapping[str, Any],
    protected: Mapping[str, Any],
    generated_at: str,
    runtime_commit: str | None,
    deployment_id: str | None,
    strategy_version_id: str | None,
) -> dict[str, Any]:
    canonical_comparison = canonicalize(comparison, path="comparison")
    canonical_protected = canonicalize(protected, path="protected")
    return {
        "schema_version": SCHEMA_VERSION,
        "fingerprint_algorithm": "sha256",
        "fingerprint": fingerprint(canonical_comparison, canonical_protected),
        "generated_at": generated_at,
        "source": {
            "runtime_commit": runtime_commit,
            "deployment_id": deployment_id,
            "strategy_version_id": strategy_version_id,
        },
        "comparison": canonical_comparison,
        "protected": canonical_protected,
    }


def classify_path(path: str) -> str:
    lowered = path.lower()
    for prefix, classification in _CLASSIFICATION_PREFIXES:
        if lowered.startswith(prefix):
            return classification
    if any(token in lowered for token in ("max_concurrent", "max_daily_loss", "max_total_position", "gross_exposure", "stop_risk")):
        return "portfolio_risk"
    if any(token in lowered for token in ("execution_enabled", "execution_authorized", "bot_armed", "live_execution")):
        return "execution_authority"
    if "asset_authority" in lowered or "broker" in lowered:
        return "broker_authority"
    if any(token in lowered for token in ("momentum", "vwap_edge", "opening_range", "breakout_extension")):
        return "entry_logic"
    if any(token in lowered for token in ("stop_pct", "target_pct", "hold_minutes", "thesis_exit", "profit_protect")):
        return "exit_logic"
    if any(token in lowered for token in ("spread", "quality", "bar_age", "quote_age")):
        return "market_quality_filter"
    if any(token in lowered for token in ("universe", "symbols", "exchange")):
        return "universe_selection"
    if any(token in lowered for token in ("order_notional", "risk_per_trade", "sizing")):
        return "position_sizing"
    if any(token in lowered for token in ("entry_start", "entry_cutoff", "extended_equity", "overnight")):
        return "session_behavior"
    return "operational_metadata"


def _walk_diff(before: Any, after: Any, path: str, changes: list[dict[str, Any]]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append(_change(child, "add", None, after[key]))
            elif key not in after:
                changes.append(_change(child, "remove", before[key], None))
            else:
                _walk_diff(before[key], after[key], child, changes)
        return
    if before != after:
        changes.append(_change(path, "replace", before, after))


def _change(path: str, operation: str, before: Any, after: Any) -> dict[str, Any]:
    classification = classify_path(path)
    row = {
        "path": path,
        "operation": operation,
        "classification": classification,
        "severity": "informational" if classification == "operational_metadata" else "protected",
    }
    if operation != "add":
        row["before"] = before
    if operation != "remove":
        row["after"] = after
    return row


def diff_snapshots(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    baseline_fp = str(baseline.get("fingerprint") or "")
    current_fp = str(current.get("fingerprint") or "")
    baseline_material = {
        "comparison": canonicalize(baseline.get("comparison") or {}, path="comparison"),
        "protected": canonicalize(baseline.get("protected") or {}, path="protected"),
    }
    current_material = {
        "comparison": canonicalize(current.get("comparison") or {}, path="comparison"),
        "protected": canonicalize(current.get("protected") or {}, path="protected"),
    }
    changes: list[dict[str, Any]] = []
    _walk_diff(baseline_material, current_material, "", changes)
    changes.sort(key=lambda row: row["path"])
    return {
        "schema_version": DRIFT_SCHEMA_VERSION,
        "baseline_fingerprint": baseline_fp,
        "current_fingerprint": current_fp,
        "comparison_completeness": "full",
        "changed_count": sum(1 for row in changes if row["operation"] == "replace"),
        "added_count": sum(1 for row in changes if row["operation"] == "add"),
        "removed_count": sum(1 for row in changes if row["operation"] == "remove"),
        "changes": changes,
    }


def legacy_partial_drift(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": DRIFT_SCHEMA_VERSION,
        "baseline_fingerprint": str(baseline.get("fingerprint") or ""),
        "current_fingerprint": str(current.get("fingerprint") or ""),
        "comparison_completeness": "legacy_partial",
        "changed_count": 0,
        "added_count": 0,
        "removed_count": 0,
        "changes": [],
        "detail_unavailable": True,
        "legacy_note": (
            "The accepted V1 baseline stored a fingerprint/summary only. "
            "Hidden historical fingerprint inputs cannot be reconstructed honestly; "
            "review the full current V2 snapshot before explicit baseline acceptance."
        ),
    }
