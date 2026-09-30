import asyncio
import json
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


def test_unfilled_hardstop_is_not_projected_but_filled_hardstop_is():
    sink = CapturingSink()
    observed_at = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    orders = [
        {
            "id": "open-stop",
            "client_order_id": "anevum-spy-hardstop-abc",
            "symbol": "SPY",
            "side": "sell",
            "status": "new",
            "qty": "0.2",
            "filled_qty": "0",
            "stop_price": "99.65",
            "submitted_at": "2026-09-24T14:01:00Z",
        },
        {
            "id": "filled-stop",
            "client_order_id": "anevum-qqq-hardstop-def",
            "symbol": "QQQ",
            "side": "sell",
            "status": "filled",
            "qty": "0.2",
            "filled_qty": "0.2",
            "filled_avg_price": "99.60",
            "stop_price": "99.65",
            "submitted_at": "2026-09-24T14:02:00Z",
            "filled_at": "2026-09-24T14:02:01Z",
        },
    ]

    events = sink._build_reconciliation_events(
        account={
            "equity": "100",
            "last_equity": "100",
            "cash": "100",
            "buying_power": "100",
        },
        positions=[],
        orders=orders,
        fills=[],
        correlation_id="cycle-hardstop",
        observed_at=observed_at,
    )

    broker_events = [
        event for event in events if event["event_type"] == "broker_order"
    ]
    assert [event["payload"]["order"]["id"] for event in broker_events] == [
        "filled-stop"
    ]
    assert (
        broker_events[0]["payload"]["exit_reason"]
        == "broker protective stop filled"
    )


def test_managed_symbols_include_owned_dynamic_universe_orders_and_fills():
    sink = CapturingSink()
    sink.settings.allowed_symbols = {"SPY"}
    orders = [
        {
            "id": "smci-buy",
            "client_order_id": "anevum-smci-buy-abc",
            "symbol": "SMCI",
        },
        {
            "id": "manual-order",
            "client_order_id": "manual-qqq",
            "symbol": "QQQ",
        },
    ]
    fills = [
        {
            "id": "fill-smci",
            "order_id": "smci-buy",
            "symbol": "SMCI",
        },
        {
            "id": "fill-manual",
            "order_id": "manual-order",
            "symbol": "QQQ",
        },
    ]
    open_orders = [
        {
            "id": "tqqq-stop",
            "client_order_id": "anevum-tqqq-hardstop-abc",
            "symbol": "TQQQ",
        }
    ]

    managed = sink.managed_symbols_from_snapshot(
        orders=orders,
        fills=fills,
        open_orders=open_orders,
    )

    assert managed == ["SMCI", "SPY", "TQQQ"]


def test_replaced_hardstop_inherits_bot_ownership_and_protective_semantics():
    sink = CapturingSink()
    sink.settings.allowed_symbols = set()
    observed_at = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
    orders = [
        {
            "id": "original-stop",
            "client_order_id": "anevum-smci-hardstop-owner-abc123",
            "symbol": "SMCI",
            "side": "sell",
            "status": "replaced",
            "qty": "0.48",
            "filled_qty": "0",
            "submitted_at": "2026-09-25T16:28:00Z",
            "replaced_by": "replacement-stop",
        },
        {
            "id": "replacement-stop",
            "client_order_id": "039ba482-08c1-46e1-baf7-03d4f81679ff",
            "symbol": "SMCI",
            "side": "sell",
            "type": "stop",
            "status": "filled",
            "qty": "0.48",
            "filled_qty": "0.48",
            "filled_avg_price": "43.20",
            "stop_price": "43.21",
            "submitted_at": "2026-09-25T16:30:00Z",
            "filled_at": "2026-09-25T16:31:00Z",
            "replaces": "original-stop",
        },
    ]
    fills = [
        {
            "id": "replacement-fill",
            "order_id": "replacement-stop",
            "symbol": "SMCI",
            "side": "sell",
            "qty": "0.48",
            "price": "43.20",
            "transaction_time": "2026-09-25T16:31:00Z",
        }
    ]

    managed = sink.managed_symbols_from_snapshot(
        orders=orders,
        fills=fills,
        open_orders=[],
    )
    assert managed == ["SMCI"]

    events = sink._build_reconciliation_events(
        account={
            "equity": "100",
            "last_equity": "100",
            "cash": "100",
            "buying_power": "100",
        },
        positions=[],
        orders=orders,
        fills=fills,
        correlation_id="cycle-replaced-hardstop",
        observed_at=observed_at,
    )

    broker_orders = [
        event for event in events if event["event_type"] == "broker_order"
    ]
    broker_fills = [
        event for event in events if event["event_type"] == "broker_fill"
    ]
    assert [event["payload"]["order"]["id"] for event in broker_orders] == [
        "replacement-stop"
    ]
    assert broker_orders[0]["payload"]["exit_reason"] == "broker protective stop filled"
    assert [event["payload"]["activity"]["id"] for event in broker_fills] == [
        "replacement-fill"
    ]


