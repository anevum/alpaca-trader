"""Pure, versioned state reduction. Identical inputs produce identical output."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from app.protected_configuration import diff_snapshots, legacy_partial_drift

UTC = timezone.utc


def identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fresh(stamp: str | None, now: datetime, seconds: int) -> bool:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        age = (now - parsed).total_seconds()
        return 0 <= age <= seconds
    except (ValueError, TypeError):
        return False


def _workflow_version_key(value: object) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value or "0").split("."))
    except ValueError:
        return (0,)


def _workflow_run_key(row: dict) -> tuple[str, tuple[int, ...], str, str]:
    return (
        str(row.get("scheduled_at") or ""),
        _workflow_version_key(row.get("workflow_version")),
        str(row.get("completed_at") or ""),
        str(row.get("started_at") or ""),
    )

def reduce_state(previous: dict, observation: dict, policy: dict) -> tuple[dict, list[dict]]:
    now = datetime.fromisoformat(observation["observed_at"])
    if now.tzinfo is None:
        raise ValueError("observation_requires_timezone")
    previous = deepcopy(previous)
    # A retry or a concurrent writer cannot count the same observation twice.
    if previous.get("observed_at") and observation["observed_at"] <= previous["observed_at"]:
        raise ValueError("observation_not_newer")
    issues: dict[str, dict] = {}

    def issue(key: str, severity: str, reason: str):
        issues[key] = {"severity": severity, "reason": reason}

    services = observation.get("services", {})
    for item in policy["services"]:
        name = item["id"]
        row = services.get(name, {})
        # Inventory-only services are observed for deployment provenance but do
        # not silently expand the established IREN incident policy. Their
        # missing identity remains a hard Codex-verification blocker.
        if item.get("inventory_only"):
            continue
        if row.get("ok") is not True:
            issue(f"service.{name}", "warning", "health_unavailable_or_unhealthy")
    rhen = services.get("RHEN", {})
    if rhen.get("ok") is True:
        for flag in ("startup_reconciled", "reconciliation_safe"):
            if rhen.get(flag) is not True:
                issue(f"safety.{flag}", "critical", "broker_reconciliation_not_confirmed")
        if rhen.get("crypto_execution_enabled") is True:
            if (
                rhen.get("crypto_broker_writes_allowed")
                is not policy["crypto_live_broker_writes_expected"]
            ):
                issue(
                    "safety.crypto_execution",
                    "critical",
                    "crypto_broker_write_authority_policy_mismatch",
                )
        if rhen.get("strategy_version_id") != policy["expected_strategy"]:
            issue("safety.strategy_identity", "critical", "unexpected_live_strategy")
        persistence = rhen.get("persistence", {})
        if persistence.get("enabled") is not True:
            issue("evidence.disabled", "critical", "durable_evidence_disabled")
        if persistence.get("last_error"):
            issue("evidence.delivery", "warning", "evidence_delivery_error")
        if not fresh(persistence.get("last_sent_at"), now, policy["stale_after_seconds"]):
            issue("evidence.stale", "warning", "evidence_delivery_timestamp_stale")
        dropped = persistence.get("dropped_count")
        prior_dropped = previous.get("metrics", {}).get("dropped_count")
        if isinstance(dropped, int) and dropped > 0 and prior_dropped is None:
            issue("evidence.loss", "warning", "existing_runtime_event_loss")
        if isinstance(dropped, int) and isinstance(prior_dropped, int) and dropped > prior_dropped:
            issue("evidence.loss", "warning", "runtime_event_loss_increasing")
    config = observation.get("configuration", {})
    baseline = previous.get("configuration_baseline")
    configuration_drift = None
    configuration_review = previous.get("configuration_review")
    if not config.get("fingerprint"):
        issue("configuration.unavailable", "warning", "protected_configuration_unobserved")
    elif baseline and baseline.get("fingerprint") != config["fingerprint"]:
        issue("configuration.drift", "critical", "protected_configuration_changed")
        if baseline.get("snapshot") and config.get("schema_version") == "rhen_protected_configuration.v2":
            configuration_drift = diff_snapshots(baseline["snapshot"], config)
        else:
            configuration_drift = legacy_partial_drift(baseline, config)
        configuration_review = {
            "status": "CONFIGURATION_REVIEW_REQUIRED",
            "baseline_fingerprint": baseline.get("fingerprint"),
            "current_fingerprint": config.get("fingerprint"),
            "comparison_completeness": configuration_drift["comparison_completeness"],
            "current_snapshot": config if config.get("schema_version") == "rhen_protected_configuration.v2" else None,
        }
    elif not baseline:
        if config.get("schema_version") == "rhen_protected_configuration.v2":
            issue(
                "configuration.review_required",
                "critical",
                "protected_configuration_baseline_acceptance_required",
            )
            configuration_review = {
                "status": "CONFIGURATION_REVIEW_REQUIRED",
                "baseline_fingerprint": None,
                "current_fingerprint": config.get("fingerprint"),
                "comparison_completeness": "unavailable",
                "current_snapshot": config,
            }
        else:
            baseline = {
                **config,
                "observed_at": observation["observed_at"],
                "basis": "observed_production_baseline",
            }
    elif baseline.get("fingerprint") == config.get("fingerprint"):
        configuration_review = None
    scheduler = observation.get("scheduler", {})
    scheduler_enabled = scheduler.get("enabled") is not False
    if scheduler_enabled:
        if scheduler.get("configured") is not True or scheduler.get("last_error"):
            issue("scheduler.health", "critical", "canonical_scheduler_degraded")
        elif (
            not scheduler.get("running_job")
            and not fresh(
                scheduler.get("last_success_at"),
                now,
                policy["stale_after_seconds"],
            )
            and not fresh(
                scheduler.get("started_at"),
                now,
                policy["stale_after_seconds"],
            )
        ):
            issue("scheduler.stale", "critical", "scheduler_tick_stale")
    # Use only the latest current execution per workflow; old misses remain history.
    latest: dict[str, dict] = {}
    if scheduler_enabled:
        for row in observation.get("runs", []):
            key = row.get("workflow_id", "")
            if key not in latest or _workflow_run_key(row) > _workflow_run_key(latest[key]):
                latest[key] = row
    for key, row in latest.items():
        if key.startswith("verification.") or not fresh(row.get("scheduled_at"), now, 86400):
            continue
        if key in {"rhen.preflight", "rhen.market_open"}:
            scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            if scheduled.astimezone(ZoneInfo("America/New_York")).date() != now.astimezone(ZoneInfo("America/New_York")).date():
                continue
        if row.get("status") in {"FAILED", "MISSED"}:
            issue("workflow." + key, "warning", "current_workflow_" + row["status"].lower())
        if row.get("status") == "RUNNING" and row.get("lease_until"):
            if datetime.fromisoformat(row["lease_until"].replace("Z", "+00:00")) < now:
                issue("workflow." + key, "warning", "workflow_lease_expired")
    for name in ("VELUM", "GRAEN"):
        row = services.get(name, {})
        if row.get("broker_orders_possible") is True or row.get("execution_authority") is True:
            issue("safety." + name, "critical", "research_execution_authority_present")

    incidents = previous.get("incidents", {})
    events = []
    for key in sorted(set(issues) | set(incidents)):
        row = incidents.get(key, {"status": "CLOSED", "failure_count": 0, "recovery_count": 0, "episode": 0})
        current = issues.get(key)
        prior_severity = row.get("severity")
        if current:
            row.update(current)
            row["failure_count"] += 1
            row["recovery_count"] = 0
            threshold = 1 if current["severity"] == "critical" else policy["failure_observations"]
            if row["status"] != "OPEN" and row["failure_count"] >= threshold:
                row.update(status="OPEN", opened_at=observation["observed_at"], episode=row["episode"] + 1)
                events.append({"key": key, "transition": "OPEN", "episode": row["episode"], **current})
            elif row["status"] == "OPEN" and current["severity"] == "critical" and prior_severity != "critical":
                events.append({"key": key, "transition": "ESCALATED", "episode": row["episode"], **current})
        else:
            row["failure_count"] = 0
            row["recovery_count"] += 1
            if row["status"] == "OPEN" and row["recovery_count"] >= policy["recovery_observations"]:
                row.update(status="CLOSED", closed_at=observation["observed_at"])
                events.append({"key": key, "transition": "RECOVERED", "episode": row["episode"], "severity": row["severity"], "reason": "healthy_observations_confirmed"})
        incidents[key] = row
    opened = [row for row in incidents.values() if row["status"] == "OPEN"]
    state = "ATTENTION_REQUIRED" if any(row["severity"] == "critical" for row in opened) else "DEGRADED" if opened or issues else "HEALTHY"
    for event in events:
        event["event_key"] = identity({"version": policy["version"], "key": event["key"], "transition": event["transition"], "episode": event["episode"]})
        event["route"] = "iren-control"
    output = {
        "version": policy["version"], "observed_at": observation["observed_at"], "state": state,
        "source_commit": observation.get("source_commit"), "incidents": incidents,
        "configuration_baseline": baseline,
        "configuration_current": config,
        "configuration_drift": configuration_drift,
        "configuration_review": configuration_review,
        "services": services,
        "scheduler": scheduler,
        "logical_subsystems": {"NOSTRA": "WAITING_ACTIVATION", "RESEARCH_AGENT": "ON_DEMAND_SLEEP_ALLOWED"},
        "metrics": {"dropped_count": rhen.get("persistence", {}).get("dropped_count")},
        "authority": {"deterministic": True, "model_invoked": False, "trading_mutations": False, "protected_actions": policy["protected_actions"]},
    }
    return output, events
