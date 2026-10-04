from datetime import datetime, timezone

from foundation.command_iren import project_command
from foundation.cloudflare_access import normalize_team_domain


def test_cloudflare_access_team_domain_requires_https():
    assert normalize_team_domain("https://anevum.cloudflareaccess.com/") == (
        "https://anevum.cloudflareaccess.com"
    )


def test_command_projection_matches_fail_closed_stale_contract():
    snapshot = {
        "control": {
            "revision": 7,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-01T19:00:00+00:00",
                "topology": {
                    "services": [
                        {
                            "name": "RHEN",
                            "independent_runtime": True,
                            "status": "RUNNING",
                            "readiness": True,
                            "liveness": True,
                        }
                    ],
                    "dependencies": {"railway": {"status": "HEALTHY"}},
                },
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {
            "objectives": [
                {"objective_key": "foundation-v2", "status": "ACTIVE"}
            ],
            "jobs": [
                {"job_id": "1", "status": "RUNNING", "requires_human": False}
            ],
            "commands": [],
        },
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert projected["schema_version"] == "iren_command.v2"
    assert projected["stale"] is True
    assert projected["state"] == "STALE"
    assert projected["topology"]["services"][0]["status"] == "STALE"
    assert projected["topology"]["dependencies"]["railway"]["status"] == "STALE"
    assert projected["work"]["active_jobs"] == 1


def test_operator_projection_is_healthy_and_read_only():
    snapshot = {
        "control": {
            "revision": 9,
            "events": [{
                "event": {
                    "key": "service.GRAEN",
                    "transition": "RECOVERED",
                    "severity": "warning",
                    "reason": "healthy_observations_confirmed",
                },
                "created_at": "2026-10-02T20:59:00+00:00",
                "delivery_status": "delivered:test",
            }],
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-02T21:00:00+00:00",
                "topology": {
                    "inventory_complete": True,
                    "inventory_verified_at": "2026-10-02T21:00:00+00:00",
                    "inventory_gaps": {},
                    "services": [{
                        "service_id": "RHEN",
                        "independent_runtime": True,
                        "status": "RUNNING",
                        "readiness": True,
                    }],
                    "dependencies": {},
                },
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {
            "objectives": [{"objective_key": "stable", "status": "COMPLETE"}],
            "jobs": [],
            "commands": [],
        },
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 2, 21, 1, 0, tzinfo=timezone.utc),
    )
    operator = projected["operator"]
    assert operator["version"] == "anevum_operator.v1"
    assert operator["state"] == "HEALTHY"
    assert operator["inventory"]["ready"] == 1
    assert operator["guidance"] == []
    assert operator["authority"]["read_only_projection"] is True
    assert operator["authority"]["trading_mutations"] is False
    assert operator["recent_transitions"][0]["transition"] == "RECOVERED"


def test_operator_projection_routes_evidence_incident_to_foundation():
    snapshot = {
        "control": {
            "revision": 10,
            "state": {
                "state": "DEGRADED",
                "observed_at": "2026-10-02T21:00:00+00:00",
                "topology": {
                    "inventory_complete": True,
                    "services": [],
                    "dependencies": {},
                },
                "incidents": {
                    "evidence.delivery": {
                        "status": "OPEN",
                        "severity": "warning",
                        "reason": "evidence_delivery_error",
                    },
                },
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {"objectives": [], "jobs": [], "commands": []},
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 2, 21, 1, 0, tzinfo=timezone.utc),
    )
    guidance = projected["operator"]["guidance"]
    assert guidance[0]["target"] == "Foundation evidence"
    assert "spool" in guidance[0]["action"]



def test_command_projection_preserves_durable_job_events():
    snapshot = {
        "control": {
            "revision": 11,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-04T15:40:00+00:00",
                "topology": {"services": [], "dependencies": {}},
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {
            "objectives": [],
            "jobs": [],
            "commands": [],
            "job_events": [{
                "event_id": 41,
                "job_id": "00000000-0000-0000-0000-000000000041",
                "event_type": "RUNNING",
                "event": {"stage": "R2H"},
                "created_at": "2026-10-04T15:39:45+00:00",
                "owner_system": "VELUM",
                "objective_key": "btc-r2h",
                "title": "Replay frozen R2H candidate",
                "job_type": "REPLAY",
            }],
        },
    }

    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 4, 15, 40, 30, tzinfo=timezone.utc),
    )

    events = projected["work"]["job_events"]
    assert events[0]["event_type"] == "RUNNING"
    assert events[0]["owner_system"] == "VELUM"
    assert events[0]["event"]["stage"] == "R2H"
