import asyncio
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.market_fabric.asset_eligibility import AssetEligibility
from app.market_fabric.runtime import ShadowFabric
from app.market_fabric.staging import ReadOnlyBroker
from test_rhen44_shadow_integration import NOW, ready, quote


def asset(**changes):
    return {"symbol":"SPY","class":"us_equity","status":"active","tradable":True,
            "fractionable":True,"overnight_tradable":True,"overnight_halted":False,**changes}


def test_eligibility_recovery_preserves_source_age_and_never_grants_authority(tmp_path):
    path = tmp_path/"assets.db"
    db = sqlite3.connect(path)
    evidence = AssetEligibility(db,("SPY",))
    evidence.replace([asset()],NOW)
    db.close()
    db = sqlite3.connect(path)
    recovered = AssetEligibility(db,("SPY",))
    assert recovered.snapshot("SPY","OVERNIGHT",NOW+timedelta(seconds=600))["eligible"]
    expired = recovered.snapshot("SPY","OVERNIGHT",NOW+timedelta(seconds=601))
    assert not expired["eligible"] and expired["quality_state"] == "UNAVAILABLE"
    assert expired["fetched_at"] == NOW.isoformat() and not expired["entry_authority"]
    assert expired["provenance"] == "DERIVED" and expired["facts_provenance"] == "OBSERVED"
    assert not recovered.snapshot("SPY","REGULAR",NOW-timedelta(seconds=1))["eligible"]


@pytest.mark.parametrize("changes,reason",[
    ({"tradable":False},"ASSET_INELIGIBLE"),({"class":"crypto"},"ASSET_INELIGIBLE"),
    ({"class":"us_option"},"ASSET_INELIGIBLE"),({"status":"inactive"},"ASSET_INELIGIBLE"),
    ({"overnight_tradable":False},"OVERNIGHT_NOT_TRADABLE"),
    ({"overnight_tradable":None},"OVERNIGHT_NOT_TRADABLE"),
    ({"overnight_halted":True},"OVERNIGHT_HALTED"),({"overnight_halted":None},"OVERNIGHT_HALTED")])
def test_explicit_session_asset_veto(changes,reason):
    evidence = AssetEligibility(sqlite3.connect(":memory:"),("SPY",))
    evidence.replace([asset(**changes)],NOW)
    assert reason in evidence.snapshot("SPY","OVERNIGHT",NOW)["rejection_codes"]
    if reason != "ASSET_INELIGIBLE":
        assert evidence.snapshot("SPY","REGULAR",NOW)["eligible"]


def test_failed_or_malformed_refresh_cannot_replace_last_valid_observation():
    evidence = AssetEligibility(sqlite3.connect(":memory:"),("SPY",))
    evidence.replace([asset()],NOW)
    for rows in ([asset(tradable="true")],[asset(symbol="QQQ")],{"SPY":asset()}):
        with pytest.raises(ValueError): evidence.replace(rows,NOW+timedelta(seconds=1))
        assert evidence.fetched_at == NOW
    evidence.replace([],NOW+timedelta(seconds=1))
    assert not evidence.snapshot("SPY","REGULAR",NOW+timedelta(seconds=1))["eligible"]


def test_asset_client_is_bounded_get_only_and_missing_asset_remains_missing():
    calls=[]
    def respond(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(404) if request.url.path.endswith("QQQ") else httpx.Response(200,json=asset())
    settings=Settings(_env_file=None,EXTENDED_EQUITY_SYMBOLS="SPY,QQQ")
    rows=asyncio.run(ReadOnlyBroker(settings,transport=httpx.MockTransport(respond)).assets())
    assert rows == [asset()]
    assert sorted(calls) == [("GET","/v2/assets/QQQ"),("GET","/v2/assets/SPY")]


def test_missing_and_expired_asset_evidence_veto_signal_without_changing_market_truth(tmp_path):
    def evaluate(*args):
        raise AssertionError("ineligible asset cannot reach candidate evaluation")
    async def run():
        fabric=ShadowFabric(Settings(_env_file=None,EXTENDED_EQUITY_SYMBOLS="SPY",
            RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"shadow.db")),SimpleNamespace(),
            evaluator=evaluate,asset_reader=lambda:None)
        ready(fabric.store)
        event=quote(seconds=1)
        fabric.store.apply(event)
        await fabric.on_event(event)
        row=fabric.visual.scanner["SPY"]
        assert row["evaluable"]  # Market availability and asset eligibility are separate.
        assert row["classification"] == "EVALUABLE_REJECTED"
        assert row["rejection_code"] == "ASSET_INELIGIBLE" and row["candidate_state"] == "BLOCKED"
        fabric.assets.replace([asset()],NOW)
        fabric.visual.scanner["SPY"]["candidate_state"]="CANDIDATE"
        fabric.refresh_observation(NOW+timedelta(seconds=601))
        assert fabric.visual.scanner["SPY"]["candidate_state"] == "BLOCKED"
        assert fabric.asset_summary(NOW+timedelta(seconds=601))["attested_symbols"] == 0
        fabric.checkpoint.close()
    asyncio.run(run())
