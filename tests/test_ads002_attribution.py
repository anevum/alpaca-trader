import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.persistence import TradingEventSink


def test_entry_intent_carries_direct_candidate_identity_before_broker_submission(monkeypatch):
    sink = TradingEventSink(
        SimpleNamespace(
            trading_ingest_url="https://telemetry.invalid",
            trading_ingest_token="test-token",
            trading_run_id="00000000-0000-0000-0000-000000000001",
            strategy_version_id="LIVE-TEST",
            strategy_name="test-strategy",
            data_feed="iex",
            bar_timeframe="1Min",
        )
    )
    captured = {}

    async def fake_emit_critical(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(sink, "emit_critical", fake_emit_critical)

    signal = SimpleNamespace(
        symbol="AAPL",
        reference_price=Decimal("100"),
        stop_price=Decimal("99"),
        take_profit_price=Decimal("102"),
        notional=Decimal("10"),
        reason="qualified test signal",
        metadata={
            "momentum_pct": 0.003,
            "vwap_edge_pct": 0.002,
            "quality_score": 75,
            "market_quality": {
                "bid": "99.99",
                "ask": "100.01",
                "midpoint": "100",
                "spread_pct": "0.0002",
                "quote_timestamp": "2026-09-28T18:00:00+00:00",
            },
            "checks": {"momentum": True},
        },
    )
    observed = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)

    refs = asyncio.run(sink.persist_entry_intent(
        signal=signal,
        qty="0.1",
        client_order_id="anevum-aapl-buy-test",
        correlation_id="cycle-abc",
        intended_at=observed,
    ))

    assert refs is not None
    payload = captured["payload"]
    candidate_key = (
        "00000000-0000-0000-0000-000000000001:cycle-abc:AAPL"
    )
    assert payload["signal"]["payload"]["candidate_key"] == candidate_key
    assert payload["intent"]["payload"]["candidate_key"] == candidate_key

    snapshot = payload["intent"]["payload"]["candidate_snapshot"]
    assert snapshot["candidate_key"] == candidate_key
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["qualified"] is True
    assert snapshot["final_decision"] == "selected_for_entry"
    assert snapshot["decision_reference_price"] == "100"
    assert snapshot["features"]["quality_score"] == 75
    assert snapshot["quote"]["spread_pct"] == "0.0002"


def test_entry_intent_identity_is_telemetry_only(monkeypatch):
    sink = TradingEventSink(
        SimpleNamespace(
            trading_ingest_url="https://telemetry.invalid",
            trading_ingest_token="test-token",
            trading_run_id="00000000-0000-0000-0000-000000000002",
            strategy_version_id="LIVE-TEST",
            strategy_name="test-strategy",
            data_feed="iex",
            bar_timeframe="1Min",
        )
    )

    async def fake_emit_critical(**kwargs):
        return True

    monkeypatch.setattr(sink, "emit_critical", fake_emit_critical)
    signal = SimpleNamespace(
        symbol="MSFT",
        reference_price=Decimal("500"),
        stop_price=Decimal("495"),
        take_profit_price=Decimal("510"),
        notional=Decimal("10"),
        reason="qualified",
        metadata={},
    )

    refs = asyncio.run(sink.persist_entry_intent(
        signal=signal,
        qty="0.02",
        client_order_id="anevum-msft-buy-test",
        correlation_id="cycle-def",
        intended_at=datetime(2026, 9, 28, 18, 1, tzinfo=timezone.utc),
    ))

    assert set(refs) == {
        "signal_id",
        "intent_id",
        "position_id",
        "client_order_id",
    }
