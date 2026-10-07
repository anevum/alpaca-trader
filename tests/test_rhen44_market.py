import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.equity_sessions import EquitySessionResolver
from app.market_fabric.contracts import normalize, utc
from app.market_fabric.feed_router import route_feed, validate_symbols
from app.market_fabric.stream_state import MarketStateStore
from app.market_fabric.stream_recovery import StreamCheckpoint, merge_bars
from app.market_fabric.stream_manager import MarketStreamManager
from app.market_fabric.broker_updates import BrokerInbox, BrokerUpdateStream, execution_marker
from app.market_fabric.rejection_engine import Evaluation, RejectionEngine

NOW = datetime(2026, 10, 6, 15, 30, tzinfo=timezone.utc)


def event(kind="q", symbol="SPY", at=NOW, generation="g1", sequence=1, **extra):
    raw = {"T": kind, "S": symbol, "t": at.isoformat()}
    if kind == "q": raw.update(bp=100, ap=101)
    if kind in {"b", "u"}: raw.update(o=100,h=102,l=99,c=101,v=100)
    raw.update(extra)
    return normalize(raw, generation=generation, sequence=sequence, feed="iex", session="REGULAR", session_id="2026-10-06/REGULAR", received_at=NOW)


def ready_store():
    store = MarketStateStore(("SPY",), warm_bars=3)
    store.begin("g1", "iex", "2026-10-06/REGULAR")
    store.subscribed.add("SPY")
    for minutes in (3,2,1): store.apply(event("b", at=NOW-timedelta(minutes=minutes)))
    store.apply(event())
    return store


@pytest.mark.parametrize("hour,minute,session,feed,policy", [
    (3,59,"OVERNIGHT","overnight","SHADOW_ONLY"), (4,0,"PREMARKET",None,"DATA_CAPABILITY_BLOCKED"),
    (7,59,"PREMARKET",None,"DATA_CAPABILITY_BLOCKED"), (8,0,"PREMARKET","iex","SHADOW_ONLY"),
    (9,29,"PREMARKET","iex","SHADOW_ONLY"), (9,30,"REGULAR","iex","SHADOW_ONLY"),
    (16,0,"AFTER_HOURS","iex","SHADOW_ONLY"), (17,0,"AFTER_HOURS",None,"DATA_CAPABILITY_BLOCKED"),
    (19,59,"AFTER_HOURS",None,"DATA_CAPABILITY_BLOCKED"), (20,0,"OVERNIGHT","overnight","SHADOW_ONLY")])
def test_calendar_feed_boundaries(hour, minute, session, feed, policy):
    from zoneinfo import ZoneInfo
    fake = AsyncMock()
    fake.market_calendar_details.return_value = [{"date": datetime(2026,10,6).date(),"open":"09:30","close":"16:00"},
                                                 {"date": datetime(2026,10,7).date(),"open":"09:30","close":"16:00"}]
    now = datetime(2026,10,6,hour,minute,tzinfo=ZoneInfo("America/New_York"))
    context = asyncio.run(EquitySessionResolver(fake).classify(now))
    route = route_feed(context, now)
    assert (route.session,route.expected_feed,route.execution_policy) == (session,feed,policy)
    assert route.entry_authority is False
    assert route_feed(context,now,tier="plus").execution_policy == "DATA_CAPABILITY_BLOCKED"
    plus = route_feed(context,now,tier="plus",plus_verified=True)
    assert plus.expected_feed == ("boats" if session == "OVERNIGHT" else "sip")
    assert not plus.entry_authority


def test_halfday_weekend_and_calendar_failure():
    from zoneinfo import ZoneInfo
    fake = AsyncMock()
    fake.market_calendar_details.return_value = [{"date": datetime(2026,11,27).date(),"open":"09:30","close":"13:00"}]
    resolver = EquitySessionResolver(fake)
    now = datetime(2026,11,27,13,tzinfo=ZoneInfo("America/New_York"))
    assert asyncio.run(resolver.classify(now)).session.value == "after_hours"
    now = now.replace(hour=20)
    assert route_feed(asyncio.run(resolver.classify(now)),now).expected_feed is None
    fake.market_calendar_details.side_effect = RuntimeError("calendar unavailable")
    with pytest.raises(RuntimeError): asyncio.run(EquitySessionResolver(fake).classify(now))


def test_entitlement_capacity():
    validate_symbols(tuple(f"S{i}" for i in range(24)))
    with pytest.raises(ValueError): validate_symbols(tuple(f"S{i}" for i in range(31)))
    with pytest.raises(ValueError): validate_symbols(("SPY","SPY"))
    with pytest.raises(ValueError): validate_symbols(("BTC/USD",))


