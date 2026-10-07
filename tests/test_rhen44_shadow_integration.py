import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.config import Settings
from app.market_fabric.contracts import normalize
from app.market_fabric.decision_evidence import DecisionEvidence
from app.market_fabric.runtime import ShadowFabric
from app.market_fabric.shadow_features import completed_features, regime_observation
from app.market_fabric.stream_recovery import merge_bars
from app.market_fabric.stream_state import MarketStateStore
from app.strategy import Signal
from app.command_visuals.account_projection import account_projection
import pytest
import httpx
from fastapi.testclient import TestClient
from app.market_fabric.staging import create_app, ReadOnlyBroker

NOW = datetime(2026,10,6,15,30,tzinfo=timezone.utc)


def test_overnight_restart_restores_only_matching_observed_bars(tmp_path):
    now = datetime.now(timezone.utc)
    settings = Settings(rhen_market_stream_checkpoint_path=str(tmp_path/"overnight.db"))
    fabric = ShadowFabric(settings, SimpleNamespace())
    symbol = fabric.store.symbols[0]
    sid = "2026-10-07/OVERNIGHT"
    fabric.store.begin("old", "overnight", sid)
    event = normalize({"T":"b","S":symbol,"t":(now-timedelta(minutes=20)).isoformat(),
        "o":100,"h":101,"l":99,"c":100,"v":12}, generation="old", sequence=1,
        feed="overnight", session="OVERNIGHT", session_id=sid, received_at=now)
    fabric.store.apply(event)
    fabric.checkpoint.save(fabric.store, now)
    fabric.store.rows.clear()
    fabric.store.begin("new", "overnight", sid)
    asyncio.run(fabric.bootstrap("overnight", sid))
    assert fabric.visual.system["restored_bar_count"] == 1
    assert fabric.visual.system["bootstrap_error"] == "OVERNIGHT_HISTORY_UNAVAILABLE"
    assert not fabric.store.subscribed
    assert "quote" not in fabric.store.rows[symbol]
    assert fabric.store.rows[symbol]["bars"][0]["quality_state"] == "DELAYED"
    assert not fabric.store.snapshot(symbol, now)["evaluable"]
    fabric.checkpoint.close()


def ready(store, generation="g1"):
    store.begin(generation, "iex", "2026-10-06/REGULAR")
    store.subscribed = set(store.symbols)
    for symbol in store.symbols:
        for i in range(16,0,-1):
            close = 100+(16-i)*.01
            event = normalize({"T":"b","S":symbol,"t":(NOW-timedelta(minutes=i)).isoformat(),"o":close,"h":close+.1,"l":close-.1,"c":close,"v":100,"vw":close},
                generation=generation, sequence=17-i, feed="iex", session="REGULAR", session_id="2026-10-06/REGULAR",received_at=NOW)
            store.apply(event)
        store.apply(quote(symbol,generation=generation))


def quote(symbol="SPY", *, seconds=0, generation="g1",sequence=20,spread=.01):
    at=NOW+timedelta(seconds=seconds)
    return normalize({"T":"q","S":symbol,"t":at.isoformat(),"bp":100,"ap":100+spread},generation=generation,sequence=sequence,
        feed="iex",session="REGULAR",session_id="2026-10-06/REGULAR",received_at=at)


