"""F2c staged provider harness tests; no cloud secrets, billing, or broker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from next_rhen.evidence_vault import ArchiveIntegrityError
from scripts.v5_f2c_r2_stage_probe import (
    BUCKET, MAX_DATA_WRITTEN, MAX_READ_CALLS, WORKSPACE, EVENTS, CANDIDATES,
    StrictSyntheticS3, MeteredStore, StagingS3RequestBudget,
    _real_s3_client, main,
    run_staging_probe, synthetic_cycle,
)
from next_rhen.remote_vault import R2S3ImmutableStore


def test_full_synthetic_mock_proves_conditional_transport_and_restore():
    client = StrictSyntheticS3()
    report = run_staging_probe(client, "f2c-offline-001", provider="MOCK_S3")
    assert report["restored"] is True
    assert report["temporary_source_deleted_before_restore"] is True
    assert report["provider_tested"] == "MOCK_S3"
    assert report["cycles"] == EVENTS
    assert report["candidates"] == CANDIDATES
    assert report["rejected"] == EVENTS
    assert report["conflicting_conditional_put_rejected"] is True
    assert report["remote_receipts"] == 2
    assert report["storage_operation_budget"]["attempted_upload_bytes"] < MAX_DATA_WRITTEN
    assert report["sha256_receipts_pinned_outside_remote"] is True
    anchors=report["receipt_anchors_for_independent_recovery"]
    assert len(anchors)==2
    assert all(key.endswith(".remote-receipt.json") and len(value)==64
               for key,value in anchors.items())
    assert report["storage_operation_budget"]["put_requests"]>=7
    assert report["evidence_state"] == "AWAITING_EVIDENCE"
    assert report["upstream_market_attested"] is False
    assert report["broker_write_authorized"] is False
    assert client.conditional_writes >= 7  # six objects + rejected overwrite
    budget=report["storage_operation_budget"]
    assert budget["put_requests"]==client.conditional_writes
    assert budget["get_requests"] > budget["put_requests"]
    assert budget["counting_scope"]=="ALL_S3_METHOD_CALLS_INCLUDING_INTERNAL_READBACKS"
    assert budget["attempted_upload_bytes"] < MAX_DATA_WRITTEN
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
                      "create_bucket(", "delete_bucket("):
        assert forbidden not in src



def test_underlying_s3_budget_counts_implicit_get_and_failed_conditional_put():
    client=StrictSyntheticS3()
    budget=StagingS3RequestBudget(client,workspace_id=WORKSPACE,run_id="f2c-offline-001")
    store=R2S3ImmutableStore(budget,BUCKET,workspace_id=WORKSPACE,run_id="f2c-offline-001")
    key="private/anevum-v5/"+WORKSPACE+"/f2c-offline-001/forged-test.json"
    store.put_once(key,b"original")
    assert budget.put_requests==1
    assert budget.get_requests==1  # Implicit exact-byte GET inside put_once.
    with pytest.raises(ArchiveIntegrityError,match="differs"):
        store.put_once(key,b"conflicting")
    assert budget.put_requests==2
    assert budget.get_requests==2
    assert budget.attempted_put_bytes==len(b"original")+len(b"conflicting")
    assert store.get(key)==b"original"
    assert budget.get_requests==3


def test_s3_request_budget_fails_closed_on_wrong_tenant_bucket_or_read_count():
    client=StrictSyntheticS3()
    budget=StagingS3RequestBudget(client,workspace_id=WORKSPACE,run_id="f2c-offline-001")
    key="private/anevum-v5/"+WORKSPACE+"/f2c-offline-001/genuine.json"
    with pytest.raises(ArchiveIntegrityError,match="bucket"):
        budget.get_object(Bucket="production-bucket",Key=key)
    with pytest.raises(ArchiveIntegrityError,match="cross-workspace"):
        budget.put_object(Bucket=BUCKET,Key="private/anevum-v5/wrk_another00000/f2c-offline-001/bad",Body=b"x",IfNoneMatch="*")
    with pytest.raises(ArchiveIntegrityError,match="create-only"):
        budget.put_object(Bucket=BUCKET,Key=key,Body=b"x")
    assert budget.get_requests==0
    assert budget.put_requests==0
    for _ in range(MAX_READ_CALLS):
        with pytest.raises(Exception) as exc:
            budget.get_object(Bucket=BUCKET,Key=key)
        assert "NoSuchKey" in str(exc.value)
    with pytest.raises(ArchiveIntegrityError,match="read request budget"):
        budget.get_object(Bucket=BUCKET,Key=key)
    assert budget.get_requests==MAX_READ_CALLS