@pytest.mark.parametrize("extra", [{"bp":float("nan")},{"ap":float("inf")},{"bp":True}])
def test_invalid_numbers(extra):
    with pytest.raises(ValueError): event(**extra)


def test_source_freshness_and_warmup():
    store = ready_store()
    assert store.snapshot("SPY",NOW)["evaluable"]
    assert store.snapshot("SPY",NOW+timedelta(seconds=45))["evaluable"]
    assert "STALE_QUOTE" in store.snapshot("SPY",NOW+timedelta(seconds=45,microseconds=1))["rejection_codes"]
    # Quiet periods create no observations; receipt/render time cannot refresh source.
    assert len(store.rows["SPY"]["bars"]) == 3
    assert store.snapshot("SPY",NOW+timedelta(seconds=61))["bar_age_ms"] == 121000
    store.connection = "DISCONNECTED"
    assert not store.snapshot("SPY",NOW)["evaluable"]


def test_out_of_order_duplicates_reconnect_and_context():
    store = ready_store()
    assert not store.apply(event(at=NOW-timedelta(seconds=1),bp=50))
    assert not store.apply(event())
    assert store.snapshot("SPY",NOW)["bid"] == 100
    assert (store.out_of_order,store.duplicates) == (1,1)
    store.begin("g2","iex","2026-10-06/REGULAR")
    assert len(store.rows["SPY"]["bars"]) == 3
    assert not store.snapshot("SPY",NOW)["evaluable"]
    assert not store.apply(event())
    store.begin("g3","overnight","2026-10-07/OVERNIGHT")
    assert store.rows == {}


def test_market_revision_is_not_a_new_candle():
    store = ready_store()
    revision = event("u",at=NOW-timedelta(minutes=2),c=102,h=103)
    assert store.apply(revision)
    assert len(store.rows["SPY"]["bars"]) == 3
    assert store.rows["SPY"]["bars"][1]["close"] == 102
    assert not store.apply(event("u",at=NOW-timedelta(minutes=10)))
    with pytest.raises(ValueError): event("b",o=105)


def test_future_clock_and_crossed_quote():
    with pytest.raises(ValueError): event(at=NOW+timedelta(microseconds=1))
    with pytest.raises(ValueError): utc("2026-10-06T01:00:00")
    store = ready_store()
    store.rows["SPY"]["quote"]["ask"] = 99
    assert "LOCKED_OR_CROSSED" in store.snapshot("SPY",NOW)["rejection_codes"]
    assert store.snapshot("SPY",NOW)["mid"] is None


def test_checkpoint_restart_does_not_restore_fresh_quote_or_subscription(tmp_path):
    db = StreamCheckpoint(str(tmp_path/"checkpoint.db"))
    db.save(ready_store(),NOW)
    db.close()
    restored = MarketStateStore(("SPY",),warm_bars=3)
    restored.begin("new","iex","2026-10-06/REGULAR")
    db = StreamCheckpoint(str(tmp_path/"checkpoint.db"))
    assert db.restore(restored,NOW) == 3
    assert not restored.snapshot("SPY",NOW)["evaluable"]
    restored.subscribed.add("SPY")
    assert restored.apply(event(generation="new"))
    assert restored.snapshot("SPY",NOW)["evaluable"]
    restored.begin("other","overnight","2026-10-07/OVERNIGHT")
    assert db.restore(restored,NOW) == 0
    db.close()


def test_bootstrap_rejects_unfinished_future_and_wrong_feed():
    store = ready_store()
    bars = [{"timestamp":NOW.isoformat(),"open":100,"high":102,"low":99,"close":101,"volume":10},
            {"timestamp":(NOW-timedelta(minutes=5)).isoformat(),"feed":"sip","open":100,"high":102,"low":99,"close":101,"volume":10}]
    assert merge_bars(store,{"SPY":bars},NOW) == 0


def test_gap_is_not_hidden_by_fresh_last_bar():
    store = ready_store()
    store.rows["SPY"]["bars"][0]["timestamp"] = (NOW-timedelta(minutes=20)).isoformat()
    assert "DATA_GAP" in store.snapshot("SPY",NOW)["rejection_codes"]


class FakeSocket:
    def __init__(self,messages): self.messages = messages; self.sent=[]
    async def send(self,raw): self.sent.append(json.loads(raw))
    def __aiter__(self):
        async def iterator():
            for msg in self.messages: yield json.dumps(msg)
        return iterator()


