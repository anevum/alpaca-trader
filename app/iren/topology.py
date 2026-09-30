"""Adapt existing endpoints without importing or changing the observed runtimes."""
from copy import deepcopy
from datetime import datetime, timezone
from app.contracts.service_health import ServiceObservation
from .core import fresh

INVENTORY = {
    "RHEN": ("SERVICE", "alpaca-trader", "Live execution; research reporting, evidence and legacy NOSTRA/GRAEN calculations remain coupled"),
    "VELUM": ("WORKER", "rhen-velum", "Independent replay worker; counterfactual functions also remain in RHEN research reporting"),
    "GRAEN": ("SERVICE", "graen", "Independent mathematical and theoretical research runtime with durable problem queue, run ledger, and artifact store"),
    "PREOPEN": ("WORKER", "rhen-preopen-state", "Independent shadow capture; not an independently activated NOSTRA forecaster"),
}

def bounded_health(name, body):
    if not isinstance(body, dict) or type(body.get("ok")) is not bool:
        raise ValueError("malformed_health")
    fields = ("ok", "startup_reconciled", "reconciliation_safe", "broker_orders_possible",
              "execution_authority", "running", "worker_alive", "shadow_only")
    for key in fields:
        if key in body and type(body[key]) is not bool:
            raise ValueError("malformed_health_flag")
    result = {key: body[key] for key in fields if key in body}
    result["last_error"] = bool(body.get("last_error"))
    provenance = body.get("runtime_provenance") or {}
    if not isinstance(provenance, dict):
        raise ValueError("malformed_provenance")
    result["runtime_identity"] = {k: provenance.get(k) for k in
        ("system_version", "git_commit", "deployment_id", "runtime_started_at")}
    if name == "GRAEN" and not result["runtime_identity"].get("system_version"):
        result["runtime_identity"]["system_version"] = body.get("program")
    for key, value in result["runtime_identity"].items():
        if value is not None and (not isinstance(value, str) or len(value) > 128):
            raise ValueError("malformed_runtime_identity")
    started = result["runtime_identity"].get("runtime_started_at")
    if started:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("malformed_runtime_timestamp")
    if name == "RHEN":
        persistence = body.get("persistence")
        crypto = body.get("crypto")
        if not isinstance(persistence, dict) or not isinstance(crypto, dict):
            raise ValueError("malformed_rhen_dependencies")
        result["persistence"] = {k: persistence.get(k) for k in ("enabled", "dropped_count", "last_sent_at")}
        result["persistence"]["last_error"] = bool(persistence.get("last_error"))
        result["strategy_version_id"] = persistence.get("strategy_version_id")
        result["crypto_execution_enabled"] = crypto.get("execution_enabled")
        result["source_commit"] = provenance.get("git_commit")
    return result

def topology(observation, state, self_identity):
    stamp = observation["observed_at"]
    now = datetime.fromisoformat(stamp)
    rows = []
    incidents = state.get("incidents", {})
    for name, (kind, service_name, scope) in INVENTORY.items():
        health = observation["services"].get(name, {})
        alive = health.get("ok") is True
        ready = alive and not health.get("last_error", False)
        if name == "RHEN":
            ready = ready and health.get("startup_reconciled") is True and health.get("reconciliation_safe") is True
        status = "OFFLINE" if not alive else "RUNNING" if health.get("running", True) else "IDLE"
        if alive and not ready:
            status = "DEGRADED"
        if incidents.get("service." + name, {}).get("status") == "OPEN":
            status = "INCIDENT"
        identity = health.get("runtime_identity") or {}
        row = ServiceObservation(service_id=name, runtime_kind=kind, independent_runtime=True,
            service_name=service_name, service_version=identity.get("system_version"),
            deployment=identity.get("deployment_id"), revision=identity.get("git_commit"),
            started_at=identity.get("runtime_started_at"), observed_at=stamp,
            last_heartbeat_at=stamp if alive else None, liveness=alive, readiness=ready, status=status,
            current_activity="worker active" if health.get("running") else None,
            last_success=stamp if ready else None,
            last_failure=health.get("error_type") or ("reported_error" if health.get("last_error") else None),
            configuration_identity=observation.get("configuration", {}).get("fingerprint") if name == "RHEN" else None,
            observation_source="iren_http_probe", scope=scope)
        rows.append(row.model_dump())
    rows.append(ServiceObservation(schema_version="service_heartbeat.v1", service_id="IREN",
        runtime_kind="SERVICE", independent_runtime=True, service_name="rhen-research-scheduler",
        service_version=self_identity["version"], deployment=self_identity.get("deployment"),
        revision=self_identity.get("revision"), started_at=self_identity["started_at"],
        observed_at=stamp, last_heartbeat_at=stamp, liveness=True, readiness=True, status="RUNNING",
        current_activity="deterministic supervision and canonical scheduler",
        last_success=stamp, dependency_state={"durable_state": "commit_required"},
        configuration_identity=self_identity["configuration_identity"],
        observation_source="durable_iren_commit", scope="Independent from RHEN; shares its process with the scheduler").model_dump())
    rows.append(ServiceObservation(service_id="NOSTRA", runtime_kind="SUBSYSTEM",
        independent_runtime=False, observed_at=stamp, status="UNKNOWN",
        current_activity="Independent forecast hooks disabled; legacy calculations in RHEN",
        observation_source="audited_registry", scope="Not independently deployed; preopen capture is separate").model_dump())
    scheduler = observation.get("scheduler", {})
    scheduler_ok = scheduler.get("configured") is True and not scheduler.get("last_error") and (
        fresh(scheduler.get("last_success_at"), now, 180) or fresh(scheduler.get("started_at"), now, 180))
    rhen = observation["services"].get("RHEN", {})
    evidence = rhen.get("persistence", {})
    evidence_incident = any(k.startswith("evidence.") and v.get("status") == "OPEN" for k, v in incidents.items())
    dependencies = {
        "scheduler": {"status": "RUNNING" if scheduler_ok else "DEGRADED", "last_success": scheduler.get("last_success_at"),
                      "independent_runtime": False, "host": "IREN"},
        "data_plane": {"status": "RUNNING", "basis": "this snapshot is visible only after its durable commit"},
        "broker": {"status": "RUNNING" if rhen.get("ok") is True and rhen.get("reconciliation_safe") is True else "UNKNOWN",
                   "basis": "RHEN reconciliation observation; no independent broker probe"},
        "telemetry": {"status": "STALE" if not fresh(evidence.get("last_sent_at"), now, 180) else "DEGRADED" if evidence.get("last_error") or evidence_incident else "RUNNING",
                      "last_success": evidence.get("last_sent_at"), "dropped_count": evidence.get("dropped_count")},
    }
    return {"schema_version": "runtime_topology.v1", "observed_at": stamp, "services": rows,
            "dependencies": dependencies, "inventory_verified_at": "2026-09-30T14:12:00+00:00",
            "inventory_source": "Railway audit; no continuous provider-inventory permission"}

def project_status(saved, now, stale_after=180):
    """Recompute freshness on every read, even while the writer is dead."""
    result = deepcopy(saved)
    state = result.get("state") or {}
    stale = not fresh(state.get("observed_at"), now, stale_after)
    result["stale"] = stale
    if stale:
        state["state"] = "STALE"
        for row in state.get("topology", {}).get("services", []):
            if row.get("independent_runtime"):
                row.update(status="STALE", readiness=False, liveness=None)
        for row in state.get("topology", {}).get("dependencies", {}).values():
            row["status"] = "STALE"
    result["state"] = state
    result["action_required"] = stale or state.get("state") != "HEALTHY"
    return result