def test_decision_cycle_event_is_deterministic_and_preserves_unavailable_quote():
    sink = CapturingSink()
    started = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    sink.record_decision_cycle(
        correlation_id="cycle-telemetry-1",
        cycle_started_at=started,
        cycle_ended_at=started,
        market_is_open=True,
        active_universe=["SPY"],
        scan={
            "SPY": {
                "action": "hold",
                "symbol": "SPY",
                "reference_price": "100",
                "reason": "latest quote is missing or invalid",
                "metadata": {"checks": {"momentum_ok": True}, "market_quality": {}},
            }
        },
        cycle_outcome="no_qualified_candidates",
    )
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_type"] == "decision_cycle"
    assert event["event_key"] == "run-1:decision-cycle:cycle-telemetry-1"
    candidate = event["payload"]["candidates"][0]
    assert candidate["qualified"] is False
    assert candidate["candidate_state"] == "rejected"
    assert candidate["quote"]["bid"] is None
    assert candidate["quote"]["ask"] is None
    assert candidate["quote"]["midpoint"] is None
    assert candidate["forward_outcomes_status"] == "pending"




def _complete_ads_metadata():
    return {
        "momentum_pct": "0.003",
        "vwap_edge_pct": "0.002",
        "relative_volume_ratio": "1.6",
        "trend_persistence": "0.75",
        "confirmation_passes": 1,
        "regime_passes": 1,
        "confirmations": {"QQQ": {"ok": True}},
        "regime_confirmations": {"SPY": {"ok": True}},
        "market_quality": {
            "bid": "99.99",
            "ask": "100.01",
            "midpoint": "100.00",
            "spread_pct": "0.0002",
            "bar_age_seconds": 2,
            "quote_age_seconds": 1,
            "quote_timestamp": "2026-09-29T14:00:00+00:00",
        },
        "ads002_v2_raw_features": {
            "relative_volume_ratio": 1.6,
            "relative_volume_ratio_raw": 1.6,
            "return_1m": 0.001,
            "return_3m": 0.002,
            "return_5m": 0.003,
            "abs_return_5m": 0.003,
            "accel_1m": 0.0002,
            "volatility_expansion_ratio": 1.2,
            "range_expansion_ratio": 1.1,
            "dollar_volume_5m": 250000,
            "trend_persistence": 0.75,
            "fast_slow_spread_pct": 0.001,
            "vwap_edge_pct": 0.002,
            "confirmation_ratio": 1.0,
            "regime_ratio": 1.0,
            "spread_bps": 2.0,
            "quote_age_ms": 1000.0,
            "bar_age_ms": 2000.0,
        },
    }


def test_entry_intent_preserves_same_ads_v1_and_v2_decision_state():
    sink = CapturingSink()
    metadata = _complete_ads_metadata()
    signal = SimpleNamespace(
        symbol="SPY",
        reference_price="100",
        stop_price="99.65",
        take_profit_price="100.50",
        notional="20",
        reason="qualified",
        metadata=metadata,
    )
    decision_scan = {
        "SPY": {
            "symbol": "SPY",
            "action": "buy",
            "reference_price": "100",
            "reason": "qualified",
            "metadata": metadata,
        },
        "QQQ": {
            "symbol": "QQQ",
            "action": "hold",
            "reference_price": "200",
            "reason": "not qualified",
            "metadata": {
                **_complete_ads_metadata(),
                "ads002_v2_raw_features": {
                    **_complete_ads_metadata()["ads002_v2_raw_features"],
                    "relative_volume_ratio": 1.1,
                    "relative_volume_ratio_raw": 1.1,
                    "return_5m": 0.001,
                    "abs_return_5m": 0.001,
                },
            },
        },
    }
    expected_v1 = sink._ads002_shadow_candidate_safe(
        symbol="SPY",
        metadata=metadata,
    )
    expected_v2 = sink._ads002_v2_shadow_cycle(decision_scan)["SPY"]

    sink.emit_critical = lambda **kwargs: asyncio.sleep(
        0,
        result=(sink.events.append(kwargs) is None),
    )
    setattr(signal, "_evidence_decision_scan", decision_scan)
    result = asyncio.run(
        sink.persist_entry_intent(
            signal=signal,
            qty="0.2",
            client_order_id="anevum-spy-buy-ads-evidence",
            correlation_id="cycle-ads",
            intended_at=datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc),
        )
    )

    assert result is not None
    snapshot = sink.events[0]["payload"]["intent"]["payload"]["candidate_snapshot"]
    assert snapshot["ads002"] == expected_v1
    assert snapshot["ads002"]["source_completeness"]["pretrade_complete"] is True
    assert snapshot["ads002"]["pretrade_composite"] is not None
    assert snapshot["ads002_v2"] == expected_v2
    assert snapshot["ads002_v2"]["source_completeness"]["pretrade_complete"] is True
    assert snapshot["ads002_v2"]["challengers"]
    assert snapshot["ads002_v2"]["execution_authority"] is False
    assert snapshot["strategy_version_id"] == "version-1"


