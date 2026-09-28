import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.post_event_evidence import (
    COMPARISON_METHODOLOGY_VERSION,
    FORWARD_HORIZONS_MINUTES,
    PostEventEvidenceRunner,
    calculate_forward_outcome,
    reconstruct_cycle,
    truncate_bars,
)


NY = ZoneInfo("America/New_York")


def bar(stamp: datetime, close: str, *, high: str | None = None, low: str | None = None):
    return {
        "t": stamp.isoformat(),
        "o": close,
        "h": high or close,
        "l": low or close,
        "c": close,
        "v": "1000",
        "vw": close,
    }


def candidate(reference_bar: datetime, reference_price: str = "100"):
    return {
        "candidate_id": 1,
        "scan_cycle_id": 10,
        "run_id": "11111111-1111-1111-1111-111111111111",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "symbol": "SPY",
        "observed_at": (reference_bar + timedelta(minutes=1, seconds=5)).isoformat(),
        "decision_reference_price": reference_price,
        "qualified": True,
        "action": "buy",
        "reason": "qualified",
        "features": {"bar_time": reference_bar.isoformat()},
    }


def test_forward_outcomes_exact_horizons_and_excursions():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    rows = []
    for minute in range(1, 61):
        start = reference_bar + timedelta(minutes=minute)
        close = Decimal("100") + Decimal(minute) / Decimal("100")
        rows.append(
            bar(
                start,
                str(close),
                high=str(close + Decimal("0.20")),
                low=str(close - Decimal("0.30")),
            )
        )
    close = datetime(2026, 9, 25, 16, 0, tzinfo=NY)

    for horizon in FORWARD_HORIZONS_MINUTES:
        result = calculate_forward_outcome(
            candidate(reference_bar),
            rows,
            horizon_minutes=horizon,
            session_close=close,
            provider="iex",
            bar_interval="1Min",
        )
        assert result["status"] == "complete"
        assert result["observation_end_at"] == (
            reference_bar + timedelta(minutes=horizon + 1)
        ).isoformat()
        assert Decimal(result["max_favorable_return"]) > 0
        assert Decimal(result["max_adverse_return"]) < Decimal("0.01")


def test_forward_outcome_missing_exact_terminal_bar_is_explicitly_incomplete():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    rows = [
        bar(reference_bar + timedelta(minutes=1), "100.1"),
        bar(reference_bar + timedelta(minutes=2), "100.2"),
        bar(reference_bar + timedelta(minutes=3), "100.3"),
        bar(reference_bar + timedelta(minutes=4), "100.4"),
    ]
    result = calculate_forward_outcome(
        candidate(reference_bar),
        rows,
        horizon_minutes=5,
        session_close=datetime(2026, 9, 25, 16, 0, tzinfo=NY),
        provider="iex",
        bar_interval="1Min",
    )
    assert result["status"] == "insufficient_future_data"
    assert result["details"]["reason"] == "exact_horizon_terminal_bar_unavailable"


def test_forward_outcome_never_bridges_regular_session_close():
    reference_bar = datetime(2026, 9, 25, 15, 30, tzinfo=NY)
    result = calculate_forward_outcome(
        candidate(reference_bar),
        [],
        horizon_minutes=60,
        session_close=datetime(2026, 9, 25, 16, 0, tzinfo=NY),
        provider="iex",
        bar_interval="1Min",
    )
    assert result["status"] == "insufficient_future_data"
    assert result["details"]["reason"] == "requested_horizon_crosses_regular_session_close"
    assert result["observation_end_at"] == datetime(
        2026, 9, 25, 16, 0, tzinfo=NY
    ).isoformat()


def test_forward_outcome_missing_decision_price_is_recorded_not_dropped():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    row = candidate(reference_bar)
    row["decision_reference_price"] = None
    result = calculate_forward_outcome(
        row,
        [],
        horizon_minutes=5,
        session_close=datetime(2026, 9, 25, 16, 0, tzinfo=NY),
        provider="iex",
        bar_interval="1Min",
    )
    assert result["status"] == "error"
    assert result["details"]["reason"] == "missing_decision_reference_price"


def test_truncate_bars_excludes_future_and_partial_bar():
    now = datetime(2026, 9, 25, 10, 2, 30, tzinfo=NY)
    rows = {
        "SPY": [
            bar(datetime(2026, 9, 25, 10, 1, tzinfo=NY), "100"),
            bar(datetime(2026, 9, 25, 10, 2, tzinfo=NY), "200"),
            bar(datetime(2026, 9, 25, 10, 3, tzinfo=NY), "300"),
        ]
    }
    safe = truncate_bars(rows, now)
    assert [row["c"] for row in safe["SPY"]] == ["100"]


