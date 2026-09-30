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

    registry = json.loads(Path("infra/schedule_registry.json").read_text())
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
    assert nostra and all(row["enabled"] is False for row in nostra)
