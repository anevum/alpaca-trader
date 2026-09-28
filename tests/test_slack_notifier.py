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
    assert "MSFT" in queued(notifier)

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


def test_reconciliation_safe_deduplicates_until_state_changes():
    notifier = SlackNotifier(Settings())
    event = {
        "at": "2026-09-28T18:00:00+00:00",
        "kind": "reconciliation",
        "action": "safe",
        "message": "broker and canonical ledger reconciled",
    }
    notifier.record_event(event)
    assert "RECONCILIATION SAFE" in queued(notifier)

    notifier.record_event(event)
    assert notifier._queue.empty()

    notifier.record_event({**event, "action": "blocked", "message": "mismatch"})
    assert "RECONCILIATION BLOCKED" in queued(notifier)


def test_market_open_and_close_only_emit_transitions():
    notifier = SlackNotifier(Settings())
    at = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)

    notifier.observe_market_state(False, observed_at=at)
    assert notifier._queue.empty()

    notifier.observe_market_state(True, observed_at=at)
    assert "MARKET OPEN" in queued(notifier)

    notifier.observe_market_state(True, observed_at=at)
    assert notifier._queue.empty()

    notifier.observe_market_state(False, observed_at=at)
    assert "MARKET CLOSED" in queued(notifier)


def test_broker_snapshot_baselines_then_reports_new_fill_and_position_transition():
    notifier = SlackNotifier(Settings())
    order = {
        "id": "order-1",
        "client_order_id": "anevum-msft-buy-1",
        "symbol": "MSFT",
    }
    notifier.observe_broker_snapshot(
        positions=[],
        recent_orders=[order],
        fills=[],
    )
    assert notifier._queue.empty()

    notifier.observe_broker_snapshot(
        positions=[{"symbol": "MSFT", "qty": "0.1"}],
        recent_orders=[order],
        fills=[
            {
                "id": "fill-1",
                "order_id": "order-1",
                "symbol": "MSFT",
                "side": "buy",
                "qty": "0.1",
                "price": "510.25",
                "transaction_time": "2026-09-28T18:45:00Z",
            }
        ],
    )
    messages = {queued(notifier), queued(notifier)}
    assert any("FILL" in message for message in messages)
    assert any("POSITION OPEN" in message for message in messages)