def test_entry_intent_keeps_missing_ads_inputs_explicit_without_imputation():
    sink = CapturingSink()
    signal = SimpleNamespace(
        symbol="SPY",
        reference_price="100",
        stop_price="99.65",
        take_profit_price="100.50",
        notional="20",
        reason="qualified with incomplete research telemetry",
        metadata={"market_quality": {}},
    )
    decision_scan = {
        "SPY": {
            "symbol": "SPY",
            "action": "buy",
            "reference_price": "100",
            "reason": signal.reason,
            "metadata": signal.metadata,
        },
    }

    sink.emit_critical = lambda **kwargs: asyncio.sleep(
        0,
        result=(sink.events.append(kwargs) is None),
    )
    setattr(signal, "_evidence_decision_scan", decision_scan)
    result = asyncio.run(
        sink.persist_entry_intent(
            signal=signal,
            qty="0.2",
            client_order_id="anevum-spy-buy-missing-ads",
            correlation_id="cycle-missing-ads",
            intended_at=datetime(2026, 9, 29, 14, 1, tzinfo=timezone.utc),
        )
    )

    assert result is not None
    snapshot = sink.events[0]["payload"]["intent"]["payload"]["candidate_snapshot"]
    v1 = snapshot["ads002"]
    assert v1["source_completeness"]["pretrade_complete"] is False
    assert v1["pretrade_composite"] is None
    assert v1["source_completeness"]["missing_requirements"]
    v2 = snapshot["ads002_v2"]
    assert v2["source_completeness"]["pretrade_complete"] is False
    assert v2["challengers"] == {}
    assert v2["source_completeness"]["missing_requirements"]
    assert v2["execution_authority"] is False


def test_entry_intent_carries_cycle_and_decision_evidence():
    sink = CapturingSink()
    signal = SimpleNamespace(
        symbol="SPY",
        reference_price="100",
        stop_price="99.65",
        take_profit_price="100.50",
        notional="20",
        reason="qualified",
        metadata={"market_quality": {"bid": "99.99", "ask": "100.01", "midpoint": "100.00", "spread_pct": "0.0002"}},
    )
    sink.emit_critical = lambda **kwargs: asyncio.sleep(0, result=(sink.events.append(kwargs) is None))
    result = asyncio.run(sink.persist_entry_intent(
        signal=signal,
        qty="0.2",
        client_order_id="anevum-spy-buy-test",
        correlation_id="cycle-2",
        intended_at=datetime(2026, 9, 28, 13, 31, tzinfo=timezone.utc),
    ))
    assert result is not None
    payload = sink.events[0]["payload"]
    assert payload["intent"]["payload"]["cycle_key"] == "run-1:cycle-2"
    assert payload["intent"]["payload"]["decision_quote"]["midpoint"] == "100.00"
    assert payload["intent"]["payload"]["decision_reference_price"] == "100"


def test_transport_chunks_respect_count_and_body_limits():
    events = [
        {
            "event_key": f"event-{index}",
            "event_type": "decision_cycle",
            "occurred_at": "2026-09-30T12:00:00+00:00",
            "payload": {"blob": "x" * 220},
        }
        for index in range(7)
    ]

    chunks = TradingEventSink._transport_chunks(
        events,
        max_events=2,
        max_body_bytes=900,
    )

    assert len(chunks) >= 4
    assert sum(len(chunk) for chunk in chunks) == len(events)
    for chunk in chunks:
        assert len(chunk) <= 2
        encoded = json.dumps(
            {"events": chunk},
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        assert len(encoded) <= 900
