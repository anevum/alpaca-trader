import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import app.crypto_post_event_evidence as crypto_evidence
from app.crypto_features import bar_activity_state, crypto_feature_state, cyclic_time_features
from app.crypto_post_event_evidence import calculate_continuous_forward_outcome
from app.research_agent.crypto_ads import score_crypto_ads
from app.research_agent.crypto_graen import assess_crypto_promotion
from app.research_agent.crypto_nostra import infer_crypto_regime


def _bar(stamp, close, volume="10", trades=2):
    return {
        "t": stamp.isoformat().replace("+00:00", "Z"),
        "o": str(close),
        "h": str(Decimal(str(close)) + Decimal("1")),
        "l": str(Decimal(str(close)) - Decimal("1")),
        "c": str(close),
        "v": str(volume),
        "n": trades,
    }


def test_crypto_time_features_are_continuous_and_weekend_aware():
    stamp = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    state = cyclic_time_features(stamp)
    assert state["timestamp_utc"].endswith("+00:00")
    assert state["is_weekend"] is True
    assert -1.0 <= state["hour_sin"] <= 1.0
    assert -1.0 <= state["weekday_cos"] <= 1.0


def test_zero_volume_quote_derived_move_is_not_trade_activity():
    start = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)
    bars = [
        _bar(start, "100", volume="0", trades=0),
        _bar(start + timedelta(minutes=1), "101", volume="0", trades=0),
        _bar(start + timedelta(minutes=2), "102", volume="5", trades=3),
    ]
    activity = bar_activity_state(bars)
    assert activity["zero_volume_bars"] == 2
    assert activity["quote_derived_zero_volume_moves"] == 1
    assert activity["active_trade_bars"] == 1
    assert activity["trade_count"] == 3


def test_crypto_features_ads_and_nostra_use_crypto_state_only():
    now = datetime(2026, 9, 29, 20, 10, tzinfo=timezone.utc)
    bars = [_bar(now - timedelta(minutes=10 - i), 100 + i) for i in range(10)]
    state = crypto_feature_state(
        bars,
        {"bp": "108.9", "ap": "109.1", "bs": "5", "as": "6", "t": now.isoformat()},
        now=now,
        fast_window=3,
        volatility_lookback=8,
    )
    assert state["market_lane"] == "crypto"
    assert state["reference_population"] == "crypto_only_rolling"
    ads = score_crypto_ads(state, calibration_version="crypto-test-v1", calibrated=False)
    assert ads["probability"] is None
    assert ads["equity_calibration_reused"] is False
    nostra = infer_crypto_regime(feature_state=state)
    assert nostra["market_lane"] == "crypto"
    assert nostra["execution_authority"] is False


def test_graen_crypto_promotion_fails_closed_without_evidence():
    result = assess_crypto_promotion({})
    assert result["promotion_ready"] is False
    assert result["status"] == "GATED"
    assert "CANDIDATE_FLOOR" in result["reason_codes"]


def test_continuous_forward_outcome_crosses_equity_close_and_midnight():
    reference_bar = datetime(2026, 9, 29, 23, 58, tzinfo=timezone.utc)
    candidate = {
        "candidate_id": 7,
        "candidate_key": "crypto-test",
        "decision_reference_price": "100",
        "features": {"bar_time": reference_bar.isoformat()},
        "market_lane": "crypto",
        "strategy_version_id": "CRYPTO-2026-09-29-001",
    }
    bars = [
        _bar(reference_bar + timedelta(minutes=i), 100 + i)
        for i in range(1, 7)
    ]
    outcome = calculate_continuous_forward_outcome(
        candidate,
        bars,
        horizon_minutes=5,
        now=reference_bar + timedelta(minutes=10),
    )
    assert outcome is not None
    assert outcome["status"] == "complete"
    assert outcome["details"]["continuous_market"] is True
    assert Decimal(outcome["forward_return"]) > 0