def replay_config():
    return {
        "strategy_name": "rolling_momentum_vwap",
        "fast_window": 2,
        "slow_window": 4,
        "min_momentum_pct": "0.0001",
        "min_vwap_edge_pct": "0",
        "stop_pct": "0.0035",
        "target_pct": "0.005",
        "entry_start": "09:31",
        "entry_cutoff": "15:30",
        "confirmation_symbols": ["QQQ"],
        "min_confirmations": 1,
        "regime_window": 3,
        "regime_min_confirmations": 1,
        "regime_min_return_pct": "0",
        "max_vwap_extension_pct": "0.008",
        "volatility_stop_enabled": False,
        "volatility_stop_multiplier": "2",
        "volatility_stop_lookback_bars": 8,
        "max_dynamic_stop_pct": "0.006",
        "max_bar_age_seconds": 90,
        "max_spread_pct": "0.002",
        "min_quality_score": "0",
        "max_pairwise_correlation": "0.85",
        "correlation_lookback_bars": 30,
        "correlation_min_observations": 8,
        "loss_streak_limit": 2,
        "loss_streak_cooldown_minutes": 10,
        "reentry_cooldown_minutes": 0,
        "order_notional": "20",
        "sizing_mode": "fixed",
        "risk_per_trade_pct": "0.001",
        "max_gross_exposure_pct": "1",
        "min_order_notional": "1",
        "max_order_notional": "80",
        "max_position_notional": "80",
        "max_total_position_notional": "250",
        "portfolio_limit_mode": "risk",
        "max_position_gross_pct": "0.25",
        "max_portfolio_stop_risk_pct": "0.01",
        "max_concurrent_positions": 2,
        "max_new_entries_per_cycle": 1,
        "max_daily_orders": 0,
        "max_daily_loss": "1",
        "dynamic_universe_enabled": True,
        "allowed_symbols": ["SPY", "QQQ"],
        "execution_authorized": True,
        "data_feed": "iex",
        "bar_timeframe": "1Min",
    }


def replay_bars():
    start = datetime(2026, 9, 25, 9, 31, tzinfo=NY)
    spy = []
    qqq = []
    for index in range(10):
        spy_price = Decimal("100") + Decimal(index) * Decimal("0.10")
        qqq_price = Decimal("500") + Decimal(index) * Decimal("0.10")
        spy.append(bar(start + timedelta(minutes=index), str(spy_price)))
        qqq.append(bar(start + timedelta(minutes=index), str(qqq_price)))
    return {"SPY": spy, "QQQ": qqq}


def replay_cycle_and_candidate():
    decision_at = datetime(2026, 9, 25, 9, 41, 5, tzinfo=NY)
    rows = replay_bars()
    live_candidate = {
        "candidate_id": 7,
        "scan_cycle_id": 4,
        "run_id": "11111111-1111-1111-1111-111111111111",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "symbol": "SPY",
        "action": "buy",
        "qualified": True,
        "final_decision": "submitted",
        "submitted": True,
        "intent_id": "intent-1",
        "reason": "rolling momentum + VWAP signal confirmed",
        "quote": {"bid": "100.89", "ask": "100.91"},
        "features": {
            "strategy_evaluation": {
                "action": "buy",
                "reason": "rolling momentum + VWAP signal confirmed",
            }
        },
    }
    cycle = {
        "scan_cycle_id": 4,
        "run_id": live_candidate["run_id"],
        "strategy_version_id": live_candidate["strategy_version_id"],
        "runtime_instance_id": "runtime-1",
        "deployment_id": "deploy-1",
        "data_status": "ok",
        "comparison_context": {
            "configuration": replay_config(),
            "execution_context": {
                "decision_at": decision_at.isoformat(),
                "entry_symbols": ["SPY"],
                "account": {
                    "cash": "100",
                    "equity": "100",
                    "last_equity": "100",
                    "buying_power": "100",
                    "account_blocked": False,
                    "trading_blocked": False,
                },
                "positions": [],
                "recent_orders": [],
                "open_order_symbols": [],
                "startup_reconciled": True,
                "reconciliation_safe": True,
                "entries_enabled": True,
                "assets": {
                    "SPY": {
                        "status": "active",
                        "tradable": True,
                        "fractionable": True,
                    }
                },
            },
            "execution_result": {},
        },
    }
    return cycle, live_candidate, rows


