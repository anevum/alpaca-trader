import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.post_event_evidence import (
    COMPARISON_METHODOLOGY_VERSION,
    FORWARD_HORIZONS_MINUTES,
    PostEventEvidenceRunner,
    PostEventRunSummary,
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
    forward_events = [
        event
        for event in sink.events
        if event["event_type"] == "candidate_forward_outcome"
    ]
    assert forward_events
    assert all(
        event["payload"]["research_eligibility"]["ads002_v1"]["eligible"] is True
        for event in forward_events
    )



def test_ads002_v1_backfill_uses_frozen_config_and_emits_versioned_derived_evidence():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    frozen = replay_config()
    reference = {
        **candidate(reference_bar),
        "candidate_id": 1,
        "candidate_key": "run:cycle:QQQ",
        "symbol": "QQQ",
        "ads002_score": {
            "methodology_version": "ads-shadow-v1",
            "source_completeness": {"pretrade_complete": True},
        },
        "decision_cycle_payload": {
            "comparison_context": {"configuration": frozen},
        },
    }
    target = {
        **candidate(reference_bar),
        "candidate_id": 2,
        "candidate_key": "run:cycle:SPY",
        "ads002_score": None,
        "features": {
            "bar_time": reference_bar.isoformat(),
            "relative_volume_ratio": "1.5",
            "momentum_pct": "0.002",
            "vwap_edge_pct": "0.001",
            "trend_persistence": "0.75",
            "confirmation_passes": 1,
            "regime_passes": 1,
            "confirmations": {"QQQ": {"ok": True}},
            "regime_confirmations": {"QQQ": {"ok": True}},
            "market_quality": {
                "spread_pct": "0.001",
                "bar_age_seconds": 5,
                "quote_age_seconds": 1,
            },
            "ads002_v2_raw_features": {"return_1m": 0.001},
        },
        "decision_cycle_payload": None,
    }

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

        def _comparison_configuration(self):
            return frozen

        def _ads002_shadow_candidate_safe(self, *, symbol, metadata):
            assert symbol == "SPY"
            assert metadata == target["features"]
            return {
                "methodology_version": "ads-shadow-v1",
                "research_only": True,
                "feature_vector": {"momentum_pct": metadata["momentum_pct"]},
                "source_completeness": {
                    "attention": True,
                    "qualification": True,
                    "timing": True,
                    "pretrade_complete": True,
                    "missing_requirements": [],
                },
                "attention": {"score": 60},
                "qualification": {"score": 70},
                "timing": {"score": 80},
                "pretrade_composite": 72,
                "legacy_quality_score": None,
            }

    sink = Sink()
    runner = PostEventEvidenceRunner(
        settings=type(
            "Settings",
            (),
            {
                "strategy_version_id": "LIVE-2026-09-25-003",
                "data_feed": "iex",
                "bar_timeframe": "1Min",
            },
        )(),
        market_data=object(),
        event_sink=sink,
        evidence_reader=None,
    )
    summary = PostEventRunSummary(session="2026-09-25")
    asyncio.run(
        runner._backfill_missing_ads002_v1(
            [reference, target],
            computed_at="2026-09-25T21:00:00+00:00",
            summary=summary,
        )
    )

    assert summary.prediction_backfill_events == 1
    assert target["ads002_score"]["pretrade_composite"] == 72
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_type"] == "candidate_prediction_backfill"
    assert event["event_key"] == (
        "candidate-prediction-backfill:2:"
        "ads-shadow-v1:candidate-prediction-backfill-v1"
    )
    assert event["payload"]["reconstruction"]["raw_evidence_rewritten"] is False
    assert event["payload"]["ads002_v2_reconstruction"] == {
        "status": "UNRECONSTRUCTABLE",
        "reason": "FULL_DECISION_CYCLE_CROSS_SECTION_NOT_PERSISTED",
        "raw_features_preserved": True,
    }


def test_ads002_v1_backfill_refuses_frozen_configuration_mismatch():
    reference_bar = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    frozen = replay_config()
    candidate_row = {
        **candidate(reference_bar),
        "candidate_key": "run:cycle:SPY",
        "features": {"bar_time": reference_bar.isoformat()},
        "ads002_score": None,
    }

    class Sink:
        def _comparison_configuration(self):
            current = dict(frozen)
            current["target_pct"] = "0.006"
            return current

        def _ads002_shadow_candidate_safe(self, *, symbol, metadata):
            raise AssertionError("scorer must not run when frozen config mismatches")

    runner = PostEventEvidenceRunner(
        settings=type(
            "Settings",
            (),
            {"strategy_version_id": "LIVE-2026-09-25-003"},
        )(),
        market_data=object(),
        event_sink=Sink(),
        evidence_reader=None,
    )
    score, reason = runner._reconstruct_ads002_v1(
        candidate_row,
        frozen_configurations={"LIVE-2026-09-25-003": frozen},
    )
    assert score is None
    assert reason == "FROZEN_SCORING_CONFIGURATION_MISMATCH:target_pct"


def test_ads002_incomplete_candidate_still_gets_outcomes_but_not_model_validation():
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
            return {
                "SPY": [
                    bar(
                        reference_bar + timedelta(minutes=minute),
                        str(Decimal("100") + Decimal(minute) / Decimal("100")),
                    )
                    for minute in range(1, 61)
                ]
            }

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

    assert summary.outcome_events == 7
    assert summary.complete_outcomes == 7
    assert summary.comparison_events == 1
    forward_events = [
        event
        for event in sink.events
        if event["event_type"] == "candidate_forward_outcome"
    ]
    assert len(forward_events) == 7
    assert all(
        event["payload"]["research_eligibility"]["ads002_v1"]["eligible"] is False
        for event in forward_events
    )
    assert all(
        "ADS002_PRETRADE_INCOMPLETE"
        in event["payload"]["research_eligibility"]["ads002_v1"]["reason_codes"]
        for event in forward_events
    )
    assert any(
        event["event_type"] == "live_offline_comparison"
        for event in sink.events
    )


def test_equity_post_event_runner_excludes_crypto_candidates():
    crypto_row = {
        "candidate_id": 99,
        "scan_cycle_id": 199,
        "strategy_version_id": "LIVE-2026-09-25-003",
        "market_lane": "crypto",
        "symbol": "BTC/USD",
        "observed_at": "2026-09-29T20:00:05-04:00",
        "decision_reference_price": "80000",
        "qualified": False,
        "action": "hold",
        "features": {
            "market": "crypto",
            "bar_time": "2026-09-29T20:00:00-04:00",
            "strategy_version_id": "CRYPTO-2026-09-29-001",
        },
        "research_attribution": {
            "market": "crypto",
            "live_strategy_version": "CRYPTO-2026-09-29-001",
        },
    }

    class MarketData:
        async def market_calendar_details(self, **kwargs):
            raise AssertionError("equity market data must not receive crypto-only evidence")

        async def historical_bars_many(self, *args, **kwargs):
            raise AssertionError("equity historical path must not receive crypto symbols")

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    async def reader(**params):
        assert params == {"evidence_session": "2026-09-29"}
        return {"candidates": [crypto_row]}

    sink = Sink()
    runner = PostEventEvidenceRunner(
        settings=type("Settings", (), {"data_feed": "iex", "bar_timeframe": "1Min"})(),
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=reader,
    )
    summary = asyncio.run(runner.run_session(date(2026, 9, 29)))

    assert summary.candidates == 0
    assert summary.outcome_events == 0
    assert summary.comparison_events == 0
    assert sink.events == []


def test_post_event_runner_skips_already_complete_horizons():
    reference_bar = datetime(2026, 9, 25, 10, 30, tzinfo=NY)
    row = {
        **candidate(reference_bar),
        "candidate_id": 1,
        "candidate_key": "candidate-1",
        "scan_cycle_id": "cycle-1",
        "decision_cycle_payload": {},
        "scan_cycle": {},
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
        return {
            "candidates": [row],
            "complete_horizons": {"1": [1, 3, 5, 10, 15, 30]},
            "post_event_complete": False,
        }

    sink = Sink()
    runner = PostEventEvidenceRunner(
        settings=type("Settings", (), {"data_feed": "iex", "bar_timeframe": "1Min"})(),
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=reader,
    )

    summary = asyncio.run(runner.run_session(date(2026, 9, 25)))
    forward_events = [
        event
        for event in sink.events
        if event["event_type"] == "candidate_forward_outcome"
    ]

    assert len(forward_events) == 1
    assert forward_events[0]["payload"]["horizon_minutes"] == 60
    assert summary.skipped_complete_outcomes == 6
    assert summary.complete_outcomes == 7
    assert summary.outcome_events == 1


def test_post_event_runner_reuses_completed_session_marker():
    reference_bar = datetime(2026, 9, 25, 10, 30, tzinfo=NY)
    row = {
        **candidate(reference_bar),
        "candidate_id": 1,
        "candidate_key": "candidate-1",
    }

    class MarketData:
        async def market_calendar_details(self, *, start, end):
            raise AssertionError("completed session should not fetch market data")

    class Sink:
        def emit(self, **event):
            raise AssertionError("completed session should not emit evidence")

    async def reader(**params):
        return {
            "candidates": [row],
            "complete_horizons": {"1": [1, 3, 5, 10, 15, 30, 60]},
            "post_event_complete": True,
        }

    runner = PostEventEvidenceRunner(
        settings=type("Settings", (), {"data_feed": "iex", "bar_timeframe": "1Min"})(),
        market_data=MarketData(),
        event_sink=Sink(),
        evidence_reader=reader,
    )

    summary = asyncio.run(runner.run_session(date(2026, 9, 25)))

    assert summary.reused_existing_evidence is True
    assert summary.skipped_complete_outcomes == 7
    assert summary.complete_outcomes == 7
    assert summary.outcome_events == 0
    assert summary.comparison_events == 0


def test_crypto_forward_outcome_carries_promotion_context():
    from datetime import datetime, timedelta, timezone
    from app.crypto_post_event_evidence import calculate_continuous_forward_outcome

    now = datetime(2026, 10, 2, 12, 10, tzinfo=timezone.utc)
    reference = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    candidate_row = {
        "candidate_id": "crypto-test",
        "candidate_key": "cycle:BTC/USD",
        "observed_at": "2026-10-02T12:01:00+00:00",
        "decision_reference_price": "100",
        "features": {
            "bar_time": reference.isoformat(),
            "feature_state": {
                "raw": {
                    "realized_volatility": "0.0012",
                    "spread_bps": "7.5",
                }
            },
        },
        "market_lane": "crypto",
        "strategy_version_id": "CRYPTO-TEST",
    }
    bars = [
        {
            "t": (reference + timedelta(minutes=i)).isoformat(),
            "h": "101",
            "l": "99",
            "c": "100.5",
        }
        for i in range(1, 6)
    ]
    outcome = calculate_continuous_forward_outcome(
        candidate_row,
        bars,
        horizon_minutes=5,
        now=now,
    )
    assert outcome["status"] == "complete"
    assert outcome["candidate_observed_at"] == candidate_row["observed_at"]
    assert outcome["promotion_context"] == {
        "realized_volatility": "0.0012",
        "spread_bps": "7.5",
    }
