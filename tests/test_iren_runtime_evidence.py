import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from app.iren.codex_github import inspect_runtime_inventory
from app.iren.core import reduce_state
from app.iren.service import POLICY, _executor_root_url, _runtime_evidence
from app.iren.topology import INVENTORY, bounded_health, topology


STAMP = datetime(2026, 10, 2, 17, 30, tzinfo=timezone.utc)
STAMP_TEXT = STAMP.isoformat()
SHA = "a" * 40


def _provenance(version: str, deployment: str) -> dict:
    return {
        "system_version": version,
        "git_commit": SHA,
        "deployment_id": deployment,
        "runtime_started_at": STAMP_TEXT,
    }


def _services() -> dict:
    return {
        "RHEN": bounded_health("RHEN", {
            "ok": True,
            "startup_reconciled": True,
            "reconciliation_safe": True,
            "runtime_provenance": _provenance("rhen", "dep-rhen"),
            "persistence": {
                "enabled": True,
                "dropped_count": 0,
                "last_sent_at": STAMP_TEXT,
                "strategy_version_id": POLICY["expected_strategy"],
            },
            "crypto": {"execution_enabled": False},
        }),
        "VELUM": bounded_health("VELUM", {
            "ok": True,
            "running": False,
            "broker_orders_possible": False,
            "runtime_provenance": _provenance("velum", "dep-velum"),
        }),
        "PREOPEN": bounded_health("PREOPEN", {
            "ok": True,
            "worker_alive": True,
            "shadow_only": True,
            "runtime_provenance": _provenance("preopen", "dep-preopen"),
        }),
        "GRAEN": bounded_health("GRAEN", {
            "ok": True,
            "running": True,
            "broker_orders_possible": False,
            "execution_authority": False,
            "runtime_provenance": _provenance("graen", "dep-graen"),
        }),
        "GRAEN_EXECUTOR": bounded_health("GRAEN_EXECUTOR", {
            "ok": True,
            "running": True,
            "broker_orders_possible": False,
            "execution_authority": False,
            "runtime_provenance": _provenance("graen-executor", "dep-graen-executor"),
        }),
        "RESEARCH_AGENT": bounded_health("RESEARCH_AGENT", {
            "ok": True,
            "runtime_provenance": _provenance("research-agent", "dep-research-agent"),
        }),
        "CRYPTO_EDGE": bounded_health("CRYPTO_EDGE", {
            "ok": True,
            "running": True,
            "broker_orders_possible": False,
            "execution_authority": False,
            "runtime_provenance": _provenance("crypto-edge", "dep-crypto-edge"),
        }),
        "IREN_EXECUTOR": bounded_health("IREN_EXECUTOR", {
            "ok": True,
            "version": "iren-executor-v1.1.0",
            "revision": SHA,
            "deployment": "dep-iren-executor",
        }),
        "NOSTRA": bounded_health("NOSTRA", {
            "ok": True,
            "runtime_version": "nostra-runtime-v1.0.0",
            "research_only": True,
            "execution_authority": False,
            "runtime_provenance": _provenance("nostra-runtime-v1.0.0", "dep-nostra"),
        }),
    }


def _observation(services=None) -> dict:
    return {
        "observed_at": STAMP_TEXT,
        "services": services if services is not None else _services(),
        "configuration": {"fingerprint": "fixed"},
        "runs": [],
        "scheduler": {
            "configured": True,
            "last_error": False,
            "last_success_at": STAMP_TEXT,
        },
    }


def _self_identity() -> dict:
    return {
        "version": POLICY["version"],
        "deployment": "dep-iren",
        "revision": SHA,
        "started_at": STAMP_TEXT,
        "configuration_identity": "fixed",
    }


def test_inventory_covers_current_independent_runtimes_and_completes_only_with_identity():
    observation = _observation()
    state, _ = reduce_state({}, observation, POLICY)
    value = topology(observation, state, _self_identity())

    assert value["inventory_complete"] is True
    assert value["inventory_gaps"] == {}
    assert value["required_inventory"] == sorted([*INVENTORY, "IREN"])
    assert _runtime_evidence({"topology": value})["complete_deployment_inventory"] is True


