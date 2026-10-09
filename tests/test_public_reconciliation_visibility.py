"""Public-facing reconciliation checks remain observable without flooding activity."""
from datetime import datetime, timedelta, timezone

from app.rhen_core.store import RhenCoreStore


UTC = timezone.utc


def test_public_feed_collapses_successful_checks_but_keeps_safety_transitions(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-observability-test.db")
    start = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    checks = [
        ("safe-first", 0, True),
        ("safe-again", 1, True),
        ("blocked-first", 2, False),
        ("blocked-again", 3, False),
        ("recovered", 4, True),
        ("safe-final", 5, True),
    ]
    events = [{
        "event_key": f"check-{key}",
        "event_type": "reconciliation",
        "occurred_at": (start + timedelta(minutes=minute)).isoformat(),
        "run_id": "run-public",
        "strategy_version_id": "test",
        "source": "rhen-core",
        "payload": {
            "safe_to_enter": safe,
            "reason": "reconciled" if safe else "unknown_open_orders:1",
            "account_secret": "NEVER_PUBLIC",
        },
    } for key, minute, safe in checks]
    store.ingest_events(events)
    observed = start + timedelta(minutes=5, seconds=5)
    feed = store.public_live_feed(now=observed)

    assert feed["broker_reconciliation"]["state"] == "SAFE"
    assert feed["broker_reconciliation"]["checks_2h"] == len(checks)
    assert feed["broker_reconciliation"]["last_checked_at"] == (
        start + timedelta(minutes=5)
    ).isoformat()

    notifications = [e for e in feed["events"] if e["type"] == "reconciliation"]
    assert len(notifications) == 2
    assert notifications[0]["kind"] == "system"
    assert "recovered" in notifications[0]["label"].lower()
    assert notifications[1]["kind"] == "warning"
    assert "blocked" in notifications[1]["label"].lower()
    assert "NEVER_PUBLIC" not in str(feed)
    assert "account_secret" not in str(feed)

    later = store.public_live_feed(now=observed + timedelta(minutes=11))
    assert later["broker_reconciliation"]["state"] == "STALE"
    assert later["broker_reconciliation"]["last_checked_at"] == feed["broker_reconciliation"]["last_checked_at"]


def test_initial_broker_failure_is_visible_without_a_prior_safe_check(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-initial-broker-failure.db")
    observed = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    store.ingest_events([{
        "event_key": "initial-blocker",
        "event_type": "reconciliation",
        "occurred_at": observed.isoformat(),
        "run_id": "run-blocked",
        "strategy_version_id": "test",
        "source": "rhen-core",
        "payload": {"safe_to_enter": False, "reason": "untracked_positions:1"},
    }])

    feed = store.public_live_feed(now=observed)
    assert feed["broker_reconciliation"]["state"] == "BLOCKED"
    notifications = [e for e in feed["events"] if e["type"] == "reconciliation"]
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "warning"
