"""F2c staged provider harness tests; no cloud secrets, billing, or broker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from next_rhen.evidence_vault import ArchiveIntegrityError
from scripts.v5_f2c_r2_stage_probe import (
    BUCKET, MAX_DATA_WRITTEN, WORKSPACE, EVENTS, CANDIDATES,
    StrictSyntheticS3, MeteredStore, _real_s3_client, main,
    run_staging_probe, synthetic_cycle,
)
from next_rhen.remote_vault import R2S3ImmutableStore


def test_full_synthetic_mock_proves_conditional_transport_and_restore():
    client = StrictSyntheticS3()
    report = run_staging_probe(client, "f2c-offline-001", provider="MOCK_S3")
    assert report["restored"] is True
    assert report["provider_tested"] == "MOCK_S3"
    assert report["cycles"] == EVENTS
    assert report["candidates"] == CANDIDATES
    assert report["rejected"] == EVENTS
    assert report["conflicting_conditional_put_rejected"] is True
    assert report["remote_receipts"] == 2
    assert report["storage_operation_budget"]["uploaded_bytes"] < MAX_DATA_WRITTEN
    assert report["sha256_receipts_pinned_outside_remote"] is True
    assert report["evidence_state"] == "AWAITING_EVIDENCE"
    assert report["upstream_market_attested"] is False
    assert report["broker_write_authorized"] is False
    assert client.conditional_writes >= 7  # six objects + rejected overwrite
    assert all(key[0] == BUCKET for key in client.objects)
    assert all(key[1].startswith("private/anevum-v5/"+WORKSPACE+"/f2c-offline-001/")
               for key in client.objects)


def test_default_cli_produces_safe_mock_report(capsys, monkeypatch):
    monkeypatch.delenv("ANEVUM_F2C_R2_SECRET_ACCESS_KEY",raising=False)
    assert main([]) == 0
    printed=json.loads(capsys.readouterr().out)
    assert printed["provider_tested"] == "MOCK_S3"
    assert printed["claim"] == "MOCK_PROTOCOL_ONLY"
    assert printed["broker_calls"] == 0
    assert "secret" not in json.dumps(printed).lower()
    assert printed["production_wal_offhost_state_promoted"] is False


def test_live_without_explicit_env_auth_is_blocked(capsys, monkeypatch):
    monkeypatch.delenv("ANEVUM_F2C_STAGING_APPROVED",raising=False)
    assert main(["--execute"]) == 2
    body=json.loads(capsys.readouterr().out)
    assert body["status"] == "F2C_STAGING_PROBE_BLOCKED"
    assert body["production_unchanged"] is True


def test_execute_requires_stage_specific_credentials_even_when_approved(monkeypatch):
    monkeypatch.setenv("ANEVUM_F2C_STAGING_APPROVED", "YES_SYNTHETIC_R2_ONLY")
    monkeypatch.delenv("ANEVUM_F2C_R2_ACCESS_KEY_ID",raising=False)
    monkeypatch.delenv("ANEVUM_F2C_R2_SECRET_ACCESS_KEY",raising=False)
    with pytest.raises(RuntimeError,match="staging account"):
        _real_s3_client()


def test_budget_checks_before_object_creation():
    store=MeteredStore(R2S3ImmutableStore(StrictSyntheticS3(),BUCKET,
             workspace_id=WORKSPACE,run_id="f2c-offline-001"))
    key="private/anevum-v5/"+WORKSPACE+"/f2c-offline-001/file.json"
    with pytest.raises(ArchiveIntegrityError,match="budget exceeded"):
        store.put_once(key,b"A"*(MAX_DATA_WRITTEN+1))
    assert store.bytes==0
    assert store.writes==0


def test_synthetic_market_evidence_is_explicitly_partial():
    c=synthetic_cycle(sequence=1,run_id="f2c-offline-001")
    assert c["execution_mode"]=="PAPER_RESEARCH_ONLY"
    assert c["market_source"]["data_status"]=="PARTIAL"
    assert c["market_source"]["feed"]=="SYNTHETIC_NO_MARKET"
    assert {x["decision"] for x in c["candidates"]}=={"QUALIFIED","REJECTED"}


def test_runner_has_no_production_deployment_or_broker_dependency():
    src=(Path(__file__).resolve().parents[1] / "scripts" / "v5_f2c_r2_stage_probe.py").read_text()
    for forbidden in ("import alpaca", "from app.", "import railway",
                      "create_bucket(", "delete_bucket(", "ANEVUM_F2C_R2_SECRET_ACCESS_KEY\","):
        assert forbidden not in src
