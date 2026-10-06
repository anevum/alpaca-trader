import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.iren import codex_github, executor_service

from app.iren.codex_github import inspect_runtime_inventory
from app.iren.core import reduce_state
from app.iren.service import POLICY, _executor_root_url, _runtime_evidence
from app.iren.topology import INVENTORY, OPTIONAL_INVENTORY, bounded_health, topology


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
        "RESEARCH_AGENT": bounded_health("RESEARCH_AGENT", {
            "ok": True,
            "runtime_provenance": _provenance("research-agent", "dep-research-agent"),
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
    assert value["required_inventory"] == sorted(
        [name for name in INVENTORY if name not in OPTIONAL_INVENTORY] + ["IREN"]
    )
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


def test_runtime_evidence_never_invents_completion_from_absent_topology():
    value = _runtime_evidence({})
    assert value["schema_version"] == "iren_runtime_evidence.v1"
    assert value["complete_deployment_inventory"] is False
    assert value["required_inventory"] == []
    assert value["inventory_gaps"] == {}


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


def _status(name):
    spec = codex_github.PINNED_RUNTIME_STATUSES[name]
    return {
        "context": spec["context"],
        "state": "success",
        "target_url": (
            f"https://railway.com/project/{spec['project_id']}/service/{spec['service_id']}"
            f"?id=02f9199d-0684-4365-9e1b-b14e17097c63&environmentId={spec['environment_id']}"
        ),
    }


def _http_error(code, message="sensitive-provider-body"):
    request = httpx.Request("GET", "https://api.github.com/secret-url?token=secret-token")
    response = httpx.Response(code, request=request, json={"message": message})
    return httpx.HTTPStatusError("secret-exception", request=request, response=response)


@pytest.mark.parametrize("failure,reason", [
    (_http_error(401), "provider_authentication_failed"),
    (_http_error(403, "Resource not accessible by personal access token"), "provider_forbidden"),
    (_http_error(404), "provider_not_found_or_inaccessible"),
    (_http_error(429), "provider_rate_limited"),
    (_http_error(503), "provider_http_error"),
    (httpx.ReadTimeout("secret-timeout"), "provider_timeout"),
    (httpx.ConnectError("secret-connection"), "provider_unavailable"),
    (ValueError("secret-json"), "provider_invalid_response"),
])
def test_unverified_provider_evidence_cannot_fill_topology_gaps():
    services = _services()
    services["VELUM"]["runtime_identity"]["git_commit"] = None
    services["VELUM"]["runtime_identity"]["deployment_id"] = None
    observation = _observation(services)
    observation["provider_inventory"] = {"services": {"VELUM": {
        "verified": False, "revision": SHA, "deployment": "untrusted-deployment",
    }}}
    state, _ = reduce_state({}, observation, POLICY)
    value = topology(observation, state, _self_identity())
    assert value["inventory_complete"] is False
    assert set(value["inventory_gaps"]["VELUM"]) == {"revision", "deployment"}
    row = next(row for row in value["services"] if row["service_id"] == "VELUM")
    assert row["deployment"] is None


def test_inventory_http_200_does_not_mean_evidence_is_verified(monkeypatch, repo, token, reason):
    monkeypatch.setenv("IREN_EXECUTOR_TOKEN", "t" * 40)
    monkeypatch.setenv("IREN_GITHUB_TOKEN", token)
    monkeypatch.setenv("IREN_GITHUB_REPOSITORY", repo)
    calls = []

    async def github(method, path):
        calls.append(path)
        raise _http_error(403)

    monkeypatch.setattr(executor_service.runtime, "_github_json", github)
    with TestClient(executor_service.app) as client:
        response = client.get("/v1/evidence/runtime-inventory", headers={"x-anevum-scheduler-token": "t" * 40})
    assert response.status_code == 200
    inventory = response.json()
    assert inventory["complete"] is False
    assert inventory["read_only"] is True
    assert inventory["provider_write_authority"] is False
    for row in inventory["services"].values():
        assert row["verified"] is False
        assert row["deployment"] is None
        assert row["reason"] == reason
    assert len(calls) == (2 if reason == "provider_forbidden" else 0)
