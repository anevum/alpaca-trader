from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any

METHODOLOGY_VERSION = "asc-strategy-family-registry-v1"

VALID_STATUSES = frozenset(
    {
        "PRODUCTION_CHAMPION",
        "SPEC_ONLY",
        "ACTIVE_SHADOW",
        "DEVELOPMENT",
        "FROZEN_VALIDATION",
        "HOLDOUT_COMPLETE",
        "CHALLENGER_CANDIDATE",
        "RETIRED",
    }
)

CURRENT_FAMILY = {
    "family_key": "rhen-long-momentum-v1",
    "strategy_name": "rolling_momentum_vwap",
    "direction": "LONG",
    "asset_class": "US_EQUITY",
    "status": "PRODUCTION_CHAMPION",
    "production_role": "CURRENT_CHAMPION",
    "regime_evidence": {},
    "research_execution_authority": False,
    "automatic_promotion_authorized": False,
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def _normalize_family(row: Mapping[str, Any]) -> dict[str, Any]:
    family_key = str(row.get("family_key") or "").strip()
    strategy_name = str(row.get("strategy_name") or "").strip()
    status = str(row.get("status") or "SPEC_ONLY").strip().upper()
    direction = str(row.get("direction") or "").strip().upper()
    asset_class = str(row.get("asset_class") or "US_EQUITY").strip().upper()

    if not family_key:
        raise ValueError("strategy family requires family_key")
    if not strategy_name:
        raise ValueError("strategy family requires strategy_name")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid strategy family status: {status}")
    if direction not in {"LONG", "SHORT", "LONG_SHORT", "MARKET_NEUTRAL"}:
        raise ValueError(f"invalid strategy family direction: {direction}")
    if row.get("research_execution_authority") is True:
        raise ValueError(
            "research family registry cannot grant execution authority"
        )
    if row.get("automatic_promotion_authorized") is True:
        raise ValueError(
            "research family registry cannot grant automatic promotion"
        )

    evidence = row.get("regime_evidence")
    evidence = dict(evidence) if isinstance(evidence, Mapping) else {}

    normalized = {
        "family_key": family_key,
        "strategy_name": strategy_name,
        "direction": direction,
        "asset_class": asset_class,
        "status": status,
        "production_role": row.get("production_role"),
        "regime_evidence": evidence,
        "parent_family_key": row.get("parent_family_key"),
        "hypothesis": row.get("hypothesis"),
        "research_execution_authority": False,
        "automatic_promotion_authorized": False,
    }
    normalized["family_fingerprint"] = _canonical_hash(normalized)
    return normalized


def build_strategy_family_registry(
    additional_families: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic research registry with the live champion declared."""

    families = [_normalize_family(CURRENT_FAMILY)]
    seen = {families[0]["family_key"]}

    for row in additional_families or ():
        family = _normalize_family(row)
        key = family["family_key"]
        if key in seen:
            raise ValueError(f"duplicate strategy family key: {key}")
        seen.add(key)
        families.append(family)

    families.sort(key=lambda row: row["family_key"])
    registry_material = {
        "methodology_version": METHODOLOGY_VERSION,
        "families": families,
    }
    return {
        **registry_material,
        "registry_fingerprint": _canonical_hash(registry_material),
        "current_production_family": CURRENT_FAMILY["family_key"],
        "research_only_registry": True,
        "routing_execution_authority": False,
        "automatic_promotion_authorized": False,
    }


def add_research_family(
    registry: Mapping[str, Any],
    family: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a new registry value; does not persist or deploy the family."""

    existing = [
        row
        for row in registry.get("families") or []
        if isinstance(row, Mapping)
        and row.get("family_key") != CURRENT_FAMILY["family_key"]
    ]
    return build_strategy_family_registry([*existing, family])
