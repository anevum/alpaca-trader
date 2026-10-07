import json
from datetime import datetime, timedelta, timezone

import pytest

from app.profile_release_approval_registry import ProfileReleaseApprovalRegistry

NOW=datetime(2026,10,7,21,0,tzinfo=timezone.utc)


def payload():
    return {
        "release_id":"release-1",
        "release_fingerprint":"sha256:"+"1"*64,
        "profile_id":"NORMAL",
        "profile_version":"v1",
        "source_strategy_version":"LIVE-2026-09-25-003",
        "target_strategy_version":"4.4",
        "configuration_fingerprint":"sha256:"+"2"*64,
        "policy_library_fingerprint":"sha256:"+"3"*64,
        "authorization_reference":"ASC008-RELEASE-1",
    }


def test_registry_authorization_is_exact_signed_and_non_executing(tmp_path):
    registry=ProfileReleaseApprovalRegistry(str(tmp_path/"approvals.db"),"secret")
    row=registry.authorize(payload(),authorized_by="operator@example.com",now=NOW)
    assert row["evidence"]["authorized_action"]=="authorize_policy_profile_release"
    assert row["evidence"]["authorized_by"]=="operator@example.com"
    assert row["evidence"]["revoked"] is False
    snap=registry.current()
    assert snap["record_count"]==1 and snap["invalid_record_count"]==0
    assert snap["execution_authority"] is False
    assert snap["active_mode_authorized"] is False
    assert snap["research_decisions"][0]["evidence"]["release_fingerprint"]==payload()["release_fingerprint"]


def test_registry_duplicate_is_idempotent_and_conflicting_binding_rejected(tmp_path):
    registry=ProfileReleaseApprovalRegistry(str(tmp_path/"approvals.db"),"secret")
    first=registry.authorize(payload(),authorized_by="operator",now=NOW)
    second=registry.authorize(payload(),authorized_by="operator",now=NOW)
    assert first["decision_id"]==second["decision_id"] and second["duplicate"] is True
    changed=payload(); changed["profile_id"]="DEFENSIVE"
    with pytest.raises(ValueError,match="binding_conflict"):
        registry.authorize(changed,authorized_by="operator",now=NOW)


def test_registry_revocation_supersedes_current_read(tmp_path):
    registry=ProfileReleaseApprovalRegistry(str(tmp_path/"approvals.db"),"secret")
    registry.authorize(payload(),authorized_by="operator",now=NOW)
    revoked=registry.revoke(payload()["release_fingerprint"],revoked_by="operator",
                            now=NOW+timedelta(minutes=1))
    assert revoked["evidence"]["revoked"] is True
    snap=registry.current()
    assert snap["record_count"]==1
    assert snap["research_decisions"][0]["evidence"]["revoked"] is True


def test_registry_tamper_fails_closed(tmp_path):
    path=str(tmp_path/"approvals.db")
    registry=ProfileReleaseApprovalRegistry(path,"secret")
    registry.authorize(payload(),authorized_by="operator",now=NOW)
    with registry.connect() as db:
        row=db.execute("select decision_id,record_json from profile_release_approval_events").fetchone()
        body=json.loads(row["record_json"])
        body["evidence"]["profile_id"]="ASSERTIVE_TREND"
        db.execute("update profile_release_approval_events set record_json=? where decision_id=?",
                   (json.dumps(body),row["decision_id"]))
        db.commit()
    snap=registry.current()
    assert snap["record_count"]==0
    assert snap["invalid_record_count"]==1


def test_registry_requires_execution_only_secret(tmp_path):
    with pytest.raises(ValueError,match="secret_unavailable"):
        ProfileReleaseApprovalRegistry(str(tmp_path/"x.db"),"")
