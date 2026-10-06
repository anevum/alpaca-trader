from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.graen.adaptive_hypothesis import (
    MAX_GENERATIONS,
    build_spec,
    conservative_exposure_ledger,
)
from app.rhen_core.store import RhenCoreStore
from app.graen.research_executor_service import GraenResearchExecutor
from graen.engineering import digest, verify_exposure


UTC = timezone.utc


def _adaptive_problem(store: RhenCoreStore, now: datetime):
    created = store.graen_action(
        "create_problem",
        {
            "title": "adaptive fixture",
            "statement": "adaptive fixture",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "priority": 96,
            "metadata": {"adaptive_program_id": "GRAEN-ADAPTIVE-FLOW-V1"},
        },
    )
    problem_id = created["problem"]["problem_id"]
    ledger = store.graen_action(
        "record_artifact",
        {
            "problem_id": problem_id,
            "run_id": None,
            "artifact_type": "RESEARCH_CORPUS_EXPOSURE_LEDGER",
            "methodology_version": "graen.adaptive-flow.v1",
            "content": conservative_exposure_ledger(now),
        },
    )
    exposure_id = ledger["artifact"]["artifact_id"]
    spec = build_spec(
        1,
        exposure_artifact_id=exposure_id,
        search_history=["fixture"],
        now=now,
    )
    return problem_id, spec


def test_adaptive_spec_uses_future_sealed_confirmatory_windows():
    now = datetime(2026, 10, 6, 4, 0, tzinfo=UTC)
    ledger = conservative_exposure_ledger(now)
    spec = build_spec(
        1,
        exposure_artifact_id="exposure-fixture",
        search_history=["fresh"],
        now=now,
    )

    validation_start = datetime.fromisoformat(
        spec["corpus"]["validation"][0]
    )
    holdout_start = datetime.fromisoformat(spec["corpus"]["holdout"][0])
    assert validation_start > now
    assert holdout_start > validation_start
    verify_exposure(
        spec,
        {**ledger, "artifact_id": "exposure-fixture"},
    )

    with pytest.raises(ValueError, match="adaptive_generation_exhausted"):
        build_spec(
            MAX_GENERATIONS + 1,
            exposure_artifact_id="exposure-fixture",
            search_history=["fresh"],
            now=now,
        )


