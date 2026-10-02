from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROVENANCE_SERVICES = (
    ROOT / "app" / "research_agent" / "service.py",
    ROOT / "app" / "preopen_state" / "service.py",
    ROOT / "app" / "velum_service.py",
    ROOT / "graen" / "crypto" / "service_v6.py",
)


def test_legacy_read_only_health_contracts_publish_railway_provenance():
    for path in PROVENANCE_SERVICES:
        source = path.read_text()
        assert '"runtime_provenance"' in source, path
        assert "RAILWAY_GIT_COMMIT_SHA" in source, path
        assert "RAILWAY_DEPLOYMENT_ID" in source, path


def test_provenance_patch_does_not_grant_broker_or_execution_authority():
    research = (ROOT / "app" / "research_agent" / "service.py").read_text()
    preopen = (ROOT / "app" / "preopen_state" / "service.py").read_text()
    velum = (ROOT / "app" / "velum_service.py").read_text()
    crypto = (ROOT / "graen" / "crypto" / "service_v6.py").read_text()

    assert '"broker_calls": False' in research
    assert '"shadow_only": True' in preopen
    assert '"broker_orders_possible": False' in velum
    assert '"broker_orders_possible": False' in crypto
    assert '"execution_authority": False' in crypto