def test_deterministic_live_offline_match_uses_same_strategy_and_no_future_data():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    first = reconstruct_cycle(cycle, [live_candidate], rows)

    rows_with_future = {
        **rows,
        "SPY": rows["SPY"]
        + [bar(datetime(2026, 9, 25, 10, 30, tzinfo=NY), "999")],
    }
    second = reconstruct_cycle(cycle, [live_candidate], rows_with_future)

    assert first[0]["match_state"] == "MATCH"
    assert first[0]["mismatch_category"] == "MATCH"
    assert first[0]["offline_result"] == "eligible_to_submit"
    for key in (
        "candidate_id",
        "live_result",
        "offline_result",
        "match_state",
        "mismatch_category",
        "source_data_completeness",
    ):
        assert first[0][key] == second[0][key]
    assert first[0]["details"]["future_data_used"] is False


def test_replay_detects_gate_mismatch():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    live_candidate["action"] = "hold"
    live_candidate["qualified"] = False
    live_candidate["submitted"] = False
    live_candidate["intent_id"] = None
    result = reconstruct_cycle(cycle, [live_candidate], rows)
    assert result[0]["match_state"] == "MISMATCH"
    assert result[0]["mismatch_category"] == "GATE_MISMATCH"


def test_historical_partial_backfill_is_unreconstructable_not_fabricated():
    cycle = {
        "scan_cycle_id": 4,
        "run_id": "11111111-1111-1111-1111-111111111111",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "data_status": "partial_backfill",
        "comparison_context": {},
    }
    result = reconstruct_cycle(
        cycle,
        [{"candidate_id": 1, "symbol": "SPY", "action": "buy", "submitted": True}],
        {},
    )
    assert result[0]["match_state"] == "UNRECONSTRUCTABLE"
    assert result[0]["mismatch_category"] == "UNRECONSTRUCTABLE"
    assert "full_candidate_population" in result[0]["details"]["missing_inputs"]


def test_missing_forward_replay_context_is_insufficient_input():
    cycle = {
        "scan_cycle_id": 5,
        "run_id": "11111111-1111-1111-1111-111111111111",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "data_status": "ok",
        "comparison_context": {"configuration": replay_config(), "execution_context": {}},
    }
    result = reconstruct_cycle(
        cycle,
        [{"candidate_id": 2, "symbol": "SPY", "action": "buy"}],
        {},
    )
    assert result[0]["match_state"] == "UNRECONSTRUCTABLE"
    assert result[0]["mismatch_category"] == "INSUFFICIENT_INPUT"
    assert result[0]["methodology_version"] == COMPARISON_METHODOLOGY_VERSION


def test_rejected_candidate_reconstructs_as_match_when_same_strategy_rejects():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    cycle["comparison_context"]["configuration"]["min_momentum_pct"] = "0.04"
    live_candidate.update(
        {
            "action": "hold",
            "qualified": False,
            "final_decision": "rejected",
            "submitted": False,
            "intent_id": None,
            "features": {
                "strategy_evaluation": {
                    "action": "hold",
                    "reason": "momentum below threshold",
                }
            },
        }
    )
    result = reconstruct_cycle(cycle, [live_candidate], rows)
    assert result[0]["match_state"] == "MATCH"
    assert result[0]["mismatch_category"] == "MATCH"
    assert result[0]["live_result"] == "rejected"
    assert result[0]["offline_result"] == "rejected"


def test_replay_detects_action_mismatch_after_matching_strategy_gate():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    cycle["comparison_context"]["execution_context"]["assets"]["SPY"]["fractionable"] = False
    result = reconstruct_cycle(cycle, [live_candidate], rows)
    assert result[0]["match_state"] == "MISMATCH"
    assert result[0]["mismatch_category"] == "ACTION_MISMATCH"
    assert result[0]["details"]["offline_reason"] == (
        "asset is not active, tradable, and fractionable"
    )


def test_replay_invalid_configuration_is_explicitly_unreconstructable():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    del cycle["comparison_context"]["configuration"]["fast_window"]
    result = reconstruct_cycle(cycle, [live_candidate], rows)
    assert result[0]["match_state"] == "UNRECONSTRUCTABLE"
    assert result[0]["mismatch_category"] == "CONFIGURATION_MISMATCH"


