import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.market_fabric.contracts import normalize
from app.market_fabric.coverage_evidence import CoverageEvidence
from app.market_fabric.decision_evidence import DecisionEvidence
from app.market_fabric.runtime import ShadowFabric
from app.market_fabric.stream_state import MarketStateStore
from app.market_fabric.stream_manager import MarketStreamManager

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)


def store_ready(now=NOW, symbols=("SPY",)):
    store = MarketStateStore(symbols, warm_bars=1)
    store.begin("g", "iex", "2026-10-07/REGULAR")
    store.subscribed = set(symbols)
    for symbol in symbols:
        for raw in ({"T":"b", "S":symbol, "t":(now-timedelta(minutes=1)).isoformat(),
                     "o":100, "h":101, "l":99, "c":100, "v":10},
                    {"T":"q", "S":symbol, "t":now.isoformat(), "bp":100, "ap":100.01}):
            store.apply(normalize(raw, generation="g", sequence=1, feed="iex", session="REGULAR",
                session_id="2026-10-07/REGULAR", received_at=now))
    return store


def tracker(db=None, configuration="config-a"):
    return CoverageEvidence(db or sqlite3.connect(":memory:"), strategy_version="4.3", configuration=configuration)


def test_quiet_stream_exposure_stops_at_source_deadline_not_next_audit():
    store, coverage = store_ready(), tracker()
    coverage.advance(store, NOW)
    summary = coverage.summary(store, NOW+timedelta(seconds=300))
    assert summary["evaluable_symbol_hours"]*3600 == pytest.approx(45)
    assert summary["eligible_symbol_hours"]*3600 == pytest.approx(300)
    assert summary["signal_candidates_per_evaluable_symbol_hour"] == 0
    assert summary["validation_state"] == "UNVALIDATED"
    assert summary["independent_evaluation_sessions"] is None
    assert not summary["entry_authority"]


def test_bar_deadline_is_independent_of_fresh_quotes():
    store, coverage = store_ready(), tracker()
    store.rows["SPY"]["timestamps"]["bar"] = NOW-timedelta(seconds=110)
    coverage.advance(store, NOW)
    assert coverage.summary(store, NOW+timedelta(seconds=20))["evaluable_symbol_hours"]*3600 == pytest.approx(10)
    row = store.snapshot("SPY", NOW+timedelta(seconds=20))
    assert row["quote_age_ms"] == 20000 and row["quality_state"] == "STALE"
    assert row["rejection_code"] == "STALE_BAR"


def test_disconnection_restart_and_session_change_exclude_unobserved_time(tmp_path):
    db = sqlite3.connect(tmp_path/"coverage.db")
    store, coverage = store_ready(), tracker(db)
    coverage.advance(store, NOW)
    store.connection = "DISCONNECTED"
    coverage.advance(store, NOW+timedelta(seconds=10))
    coverage.save()
    restarted = tracker(db)
    later = NOW+timedelta(minutes=10)
    store = store_ready(later)
    restarted.advance(store, later)
    summary = restarted.summary(store, later+timedelta(seconds=5))
    assert summary["evaluable_symbol_hours"]*3600 == pytest.approx(15)
    assert summary["eligible_symbol_hours"]*3600 == pytest.approx(15)
    assert summary["restart_downtime_included"] is False
    store.begin("g2", "iex", "2026-10-07/AFTER_HOURS")
    after = restarted.summary(store, later+timedelta(seconds=6))
    assert after["evaluable_symbol_hours"] == 0
    assert after["symbols"]["SPY"]["session"] == "AFTER_HOURS"


