from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import httpx


CONTRACT_KEYS = (
    "strategy_family",
    "strategy_version_id",
    "model_version",
    "calibration_version",
    "regime_version",
    "execution_adapter_version",
)


def crypto_execution_contract(settings: Any) -> dict[str, str]:
    return {
        "strategy_family": str(getattr(settings, "crypto_strategy_family", "") or "").strip(),
        "strategy_version_id": str(getattr(settings, "crypto_strategy_version_id", "") or "").strip(),
        "model_version": str(getattr(settings, "crypto_model_version", "") or "").strip(),
        "calibration_version": str(getattr(settings, "crypto_calibration_version", "") or "").strip(),
        "regime_version": str(getattr(settings, "crypto_regime_version", "") or "").strip(),
        "execution_adapter_version": str(
            getattr(settings, "crypto_execution_adapter_version", "") or ""
        ).strip(),
    }


def _promotion_endpoint(settings: Any) -> tuple[str, str] | None:
    url = str(getattr(settings, "foundation_ingest_url", "") or "").strip()
    token = str(getattr(settings, "foundation_ingest_token", "") or "").strip()
    if not url or not token:
        return None
    if "/v1/" in url:
        base = url.split("/v1/", 1)[0]
    else:
        base = url.rstrip("/")
    return base + "/v1/crypto-promotion-status", token


def _normalize_contract(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    return {key: str(source.get(key) or "").strip() for key in CONTRACT_KEYS}


async def fetch_crypto_promotion_status(settings: Any) -> dict[str, Any]:
    expected = crypto_execution_contract(settings)
    checked_at = datetime.now(timezone.utc).isoformat()
    endpoint = _promotion_endpoint(settings)
    if endpoint is None:
        return {
            "status": "GATED",
            "promotion_ready": False,
            "reason": "foundation_promotion_bridge_not_configured",
            "execution_contract": expected,
            "checked_at": checked_at,
            "execution_authority": False,
            "live_execution_authorized": False,
        }
    if any(not expected[key] for key in CONTRACT_KEYS):
        return {
            "status": "GATED",
            "promotion_ready": False,
            "reason": "incomplete_local_execution_contract",
            "execution_contract": expected,
            "checked_at": checked_at,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    url, token = endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url,
                headers={"x-anevum-foundation-token": token},
                params=expected,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "promotion_ready": False,
            "reason": f"promotion_status_unavailable:{type(exc).__name__}",
            "execution_contract": expected,
            "checked_at": checked_at,
            "execution_authority": False,
            "live_execution_authorized": False,
        }

    observed = _normalize_contract(
        payload.get("execution_contract")
        if isinstance(payload, Mapping)
        else None
    )
    matches = observed == expected
    ready = bool(
        isinstance(payload, Mapping)
        and payload.get("promotion_ready") is True
        and matches
    )
    return {
        "status": "PROMOTION_READY" if ready else "GATED",
        "promotion_ready": ready,
        "reason": (
            str(payload.get("reason") or "matching_protected_research_artifact")
            if ready
            else "execution_contract_mismatch"
            if not matches
            else str(payload.get("reason") or "research_promotion_gate_not_satisfied")
        ),
        "execution_contract": expected,
        "artifact": (
            dict(payload.get("artifact") or {})
            if isinstance(payload, Mapping)
            and isinstance(payload.get("artifact"), Mapping)
            else None
        ),
        "checked_at": checked_at,
        "execution_authority": False,
        "live_execution_authorized": False,
    }