def test_quote_bursts_and_restart_keep_one_durable_candidate(tmp_path):
    settings = Settings(_env_file=None,EXTENDED_EQUITY_SYMBOLS="SPY,QQQ",CONFIRMATION_SYMBOLS="QQQ",RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"state.db"))
    calls=[]
    def evaluate(*args):
        calls.append(args[0])
        return Signal("buy",symbol=args[0],reference_price=Decimal("100"),metadata={"checks":{"momentum_ok":True}})
    async def run():
        for cycle in range(2):
            fabric=ShadowFabric(settings,SimpleNamespace(),evaluator=evaluate)
            ready(fabric.store,generation="g"+str(cycle))
            for i in range(100):
                event=quote(seconds=i*.001,generation="g"+str(cycle),sequence=30+i)
                fabric.store.apply(event)
                await fabric.on_event(event)
            summary=fabric.evidence.summary("2026-10-06","REGULAR")
            assert summary["symbols"]["SPY"]["candidates"] == 1
            assert fabric.policy_snapshot.execution_values["stop_pct"] == str(settings.stop_pct)
            assert not fabric.policy_snapshot.entry_authority
            assert fabric.policy_snapshot.proposed_profile == "NO_TRADE"
            assert fabric.visual.scanner["SPY"]["classification"] == "CANDIDATE"
            fabric.checkpoint.close()
    asyncio.run(run())
    assert len(calls)==2


def test_missing_stale_gap_and_future_features_are_not_completed_truth():
    store=MarketStateStore(("SPY","QQQ"))
    ready(store)
    regime,features=regime_observation(store,NOW)
    assert len(features)==2 and regime["quality_state"]=="LIVE"
    features,_=completed_features(store,NOW-timedelta(seconds=1))
    assert not features  # quotes are from a future time and the final bar is unfinished
    regime,features=regime_observation(store,NOW+timedelta(seconds=46))
    assert not features and regime["primary_regime"]=="UNKNOWN"
    ready(store,generation="g2")
    store.rows["QQQ"]["bars"].popleft()
    regime,features=regime_observation(store,NOW)
    assert len(features)==1 and regime["primary_regime"]=="UNKNOWN"


def test_checkpoint_merge_preserves_provider_vwap():
    store=MarketStateStore(("SPY",))
    store.begin("g","iex","2026-10-06/REGULAR")
    merge_bars(store,{"SPY":[{"t":(NOW-timedelta(minutes=1)).isoformat(),"o":100,"h":102,"l":99,"c":101,"v":100,"vw":100.4}]},NOW)
    assert store.rows["SPY"]["bars"][0]["vwap"] == 100.4


def test_durable_rollups_survive_retention_and_have_no_validation_pass():
    db=sqlite3.connect(":memory:")
    ledger=DecisionEvidence(db,retain=2,retain_candidates=1)
    for i in range(4):
        body={"observed_at":(NOW+timedelta(seconds=i)).isoformat(),"entry_authority":False,"classification":"CANDIDATE",
            "session_day":"2026-10-06","session":"REGULAR","symbol":"SPY","reasons":[]}
        assert ledger.record(str(i),body)
        assert not ledger.record(str(i),body)
    assert db.execute("SELECT COUNT(*) FROM shadow_decision").fetchone()[0]==2
    assert db.execute("SELECT COUNT(*) FROM shadow_candidate").fetchone()[0]==1
    assert not ledger.record("0",body)  # identity survives bounded payload eviction
    summary=ledger.summary("2026-10-06","REGULAR")
    assert summary["symbols"]["SPY"]["candidates"]==4
    assert summary["validation_state"]=="UNVALIDATED" and summary["independent_evaluation_sessions"] is None


def test_account_projection_uses_only_broker_values_and_order_identities():
    snapshot={"account":{"equity":"501","cash":"401"},
        "positions":[{"symbol":"SPY","qty":"1","avg_entry_price":"99","market_value":"100","unrealized_pl":"1"}],
        "open_orders":[{"id":"parent","symbol":"SPY","status":"new","limit_price":"101","legs":[{"id":"stop","symbol":"SPY","status":"new","stop_price":"98"}]}]}
    observed=account_projection(snapshot,NOW)
    assert observed["points"]["equity"]["value"]==501
    assert observed["positions"][0]["average_entry_price"]==99
    assert {(o["order_ref"],o["value"]) for o in observed["overlays"]}=={("parent",101),("stop",98)}
    snapshot["open_orders"]=[]
    assert account_projection(snapshot,NOW)["overlays"]==[]  # no synthetic stop from settings
    snapshot["account"]["equity"]="nan"
    with pytest.raises(ValueError): account_projection(snapshot,NOW)


