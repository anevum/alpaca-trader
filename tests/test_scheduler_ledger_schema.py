from pathlib import Path


def test_scheduler_ledger_is_unique_and_lease_recoverable():
    sql = Path("database/20260930032000_anevum_scheduler_ledger.sql").read_text()
    assert "job_key text not null unique" in sql
    assert "lease_until timestamptz" in sql
    assert "for update" in sql.lower()
    assert "lease_recovery" in sql
    assert "error_classification in" in sql
    assert "'transient_infrastructure'" in sql
    assert "'dependency_unavailable'" in sql
    assert "'evidence_unavailable'" in sql


def test_scheduler_ledger_has_no_live_execution_authority():
    sql = Path("database/20260930032000_anevum_scheduler_ledger.sql").read_text().lower()
    forbidden = [
        "insert into private.trading_order_intents",
        "insert into private.trading_orders",
        "update private.trading_strategy_versions",
        "crypto_execution_enabled",
    ]
    assert all(fragment not in sql for fragment in forbidden)


def test_registry_contains_one_owner_for_required_workflows():
    import json

    registry = json.loads(Path("app/schedule_registry.json").read_text())
    ids = [row["workflow_id"] for row in registry["workflows"]]
    assert len(ids) == len(set(ids))
    required = {
        "rhen.preflight",
        "rhen.market_open",
        "rhen.session_close",
        "rhen.research.daily",
        "velum.equity.replay",
        "velum.crypto.replay",
        "graen.research.checkpoint",
        "rhen.weekly_review",
        "iren.scheduler.health",
    }
    assert required.issubset(ids)
    nostra = [row for row in registry["workflows"] if row["subsystem"] == "NOSTRA"]
    assert nostra == []
    independent = {
        row["subsystem"]: row
        for row in registry.get("independent_runtimes", [])
    }
    assert independent["NOSTRA"]["mode"] == "independent_autorun"
    assert independent["NOSTRA"]["scheduler_owned"] is False
    assert independent["NOSTRA"]["health_owner"] == "IREN"
    assert independent["NOSTRA"]["research_only"] is True
    assert independent["NOSTRA"]["execution_authority"] is False


def test_scheduler_registry_is_packaged_by_production_image():
    registry = Path("app/schedule_registry.json")
    assert registry.exists()
    dockerfile = Path("Dockerfile").read_text()
    assert "COPY app ./app" in dockerfile


def test_scheduler_gateway_validates_and_persists_native_payloads():
    gateway = Path("foundation/iren_gateway.py").read_text()
    assert "def scheduler_claim" in gateway
    assert "def scheduler_complete" in gateway
    assert "invalid_claim_shape:" in gateway
    assert "for update" in gateway.lower()
