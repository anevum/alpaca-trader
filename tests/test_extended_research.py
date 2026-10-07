import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.extended_research import ExtendedResearchRecorder


def test_checkpoint_receipt_retry_separate_lane_and_no_stale_reuse():
    async def scenario():
        stamp = datetime(2026, 10, 7, 23, 15, tzinfo=timezone.utc)
        snapshot = {"scan_observed_at": stamp.isoformat(), "strategy_version_id": "EXT-001",
                    "session": {"session": "after_hours"}, "scanner": {
                        "SPY": {"action": "hold", "reason": "stale quote", "reference_price": "0"}}}
        sink = SimpleNamespace(enabled=True, emit_critical=AsyncMock(side_effect=[False, True]))
        recorder = ExtendedResearchRecorder(SimpleNamespace(snapshot=lambda: snapshot), sink)
        assert not await recorder.capture(stamp)
        assert recorder.last_key is None
        assert await recorder.capture(stamp)
        assert not await recorder.capture(stamp)
        assert sink.emit_critical.await_count == 2
        payload = sink.emit_critical.call_args.kwargs["payload"]
        assert payload["market_lane"] == "us_equity_extended"
        assert payload["rejection_reasons"] == {"stale quote": 1}
        assert payload["forward_outcomes"] == "NOT_MEASURED"
        assert payload["live_configuration_changed"] is False
        assert sink.emit_critical.call_args.kwargs["strategy_version_id"] == "EXT-001"
        assert not await recorder.capture(stamp.replace(minute=20))
    asyncio.run(scenario())
