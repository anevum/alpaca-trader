"""Offline-only ANEVUM F3b paper scanner rehearsal; no network or broker.

Usage: python -m scripts.v5_f3b_paper_capture_probe
Does not use the F2c R2 secrets, production settings or Alpaca Connect.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from next_rhen.evidence_journal import DecisionJournal
from next_rhen.paper_capture import (
    MarketEvidenceStore, SNAPSHOT_SCHEMA, capture_scheduled_paper_scan,
    verify_paper_session,
)
from next_rhen.paper_schedule import PaperScheduleLedger
from next_rhen.source_attestation import SourceScanLedger


def _iso(offset_s: int) -> str:
    start = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
    return (start + timedelta(seconds=offset_s)).isoformat()


def main() -> int:
    workspace, run, session = "wrk_f3bstage000001", "paper-f3b-rehearsal", "2026-10-09"
    slot = {
        "schema_version": "anevum.paper-schedule.v1",
        "workspace_id": workspace, "run_id": run, "session_date": session,
        "cycle_id": "cycle-0001", "sequence_no": 1,
        "expected_at": _iso(0), "execution_mode": "PAPER_RESEARCH_ONLY",
        "planner_origin": "SYNTHETIC_PLAN",
    }
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "workspace_id": workspace, "run_id": run, "session_date": session,
        "cycle_id": "cycle-0001", "sequence_no": 1, "occurred_at": _iso(0),
        "provider": "OFFLINE_SYNTHETIC", "source_origin": "SYNTHETIC_OFFLINE",
        "asof_timestamp": _iso(0),
        "universe_symbols": ["AAPL", "MSFT"],
        "bars": [
            {"symbol":"AAPL", "start":_iso(-60), "end":_iso(0),
             "open":100.0,"high":100.8,"low":99.9,"close":100.5,"volume":1000},
            {"symbol":"MSFT", "start":_iso(-60), "end":_iso(0),
             "open":300.0,"high":300.1,"low":299.1,"close":299.4,"volume":1000},
        ],
        "quotes": [
            {"symbol":"AAPL", "observed_at":_iso(-2),
             "bid":100.0,"ask":100.1,"bid_size":10,"ask_size":10},
            {"symbol":"MSFT", "observed_at":_iso(-2),
             "bid":299.9,"ask":300.0,"bid_size":10,"ask_size":10},
        ],
    }
    with tempfile.TemporaryDirectory(prefix="anevum-f3b-offline-") as temp:
        root = Path(temp)
        with (PaperScheduleLedger(root/"planner.sqlite") as planner,
              SourceScanLedger(root/"source.sqlite") as source,
              DecisionJournal(root/"journal.sqlite") as journal):
            store = MarketEvidenceStore(root/"market")
            planner.record_slot(slot)
            first = capture_scheduled_paper_scan(
                planner,source,journal,store,slot=slot,snapshot=snapshot)
            again = capture_scheduled_paper_scan(
                planner,source,journal,store,slot=slot,snapshot=snapshot)
            audit = verify_paper_session(
                planner,source,journal,store,workspace_id=workspace,
                run_id=run,session_date=session)
            if not (audit["locally_consistent"] and
                    audit["evidence_state"] == "AWAITING_EVIDENCE" and
                    audit["market_cycles_locally_restored"] == 1 and
                    first["candidate_count"] == 2 and
                    again["journal_status"] == "ALREADY_RECORDED"):
                print(json.dumps({"status":"F3B_OFFLINE_BLOCKED","broker_calls":0}))
                return 2
            print(json.dumps({
                "status": "F3B_OFFLINE_SCANNER_SOURCE_REHEARSAL_VERIFIED",
                "planned_slots": audit["planned_slots"],
                "scanner_cycles": 1, "journal_cycles": audit["journal_cycles"],
                "candidates": first["candidate_count"],
                "market_objects_locally_restored": audit["market_cycles_locally_restored"],
                "local_recovery_state": audit["evidence_state"],
                "real_market_source_attested": False,
                "upstream_scheduler_independently_attested": False,
                "production_wal_offhost_verified": False,
                "broker_calls": 0, "live_trading_authorized": False,
            },sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