def test_native_iren_work_defaults_to_bounded_autopilot(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-core.db")
    snapshot = store.iren_work_snapshot()

    assert snapshot["settings"]["autopilot_enabled"] is True
    assert snapshot["settings"]["autopilot_max_jobs_per_day"] == 3


def test_native_research_promotion_claim_and_freeze(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-core.db")
    now = datetime(2026, 10, 6, 4, 0, tzinfo=UTC)
    problem_id, spec = _adaptive_problem(store, now)
    store.graen_action(
        "queue_research_stage",
        {
            "problem_id": problem_id,
            "stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
            "metadata": {
                "research_implementation_spec": spec,
                "code_promotion": {},
                "code_promotion_revision": 0,
            },
        },
    )

    owner = str(uuid4())
    claimed = store.graen_action(
        "research_promotion_claim",
        {"problem_id": problem_id, "owner": owner},
    )
    assert claimed["claimed"] is True
    assert claimed["revision"] == 0
    assert claimed["prespec"] == spec
    assert claimed["exposure"]["artifact_id"] == spec["exposure_artifact_id"]

    state = {
        "phase": "BRANCH",
        "spec_hash": digest(spec),
        "prespec": spec,
    }
    saved = store.graen_action(
        "research_promotion_save",
        {
            "problem_id": problem_id,
            "owner": owner,
            "expected_revision": 0,
            "state": state,
        },
    )
    assert saved["revision"] == 1
    assert saved["prespec_artifact_id"]

    next_claim = store.graen_action(
        "research_promotion_claim",
        {"problem_id": problem_id, "owner": str(uuid4())},
    )
    assert next_claim["claimed"] is True
    assert next_claim["revision"] == 1
    assert next_claim["state"] == state


def test_adaptive_claim_waits_for_sealed_stage_maturity(tmp_path):
    store = RhenCoreStore(tmp_path / "rhen-core.db")
    now = datetime.now(UTC)
    problem_id, spec = _adaptive_problem(store, now)
    state = {
        "phase": "COMPLETE",
        "spec_hash": digest(spec),
        "prespec": spec,
        "resume_stage": "CRYPTO_COMPILED_DEVELOPMENT",
        "merge_sha": "a" * 40,
        "deployment_id": "deployment",
        "executor_heartbeat_at": now.isoformat(),
        "ci": [{"name": "test", "conclusion": "success"}],
    }

    store.graen_action(
        "queue_research_stage",
        {
            "problem_id": problem_id,
            "stage": "CRYPTO_COMPILED_VALIDATION",
            "metadata": {
                "research_implementation_spec": spec,
                "code_promotion": state,
                "compiled_specification_hash": digest(spec),
            },
        },
    )
    waiting = store.graen_action(
        "claim_adaptive_research_problem",
        {
            "worker_id": "adaptive",
            "runtime_version": "test",
            "methodology_version": "test",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
        },
    )
    assert waiting["problem"] is None
    assert waiting["waiting_until"] == spec["corpus"]["validation"][1]

    past = {
        **spec,
        "corpus": {
            "development": [
                (now - timedelta(days=180)).isoformat(),
                (now - timedelta(days=120)).isoformat(),
            ],
            "validation": [
                (now - timedelta(days=120)).isoformat(),
                (now - timedelta(days=80)).isoformat(),
            ],
            "holdout": [
                (now - timedelta(days=80)).isoformat(),
                (now - timedelta(days=40)).isoformat(),
            ],
        },
    }
    past_state = {**state, "spec_hash": digest(past), "prespec": past}
    with store.connect() as conn:
        import json
        row = conn.execute(
            "select metadata_json from graen_problems where problem_id=?",
            (problem_id,),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["research_implementation_spec"] = past
        metadata["code_promotion"] = past_state
        metadata["compiled_specification_hash"] = digest(past)
        conn.execute(
            "update graen_problems set metadata_json=? where problem_id=?",
            (json.dumps(metadata), problem_id),
        )
        conn.commit()

    ready = store.graen_action(
        "claim_adaptive_research_problem",
        {
            "worker_id": "adaptive",
            "runtime_version": "test",
            "methodology_version": "test",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
        },
    )
    assert ready["problem"]["problem_id"] == problem_id
    assert ready["run"]["status"] == "RUNNING"


def test_runtime_compiler_persists_exact_bundle_without_repository_write():
    now = datetime(2026, 10, 6, 4, 0, tzinfo=UTC)
    spec = build_spec(
        1,
        exposure_artifact_id=str(uuid4()),
        search_history=["fixture"],
        now=now,
    )
    problem = {
        "problem_id": str(uuid4()),
        "metadata": {
            "research_implementation_spec": spec,
            "code_promotion": {
                "phase": "BRANCH",
                "prespec": spec,
                "spec_hash": digest(spec),
                "blocked_reason": "runtime_github_authorization_not_configured",
            },
        },
    }
    gateway = type(
        "Gateway",
        (),
        {
            "record_artifact": AsyncMock(
                return_value={"artifact": {"artifact_id": str(uuid4())}}
            ),
            "queue_research_stage": AsyncMock(
                return_value={"problem": problem}
            ),
        },
    )()
    runtime = object.__new__(GraenResearchExecutor)
    runtime.gateway = gateway

    result = asyncio.run(runtime._runtime_compile_adaptive(problem))

    assert result["phase"] == "RUNTIME_COMPILED"
    assert result["repository_published"] is False
    assert result["next_stage"] == "CRYPTO_COMPILED_DEVELOPMENT"
    record_kwargs = gateway.record_artifact.await_args.kwargs
    assert record_kwargs["artifact_type"] == "RESEARCH_RUNTIME_COMPILED_BUNDLE"
    assert record_kwargs["content"]["files"]
    queue_kwargs = gateway.queue_research_stage.await_args.kwargs
    assert queue_kwargs["stage"] == "CRYPTO_COMPILED_DEVELOPMENT"
    assert queue_kwargs["metadata"]["code_promotion"]["phase"] == "RUNTIME_COMPILED"
