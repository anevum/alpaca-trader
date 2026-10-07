
from datetime import datetime, timedelta, timezone

import asyncio
import httpx

import app.main as main
from foundation.nostra_gateway import read_nostra_forecasts


UTC=timezone.utc
NOW=datetime(2026,10,7,20,45,tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows):
        self.rows=rows
        self.executed=[]
    def execute(self, sql, params=None):
        self.executed.append((sql,params))
    def fetchall(self):
        return self.rows
    def __enter__(self): return self
    def __exit__(self,*args): return False


class FakeConn:
    def __init__(self, rows): self.cur=FakeCursor(rows)
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self,*args): return False


def forecast(**overrides):
    body={
        "forecast_id":"nsf_fixture","snapshot_id":"nss_fixture","symbol":"SPY",
        "market_lane":"us_equity","as_of_timestamp":(NOW-timedelta(minutes=1)).isoformat(),
        "generated_at":NOW.isoformat(),"horizon_minutes":10,"target_kind":"return",
        "model_id":"zero_return","model_version":"nostra-live-zero-return-v1",
        "feature_set_version":"equity-features-v1","forecast_payload":{"expected_return":0.0},
        "uncertainty":{},"authority_state":"LOW_SUPPORT","methodology_version":"FORECAST-001",
        "research_only":True,"execution_authority":False,
    }
    body.update(overrides)
    return body


def test_canonical_forecast_read_is_bounded_and_authority_safe(monkeypatch):
    rows=[(forecast(),),(forecast(forecast_id="bad",execution_authority=True),)]
    monkeypatch.setattr("foundation.nostra_gateway.psycopg.connect",lambda *a,**k:FakeConn(rows))
    result=read_nostra_forecasts("postgres://fixture",now=NOW,limit=200)
    assert result["schema_version"]=="nostra-canonical-forecast-read-v1"
    assert result["returned_count"]==1 and result["rejected_count"]==1
    assert result["forecasts"][0]["forecast_id"]=="nsf_fixture"
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_write_authority"] is False


def test_scheduler_nostra_forecast_read_uses_internal_foundation_get(monkeypatch):
    body={"ok":True,"schema_version":"nostra-canonical-forecast-read-v1",
        "observed_at":NOW.isoformat(),"forecasts":[forecast()],"returned_count":1,
        "rejected_count":0,"truncated":False,"research_only":True,
        "execution_authority":False,"broker_write_authority":False}
    requests=[]
    async def fake_get(self,url,**kwargs):
        requests.append((url,kwargs))
        return httpx.Response(200,json=body,request=httpx.Request("GET",url))
    monkeypatch.setattr(main,"require_scheduler_token",lambda token:None)
    monkeypatch.setattr(main.settings,"foundation_ingest_token","fixture-token")
    monkeypatch.setattr(httpx.AsyncClient,"get",fake_get)
    result=asyncio.run(main.scheduler_nostra_forecasts("scheduler-token"))
    assert result["forecasts"][0]["forecast_id"]=="nsf_fixture"
    assert requests[0][0]=="http://127.0.0.1:8102/v1/nostra-forecasts"
    assert requests[0][1]["headers"]["x-anevum-ingest-token"]=="fixture-token"