def test_market_handshake_generation_and_exact_subscription():
    store = MarketStateStore(("SPY",),warm_bars=1)
    seen = []
    async def callback(event): seen.append(event)
    manager = MarketStreamManager(store,api_key="test-only",api_secret="test-only",on_event=callback)
    ws = FakeSocket([[{"T":"success","msg":"connected"}],[{"T":"success","msg":"authenticated"}],
                     [{"T":"subscription","quotes":["SPY"],"bars":["SPY"],"updatedBars":["SPY"]}],
                     [{"T":"q","S":"SPY","t":NOW.isoformat(),"bp":100,"ap":101}]])
    asyncio.run(manager.consume(ws,"iex","2026-10-06/REGULAR"))
    first = store.generation
    assert len(seen) == 1 and len(ws.sent) == 2
    asyncio.run(manager.consume(ws,"iex","2026-10-06/REGULAR"))
    assert store.generation != first
    ws = FakeSocket([[{"T":"success","msg":"authenticated"}], [{"T":"subscription","quotes":[],"bars":[],"updatedBars":[]}]])
    with pytest.raises(ValueError): asyncio.run(manager.consume(ws,"iex","2026-10-06/REGULAR"))


def test_broker_inbox_restart_idempotence_and_overload(tmp_path):
    path = str(tmp_path/"broker.db")
    inbox = BrokerInbox(path,max_pending=1)
    raw = {"event":"partial_fill","timestamp":NOW.isoformat(),"qty":"1","price":"101",
           "order":{"id":"order-1","symbol":"SPY","side":"buy","filled_qty":"1"}}
    key = inbox.retain(raw)
    assert inbox.retain(raw) == key
    with pytest.raises(RuntimeError): inbox.retain({**raw,"timestamp":(NOW+timedelta(seconds=1)).isoformat()})
    inbox.close()
    inbox = BrokerInbox(path)
    calls = []
    async def failed(*args): return False
    with pytest.raises(RuntimeError): asyncio.run(inbox.deliver(failed))
    async def success(k,data): calls.append((k,data)); return True
    asyncio.run(inbox.deliver(success)); asyncio.run(inbox.deliver(success))
    assert len(calls) == 1
    marker = execution_marker(key,raw)
    assert marker["event_type"] == "PARTIAL_FILL" and marker["price"] == 101
    assert execution_marker(key,{**raw,"event":"pending_new"}) is None
    inbox.close()


def test_broker_handshake_never_accepts_before_listening(tmp_path):
    inbox = BrokerInbox(str(tmp_path/"b.db"))
    stream = BrokerUpdateStream(api_key="test",api_secret="test",paper=True,inbox=inbox,callback=AsyncMock(return_value=True))
    ws = FakeSocket([{"stream":"authorization","data":{"status":"authorized"}}, {"stream":"listening","data":{"streams":["trade_updates"]}}])
    asyncio.run(stream.consume(ws))
    assert stream.state == "HEALTHY" and ws.sent[1]["action"] == "listen"
    with pytest.raises(ValueError): asyncio.run(stream.consume(FakeSocket([{"stream":"authorization","data":{"status":"unauthorized"}}])))
    inbox.close()


def test_rejection_denominator_missing_code_and_bounded_ids():
    engine = RejectionEngine(max_events=2)
    e = Evaluation("1",NOW,"REGULAR","iex","SPY","NOT_EVALUABLE",("STALE_QUOTE",))
    assert engine.record(e) and not engine.record(e)
    engine.record(Evaluation("2",NOW,"REGULAR","iex","QQQ","CANDIDATE"))
    summary = engine.summary(NOW)
    assert summary["evaluable_rate"] == .5 and summary["candidates"] == 1 and summary["candidate_rate_per_1000_evaluations"] == 1000
    engine.record(Evaluation("3",NOW,"OVERNIGHT","overnight","SPY","EVALUABLE_REJECTED",("MOMENTUM_FAIL",)))
    assert len(engine.events) == len(engine.ids) == 2
    assert engine.summary(NOW,session="REGULAR")["candidates"] == 1
    with pytest.raises(ValueError): Evaluation("x",NOW,"REGULAR","iex","SPY","NOT_EVALUABLE")
    with pytest.raises(ValueError): Evaluation("x",NOW,"REGULAR","iex","SPY","NOT_EVALUABLE",("magic",))


def test_production_defaults_have_no_shadow_or_new_authority():
    settings = Settings(_env_file=None)
    assert not settings.rhen_market_stream_enabled
    assert not settings.rhen_broker_stream_shadow_enabled
    assert not settings.command_live_stream_enabled
    with pytest.raises(ValueError): Settings(_env_file=None,COMMAND_LIVE_FLUSH_MS=5000)