def test_missing_deployment_identity_fails_closed_with_specific_gap():
    services = _services()
    services["VELUM"] = deepcopy(services["VELUM"])
    services["VELUM"]["runtime_identity"]["deployment_id"] = None
    observation = _observation(services)
    state, _ = reduce_state({}, observation, POLICY)
    value = topology(observation, state, _self_identity())

    assert value["inventory_complete"] is False
    assert value["inventory_gaps"]["VELUM"] == ["deployment"]
    evidence = _runtime_evidence({"topology": value})
    assert evidence["complete_deployment_inventory"] is False
    assert evidence["inventory_gaps"]["VELUM"] == ["deployment"]


def test_direct_executor_identity_is_bounded_without_inference():
    observed = bounded_health("IREN_EXECUTOR", {
        "ok": True,
        "version": "iren-executor-v1.1.0",
        "revision": SHA,
        "deployment": "dep-executor",
    })
    assert observed["runtime_identity"] == {
        "system_version": "iren-executor-v1.1.0",
        "git_commit": SHA,
        "deployment_id": "dep-executor",
        "runtime_started_at": None,
    }

    absent = bounded_health("IREN_EXECUTOR", {"ok": True})
    assert absent["runtime_identity"]["git_commit"] is None
    assert absent["runtime_identity"]["deployment_id"] is None


def test_inventory_only_probes_do_not_expand_existing_incident_policy():
    services = _services()
    for key in ("GRAEN_EXECUTOR", "RESEARCH_AGENT", "CRYPTO_EDGE", "IREN_EXECUTOR", "NOSTRA"):
        services.pop(key)
    state, events = reduce_state({}, _observation(services), POLICY)
    assert state["state"] == "HEALTHY"
    assert events == []
    assert not any(key.startswith("service.") and key.split(".", 1)[1] in {
        "GRAEN_EXECUTOR", "RESEARCH_AGENT", "CRYPTO_EDGE", "IREN_EXECUTOR", "NOSTRA"
    } for key in state["incidents"])


def test_runtime_evidence_never_invents_completion_from_absent_topology():
    value = _runtime_evidence({})
    assert value["schema_version"] == "iren_runtime_evidence.v1"
    assert value["complete_deployment_inventory"] is False
    assert value["required_inventory"] == []
    assert value["inventory_gaps"] == {}


def test_provider_status_supplies_exact_pinned_runtime_identity():
    async def get(path):
        assert path == "commits/1663c5ab4516df890cdea70929a9d14275bfc7d6/status"
        return {
            "statuses": [
                {
                    "context": "RHEN - rhen-velum",
                    "state": "success",
                    "target_url": (
                        "https://railway.com/project/808098a9-937e-4ca4-ac98-dd2dcfef5d0c/"
                        "service/55a298e1-ea60-4342-a10f-a736a2d71f8c"
                        "?id=02f9199d-0684-4365-9e1b-b14e17097c63"
                        "&environmentId=63a64723-574d-497b-b01b-a9fef7ea78ab"
                    ),
                },
                {
                    "context": "RHEN - rhen-crypto-edge-discovery",
                    "state": "success",
                    "target_url": (
                        "https://railway.com/project/808098a9-937e-4ca4-ac98-dd2dcfef5d0c/"
                        "service/4ed9d192-102c-4b66-8ed7-b9a650a064c5"
                        "?id=184a4dad-5ae3-4a06-9b17-b7da9a76c868"
                        "&environmentId=63a64723-574d-497b-b01b-a9fef7ea78ab"
                    ),
                },
            ]
        }

    value = asyncio.run(inspect_runtime_inventory(get))
    assert value["read_only"] is True
    assert value["provider_write_authority"] is False
    assert value["services"]["VELUM"]["deployment"] == "02f9199d-0684-4365-9e1b-b14e17097c63"
    assert value["services"]["CRYPTO_EDGE"]["deployment"] == "184a4dad-5ae3-4a06-9b17-b7da9a76c868"


