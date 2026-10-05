from datetime import datetime, timezone

from app.slack_notifier import SlackNotifier


class Settings:
    slack_webhook_url = "https://hooks.slack.test/services/example"
    slack_webhook_timeout_seconds = 5.0


def queued(notifier: SlackNotifier) -> str:
    item = notifier._queue.get_nowait()
    assert item is not None
    notifier._queue.task_done()
    return item


def test_execution_event_is_queued_but_scan_noise_is_not():
    notifier = SlackNotifier(Settings())
    notifier.record_event(
        {
            "at": "2026-09-28T18:00:00+00:00",
            "kind": "execution",
            "action": "buy",
            "symbol": "MSFT",
            "message": "entry submitted",
        }
    )
    message = queued(notifier)
    assert message.startswith(":rhen: ↗️")
    assert "MSFT" in message

    notifier.record_event(
        {
            "at": "2026-09-28T18:00:10+00:00",
            "kind": "scan",
            "action": "hold",
            "symbol": "QQQ",
            "message": "no qualified entry",
        }
    )
    assert notifier._queue.empty()


def test_reconciliation_state_deduplicates_until_it_changes():
    notifier = SlackNotifier(Settings())
    event = {
        "at": "2026-09-28T18:00:00+00:00",
        "kind": "reconciliation",
        "action": "safe",
        "message": "canonical state reconciled",
    }
    notifier.record_event(event)
    safe_message = queued(notifier)
    assert safe_message.startswith(":rhen: ✅")
    assert "RECONCILIATION SAFE" in safe_message

    notifier.record_event(event)
    assert notifier._queue.empty()

    notifier.record_event({**event, "action": "blocked", "message": "state mismatch"})
    blocked_message = queued(notifier)
    assert blocked_message.startswith(":rhen: 🛡️")
    assert "RECONCILIATION BLOCKED" in blocked_message


def test_market_open_and_close_only_emit_transitions():
    notifier = SlackNotifier(Settings())
    at = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)

    notifier.observe_market_state(False, observed_at=at)
    assert notifier._queue.empty()

    notifier.observe_market_state(True, observed_at=at)
    open_message = queued(notifier)
    assert open_message.startswith(":rhen: 🔵")
    assert "RHEN // EXECUTION // MARKET OPEN" in open_message

    notifier.observe_market_state(True, observed_at=at)
    assert notifier._queue.empty()

    notifier.observe_market_state(False, observed_at=at)
    close_message = queued(notifier)
    assert close_message.startswith(":rhen: 🔵")
    assert "RHEN // EXECUTION // MARKET CLOSED" in close_message


def test_asc_state_changes_are_queued_but_unrecognized_asc_noise_is_not():
    notifier = SlackNotifier(Settings())
    notifier.record_event(
        {
            "at": "2026-09-29T20:10:00+00:00",
            "kind": "asc",
            "action": "research",
            "message": "IREN ASC ADAPT -> RESEARCH; production unchanged",
        }
    )
    message = queued(notifier)
    assert message.startswith(":rhen: 🎛️")
    assert "ASC RESEARCH" in message

    notifier.record_event(
        {
            "at": "2026-09-29T20:10:10+00:00",
            "kind": "asc",
            "action": "health_snapshot",
            "message": "routine snapshot",
        }
    )
    assert notifier._queue.empty()


def test_asc_promotion_ready_is_operational_notification_only():
    notifier = SlackNotifier(Settings())
    notifier.record_event(
        {
            "at": "2026-10-15T20:10:00+00:00",
            "kind": "asc",
            "action": "promotion_ready",
            "message": "proposal ready for human review; no deployment occurred",
        }
    )
    message = queued(notifier)
    assert message.startswith(":rhen: 🎛️")
    assert "PROMOTION_READY" in message
    assert "no deployment occurred" in message


def test_research_event_uses_graen_identity():
    notifier = SlackNotifier(Settings())
    notifier.record_event(
        {
            "at": "2026-09-30T20:00:00+00:00",
            "kind": "research_agent",
            "action": "completed",
            "message": "daily research review completed",
        }
    )
    message = queued(notifier)
    assert message.startswith(":rhen: ✅")
    assert "*RHEN // RESEARCH // RESEARCH_AGENT COMPLETED*" in message


def test_asc_event_uses_iren_identity():
    notifier = SlackNotifier(Settings())
    notifier.record_event(
        {
            "at": "2026-09-30T20:00:00+00:00",
            "kind": "asc",
            "action": "research",
            "message": "IREN ASC entered research state",
        }
    )
    message = queued(notifier)
    assert message.startswith(":rhen: 🎛️")
    assert "*RHEN // CONTROL // ASC RESEARCH*" in message
