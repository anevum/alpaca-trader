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
