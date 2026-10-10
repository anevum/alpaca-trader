"""F3d mock-only witness protocol: distinct pre-scan plan and raw-source receipts.

All stores are in-memory S3 simulations, NOT real R2 or authenticated Alpaca.
An accepted source is AWAITING_EVIDENCE, not genuine provider attestation.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from next_rhen.paper_schedule import PaperScheduleLedger
from next_rhen.paper_source_archive import seal_provider_snapshot
from next_rhen.paper_witness import (
    PaperWitnessError, audit_witnessed_session, publish_schedule_witness,
    publish_source_witness, restore_witnessed_session,
)
from next_rhen.remote_vault import R2S3ImmutableStore
from scripts.v5_f2c_r2_stage_probe import BUCKET, StrictSyntheticS3
from test_v5_paper_capture import W, R, D, slot
from test_v5_paper_market_feed import capture as source_capture


def separate_stores():
    primary = StrictSyntheticS3()
    secondary = StrictSyntheticS3()
    # These are separate mock backends; never claim actual provider
    # independence even though these two Python objects are distinct.
    source = R2S3ImmutableStore(primary, BUCKET, workspace_id=W, run_id=R)
    witness = R2S3ImmutableStore(secondary, BUCKET, workspace_id=W, run_id=R)
    return primary, source, secondary, witness


def scope():
    return {"workspace_id": W, "run_id": R, "session_date": D}


@contextmanager
def one_cycle(tmp_path):
    with PaperScheduleLedger(tmp_path / "schedule.sqlite") as planner:
        planner.record_slot(slot())
        primary, source, secondary, witness = separate_stores()
        plan = publish_schedule_witness(witness, planner, **scope())
        original = source_capture()
        provider = seal_provider_snapshot(source, slot=slot(), captured=original)
        ref = {"receipt_key": provider["receipt_key"],
               "receipt_sha256": provider["receipt_sha256"]}
        source_anchor = publish_source_witness(
            witness, source, plan_anchor=plan, source_receipt=ref,
            **scope(), cycle_id=slot()["cycle_id"])
        yield planner, primary, source, secondary, witness, plan, source_anchor, ref


def proof(witness, source, plan, anchors):
    return restore_witnessed_session(
        witness, source, plan_anchor=plan, source_anchors=anchors, **scope())


def test_predeclared_plan_and_separately_witnessed_source_survive_all_local_loss(tmp_path):
    with one_cycle(tmp_path) as (planner, primary, source, secondary, witness,
                                 plan, anchor, receipt):
        assert len(primary.objects) == 6
        assert len(secondary.objects) == 2
        assert plan["sha256"] != anchor["sha256"]
        assert planner.read_session_verified(**scope())["slots"][0] == slot()
    for local in ("schedule.sqlite", "scanner.sqlite", "journal.sqlite"):
        (tmp_path / local).unlink(missing_ok=True)
    result = proof(witness, source, plan, [anchor])
    assert result["scheduled_cycles"] == 1
    assert result["remote_source_cycles_restored"] == 1
    assert result["external_plan_witness_hash_checked"] is True
    assert result["external_source_witness_hashes_checked"] is True
    assert result["remote_provider_pages_replayed"] is True
    assert result["local_sqlite_wal_required"] is False
    assert result["market_feed_origin_independently_attested"] is False
    assert result["witness_provider_independently_controlled"] is False
    assert result["external_sha256_pins_independently_custodied"] is False
    assert result["f1_journal_remote_archive_proven"] is False
    assert result["evidence_state"] == "AWAITING_EVIDENCE"
    assert result["broker_calls"] == 0
    assert result["research_ready"] is False
    assert result["broker_write_authority"] is False


def test_missing_scheduled_source_witness_is_blocked(tmp_path):
    with PaperScheduleLedger(tmp_path / "scheduler.sqlite") as planner:
        planner.record_slot(slot(1))
        planner.record_slot(slot(2))
        _, provider, _, witness = separate_stores()
        plan = publish_schedule_witness(witness, planner, **scope())
        captured = source_capture()
        receipt = seal_provider_snapshot(provider, slot=slot(), captured=captured)
        anchor = publish_source_witness(
            witness, provider, plan_anchor=plan,
            source_receipt={k: receipt[k] for k in ("receipt_key", "receipt_sha256")},
            **scope(), cycle_id=slot()["cycle_id"])
    with pytest.raises(PaperWitnessError, match="omit"):
        proof(witness, provider, plan, [anchor])
    blocked = audit_witnessed_session(
        witness, provider, plan_anchor=plan, source_anchors=[anchor], **scope())
    assert blocked["evidence_state"] == "BLOCKED"
    assert not blocked["research_ready"]


def test_missing_predeclared_plan_cannot_publish_source(tmp_path):
    _, source, _, witness = separate_stores()
    with pytest.raises(PaperWitnessError, match="absent or changed"):
        publish_source_witness(
            witness, source,
            plan_anchor={"key": f"private/anevum-v5/{W}/{R}/paper-witness/{D}/plan-" + "1"*64 + ".json",
                         "sha256": "1"*64},
            source_receipt={"receipt_key": "unknown", "receipt_sha256": "2"*64},
            cycle_id="paper-0001", **scope())


def test_repeated_plan_and_source_receipts_idempotent(tmp_path):
    with one_cycle(tmp_path) as (planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        same_plan = publish_schedule_witness(witness, planner, **scope())
        same_source = publish_source_witness(
            witness, source, plan_anchor=plan, source_receipt=receipt,
            cycle_id=slot()["cycle_id"], **scope())
        assert same_plan == plan
        assert same_source == anchor
        assert len(wr.objects) == 2


def test_source_and_witness_object_cannot_be_same_adapter(tmp_path):
    with one_cycle(tmp_path) as (_planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        with pytest.raises(PaperWitnessError, match="separate"):
            publish_source_witness(
                source, source, plan_anchor=plan, source_receipt=receipt,
                cycle_id=slot()["cycle_id"], **scope())
        assert audit_witnessed_session(
            source, source, plan_anchor=plan,
            source_anchors=[anchor], **scope())["evidence_state"] == "BLOCKED"


def test_wrong_workspace_and_wrong_receipt_pin_block(tmp_path):
    with one_cycle(tmp_path) as (_planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        with pytest.raises(PaperWitnessError):
            restore_witnessed_session(
                witness, source, plan_anchor=plan, source_anchors=[anchor],
                workspace_id="wrk_otherperson001", run_id=R, session_date=D)
        bad = dict(plan, sha256="0"*64)
        with pytest.raises(PaperWitnessError):
            proof(witness, source, bad, [anchor])
        invalid = dict(anchor, sha256="f"*64)
        with pytest.raises(PaperWitnessError):
            proof(witness, source, plan, [invalid])


@pytest.mark.parametrize("target", ["plan", "source", "provider"])
def test_tampered_independent_witness_or_original_source_refuses_restore(tmp_path, target):
    with one_cycle(tmp_path) as (_planner, primary, source, secondary, witness,
                                 plan, anchor, receipt):
        if target == "plan":
            key = plan["key"]
            secondary.objects[(BUCKET, key)] = b'{"tampered":true}'
        elif target == "source":
            key = anchor["key"]
            secondary.objects[(BUCKET, key)] = b'{"tampered":true}'
        else:
            key = next(key for bucket, key in primary.objects if "/pages/" in key)
            primary.objects[(BUCKET, key)] = b'{"tampered":true}'
        assert audit_witnessed_session(
            witness, source, plan_anchor=plan,
            source_anchors=[anchor], **scope())["evidence_state"] == "BLOCKED"


def test_duplicate_or_wrong_cycle_witness_is_rejected(tmp_path):
    with one_cycle(tmp_path) as (_planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        with pytest.raises(PaperWitnessError):
            proof(witness, source, plan, [anchor, anchor])
        with pytest.raises(PaperWitnessError, match="not in witnessed"):
            publish_source_witness(
                witness, source, plan_anchor=plan, source_receipt=receipt,
                cycle_id="not-scheduled", **scope())


def test_bad_unsafely_scoped_receipt_ref_fails_closed(tmp_path):
    with one_cycle(tmp_path) as (_planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        fake = deepcopy(receipt)
        fake["receipt_key"] = fake["receipt_key"].replace("/" + W + "/", "/wrk_otherperson001/")
        with pytest.raises(PaperWitnessError, match="provider source"):
            publish_source_witness(
                witness, source, plan_anchor=plan, source_receipt=fake,
                cycle_id=slot()["cycle_id"], **scope())


def test_noncanonical_and_unbounded_source_anchors_fail_closed(tmp_path):
    with one_cycle(tmp_path) as (_planner, raw, source, wr, witness,
                                 plan, anchor, receipt):
        with pytest.raises(PaperWitnessError):
            proof(witness, source, plan, [{"key":anchor["key"], "sha256":[] }])
        with pytest.raises(PaperWitnessError):
            proof(witness, source, plan, "wrong-object")


def test_witness_module_has_no_api_network_or_broker_imports():
    code = (Path(__file__).resolve().parents[1] /
            "next_rhen" / "paper_witness.py").read_text()
    for banned in ("import requests", "import httpx", "import boto3",
                   "alpaca.trade", "submit_order", "ALPACA_CONNECT_",
                   "from app.", "api.alpaca.markets/v2/orders"):
        assert banned not in code
