"""F3c offline provider ingest and remote-only source recovery. No credentials/network."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import shutil
import pytest
from next_rhen.paper_capture import capture_scheduled_paper_scan,verify_paper_session
from next_rhen.paper_market_feed import AlpacaHTTPSReadOnly,MarketFeedError,BARS_PATH,QUOTES_PATH,build_paper_snapshot,MAX_BODY_BYTES
from next_rhen.paper_source_archive import PaperRemoteIntegrityError,seal_provider_snapshot,restore_provider_snapshot
from next_rhen.remote_vault import R2S3ImmutableStore
from scripts.v5_f2c_r2_stage_probe import StrictSyntheticS3,BUCKET
from test_v5_paper_capture import W,R,D,slot,system,ts

def pages():
    return {
      (BARS_PATH,None):{"bars":{"AAPL":[{"t":ts(-1),"o":100.0,"h":100.7,"l":99.9,"c":100.5,"v":1000}]},"next_page_token":"b2"},
      (BARS_PATH,"b2"):{"bars":{"MSFT":[{"t":ts(-1),"o":300.0,"h":300.1,"l":299.5,"c":299.7,"v":1200}]},"next_page_token":None},
      (QUOTES_PATH,None):{"quotes":{"AAPL":[{"t":ts(0,-2),"bp":100.0,"ap":100.1,"bs":30,"as":30}]},"next_page_token":"q2"},
      (QUOTES_PATH,"q2"):{"quotes":{"MSFT":[{"t":ts(0,-3),"bp":299.8,"ap":299.9,"bs":40,"as":25}]},"next_page_token":None},
    }
class FakeReader:
    def __init__(self,data=None):
        self.data=pages() if data is None else data
        self.calls=[]
    def get(self,*,path,params):
        self.calls.append((path,dict(params)))
        key=(path,params.get("page_token"))
        if key not in self.data:
            raise MarketFeedError("missing fake provider page")
        return deepcopy(self.data[key])
def capture(reader=None):
    return build_paper_snapshot(reader or FakeReader(),slot=slot(),symbols=["AAPL","MSFT"],feed="iex")
def remote():
    raw=StrictSyntheticS3()
    return raw,R2S3ImmutableStore(raw,BUCKET,workspace_id=W,run_id=R)
def restore(store,receipt,*,workspace=W,pin=None):
    return restore_provider_snapshot(store,receipt_key=receipt["receipt_key"],
      pinned_receipt_sha256=receipt["receipt_sha256"] if pin is None else pin,
      workspace_id=workspace,run_id=R,cycle_id="paper-0001",session_date=D)

def test_alpaca_two_endpoint_paginated_input_integrates_real_f1_and_f3b(tmp_path):
    reader=FakeReader()
    inp=capture(reader)
    assert len(reader.calls)==4
    assert {p for p,_ in reader.calls}=={BARS_PATH,QUOTES_PATH}
    assert inp["provenance"]["page_count"]==4
    assert inp["provenance"]["missing_bar_symbols"]==[]
    assert inp["provenance"]["missing_quote_symbols"]==[]
    assert inp["snapshot"]["source_origin"]=="UNATTESTED_IMPORTED"
    with system(tmp_path) as (sch,src,journal,market):
        s=slot(origin="INJECTED_SCHEDULER_UNVERIFIED")
        sch.record_slot(s)
        ack=capture_scheduled_paper_scan(sch,src,journal,market,slot=s,snapshot=inp["snapshot"])
        out=journal.export_session(workspace_id=W,run_id=R,session_date=D)
        assert [c["decision"] for c in out["private_cycles"][0]["candidates"]]==["QUALIFIED","REJECTED"]
        assert out["private_cycles"][0]["market_source"]["data_status"]=="PARTIAL"
        audit=verify_paper_session(sch,src,journal,market,workspace_id=W,run_id=R,session_date=D)
        assert ack["broker_calls"]==0
        assert audit["locally_consistent"] is True and audit["evidence_state"]=="AWAITING_EVIDENCE"
        assert audit["provider_market_data_independently_attested"] is False

def test_remote_only_provider_source_pages_reproduce_after_all_local_source_loss(tmp_path):
    inp=capture()
    raw,store=remote()
    receipt=seal_provider_snapshot(store,slot=slot(),captured=inp)
    assert receipt["remote_object_count"]==6
    assert receipt["independent_anchor_pinned_by_this_module"] is False
    with system(tmp_path) as (sch,src,journal,market):
        s=slot(origin="INJECTED_SCHEDULER_UNVERIFIED")
        sch.record_slot(s)
        capture_scheduled_paper_scan(sch,src,journal,market,slot=s,snapshot=inp["snapshot"])
    for name in ("planner.sqlite","scanner.sqlite","journal.sqlite"):
        (tmp_path/name).unlink(missing_ok=True)
    shutil.rmtree(tmp_path/"private-market")
    replay=restore(store,receipt)
    assert replay["snapshot"]==inp["snapshot"]
    assert replay["remote_source_replay_verified"] is True
    assert replay["raw_market_page_count"]==4 and replay["broker_calls"]==0
    assert replay["provider_independently_attested"] is False
    assert replay["full_market_session_proven"] is False
    assert len(raw.objects)==6

@pytest.mark.parametrize("corrupt",["pages/","manifest-","receipt-"])
def test_remote_corruption_never_verifies(corrupt):
    raw,store=remote()
    receipt=seal_provider_snapshot(store,slot=slot(),captured=capture())
    key=next(k for b,k in raw.objects if corrupt in k)
    raw.objects[(BUCKET,key)]=b'{"corrupted":true}'
    with pytest.raises(PaperRemoteIntegrityError,match="absent or altered"):
        restore(store,receipt)

def test_wrong_receipt_hash_and_workspace_are_blocked():
    raw,store=remote()
    receipt=seal_provider_snapshot(store,slot=slot(),captured=capture())
    with pytest.raises(PaperRemoteIntegrityError,match="external anchor"):
        restore(store,receipt,pin="0"*64)
    with pytest.raises(PaperRemoteIntegrityError,match="scope"):
        restore(store,receipt,workspace="wrk_otherperson001")
    another=R2S3ImmutableStore(raw,BUCKET,workspace_id="wrk_otherperson001",run_id=R)
    with pytest.raises(PaperRemoteIntegrityError):
        restore(another,receipt)

def test_missing_quote_is_unmeasurable_not_green(tmp_path):
    d=pages()
    d[QUOTES_PATH,"q2"]["quotes"]={"MSFT":[]}
    inp=capture(FakeReader(d))
    assert inp["provenance"]["missing_quote_symbols"]==["MSFT"]
    with system(tmp_path) as (sch,src,journal,market):
        sch.record_slot(slot())
        capture_scheduled_paper_scan(sch,src,journal,market,slot=slot(),snapshot=inp["snapshot"])
        c=journal.export_session(workspace_id=W,run_id=R,session_date=D)["private_cycles"][0]["candidates"][1]
        assert c["decision"]=="UNMEASURABLE" and c["reason"]=="NO_QUOTE"

@pytest.mark.parametrize("edit,message",[
    (lambda p:p[BARS_PATH,None].__setitem__("next_page_token","unavailable"),"missing fake"),
    (lambda p:p[BARS_PATH,None]["bars"]["AAPL"][0].__setitem__("t",ts(0)),"incomplete"),
    (lambda p:p[QUOTES_PATH,None]["quotes"]["AAPL"][0].__setitem__("t",ts(1)),"future"),
    (lambda p:p[BARS_PATH,"b2"]["bars"].__setitem__("OTHER",[]),"symbol scope"),
    (lambda p:p[QUOTES_PATH,None].__setitem__("quotes",[]),"symbol scope"),
    (lambda p:p[BARS_PATH,"b2"]["bars"].__setitem__("AAPL",deepcopy(p[BARS_PATH,None]["bars"]["AAPL"])),"duplicate bar"),
])
def test_missing_invalid_future_and_duplicate_provider_pages_block(edit,message):
    d=pages();edit(d)
    with pytest.raises(MarketFeedError,match=message):
        capture(FakeReader(d))

def test_provider_cycle_and_bounded_pagination_fail_closed():
    d=pages()
    d[BARS_PATH,"b2"]={"bars":{},"next_page_token":"b2"}
    with pytest.raises(MarketFeedError,match="pagination token cycle"):
        capture(FakeReader(d))
    d=pages()
    d[BARS_PATH,None]={"bars":{},"next_page_token":"1"}
    for i in range(1,5):
        d[BARS_PATH,str(i)]={"bars":{},"next_page_token":str(i+1)}
    with pytest.raises(MarketFeedError,match="pagination truncated"):
        capture(FakeReader(d))

def test_alpaca_reader_requires_distinct_credentials():
    for key,secret in (("","s"),("k",""),("","")):
        with pytest.raises(MarketFeedError,match="credentials required"):
            AlpacaHTTPSReadOnly(key_id=key,secret_key=secret)

class Response:
    def __init__(self,url,body,status=200,redirect=None):
        self.url=redirect or url
        self.body=body
        self.status=status
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def geturl(self):return self.url
    def read(self,n):return self.body[:n]
class Spy:
    def __init__(self,*,redirect=None,status=200,body=b'{"bars":{}}'):
        self.calls=[]
        self.redirect,self.status,self.body=redirect,status,body
    def open(self,req,timeout):
        self.calls.append(req)
        assert timeout<=8
        return Response(req.full_url,self.body,self.status,self.redirect)
def args():
    return {"symbols":"AAPL","timeframe":"1Min","start":ts(-3),
            "end":ts(0),"feed":"iex","limit":"300","sort":"asc"}

def test_read_only_https_fixed_host_no_order_routes_or_secret_logging():
    spy=Spy()
    client=AlpacaHTTPSReadOnly(key_id="test-key",secret_key="test-secret",opener=spy)
    assert client.get(path=BARS_PATH,params=args())["bars"]=={}
    req=spy.calls[0]
    assert req.get_method()=="GET"
    assert req.full_url.startswith("https://data.alpaca.markets/v2/stocks/bars?")
    assert req.get_header("Apca-api-key-id")=="test-key"
    assert req.get_header("Apca-api-secret-key")=="test-secret"
    with pytest.raises(MarketFeedError,match="route"):
        client.get(path="/v2/orders",params=args())
    with pytest.raises(MarketFeedError,match="parameters"):
        client.get(path=BARS_PATH,params={**args(),"order_id":"1"})
    for spy in (Spy(status=403),Spy(redirect="https://evil.example/redirect"),
                Spy(body=b"x"*(MAX_BODY_BYTES+3))):
        cli=AlpacaHTTPSReadOnly(key_id="KEY_NEVER_LOG",secret_key="SECRET_NEVER_LOG",opener=spy)
        with pytest.raises(MarketFeedError) as exc:
            cli.get(path=BARS_PATH,params=args())
        assert "SECRET_NEVER_LOG" not in str(exc.value)
        assert "KEY_NEVER_LOG" not in str(exc.value)