def test_distinct_candidates_and_exposure_have_matching_configuration_lineage():
    store, coverage = store_ready(), tracker()
    evidence = DecisionEvidence(coverage.db)
    coverage.advance(store, NOW)
    body = {"observed_at":NOW.isoformat(), "entry_authority":False, "classification":"CANDIDATE",
        "session_day":"2026-10-07", "session":"REGULAR", "session_id":"2026-10-07/REGULAR", "feed":"iex",
        "symbol":"SPY", "strategy_version":"4.3", "shadow_configuration_fingerprint":"config-a", "reasons":[]}
    assert evidence.record("one", body, coverage=coverage)
    assert not evidence.record("one", body, coverage=coverage)
    summary = coverage.summary(store, NOW+timedelta(seconds=30))
    assert summary["signal_candidates"] == 1
    assert summary["signal_candidates_per_evaluable_symbol_hour"] == pytest.approx(120)
    changed = tracker(coverage.db, configuration="config-b")
    assert changed.summary(store, NOW+timedelta(seconds=30))["signal_candidates"] == 0
    with pytest.raises(ValueError): evidence.record("wrong", body, coverage=changed)
    assert not coverage.db.execute("SELECT id FROM shadow_seen WHERE id='wrong'").fetchone()


def test_targeted_coverage_advance_only_touches_the_event_symbol():
    store, coverage = store_ready(symbols=("SPY", "QQQ")), tracker()
    coverage.advance(store, NOW, symbols=("SPY",))
    assert set(coverage.cursors) == {"SPY"}
    assert len(coverage.totals) == 1
    coverage.advance(store, NOW+timedelta(seconds=5), symbols=("QQQ",))
    assert set(coverage.cursors) == {"SPY", "QQQ"}
    assert len(coverage.totals) == 2


def test_clock_reversal_cannot_duplicate_exposure_and_rows_are_bounded():
    store, coverage = store_ready(symbols=("SPY", "QQQ")), tracker()
    coverage.advance(store, NOW)
    coverage.advance(store, NOW+timedelta(seconds=10))
    coverage.advance(store, NOW+timedelta(seconds=5))
    assert coverage.summary(store, NOW+timedelta(seconds=15))["evaluable_symbol_hours"]*3600 == pytest.approx(30)
    coverage.save()
    assert len(coverage.cursors) == 2 and len(coverage.totals) == 2


def test_expiry_marks_candidate_blocked_without_creating_market_or_decision_events(tmp_path):
    fabric = ShadowFabric(Settings(_env_file=None, EXTENDED_EQUITY_SYMBOLS="SPY",
        RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"state.db")), SimpleNamespace())
    fabric.store = store_ready()
    fabric.visual.store = fabric.store
    fabric.visual.scanner["SPY"] = {**fabric.store.snapshot("SPY",NOW), "candidate_state":"CANDIDATE",
                                   "classification":"CANDIDATE", "decision_id":"historical"}
    fabric.refresh_observation(NOW)
    fabric.refresh_observation(NOW+timedelta(seconds=46))
    row = fabric.visual.scanner["SPY"]
    assert row["candidate_state"] == "BLOCKED" and row["classification"] == "NOT_EVALUABLE"
    assert row["rejection_code"] == "STALE_QUOTE"
    assert row["observed_bar_count"] == 1 and row["required_bar_count"] == 1
    assert row["state_observed_at"] == (NOW+timedelta(seconds=46)).isoformat()
    assert not fabric.visual.series
    assert not fabric.checkpoint.db.execute("SELECT id FROM shadow_decision").fetchone()
    fabric.checkpoint.close()


def test_deadline_task_expires_quiet_state_without_a_polling_loop(tmp_path):
    async def run():
        now = datetime.now(timezone.utc)
        fabric = ShadowFabric(Settings(_env_file=None, EXTENDED_EQUITY_SYMBOLS="SPY",
            RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"timer.db")), SimpleNamespace())
        fabric.store = store_ready(now)
        fabric.store.QUOTE_FRESHNESS_MS = 25  # only this isolated test fixture
        fabric.refresh_observation(now)
        task = asyncio.create_task(fabric.freshness_observer())
        try:
            # Observe an actual publisher invalidation, not another market event.
            for _ in range(100):
                if fabric.visual.scanner["SPY"]["rejection_code"] == "STALE_QUOTE":
                    break
                await asyncio.sleep(.005)
            assert fabric.visual.scanner["SPY"]["rejection_code"] == "STALE_QUOTE"
            assert fabric.coverage.summary(fabric.store, datetime.now(timezone.utc))["evaluable_symbol_hours"]*3600 <= .0251
            assert not fabric.visual.series
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            fabric.checkpoint.close()
    asyncio.run(run())


