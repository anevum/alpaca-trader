from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    token = os.environ["FOUNDATION_INGEST_TOKEN"].strip()
    gateway = ingest_url.rsplit("/v1/events", 1)[0] + "/v1/graen-gateway"
    headers = {
        "x-graen-gateway-token": token,
        "content-type": "application/json",
    }
    marker = uuid4().hex[:12]

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
                raise RuntimeError(f"{action} failed: {body}")
            return body

        created = await post(
            "create_problem",
            title=f"Foundation GRAEN contract probe {marker}",
            statement="Synthetic non-production contract verification.",
            domain="FOUNDATION_VERIFICATION",
            priority=0,
            source="FOUNDATION_PROBE",
            constraints={"execution_authority": False},
            success_criteria={"gateway_contract": "verified"},
            metadata={"synthetic": True, "probe": marker},
        )
        problem = created.get("problem") or {}
        problem_id = problem.get("problem_id")
        if not problem_id:
            raise RuntimeError("problem creation returned no problem_id")

        claimed = await post(
            "claim_problem",
            worker_id=f"foundation-probe-{marker}",
            runtime_version="foundation-graen-probe-v1",
            source_commit="probe",
            deployment_id="probe",
        )
        if (claimed.get("problem") or {}).get("problem_id") != problem_id:
            raise RuntimeError(f"wrong problem claimed: {claimed}")
        run = claimed.get("run") or {}
        run_id = run.get("run_id")
        if not run_id:
            raise RuntimeError("claim returned no run")

        artifact = await post(
            "record_artifact",
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="FOUNDATION_CONTRACT_PROBE",
            methodology_version="foundation-graen-probe-v1",
            source_commit="probe",
            content={
                "synthetic": True,
                "execution_authority": False,
                "broker_orders_possible": False,
            },
        )
        if not artifact.get("inserted"):
            raise RuntimeError(f"artifact was not inserted: {artifact}")

        completed = await post(
            "complete_problem",
            problem_id=problem_id,
            run_id=run_id,
            status="SUCCEEDED",
            result_summary={
                "synthetic": True,
                "state": "CONTRACT_VERIFIED",
                "execution_authority": False,
            },
            model_usage={"invoked": False},
        )
        if completed.get("ok") is not True:
            raise RuntimeError(f"completion failed: {completed}")

        snap = await http.get(gateway, headers=headers)
        snap.raise_for_status()
        payload = snap.json()
        found = next(
            (
                row
                for row in payload.get("problems") or []
                if row.get("problem_id") == problem_id
            ),
            None,
        )
        if not found or found.get("status") != "SUCCEEDED":
            raise RuntimeError(f"completed problem missing from snapshot: {found}")

    print(
        "FOUNDATION_GRAEN_GATEWAY_PROBE_PASSED",
        {
            "problem_id": problem_id,
            "run_id": run_id,
            "artifact_inserted": True,
            "completion_persisted": True,
            "execution_authority": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
