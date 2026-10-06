"""Adapt existing endpoints without importing or changing the observed runtimes."""
from copy import deepcopy
from datetime import datetime, timezone
from app.contracts.service_health import ServiceObservation
from .core import fresh

OPTIONAL_INVENTORY = {"PREOPEN", "IREN_EXECUTOR", "CRYPTO_EDGE"}

INVENTORY = {
    "RHEN": ("SERVICE", "rhen", "Live execution; protected trading runtime"),
    "VELUM": ("WORKER", "rhen-velum", "Independent replay worker; broker-isolated research"),
    "GRAEN": ("SERVICE", "graen", "Independent mathematical and theoretical research runtime"),
    "GRAEN_EXECUTOR": ("WORKER", "graen-research-executor", "Independent research executor; no broker-order authority"),
    "PREOPEN": ("WORKER", "rhen-preopen-state", "Independent shadow capture; not an independently activated NOSTRA forecaster"),
    "RESEARCH_AGENT": ("WORKER", "rhen-research-agent", "Independent evidence-review worker"),
    "CRYPTO_EDGE": ("WORKER", "rhen-crypto-edge-discovery", "Independent crypto research/shadow service; execution disabled"),
    "IREN_EXECUTOR": ("SERVICE", "iren-executor", "Bounded IREN execution and GitHub evidence boundary"),
    "NOSTRA": ("SERVICE", "nostra", "Independent FORWARD forecasting research runtime; research-only evidence authority"),
}