def test_buffered_market_burst_yields_to_other_tasks_without_losing_events():
    class BufferedSocket:
        def __init__(self):
            now = datetime.now(timezone.utc)-timedelta(seconds=1)
            self.frames = iter([json.dumps([
                {"T":"success", "msg":"authenticated"},
                {"T":"subscription", "quotes":["SPY"], "bars":["SPY"], "updatedBars":["SPY"]},
                *[{"T":"q", "S":"SPY", "t":(now+timedelta(microseconds=i)).isoformat(),
                   "bp":100, "ap":100.01} for i in range(40)]])])
        async def send(self, _): pass
        def __aiter__(self): return self
        async def __anext__(self):
            try: return next(self.frames)
            except StopIteration: raise StopAsyncIteration
    async def run():
        processed, other_task_progress = [], []
        async def callback(event):
            processed.append(event.sequence)
            # Simulate bounded synchronous feature work, without an implicit yield.
            until = time.perf_counter()+.001
            while time.perf_counter() < until: pass
        async def other():
            other_task_progress.append(len(processed))
        manager = MarketStreamManager(MarketStateStore(("SPY",), warm_bars=1),
            api_key="test-only", api_secret="test-only", on_event=callback)
        broker_or_publisher = asyncio.create_task(other())
        await manager.consume(BufferedSocket(), "iex", "2026-10-07/REGULAR")
        await broker_or_publisher
        assert processed == list(range(1,41))
        assert manager.cooperative_yields > 0 and manager.processed_events == 40
        assert other_task_progress and other_task_progress[0] < 40
    asyncio.run(run())


def test_cached_continuity_detects_intermediate_source_timestamp_changes():
    store = store_ready()
    store.warm_bars = 3
    row = store.rows["SPY"]
    original = row["bars"][0]
    row["bars"].clear()
    row["bars"].extend({**original, "timestamp":(NOW-timedelta(minutes=i)).isoformat()} for i in (3,2,1))
    assert store.snapshot("SPY",NOW)["evaluable"]
    row["bars"][1] = {**row["bars"][1], "timestamp":(NOW-timedelta(minutes=6)).isoformat()}
    assert "DATA_GAP" in store.snapshot("SPY",NOW)["rejection_codes"]


def test_late_deadline_observer_invalidates_before_waiting(tmp_path, monkeypatch):
    async def run():
        fabric = ShadowFabric(Settings(_env_file=None, EXTENDED_EQUITY_SYMBOLS="SPY",
            RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path / "late-timer.db")), SimpleNamespace())
        fabric.store = store_ready(NOW)
        fabric.store.QUOTE_FRESHNESS_MS = 25
        fabric.refresh_observation(NOW)
        class LateClock:
            @staticmethod
            def now(_timezone):
                return NOW + timedelta(milliseconds=50)
        monkeypatch.setattr("app.market_fabric.runtime.datetime", LateClock)
        task = asyncio.create_task(fabric.freshness_observer())
        try:
            await asyncio.sleep(0)
            assert fabric.visual.scanner["SPY"]["rejection_code"] == "STALE_QUOTE"
            assert fabric.coverage.summary(fabric.store, NOW + timedelta(milliseconds=50))["evaluable_symbol_hours"] * 3600 <= .0251
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            fabric.checkpoint.close()
    asyncio.run(run())