def test_crypto_forward_evidence_bounds_catchup_and_skips_nonfinal_states(monkeypatch):
    reference_bar = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    now = reference_bar + timedelta(minutes=10)
    candidates = [
        {
            "candidate_id": candidate_id,
            "candidate_key": f"crypto-{candidate_id}",
            "symbol": "BTC/USD",
            "decision_reference_price": "100",
            "features": {"bar_time": reference_bar.isoformat(), "market": "crypto"},
            "market_lane": "crypto",
            "strategy_version_id": "CRYPTO-2026-09-29-001",
            "forward_outcomes": {},
        }
        for candidate_id in range(1, 4)
    ]
    bars = [
        _bar(reference_bar + timedelta(minutes=i), 100 + i)
        for i in range(1, 7)
    ]

    class MarketData:
        async def historical_bars_many(self, symbols, *, start, end):
            return {"BTC/USD": bars}

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    async def evidence_reader(*, evidence_session):
        return {"candidates": candidates}

    monkeypatch.setattr(
        crypto_evidence,
        "MAX_EMITTED_OUTCOMES_PER_RUN",
        2,
    )
    sink = Sink()
    state = SimpleNamespace(crypto_forward_evidence_state={})
    runner = crypto_evidence.CryptoForwardEvidenceRunner(
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=evidence_reader,
        state=state,
    )

    summary = asyncio.run(runner.run_recent(now))

    assert summary.emitted == 2
    assert summary.complete == summary.emitted
    assert summary.evaluation_limited is True
    assert summary.evaluated_candidates < summary.candidates
    assert summary.deferred > 0
    assert summary.incomplete == 0
    assert all(
        event["payload"]["status"] == "complete"
        for event in sink.events
    )
    assert state.crypto_forward_evidence_state["deferred"] == summary.deferred
    assert state.crypto_forward_evidence_state["deferred_unit"] == "candidate_rows"
    assert state.crypto_forward_evidence_state["evaluation_limited"] is True


def test_crypto_forward_evidence_does_not_emit_incomplete_outcomes():
    reference_bar = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    now = reference_bar + timedelta(minutes=5)
    candidate = {
        "candidate_id": 77,
        "candidate_key": "crypto-incomplete",
        "symbol": "BTC/USD",
        "decision_reference_price": "100",
        "features": {"bar_time": reference_bar.isoformat(), "market": "crypto"},
        "market_lane": "crypto",
        "strategy_version_id": "CRYPTO-2026-09-29-001",
        "forward_outcomes": {},
    }

    class MarketData:
        async def historical_bars_many(self, symbols, *, start, end):
            return {"BTC/USD": []}

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    async def evidence_reader(*, evidence_session):
        return {"candidates": [candidate]}

    sink = Sink()
    runner = crypto_evidence.CryptoForwardEvidenceRunner(
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=evidence_reader,
        state=SimpleNamespace(crypto_forward_evidence_state={}),
    )

    summary = asyncio.run(runner.run_recent(now))

    assert summary.incomplete > 0
    assert summary.emitted == 0
    assert sink.events == []



def test_crypto_forward_evidence_prioritizes_current_day_before_backlog(monkeypatch):
    now = datetime(2026, 10, 2, 20, 20, tzinfo=timezone.utc)
    current_reference = now - timedelta(minutes=10)
    prior_reference = current_reference - timedelta(days=1)

    def row(identity, reference):
        return {
            "candidate_id": identity,
            "candidate_key": identity,
            "symbol": "BTC/USD",
            "decision_reference_price": "100",
            "observed_at": (reference + timedelta(minutes=1)).isoformat(),
            "features": {"bar_time": reference.isoformat(), "market": "crypto"},
            "market_lane": "crypto",
            "strategy_version_id": "CRYPTO-2026-09-29-001",
            "forward_outcomes": {},
        }

    current = row("current-candidate", current_reference)
    prior = row("prior-candidate", prior_reference)
    calls = []

    async def evidence_reader(*, evidence_session):
        calls.append(evidence_session)
        if evidence_session == now.date().isoformat():
            return {"candidates": [current]}
        return {"candidates": [prior]}

    bars = [
        _bar(current_reference + timedelta(minutes=i), 100 + i)
        for i in range(1, 11)
    ] + [
        _bar(prior_reference + timedelta(minutes=i), 100 + i)
        for i in range(1, 11)
    ]

    class MarketData:
        async def historical_bars_many(self, symbols, *, start, end):
            return {"BTC/USD": bars}

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

    monkeypatch.setattr(crypto_evidence, "MAX_EMITTED_OUTCOMES_PER_RUN", 1)
    sink = Sink()
    runner = crypto_evidence.CryptoForwardEvidenceRunner(
        market_data=MarketData(),
        event_sink=sink,
        evidence_reader=evidence_reader,
        state=SimpleNamespace(crypto_forward_evidence_state={}),
    )

    summary = asyncio.run(runner.run_recent(now))

    assert calls == [
        now.date().isoformat(),
        (now - timedelta(days=1)).date().isoformat(),
    ]
    assert summary.emitted == 1
    assert sink.events[0]["payload"]["candidate_id"] == "current-candidate"
