"""F3f market preflight: no external Alpaca or R2 requests in CI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from next_rhen.paper_market_feed import BARS_PATH, QUOTES_PATH, MarketFeedError
from scripts import v5_f3f_market_stage_probe as probe


AT = datetime(2026, 10, 9, 14, 33, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 10, 14, 33, tzinfo=timezone.utc)


def parse(capsys):
    return json.loads(capsys.readouterr().out.strip())


def test_offline_default_uses_no_credentials_or_network(monkeypatch, capsys):
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_KEY_ID", raising=False)
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_SECRET_KEY", raising=False)
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_APPROVED", raising=False)
    def refuse_transport(*args, **kwargs):
        raise AssertionError("offline probe must never instantiate authenticated HTTP")
    monkeypatch.setattr(probe, "AlpacaHTTPSReadOnly", refuse_transport)
    assert probe.main([], now=NOW) == 0
    result = parse(capsys)
    assert result["probe_mode"] == "OFFLINE_MOCK"
    assert result["provider_tested"] == "OFFLINE_INJECTED_FAKE"
    assert result["evidence_state"] == "AWAITING_EVIDENCE"
    assert result["source_pages_fetched"] == 3
    assert result["get_requests"] == 3
    assert result["requested_symbols"] == 2
    assert result["symbols_with_sampled_quote"] == 2
    assert result["symbols_with_complete_bar"] == 2
    assert result["scanner_and_journal_locally_replayable"] is True
    assert result["market_provider_independently_attested"] is False
    assert result["offhost_raw_evidence_retained"] is False
    assert result["complete_market_session_proven"] is False
    assert result["remote_R2_source_restore_verified"] is False
    assert result["full_quote_history_retrieved"] is False
    assert result["broker_calls"] == 0
    assert result["live_trading_authorized"] is False


def test_real_execute_without_explicit_approval_does_not_create_client(monkeypatch,capsys):
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_KEY_ID", "NEVER_PRINT_API_ID")
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_SECRET_KEY", "NEVER_PRINT_API_SECRET")
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_APPROVED", raising=False)
    monkeypatch.setattr(probe, "AlpacaHTTPSReadOnly",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("no HTTP allowed")))
    assert probe.main(["--execute"], now=NOW) == 2
    result = parse(capsys)
    assert result["evidence_state"] == "BLOCKED"
    assert "NEVER_PRINT_API" not in json.dumps(result)
    assert result["broker_calls"] == 0


def test_real_execute_missing_dedicated_credentials_blocked(monkeypatch,capsys):
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_APPROVED", probe.APPROVAL)
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_KEY_ID", raising=False)
    monkeypatch.delenv("ANEVUM_F3F_PAPER_DATA_SECRET_KEY", raising=False)
    assert probe.main(["--execute"], now=NOW) == 2
    assert parse(capsys)["error"] == "PAPER_SOURCE_PREFLIGHT_BLOCKED"


def test_real_execute_with_explicit_approval_can_only_inject_mock_in_unit_test(monkeypatch,capsys):
    created = []
    def factory(*, key_id, secret_key):
        created.append((key_id,secret_key))
        return probe.OfflineSyntheticReader(at=AT, symbols=list(probe.DEFAULT_SYMBOLS))
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_APPROVED", probe.APPROVAL)
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_KEY_ID", "FAKE_STAGE_ID")
    monkeypatch.setenv("ANEVUM_F3F_PAPER_DATA_SECRET_KEY", "FAKE_STAGE_SECRET")
    monkeypatch.setattr(probe, "AlpacaHTTPSReadOnly", factory)
    assert probe.main(["--execute"], now=NOW) == 0
    result = parse(capsys)
    assert len(created)==1
    assert result["probe_mode"] == "REAL_READONLY_IEX"
    assert result["evidence_state"] == "AWAITING_EVIDENCE"
    assert result["get_requests"] == 3
    assert "FAKE_STAGE_" not in json.dumps(result)
    assert result["broker_calls"] == 0


@pytest.mark.parametrize("when,reason", [
    ("2026-10-09T14:33:45Z", "aligned"),
    ("2026-10-09T14:33:00", "UTC"),
    ("2026-10-09T10:33:00-04:00", "UTC"),
    ("2026-10-10T14:33:00Z", "weekday"),
    ("2026-10-09T12:33:00Z", "weekday"),
    ("2026-10-09T21:33:00Z", "weekday"),
    ("2026-10-09T14:33:BAD", "invalid"),
])
def test_unsafe_observation_time_refused(when,reason):
    with pytest.raises(probe.PaperProbeError,match=reason):
        probe._asof(when,now=NOW,require_past=False)


@pytest.mark.parametrize("age,reason", [
    (timedelta(minutes=5), "too recent"),
    (timedelta(days=15), "outside"),
])
def test_explicit_live_probe_historical_window_is_bounded(age,reason):
    now = AT + age
    with pytest.raises(probe.PaperProbeError,match=reason):
        probe._asof(AT.isoformat(),now=now,require_past=True)


@pytest.mark.parametrize("symbols",[
    "AAPL,MSFT,TSLA", "AAPL,AAPL", "aapl", "AAPL,,MSFT", "AAPL,../../key", "",
])
def test_probe_refuses_broad_or_unsafe_symbol_scope(symbols):
    with pytest.raises(probe.PaperProbeError,match="symbol"):
        probe._symbols(symbols)


def test_reader_blocks_broker_routes_before_calling_transport():
    transport = probe.OfflineSyntheticReader(at=AT,symbols=["AAPL","MSFT"])
    wrapper = probe.BoundedPaperReader(transport,symbols=["AAPL","MSFT"],at=AT)
    with pytest.raises(probe.PaperProbeError,match="non-market"):
        wrapper.get(path="/v2/orders",params={"symbols":"AAPL","feed":"iex"})
    assert wrapper.calls == 0
    with pytest.raises(probe.PaperProbeError,match="one as-of"):
        wrapper.get(path=QUOTES_PATH,params={"symbols":"AAPL,MSFT","feed":"iex",
                                            "sort":"desc","limit":"1","end":AT.isoformat()})
    assert wrapper.calls == 0


def test_reader_hard_cap_prevents_extra_billable_requests():
    transport = probe.OfflineSyntheticReader(at=AT,symbols=["AAPL","MSFT"])
    wrapper = probe.BoundedPaperReader(transport,symbols=["AAPL","MSFT"],at=AT)
    params={"symbols":"AAPL","start":(AT-timedelta(minutes=2)).isoformat(),
            "end":AT.isoformat(),"feed":"iex","limit":"1","sort":"desc"}
    for n in range(6):
        wrapper.get(path=QUOTES_PATH,params=params)
    assert wrapper.calls == 6
    with pytest.raises(probe.PaperProbeError,match="budget exhausted"):
        wrapper.get(path=QUOTES_PATH,params=params)
    assert wrapper.calls == 6


class MissingQuoteReader(probe.OfflineSyntheticReader):
    def get(self,*,path,params):
        response=super().get(path=path,params=params)
        if path==QUOTES_PATH and params["symbols"]=="MSFT":
            response["quotes"]={"MSFT":[]}
            response["next_page_token"]=None
        return response


def test_missing_real_symbol_quote_blocks_eligibility_without_inventing_data():
    reader=MissingQuoteReader(at=AT,symbols=["AAPL","MSFT"])
    out=probe.run_probe(reader,when=AT,symbols=["AAPL","MSFT"],mode="OFFLINE_MOCK")
    assert out["evidence_state"]=="BLOCKED"
    assert "PROBE_QUOTE_COVERAGE_INCOMPLETE" in out["issues"]
    assert out["symbols_with_sampled_quote"]==1
    assert out["full_quote_history_retrieved"] is False
    assert out["broker_calls"] == 0


def test_no_alpaca_connect_keys_or_trade_calls_in_staging_probe():
    from pathlib import Path
    source=Path(probe.__file__).read_text(encoding="utf-8")
    for forbidden in ("ALPACA_CONNECT_CLIENT_ID","ALPACA_CONNECT_CLIENT_SECRET",
                      "submit_order(", "/v2/orders", "import boto3",
                      "AWS_SECRET_ACCESS_KEY", "import alpaca", "from app."):
        assert forbidden not in source
