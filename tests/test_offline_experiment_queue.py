from pathlib import Path

import pytest

from app.research_agent.offline_experiment_queue import OfflineExperimentQueue


FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64


def spec():
    return {
        "id": "EXP-20261008-01",
        "hypothesis": "A better entry-quality gate may improve net expectancy",
        "control": {"min_quality_score": 80},
        "treatment": {"min_quality_score": 85},
        "required_data": ["point-in-time candidate prices", "verified forward outcome"],
        "failure_stop": ["no positive independent holdout advantage"],
        "authority": "OFFLINE_RESEARCH_ONLY",
        "live_change_authorized": False,
        "promotion_authorized": False,
    }


def test_offline_queue_persists_proposals_idempotently_and_rejects_redefinition(tmp_path):
    path = tmp_path / "isolated-research.sqlite"
    queue = OfflineExperimentQueue(path)
    first = queue.propose(spec(), source_fingerprint=FINGERPRINT)
    second = queue.propose(spec(), source_fingerprint=FINGERPRINT)
    assert first == second
    assert first["state"] == "PROPOSED"
    assert first["execution_authority"] is False
    assert first["promotion_authorized"] is False
    assert len(queue.history(spec()["id"])) == 1
    reopened = OfflineExperimentQueue(path)
    assert reopened.get(spec()["id"]) == first
    changed = spec()
    changed["treatment"] = {"min_quality_score": 100}
    with pytest.raises(ValueError, match="identity conflict"):
        queue.propose(changed, source_fingerprint=FINGERPRINT)
    with pytest.raises(ValueError, match="identity conflict"):
        queue.propose(spec(), source_fingerprint=OTHER_FINGERPRINT)


def test_offline_queue_has_review_and_evidence_quality_gates(tmp_path):
    queue = OfflineExperimentQueue(tmp_path / "offline.sqlite")
    queue.propose(spec(), source_fingerprint=FINGERPRINT)
    identifier = spec()["id"]

    with pytest.raises(ValueError, match="protected offline"):
        queue.transition(identifier, "RUNNING_OFFLINE", evidence_fingerprint=FINGERPRINT)
    ready = queue.transition(
        identifier, "AWAITING_EVIDENCE",
        evidence_fingerprint=FINGERPRINT,
    )
    assert ready["state"] == "AWAITING_EVIDENCE"
    with pytest.raises(ValueError, match="frozen point-in-time"):
        queue.transition(
            identifier, "READY_FOR_OFFLINE_RUN",
            evidence_fingerprint=FINGERPRINT,
        )
    data_checks = {
        "data_frozen": True,
        "source_version_locked": True,
        "decision_time_features_verified": True,
    }
    ready = queue.transition(
        identifier, "READY_FOR_OFFLINE_RUN",
        evidence_fingerprint=FINGERPRINT, checks=data_checks,
    )
    assert ready["state"] == "READY_FOR_OFFLINE_RUN"
    queue.transition(identifier, "RUNNING_OFFLINE", evidence_fingerprint=FINGERPRINT)
    queue.transition(identifier, "REVIEW_REQUIRED", evidence_fingerprint=FINGERPRINT)
    with pytest.raises(ValueError, match="incomplete holdout"):
        queue.transition(
            identifier, "ELIGIBLE_FOR_HUMAN_VALIDATION",
            evidence_fingerprint=FINGERPRINT,
        )
    finished = queue.transition(
        identifier, "ELIGIBLE_FOR_HUMAN_VALIDATION",
        evidence_fingerprint=OTHER_FINGERPRINT,
        checks={
            "holdout_evaluated": True,
            "execution_costs_stressed": True,
            "replay_limitations_reviewed": True,
        },
    )
    assert finished["promotion_authorized"] is False
    assert finished["state"] == "ELIGIBLE_FOR_HUMAN_VALIDATION"
    assert len(queue.history(identifier)) == 6
    with pytest.raises(ValueError, match="protected offline"):
        queue.transition(
            identifier, "PROPOSED", evidence_fingerprint=FINGERPRINT
        )


def test_offline_queue_blocks_broker_authority_and_unaudited_sources(tmp_path):
    queue = OfflineExperimentQueue(tmp_path / "offline.sqlite")
    dangerous = spec()
    dangerous["promotion_authorized"] = True
    with pytest.raises(ValueError, match="promotion_authorized"):
        queue.propose(dangerous, source_fingerprint=FINGERPRINT)
    with pytest.raises(ValueError, match="fingerprint"):
        queue.propose(spec(), source_fingerprint="not-a-real-hash")
    no_authority = spec()
    no_authority.pop("authority")
    with pytest.raises(ValueError, match="research-only authority"):
        queue.propose(no_authority, source_fingerprint=FINGERPRINT)
