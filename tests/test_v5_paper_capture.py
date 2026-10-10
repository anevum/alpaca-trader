"""ANEVUM V5 F3b: separate scheduler, market, scanner and F1 capture tests."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from next_rhen.evidence_journal import DecisionJournal
from next_rhen.paper_schedule import (
    PaperScheduleLedger, PaperScheduleError, PaperScheduleIntegrityError,
)
from next_rhen.paper_capture import (
    SNAPSHOT_SCHEMA, MarketEvidenceStore, PaperCaptureError,
    PaperCaptureIntegrityError, capture_scheduled_paper_scan,
    verify_paper_session, validate_snapshot,
)
from next_rhen.source_attestation import SourceScanLedger

W = "wrk_papercapture001"
R = "f3b-paper-offline"
D = "2026-10-09"


def ts(minutes=0, seconds=0):
    return (datetime(2026,10,9,14,30,tzinfo=timezone.utc)
            + timedelta(minutes=minutes,seconds=seconds)).isoformat()


def slot(sequence=1, *, workspace=W, origin="SYNTHETIC_PLAN"):
    return {
        "schema_version": "anevum.paper-schedule.v1",
        "workspace_id": workspace, "run_id": R,
        "cycle_id": f"paper-{sequence:04d}",
        "sequence_no": sequence, "session_date": D,
        "expected_at": ts(sequence-1), "execution_mode": "PAPER_RESEARCH_ONLY",
        "planner_origin": origin,
    }


def bar(symbol, minute, *, open_=100.0, close=100.4):
    t = datetime.fromisoformat(ts(minute))
    return {"symbol":symbol,
            "start":(t-timedelta(minutes=1)).isoformat(), "end":t.isoformat(),
            "open":open_, "high":max(open_,close)+0.2,
            "low":min(open_,close)-0.2, "close":close, "volume":1234}


def quote(symbol, minute, *, bid=100.0, ask=100.1):
    return {"symbol":symbol, "observed_at":ts(minute,-2),
            "bid":bid, "ask":ask, "bid_size":20, "ask_size":25}


def snapshot(sequence=1, *, workspace=W, origin="SYNTHETIC_OFFLINE"):
    minute = sequence - 1
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "workspace_id": workspace, "run_id": R,
        "cycle_id": f"paper-{sequence:04d}",
        "sequence_no":sequence, "session_date": D,
        "occurred_at":ts(minute),
        "source_origin":origin, "provider":"OFFLINE_SYNTHETIC",
        "asof_timestamp":ts(minute),
        "universe_symbols":["AAPL", "MSFT"],
        "bars":[bar("AAPL", minute),bar("MSFT",minute,open_=300.0,close=299.8)],
        "quotes":[quote("AAPL",minute),
                  quote("MSFT",minute,bid=299.9,ask=300.05)],
    }


@contextmanager
def system(tmp_path, *, suffix=""):
    # The independent planner, upstream scanner and journal may never share a
    # physical DB, and the market records are separate content-addressed files.
    with (PaperScheduleLedger(tmp_path/f"planner{suffix}.sqlite") as schedule,
          SourceScanLedger(tmp_path/f"scanner{suffix}.sqlite") as source,
          DecisionJournal(tmp_path/f"journal{suffix}.sqlite") as journal):
        market = MarketEvidenceStore(tmp_path/f"private-market{suffix}")
        yield schedule,source,journal,market


def capture(schedule,source,journal,market,sequence=1, *, snap=None, scheduled=None):
    return capture_scheduled_paper_scan(schedule,source,journal,market,
          slot=scheduled or slot(sequence),snapshot=snap or snapshot(sequence))


def audit(schedule,source,journal,market, *, workspace=W):
    return verify_paper_session(schedule,source,journal,market,
                               workspace_id=workspace,run_id=R,session_date=D)


def test_predeclared_two_cycles_full_candidate_population_and_local_restoration(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot(1))
        sch.record_slot(slot(2))
        for n in (1,2):
            ack=capture(sch,src,jour,store,n)
            assert ack["source_status"]=="SCANNER_RECORDED"
            assert ack["journal_status"]=="RECORDED"
            assert ack["candidate_count"]==2
            assert ack["unmeasurable_count"]==0
            assert ack["evidence_state"]=="AWAITING_EVIDENCE"
        report=audit(sch,src,jour,store)
        assert report["planned_slots"]==2
        assert report["journal_cycles"]==2
        assert report["market_cycles_locally_restored"]==2
        assert report["locally_consistent"] is True
        assert report["evidence_state"]=="AWAITING_EVIDENCE"
        assert report["f3a_evidence_state"]=="AWAITING_EVIDENCE"
        assert report["upstream_schedule_independently_attested"] is False
        assert report["provider_market_data_independently_attested"] is False
        assert report["offhost_wal_ack_verified"] is False
        assert report["research_ready"] is False
        assert report["broker_write_authority"] is False
        assert report["broker_calls"]==0
        exported=jour.export_session(workspace_id=W,run_id=R,session_date=D)
        assert [c["symbol"] for c in exported["private_cycles"][0]["candidates"]]==["AAPL","MSFT"]
        assert [c["decision"] for c in exported["private_cycles"][0]["candidates"]]==["QUALIFIED","REJECTED"]
        assert all(c["market_source"]["data_status"]=="PARTIAL" for c in exported["private_cycles"])
        assert "cycle:1:MARKET_SOURCE_NOT_COMPLETE" in exported["manifest"]["quality_issues"]


def test_scanner_is_always_durable_before_f1_journal_with_retry(tmp_path,monkeypatch):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        original=jour.append_cycle
        def broken(_cycle):
            raise RuntimeError("injected journal power failure")
        monkeypatch.setattr(jour,"append_cycle",broken)
        with pytest.raises(RuntimeError,match="power failure"):
            capture(sch,src,jour,store)
        assert len(src.read_session_verified(workspace_id=W,run_id=R,session_date=D)["scans"])==1
        incomplete=audit(sch,src,jour,store)
        assert incomplete["evidence_state"]=="BLOCKED"
        assert "SCHEDULED_SCAN_NOT_CAPTURED:1" in incomplete["issue_codes"]
        monkeypatch.setattr(jour,"append_cycle",original)
        retry=capture(sch,src,jour,store)
        assert retry["source_status"]=="ALREADY_RECORDED"
        assert retry["journal_status"]=="RECORDED"
        second=capture(sch,src,jour,store)
        assert second["source_status"]=="ALREADY_RECORDED"
        assert second["journal_status"]=="ALREADY_RECORDED"
        assert audit(sch,src,jour,store)["evidence_state"]=="AWAITING_EVIDENCE"


def test_unplanned_scan_cannot_be_backfilled_from_journal(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot(1))
        with pytest.raises(PaperCaptureError,match="not independently predeclared"):
            capture(sch,src,jour,store,sequence=2)
        assert src.read_session_verified(workspace_id=W,run_id=R,session_date=D)["scans"]==[]
        with pytest.raises(RuntimeError,match="no events"):
            jour.export_session(workspace_id=W,run_id=R,session_date=D)


def test_second_missing_planned_scan_is_blocked_not_filled_with_estimate(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot(1))
        sch.record_slot(slot(2))
        capture(sch,src,jour,store)
        result=audit(sch,src,jour,store)
        assert result["evidence_state"]=="BLOCKED"
        assert "SCHEDULED_SCAN_NOT_CAPTURED:2" in result["issue_codes"]
        assert "SCANNER_JOURNAL_RECONCILIATION_BLOCKED" in result["issue_codes"]


@pytest.mark.parametrize("edit,message",[
    (lambda s:s.__setitem__("execution_mode","LIVE"),"cannot authorize trading"),
    (lambda s:s.__setitem__("planner_origin","PRODUCTION_BROKER"),"unknown planner"),
    (lambda s:s.__setitem__("expected_at","2026-10-10T14:30:00Z"),"another session"),
    (lambda s:s.__setitem__("sequence_no",True),"sequence"),
    (lambda s:s.__setitem__("cycle_id","../escape"),"cycle ID"),
])
def test_scheduler_refuses_unsafe_slots(tmp_path,edit,message):
    with PaperScheduleLedger(tmp_path/"plans.sqlite") as sch:
        bad=slot()
        edit(bad)
        with pytest.raises((PaperScheduleError,ValueError),match=message):
            sch.record_slot(bad)


def test_scheduler_idempotence_conflicting_replay_and_gap(tmp_path):
    with PaperScheduleLedger(tmp_path/"plans.sqlite") as sch:
        assert sch.record_slot(slot(1))["status"]=="PLANNED"
        assert sch.record_slot(slot(1))["status"]=="ALREADY_PLANNED"
        changed=slot(1)
        changed["expected_at"]=ts(0,1)
        with pytest.raises(PaperScheduleIntegrityError,match="conflicting"):
            sch.record_slot(changed)
        with pytest.raises(PaperScheduleIntegrityError,match="sequence gap"):
            sch.record_slot(slot(3))
        assert sch.record_slot(slot(2))["status"]=="PLANNED"


def test_schedule_hash_corruption_detected_and_never_reported_complete(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        capture(sch,src,jour,store)
        sch.conn.execute("UPDATE paper_schedule SET payload_json='{}' WHERE sequence_no=1")
        with pytest.raises(PaperScheduleIntegrityError,match="hash chain"):
            sch.read_session_verified(workspace_id=W,run_id=R,session_date=D)
        result=audit(sch,src,jour,store)
        assert result["evidence_state"]=="BLOCKED"
        assert "SCHEDULER_LEDGER_INTEGRITY_BLOCKED" in result["issue_codes"]


@pytest.mark.parametrize("mutate,message",[
    (lambda s:s["bars"][0].__setitem__("end",ts(1)),"completed 1-minute"),
    (lambda s:s["bars"][0].__setitem__("start",ts(-3)),"completed 1-minute"),
    (lambda s:s["bars"][0].__setitem__("low",101.0),"OHLC bounds"),
    (lambda s:s["bars"][0].__setitem__("volume",-1),"nonnegative integer"),
    (lambda s:s["quotes"][0].__setitem__("observed_at",ts(0,2)),"future quote"),
    (lambda s:s["quotes"][0].__setitem__("bid",105.0),"crossed"),
    (lambda s:s["quotes"][0].__setitem__("bid_size",True),"nonnegative integer"),
    (lambda s:s["universe_symbols"].append("AAPL"),"duplicate symbol"),
    (lambda s:s["bars"].append(deepcopy(s["bars"][0])),"duplicate completed bar"),
    (lambda s:s.__setitem__("asof_timestamp",ts(0,4)),"future"),
    (lambda s:s.__setitem__("source_origin","PROVEN_MARKET"),"synthetic or unverified"),
    (lambda s:s.__setitem__("provider","key=secret"),"provider"),
    (lambda s:s["quotes"].append({"symbol":"TSLA","observed_at":ts(0),
                                  "bid":100,"ask":100,"bid_size":1,"ask_size":1}),"out-of-universe"),
])
def test_offline_market_contract_rejects_future_invalid_and_fabricated_claims(tmp_path,mutate,message):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        s=snapshot()
        mutate(s)
        with pytest.raises(PaperCaptureError,match=message):
            capture(sch,src,jour,store,snap=s)
        assert src.read_session_verified(workspace_id=W,run_id=R,session_date=D)["scans"]==[]


def test_partial_missing_quote_is_reason_coded_not_defaulted_to_zero(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        s=snapshot()
        s["quotes"].pop()
        ack=capture(sch,src,jour,store,snap=s)
        assert ack["unmeasurable_count"]==1
        result=jour.export_session(workspace_id=W,run_id=R,session_date=D)
        candidate=result["private_cycles"][0]["candidates"][1]
        assert candidate["decision"]=="UNMEASURABLE"
        assert candidate["reason"]=="NO_QUOTE"
        assert "spread_bps" not in candidate["features"]
        assert audit(sch,src,jour,store)["evidence_state"]=="AWAITING_EVIDENCE"


def test_bar_stale_and_missing_are_reason_coded(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        s=snapshot()
        s["bars"]=s["bars"][:1]
        ack=capture(sch,src,jour,store,snap=s)
        assert ack["unmeasurable_count"]==1
        rows=jour.export_session(workspace_id=W,run_id=R,session_date=D)["private_cycles"][0]["candidates"]
        assert rows[1]["decision"]=="UNMEASURABLE"
        assert rows[1]["reason"]=="NO_COMPLETED_BAR"


def test_repeated_market_record_is_deduplicated_in_same_workspace_and_run(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot(1))
        sch.record_slot(slot(2))
        capture(sch,src,jour,store)
        s=snapshot(2)
        first=snapshot(1)
        s["bars"]=first["bars"]
        s["quotes"]=first["quotes"]
        capture(sch,src,jour,store,2,snap=s)
        bar_objects=list(store.root.rglob("paper-market/bar/*.json"))
        quote_objects=list(store.root.rglob("paper-market/quote/*.json"))
        assert len(bar_objects)==2
        assert len(quote_objects)==2
        report=audit(sch,src,jour,store)
        assert report["evidence_state"]=="AWAITING_EVIDENCE"
        assert report["market_cycles_locally_restored"]==2


def test_local_raw_market_object_corruption_detected_even_if_journal_and_scanner_match(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        ack=capture(sch,src,jour,store)
        ref=ack["market_source"]["bars_ref"]
        path=next(store.root.rglob(f"{ref}.json"))
        path.write_text('{"tampered":true}',encoding="utf-8")
        result=audit(sch,src,jour,store)
        assert result["evidence_state"]=="BLOCKED"
        assert "MARKET_SOURCE_LOCAL_RESTORE_BLOCKED:1" in result["issue_codes"]
        assert result["market_cycles_locally_restored"]==0


def test_corrupt_raw_price_record_rejects_restore(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        capture(sch,src,jour,store)
        record=next(store.root.rglob("paper-market/bar/*.json"))
        obj=json.loads(record.read_text())
        obj["record"]["close"]=1_000_000
        record.write_text(json.dumps(obj),encoding="utf-8")
        assert audit(sch,src,jour,store)["evidence_state"]=="BLOCKED"


def test_source_journal_mismatch_remains_blocked_despite_valid_market_objects(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        capture(sch,src,jour,store)
        jour.conn.execute("UPDATE decision_journal SET payload_json='{}' WHERE sequence_no=1")
        report=audit(sch,src,jour,store)
        assert report["evidence_state"]=="BLOCKED"
        assert "SCANNER_JOURNAL_RECONCILIATION_BLOCKED" in report["issue_codes"]


def test_workspace_scope_blocks_market_document_cross_read(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        ack=capture(sch,src,jour,store)
        with pytest.raises(PaperCaptureIntegrityError,match="missing"):
            store.load(workspace_id="wrk_otherperson001",run_id=R,kind="bars_index",
                       digest_sha256=ack["market_source"]["bars_ref"])
        assert audit(sch,src,jour,store,workspace="wrk_otherperson001")["evidence_state"]=="BLOCKED"


def test_invalid_snapshot_wrong_scope_and_time_cannot_publish_any_source(tmp_path):
    with system(tmp_path) as (sch,src,jour,store):
        sch.record_slot(slot())
        s=snapshot()
        s["workspace_id"]="wrk_another000001"
        with pytest.raises(PaperCaptureError,match="mismatches"):
            capture(sch,src,jour,store,snap=s)
        s=snapshot()
        s["occurred_at"]=ts(-1)
        with pytest.raises(PaperCaptureError,match="outside planned"):
            capture(sch,src,jour,store,snap=s)
        assert src.read_session_verified(workspace_id=W,run_id=R,session_date=D)["scans"]==[]


def test_f3b_code_cannot_import_broker_production_or_network():
    root=Path(__file__).resolve().parents[1]/"next_rhen"
    for filename in ("paper_schedule.py","paper_capture.py"):
        code=(root/filename).read_text(encoding="utf-8")
        for forbidden in ("import alpaca","from alpaca","from app.","import requests",
                          "import httpx","import boto3","from foundation.","import railway",
                          "from .execution","broker.submit_order"):
            assert forbidden not in code
