from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx


UTC = timezone.utc


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def state(
    observed_at: datetime,
    *,
    label: str,
    health: str,
    baseline: dict | None,
) -> dict:
    return {
        "version": "iren-control-v2.0.0",
        "observed_at": observed_at.isoformat(),
        "state": health,
        "source_commit": "foundation-iren-gateway-probe",
        "incidents": {},
        "configuration_baseline": baseline,
        "services": {},
        "scheduler": {},
        "logical_subsystems": {
            "NOSTRA": "WAITING_ACTIVATION",
            "RESEARCH_AGENT": "ON_DEMAND_SLEEP_ALLOWED",
        },
        "metrics": {"dropped_count": None},
        "authority": {
            "deterministic": True,
            "model_invoked": False,
            "trading_mutations": False,
            "protected_actions": [],
        },
        "probe_label": label,
    }


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    token = os.environ["FOUNDATION_INGEST_TOKEN"].strip()
    gateway = ingest_url.rsplit("/v1/events", 1)[0] + "/v1/scheduler-gateway"
    headers = {
        "x-anevum-ingest-token": token,
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        async def post(action: str, **payload):
            response = await http.post(
                gateway,
                headers=headers,
                json={"action": action, **payload},
            )
            response.raise_for_status()
            body = response.json()
            if body.get("ok") is not True:
                raise RuntimeError(f"gateway action failed: {action}: {body}")
            return body

        read = await post("iren_read")
        initial_revision = int(read.get("revision") or 0)

        now = datetime.now(UTC)
        first_state = state(
            now,
            label="contract-test",
            health="HEALTHY",
            baseline={"fingerprint": "synthetic-probe"},
        )
        event_key = digest(
            {
                "kind": "foundation_iren_gateway_probe",
                "at": now.isoformat(),
                "id": str(uuid4()),
            }
        )
        event = {
            "event_key": event_key,
            "key": "probe.gateway",
            "transition": "OPEN",
            "episode": 1,
            "severity": "warning",
            "reason": "synthetic_contract_test",
            "route": "iren-control",
        }
        committed = await post(
            "iren_commit",
            expected_revision=initial_revision,
            observation_key=digest(first_state),
            state=first_state,
            events=[event],
        )
        if committed.get("committed") is not True:
            raise RuntimeError(f"IREN commit failed: {committed}")

        duplicate = await post(
            "iren_commit",
            expected_revision=initial_revision,
            observation_key=digest(first_state),
            state=first_state,
            events=[event],
        )
        if duplicate.get("committed") is not True or duplicate.get("idempotent") is not True:
            raise RuntimeError(f"IREN idempotency failed: {duplicate}")

        owner = str(uuid4())
        claimed = await post("iren_notifications_claim", owner=owner)
        events = list(claimed.get("events") or [])
        matching = next(
            (item for item in events if item.get("event_key") == event_key),
            None,
        )
        if matching is None:
            raise RuntimeError(f"IREN notification lease failed: {claimed}")
        completed = await post(
            "iren_notification_complete",
            event_key=event_key,
            owner=owner,
            delivery_status="delivered:probe",
        )
        if completed.get("updated") is not True:
            raise RuntimeError(f"IREN notification completion failed: {completed}")

        job_key = f"verification.foundation-iren:{uuid4()}"
        scheduled_at = datetime.now(UTC).isoformat()
        claim = await post(
            "claim",
            job={
                "job_key": job_key,
                "workflow_id": "verification.foundation-iren",
                "workflow_version": "v1",
                "scheduler_version": "foundation-probe-v1",
                "scheduled_at": scheduled_at,
                "trigger_type": "verification",
                "max_attempts": 2,
                "lease_seconds": 180,
                "allow_retry": True,
                "worker_identity": "foundation-probe",
                "source_commit": "foundation-probe",
                "input_identity": digest({"job_key": job_key}),
                "catchup_state": None,
                "details": {"synthetic": True},
            },
        )
        if claim.get("claimed") is not True:
            raise RuntimeError(f"scheduler claim failed: {claim}")

        duplicate_claim = await post(
            "claim",
            job={
                "job_key": job_key,
                "workflow_id": "verification.foundation-iren",
                "workflow_version": "v1",
                "scheduler_version": "foundation-probe-v1",
                "scheduled_at": scheduled_at,
                "trigger_type": "verification",
                "max_attempts": 2,
                "lease_seconds": 180,
                "allow_retry": True,
                "worker_identity": "foundation-probe",
                "source_commit": "foundation-probe",
                "input_identity": digest({"job_key": job_key}),
                "details": {"synthetic": True},
            },
        )
        if duplicate_claim.get("claimed") is not False or duplicate_claim.get("busy") is not True:
            raise RuntimeError(f"scheduler duplicate fencing failed: {duplicate_claim}")

        completed_job = await post(
            "complete",
            job_key=job_key,
            status="SUCCEEDED",
            output_identity=digest({"result": "ok"}),
            error_summary={},
            details={"synthetic": True, "result": {"ok": True}},
            slack_notification_status="suppressed:verification",
        )
        if completed_job.get("updated") is not True:
            raise RuntimeError(f"scheduler completion failed: {completed_job}")

        repeat_completion = await post(
            "complete",
            job_key=job_key,
            status="SUCCEEDED",
            output_identity=digest({"result": "ok"}),
            error_summary={},
            details={"synthetic": True},
        )
        if repeat_completion.get("idempotent") is not True:
            raise RuntimeError(f"scheduler completion idempotency failed: {repeat_completion}")

        recent_response = await http.get(
            gateway,
            headers=headers,
            params={"limit": "25"},
        )
        recent_response.raise_for_status()
        recent = recent_response.json()
        if not any(row.get("job_key") == job_key for row in recent.get("runs") or []):
            raise RuntimeError("scheduler recent-run read did not return probe job")

        # Leave canonical IREN state neutral so the production controller can
        # establish the real configuration fingerprint on first observation.
        neutral_at = now + timedelta(seconds=1)
        neutral_state = state(
            neutral_at,
            label="handoff-neutral",
            health="STARTING",
            baseline=None,
        )
        neutral = await post(
            "iren_commit",
            expected_revision=int(committed["revision"]),
            observation_key=digest(neutral_state),
            state=neutral_state,
            events=[],
        )
        if neutral.get("committed") is not True:
            raise RuntimeError(f"neutral handoff failed: {neutral}")

        final = await post("iren_read")
        if (final.get("state") or {}).get("configuration_baseline") is not None:
            raise RuntimeError(f"probe baseline leaked into handoff: {final}")
        if (final.get("state") or {}).get("probe_label") != "handoff-neutral":
            raise RuntimeError(f"neutral handoff state missing: {final}")

    print(
        "FOUNDATION_IREN_GATEWAY_PROBE_PASSED",
        {
            "revision_start": initial_revision,
            "revision_end": int(final.get("revision") or 0),
            "commit_idempotency": True,
            "notification_lease": True,
            "scheduler_claim_fencing": True,
            "scheduler_completion_idempotency": True,
            "neutral_handoff": True,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