def bounded_health(name, body):
    if not isinstance(body, dict) or type(body.get("ok")) is not bool:
        raise ValueError("malformed_health")
    fields = ("ok", "startup_reconciled", "reconciliation_safe", "broker_orders_possible",
              "execution_authority", "running", "worker_alive", "shadow_only", "activity_active")
    for key in fields:
        if key in body and type(body[key]) is not bool:
            raise ValueError("malformed_health_flag")
    if "current_activity" in body and (
        body["current_activity"] is not None
        and (not isinstance(body["current_activity"], str) or len(body["current_activity"]) > 240)
    ):
        raise ValueError("malformed_current_activity")
    result = {key: body[key] for key in fields if key in body}
    if "current_activity" in body:
        result["current_activity"] = body["current_activity"]
    result["last_error"] = bool(body.get("last_error"))
    provenance = body.get("runtime_provenance") or {}
    if not isinstance(provenance, dict):
        raise ValueError("malformed_provenance")
    # Prefer explicit runtime provenance, then bounded direct health fields used by
    # older read-only services. Never infer a deployment or revision from a name.
    result["runtime_identity"] = {
        "system_version": (
            provenance.get("system_version")
            or body.get("runtime_version")
            or body.get("version")
            or body.get("agent_version")
            or body.get("program")
        ),
        "git_commit": (
            provenance.get("git_commit")
            or body.get("revision")
            or body.get("source_commit")
        ),
        "deployment_id": provenance.get("deployment_id") or body.get("deployment"),
        "runtime_started_at": (
            provenance.get("runtime_started_at")
            or provenance.get("started_at")
            or body.get("started_at")
        ),
    }
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
        result["crypto_execution_mode"] = crypto.get("execution_mode")
        result["crypto_broker_writes_allowed"] = crypto.get("broker_writes_allowed")
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
        # A live background loop is process liveness, not evidence that the
        # subsystem is doing useful work right now. Default healthy runtimes to
        # IDLE and require an explicit activity signal to project RUNNING.
        activity_active = health.get("activity_active") is True
        status = "OFFLINE" if not alive else "RUNNING" if activity_active else "IDLE"
        if alive and not ready:
            status = "DEGRADED"
        if incidents.get("service." + name, {}).get("status") == "OPEN":
            status = "INCIDENT"
        identity = dict(health.get("runtime_identity") or {})
        provider = observation.get("provider_inventory") or {}
        provider_services = provider.get("services") if isinstance(provider, dict) else {}
        provider_services = provider_services if isinstance(provider_services, dict) else {}
        provider_row = provider_services.get(name)
        provider_used = False
        if isinstance(provider_row, dict) and provider_row.get("verified") is True:
            if not identity.get("git_commit") and provider_row.get("revision"):
                identity["git_commit"] = provider_row["revision"]
                provider_used = True
            if not identity.get("deployment_id") and provider_row.get("deployment"):
                identity["deployment_id"] = provider_row["deployment"]
                provider_used = True
        row = ServiceObservation(service_id=name, runtime_kind=kind, independent_runtime=True,
            service_name=service_name, service_version=identity.get("system_version"),
            deployment=identity.get("deployment_id"), revision=identity.get("git_commit"),
            started_at=identity.get("runtime_started_at"), observed_at=stamp,
            last_heartbeat_at=stamp if alive else None, liveness=alive, readiness=ready, status=status,
            current_activity=(
                health.get("current_activity")
                if activity_active and health.get("current_activity")
                else None
            ),
            last_success=stamp if ready else None,
            last_failure=health.get("error_type") or ("reported_error" if health.get("last_error") else None),
            configuration_identity=observation.get("configuration", {}).get("fingerprint") if name == "RHEN" else None,
            observation_source="iren_http_probe+github_railway_status" if provider_used else "iren_http_probe", scope=scope)
        rows.append(row.model_dump())
    rows.append(ServiceObservation(schema_version="service_heartbeat.v1", service_id="IREN",
        runtime_kind="SERVICE", independent_runtime=True, service_name="rhen-research-scheduler",
        service_version=self_identity["version"], deployment=self_identity.get("deployment"),
        revision=self_identity.get("revision"), started_at=self_identity["started_at"],
        observed_at=stamp, last_heartbeat_at=stamp, liveness=True, readiness=True, status="IDLE",
        current_activity=None,
        last_success=stamp, dependency_state={"durable_state": "commit_required"},
        configuration_identity=self_identity["configuration_identity"],
        observation_source="durable_iren_commit", scope="Independent from RHEN; shares its process with the scheduler").model_dump())
    scheduler = observation.get("scheduler", {})
    scheduler_enabled = scheduler.get("enabled") is not False
    scheduler_ok = (
        not scheduler_enabled
        or (
            scheduler.get("configured") is True
            and not scheduler.get("last_error")
            and (
                fresh(scheduler.get("last_success_at"), now, 180)
                or fresh(scheduler.get("started_at"), now, 180)
            )
        )
    )
    rhen = observation["services"].get("RHEN", {})
    evidence = rhen.get("persistence", {})
    evidence_incident = any(k.startswith("evidence.") and v.get("status") == "OPEN" for k, v in incidents.items())
    dependencies = {
        "scheduler": {"status": "DISABLED" if not scheduler_enabled else "RUNNING" if scheduler_ok else "DEGRADED",
                      "last_success": scheduler.get("last_success_at"),
                      "independent_runtime": False, "host": "IREN"},
        "data_plane": {"status": "RUNNING", "basis": "this snapshot is visible only after its durable commit"},
        "broker": {"status": "RUNNING" if rhen.get("ok") is True and rhen.get("reconciliation_safe") is True else "UNKNOWN",
                   "basis": "RHEN reconciliation observation; no independent broker probe"},
        "telemetry": {"status": "STALE" if not fresh(evidence.get("last_sent_at"), now, 180) else "DEGRADED" if evidence.get("last_error") or evidence_incident else "RUNNING",
                      "last_success": evidence.get("last_sent_at"), "dropped_count": evidence.get("dropped_count")},
    }
    required_ids = sorted(
        [service_id for service_id in INVENTORY if service_id not in OPTIONAL_INVENTORY]
        + ["IREN"]
    )
    by_id = {row.get("service_id"): row for row in rows}
    inventory_gaps = {}
    for service_id in required_ids:
        row = by_id.get(service_id) or {}
        missing = [
            field for field in ("deployment", "revision")
            if not row.get(field)
        ]
        if row.get("readiness") is not True:
            missing.append("readiness")
        if missing:
            inventory_gaps[service_id] = sorted(set(missing))
    inventory_complete = not inventory_gaps
    return {"schema_version": "runtime_topology.v1", "observed_at": stamp, "services": rows,
            "dependencies": dependencies, "required_inventory": required_ids,
            "inventory_complete": inventory_complete, "inventory_gaps": inventory_gaps,
            "inventory_verified_at": stamp if inventory_complete else None,
            "inventory_source": "runtime self-report plus bounded GitHub/Railway deployment status fallback; fail closed on missing identity"}

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
