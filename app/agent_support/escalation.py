"""Idempotent transition contract; persistence adapter applies actions transactionally."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def alert_key(finding: Mapping[str, str]) -> str:
    return ":".join((finding["source"], finding["code"], finding["reference"]))


def reconcile(
    reasons: list[dict[str, str]],
    open_alerts: Mapping[str, Mapping[str, Any]],
    *,
    observed_at: datetime,
    assessed_sources: set[str],
) -> list[dict[str, Any]]:
    """Return only changed open/resolved records; caller must persist in one transaction.

    A repeated observation updates last_observed_at on the same durable row, rather
    than creating a new alert. Healthy checks create no alert.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observation must be timezone aware")
    current: dict[str, dict] = {}
    for item in reasons:
        if item["state"] == "HEALTHY":
            continue
        key = alert_key(item)
        current[key] = {
            "alert_key": key, "reason_code": item["code"],
            "severity": "critical" if item["state"] == "BLOCKED" else "warning",
            "source_component": item["source"], "evidence_reference": item["reference"],
            "last_observed_at": observed_at.isoformat(), "state": "OPEN",
        }
    actions = []
    for key, item in sorted(current.items()):
        previous = open_alerts.get(key)
        item["first_observed_at"] = (previous or {}).get("first_observed_at") or observed_at.isoformat()
        actions.append(item)
    for key, previous in sorted(open_alerts.items()):
        # An unavailable source cannot prove that an earlier incident cleared.
        if key not in current and previous.get("source_component") in assessed_sources:
            actions.append({"alert_key": key, "state": "RESOLVED",
                            "resolved_at": observed_at.isoformat(),
                            "last_observed_at": previous["last_observed_at"]})
    return actions
