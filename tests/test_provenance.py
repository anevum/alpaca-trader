from datetime import datetime, timezone

from app.provenance import (
    RHEN_VERSION,
    capture_runtime_provenance,
    classify_runtime_transition,
)


def railway_env(**overrides):
    values = {
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "RAILWAY_GIT_BRANCH": "main",
        "RAILWAY_GIT_REPO_OWNER": "anevum",
        "RAILWAY_GIT_REPO_NAME": "alpaca-trader",
        "RAILWAY_DEPLOYMENT_ID": "deployment-1",
        "RAILWAY_SNAPSHOT_ID": "snapshot-1",
        "RAILWAY_PROJECT_ID": "project-1",
        "RAILWAY_PROJECT_NAME": "RHEN",
        "RAILWAY_ENVIRONMENT_ID": "environment-1",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_SERVICE_ID": "service-1",
        "RAILWAY_SERVICE_NAME": "alpaca-trader",
        "RAILWAY_REPLICA_ID": "replica-1",
        "RAILWAY_REPLICA_REGION": "sfo",
    }
    values.update(overrides)
    return values


def test_capture_runtime_provenance_reads_deployment_identity():
    observed = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    runtime = capture_runtime_provenance(
        railway_env(),
        now=observed,
        instance_id="instance-1",
    )

    assert runtime.git_commit == "a" * 40
    assert runtime.deployment_id == "deployment-1"
    assert runtime.branch == "main"
    assert runtime.repository == "anevum/alpaca-trader"
    assert runtime.runtime_instance_id == "instance-1"
    assert runtime.runtime_started_at == observed.isoformat()
    assert runtime.system == "RHEN"
    assert runtime.system_version == RHEN_VERSION
    assert runtime.metadata_quality == "complete"
    assert runtime.missing_critical_fields == ()


def test_missing_deployment_metadata_is_explicit_not_fabricated():
    runtime = capture_runtime_provenance(
        {},
        now=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        instance_id="instance-missing",
    )

    assert runtime.source == "unknown"
    assert runtime.git_commit is None
    assert runtime.deployment_id is None
    assert runtime.metadata_quality == "partial"
    assert runtime.missing_critical_fields == ("git_commit", "deployment_id")


def test_redeploy_and_restart_are_distinguishable_without_new_run_identity():
    observed = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    first = capture_runtime_provenance(
        railway_env(),
        now=observed,
        instance_id="instance-1",
    )
    restarted = capture_runtime_provenance(
        railway_env(),
        now=observed,
        instance_id="instance-2",
    )
    redeployed = capture_runtime_provenance(
        railway_env(
            RAILWAY_DEPLOYMENT_ID="deployment-2",
            RAILWAY_GIT_COMMIT_SHA="b" * 40,
        ),
        now=observed,
        instance_id="instance-3",
    )

    assert classify_runtime_transition(None, first) == "initial_start"
    assert classify_runtime_transition(first, restarted) == "restart"
    assert classify_runtime_transition(restarted, redeployed) == "redeploy"