def test_broker_event_triggers_read_projection_without_writes(tmp_path):
    async def run():
        calls=[]
        async def reader():
            calls.append("READ")
            return {"account":{"equity":"500","cash":"300"},"positions":[],"open_orders":[],
                "sizing":{"base_safe_notional":"80","cash":"300","hard_gross_envelope":"400","existing_gross_exposure":"0"}}
        settings=Settings(_env_file=None,RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"account.db"))
        fabric=ShadowFabric(settings,SimpleNamespace(),account_reader=reader)
        await fabric.broker_event("e1",{"event":"fill","timestamp":NOW.isoformat(),"qty":"1","price":"100","order":{"id":"o1","symbol":"SPY","side":"buy"}})
        assert fabric.account_refresh.is_set() and not calls
        await fabric.reconcile_account()
        assert calls==["READ"] and fabric.visual.snapshot()["series"]["account:equity"][0]["value"]==500
        governor=fabric.visual.system["capital_governor"]
        assert governor["notional"]=="0" and governor["entry_authority"] is False
        assert fabric.visual.executions.points[0]["order_ref"]=="o1"
        fabric.checkpoint.close()
    asyncio.run(run())


def test_isolated_process_rejects_armed_configuration_and_has_no_order_routes(monkeypatch):
    monkeypatch.setenv("CRYPTO_EXECUTION_ENABLED", "false")
    settings=Settings(_env_file=None,EXECUTION_ENABLED=False,BOT_ARMED=False,LIVE_TRADING=False,SCAN_ONLY=True,
        EXTENDED_EQUITY_EXECUTION_ENABLED=False,RHEN_MARKET_STREAM_ENABLED=False)
    app=create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json()["broker_orders_possible"] is False
        assert client.post("/v2/orders",json={"symbol":"SPY"}).status_code==404
        assert client.get("/v1/command/shadow/status").status_code==401
    for field in ("execution_enabled","bot_armed","live_trading","extended_equity_execution_enabled"):
        with pytest.raises(ValueError): create_app(settings.model_copy(update={field:True}))
    monkeypatch.setenv("CRYPTO_EXECUTION_ENABLED", "true")
    with pytest.raises(ValueError): create_app(settings)


def test_staging_broker_client_only_issues_allowlisted_gets():
    calls=[]
    def respond(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json={"cash":"100","equity":"100","last_equity":"100"} if request.url.path=="/v2/account" else [])
    async def run():
        reader=ReadOnlyBroker(Settings(_env_file=None),transport=httpx.MockTransport(respond))
        await reader.snapshot()
        with pytest.raises(ValueError): await reader.read("/v2/orders/new")
    asyncio.run(run())
    assert sorted(calls)==[("GET","/v2/account"),("GET","/v2/orders"),("GET","/v2/positions")]


def test_valid_but_wide_quote_is_rejected_before_signal_evaluation(tmp_path):
    def evaluate(*args):
        raise AssertionError("wide spread must veto candidate evaluation")
    async def run():
        fabric=ShadowFabric(Settings(_env_file=None,EXTENDED_EQUITY_SYMBOLS="SPY",RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"spread.db")),SimpleNamespace(),evaluator=evaluate)
        ready(fabric.store)
        event=quote(seconds=1,spread=1)
        fabric.store.apply(event)
        assert fabric.store.snapshot("SPY",event.received_at)["evaluable"]
        await fabric.on_event(event)
        assert fabric.visual.scanner["SPY"]["classification"]=="EVALUABLE_REJECTED"
        assert fabric.visual.scanner["SPY"]["rejection_code"]=="SPREAD_TOO_WIDE"
        fabric.checkpoint.close()
    asyncio.run(run())
