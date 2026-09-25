from datetime import datetime, timezone
from types import SimpleNamespace

from app.persistence import TradingEventSink


class CapturingSink(TradingEventSink):
    def __init__(self):
        settings = SimpleNamespace(
            trading_ingest_url="https://example.invalid",
            trading_ingest_token="token",
            trading_run_id="run-1",
            strategy_version_id="version-1",
            ledger_reconcile_seconds=60,
            trading_run_started_at=datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc),
        )
        super().__init__(settings)
        self.order_ids = []
        self.events = []

    def record_broker_order(self, order, **metadata):
        self.order_ids.append(order["id"])

    def emit(self, **event):
        self.events.append(event)


def test_reconciliation_replays_bot_orders_oldest_first_and_filters_fills():
    sink = CapturingSink()
    observed_at = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    orders = [
        {
            "id": "sell-order",
            "client_order_id": "anevum-spy-target-2",
            "submitted_at": "2026-09-24T15:00:00Z",
        },
        {
            "id": "buy-order",
            "client_order_id": "anevum-spy-buy-1",
            "submitted_at": "2026-09-24T14:00:00Z",
        },
        {
            "id": "old-bot-order",
            "client_order_id": "anevum-spy-buy-before-run",
            "submitted_at": "2026-09-24T13:00:00Z",
        },
        {
            "id": "manual-order",
            "client_order_id": "manual-1",
            "submitted_at": "2026-09-24T13:45:00Z",
        },
    ]
    fills = [
        {
            "id": "fill-buy",
            "order_id": "buy-order",
            "symbol": "SPY",
            "side": "buy",
            "qty": "0.2",
            "price": "100",
            "transaction_time": "2026-09-24T14:00:01Z",
        },
        {
            "id": "fill-old-bot",
            "order_id": "old-bot-order",
            "symbol": "SPY",
            "side": "buy",
            "qty": "0.2",
            "price": "99",
            "transaction_time": "2026-09-24T13:00:01Z",
        },
        {
            "id": "fill-manual",
            "order_id": "manual-order",
            "symbol": "QQQ",
            "side": "buy",
            "qty": "1",
            "price": "100",
            "transaction_time": "2026-09-24T13:00:01Z",
        },
    ]

    sink.record_reconciliation(
        account={
            "equity": "100",
            "last_equity": "100",
            "cash": "100",
            "buying_power": "100",
        },
        positions=[],
        orders=orders,
        fills=fills,
        correlation_id="cycle-1",
        observed_at=observed_at,
    )

    assert sink.order_ids == ["buy-order", "sell-order"]
    fill_events = [
        event for event in sink.events if event["event_type"] == "broker_fill"
    ]
    assert [event["payload"]["activity"]["id"] for event in fill_events] == ["fill-buy"]
    assert any(event["event_type"] == "account_snapshot" for event in sink.events)


def test_reconciliation_only_projects_orders_owned_by_runtime_tag():
    sink = CapturingSink()
    sink.settings.order_owner_tag = "live1234"
    observed_at = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    orders = [
        {
            "id": "live-order",
            "client_order_id": "anevum-spy-buy-live1234-abc",
            "submitted_at": "2026-09-24T14:00:00Z",
        },
        {
            "id": "shadow-order",
            "client_order_id": "anevum-qqq-buy-shadow99-def",
            "submitted_at": "2026-09-24T14:01:00Z",
        },
    ]
    fills = [
        {
            "id": "fill-live",
            "order_id": "live-order",
            "symbol": "SPY",
            "side": "buy",
            "qty": "0.2",
            "price": "100",
            "transaction_time": "2026-09-24T14:00:01Z",
        },
        {
            "id": "fill-shadow",
            "order_id": "shadow-order",
            "symbol": "QQQ",
            "side": "buy",
            "qty": "0.2",
            "price": "100",
            "transaction_time": "2026-09-24T14:01:01Z",
        },
    ]

    sink.record_reconciliation(
        account={
            "equity": "100",
            "last_equity": "100",
            "cash": "100",
            "buying_power": "100",
        },
        positions=[],
        orders=orders,
        fills=fills,
        correlation_id="cycle-owned",
        observed_at=observed_at,
    )

    assert sink.order_ids == ["live-order"]
    fill_events = [
        event for event in sink.events if event["event_type"] == "broker_fill"
    ]
    assert [event["payload"]["activity"]["id"] for event in fill_events] == ["fill-live"]
