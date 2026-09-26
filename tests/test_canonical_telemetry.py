import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.persistence import TradingEventSink


def telemetry_sink() -> TradingEventSink:
    return TradingEventSink(
        SimpleNamespace(
            trading_ingest_url="https://telemetry.invalid",
            trading_ingest_token="test-token",
            trading_run_id="run-telemetry-test",
            strategy_version_id="LIVE-2026-09-25-003",
        )
    )


def test_closed_market_cycle_is_durable_without_extra_market_state_lookup():
    sink = telemetry_sink()
    observed = datetime(2026, 9, 26, 14, 30, tzinfo=timezone.utc)

    sink.record_decision_cycle(
        correlation_id="closed-cycle-1",
        cycle_started_at=observed,
        cycle_ended_at=observed,
        market_is_open=None,
        active_universe=[],
        scan={},
        cycle_outcome="market is closed",
    )

    event = sink.queue.get_nowait()
    assert event["event_type"] == "decision_cycle"
    assert event["run_id"] == "run-telemetry-test"
    assert event["strategy_version_id"] == "LIVE-2026-09-25-003"
    assert event["event_key"] == "run-telemetry-test:decision-cycle:closed-cycle-1"
    assert event["payload"]["cycle_key"] == "run-telemetry-test:closed-cycle-1"
    assert event["payload"]["market_is_open"] is False
    assert event["payload"]["candidate_count"] == 0
    assert event["payload"]["qualified_count"] == 0
    assert event["payload"]["rejected_count"] == 0


def test_decision_cycle_identity_is_stable_for_retry():
    sink = telemetry_sink()
    observed = datetime(2026, 9, 26, 14, 30, tzinfo=timezone.utc)

    for _ in range(2):
        sink.record_decision_cycle(
            correlation_id="retry-cycle-1",
            cycle_started_at=observed,
            cycle_ended_at=observed,
            market_is_open=False,
            active_universe=[],
            scan={},
            cycle_outcome="market is closed",
        )

    first = sink.queue.get_nowait()
    second = sink.queue.get_nowait()
    assert first["event_key"] == second["event_key"]
    assert first["payload"]["cycle_key"] == second["payload"]["cycle_key"]


def test_noncritical_analytics_telemetry_is_fail_open_when_queue_is_full():
    sink = telemetry_sink()
    sink.queue = asyncio.Queue(maxsize=1)

    sink.emit(event_type="analytics-one", payload={"n": 1})
    sink.emit(event_type="analytics-two", payload={"n": 2})

    assert sink.queue.qsize() == 1
    assert sink.dropped_count == 1
    assert sink.last_error == "event queue full; telemetry event dropped"