def test_replay_invalid_decision_timestamp_is_stale_input():
    cycle, live_candidate, rows = replay_cycle_and_candidate()
    cycle["comparison_context"]["execution_context"]["decision_at"] = "not-a-timestamp"
    result = reconstruct_cycle(cycle, [live_candidate], rows)
    assert result[0]["match_state"] == "UNRECONSTRUCTABLE"
    assert result[0]["mismatch_category"] == "STALE_INPUT"


def test_post_event_backfill_is_restart_safe_and_uses_stable_event_keys():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    row = {
        **candidate(reference_bar),
        "data_feed": "iex",
        "bar_interval": "1Min",
        "scan_cycle": {
            "scan_cycle_id": 10,
            "cycle_key": "run:cycle",
            "data_status": "partial_backfill",
            "strategy_version_id": "LIVE-2026-09-25-003",
            "run_id": "11111111-1111-1111-1111-111111111111",
        },
        "decision_cycle_payload": None,
        "submitted": True,
        "intent_id": "historical-intent",
        "ads002_score": {
            "methodology_version": "ads-shadow-v1",
            "source_completeness": {"pretrade_complete": True},
            "attention_score": 60,
            "qualification_score": 70,
            "timing_score": 80,
            "pretrade_composite": 70,
        },
    }
    rows = [
        bar(
            reference_bar + timedelta(minutes=minute),
            str(Decimal("100") + Decimal(minute) / Decimal("100")),
        )
        for minute in range(1, 61)
    ]

    class MarketData:
        async def market_calendar_details(self, *, start, end):
            return [{"date": start, "open": "09:30", "close": "16:00"}]

        async def historical_bars_many(self, symbols, *, start, end):
            return {"SPY": rows}

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    async def reader(**params):
        assert params == {"evidence_session": "2026-09-25"}
        return {"candidates": [row]}

    sink = Sink()
    runner = PostEventEvidenceRunner(
        settings=type("Settings", (), {"data_feed": "iex", "bar_timeframe": "1Min"})(),
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=reader,
    )

    first = asyncio.run(runner.run_session(date(2026, 9, 25)))
    first_keys = [event["event_key"] for event in sink.events]
    sink.events.clear()
    second = asyncio.run(runner.run_session(date(2026, 9, 25)))
    second_keys = [event["event_key"] for event in sink.events]

    assert first.complete_outcomes == 7
    assert first.incomplete_outcomes == 0
    assert first.error_outcomes == 0
    assert first.comparison_events == 1
    assert second.complete_outcomes == first.complete_outcomes
    assert second_keys == first_keys
    assert len(first_keys) == len(set(first_keys)) == 8
    comparison = next(
        event for event in sink.events
        if event["event_type"] == "live_offline_comparison"
    )
    assert comparison["payload"]["match_state"] == "UNRECONSTRUCTABLE"
    assert comparison["payload"]["mismatch_category"] == "UNRECONSTRUCTABLE"


def test_ads002_ineligible_candidate_skips_forward_outcomes_but_keeps_comparison():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    row = {
        **candidate(reference_bar),
        "data_feed": "iex",
        "bar_interval": "1Min",
        "scan_cycle": {
            "scan_cycle_id": 10,
            "cycle_key": "run:cycle",
            "data_status": "partial_backfill",
            "strategy_version_id": "LIVE-2026-09-25-003",
            "run_id": "11111111-1111-1111-1111-111111111111",
        },
        "decision_cycle_payload": None,
        "submitted": False,
        "intent_id": None,
        "ads002_score": {
            "methodology_version": "ads-shadow-v1",
            "source_completeness": {"pretrade_complete": False},
        },
    }

    class MarketData:
        async def market_calendar_details(self, *, start, end):
            return [{"date": start, "open": "09:30", "close": "16:00"}]

        async def historical_bars_many(self, symbols, *, start, end):
            return {"SPY": []}

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    async def reader(**params):
        return {"candidates": [row]}

    sink = Sink()
    runner = PostEventEvidenceRunner(
        settings=type("Settings", (), {"data_feed": "iex", "bar_timeframe": "1Min"})(),
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=reader,
    )
    summary = asyncio.run(runner.run_session(date(2026, 9, 25)))

    assert summary.outcome_events == 0
    assert summary.comparison_events == 1
    assert [event["event_type"] for event in sink.events] == [
        "live_offline_comparison"
    ]
