import asyncio
from types import SimpleNamespace

from app.persistence import TradingEventSink


def settings(tmp_path):
    return SimpleNamespace(
        trading_ingest_url="",
        trading_ingest_token="",
        trading_run_id="foundation-shadow-test",
        strategy_version_id="FOUNDATION-SHADOW-001",
        trading_run_started_at=None,
        foundation_shadow_enabled=True,
        foundation_outbox_path=str(tmp_path / "rhen-outbox.sqlite3"),
        foundation_ingest_url="http://foundation-ingest.invalid/v1/events",
        foundation_flush_seconds=1.0,
        foundation_batch_size=50,
    )


def test_shadow_emit_is_durable_without_legacy_transport(tmp_path):
    sink = TradingEventSink(settings(tmp_path))
    assert sink.enabled is False
    assert sink.foundation_enabled is True

    sink.emit(
        event_type="test_event",
        event_key="shadow:test:1",
        payload={"value": 1},
    )

    status = sink.status()
    assert status["foundation"]["outbox"]["queued"] == 1
    assert status["dropped_count"] == 0


def test_shadow_critical_event_does_not_replace_live_gate(tmp_path):
    sink = TradingEventSink(settings(tmp_path))
    accepted = asyncio.run(
        sink.emit_critical(
            event_type="order_intent",
            event_key="shadow:critical:1",
            payload={"research_only": True},
        )
    )
    assert accepted is True
    assert sink.status()["foundation"]["outbox"]["queued"] == 1


def test_legacy_queue_overflow_preserves_foundation_copy(tmp_path):
    sink = TradingEventSink(settings(tmp_path))
    sink.settings.trading_ingest_url = "https://legacy.invalid/trading-ingest"
    sink.settings.trading_ingest_token = "legacy-token"

    sink.queue = asyncio.Queue(maxsize=1)
    sink.queue.put_nowait({"event_key": "already-full"})

    sink.emit(
        event_type="decision_cycle",
        event_key="shadow:overflow:1",
        payload={"cycle": 1},
    )

    status = sink.status()
    assert status["dropped_count"] == 1
    assert status["foundation"]["outbox"]["queued"] == 1


def test_foundation_event_key_is_idempotent(tmp_path):
    sink = TradingEventSink(settings(tmp_path))
    for _ in range(2):
        sink.emit(
            event_type="test_event",
            event_key="shadow:idempotent:1",
            payload={"value": 1},
        )
    assert sink.status()["foundation"]["outbox"]["queued"] == 1