def test_provider_status_fails_closed_on_wrong_service_or_environment():
    async def get(path):
        return {
            "statuses": [
                {
                    "context": "RHEN - rhen-velum",
                    "state": "success",
                    "target_url": (
                        "https://railway.com/project/808098a9-937e-4ca4-ac98-dd2dcfef5d0c/"
                        "service/WRONG?id=02f9199d-0684-4365-9e1b-b14e17097c63"
                        "&environmentId=63a64723-574d-497b-b01b-a9fef7ea78ab"
                    ),
                },
                {
                    "context": "RHEN - rhen-crypto-edge-discovery",
                    "state": "failed",
                    "target_url": (
                        "https://railway.com/project/808098a9-937e-4ca4-ac98-dd2dcfef5d0c/"
                        "service/4ed9d192-102c-4b66-8ed7-b9a650a064c5"
                        "?id=184a4dad-5ae3-4a06-9b17-b7da9a76c868"
                        "&environmentId=63a64723-574d-497b-b01b-a9fef7ea78ab"
                    ),
                },
            ]
        }

    value = asyncio.run(inspect_runtime_inventory(get))
    assert value["services"]["VELUM"]["verified"] is False
    assert value["services"]["VELUM"]["deployment"] is None
    assert value["services"]["CRYPTO_EDGE"]["verified"] is False


def test_topology_uses_provider_evidence_only_when_health_identity_is_missing():
    services = _services()
    for key in ("VELUM", "CRYPTO_EDGE"):
        services[key] = deepcopy(services[key])
        services[key]["runtime_identity"]["git_commit"] = None
        services[key]["runtime_identity"]["deployment_id"] = None
    observation = _observation(services)
    observation["provider_inventory"] = {
        "schema_version": "iren_provider_inventory.v1",
        "read_only": True,
        "provider_write_authority": False,
        "services": {
            "VELUM": {
                "verified": True,
                "revision": SHA,
                "deployment": "dep-velum-provider",
            },
            "CRYPTO_EDGE": {
                "verified": True,
                "revision": SHA,
                "deployment": "dep-crypto-provider",
            },
        },
    }
    state, _ = reduce_state({}, observation, POLICY)
    value = topology(observation, state, _self_identity())
    rows = {row["service_id"]: row for row in value["services"]}

    assert value["inventory_complete"] is True
    assert rows["VELUM"]["deployment"] == "dep-velum-provider"
    assert rows["CRYPTO_EDGE"]["deployment"] == "dep-crypto-provider"
    assert rows["VELUM"]["observation_source"] == "iren_http_probe+github_railway_status"


def test_provider_evidence_never_overrides_self_reported_runtime_identity():
    services = _services()
    observation = _observation(services)
    observation["provider_inventory"] = {
        "services": {
            "VELUM": {
                "verified": True,
                "revision": "c" * 40,
                "deployment": "provider-would-conflict",
            }
        }
    }
    state, _ = reduce_state({}, observation, POLICY)
    value = topology(observation, state, _self_identity())
    row = next(row for row in value["services"] if row["service_id"] == "VELUM")
    assert row["deployment"] == "dep-velum"
    assert row["revision"] == SHA
    assert row["observation_source"] == "iren_http_probe"


def test_executor_root_url_accepts_configured_job_accept_endpoint():
    assert _executor_root_url(
        "http://iren-executor.railway.internal:8080/v1/jobs/accept"
    ) == "http://iren-executor.railway.internal:8080"
    assert _executor_root_url(
        "http://iren-executor.railway.internal:8080/v1/jobs/accept/"
    ) == "http://iren-executor.railway.internal:8080"
    assert _executor_root_url(
        "http://iren-executor.railway.internal:8080"
    ) == "http://iren-executor.railway.internal:8080"
