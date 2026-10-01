from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.research_agent.evidence import CanonicalEvidenceReader, current_strategy_identity
from app.research_agent.runner import ResearchAgentRunner


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    token = os.environ["FOUNDATION_INGEST_TOKEN"].strip()
    gateway = ingest_url.rsplit("/v1/events", 1)[0] + "/v1/research-agent-gateway"
    headers = {
        "x-anevum-ingest-token": token,
        "content-type": "application/json",
    }
    marker = uuid4().hex[:12]
    run_id = str(uuid4())
    family_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(gateway, headers=headers)
        response.raise_for_status()
        body = response.json()
        if body.get("ok") is not True or not isinstance(body.get("evidence"), dict):
            raise RuntimeError("research-agent evidence response is invalid")

        evidence = CanonicalEvidenceReader(body["evidence"]).read()
        version, name = current_strategy_identity(evidence)
        try:
            review = ResearchAgentRunner(evidence).daily_review(dry_run=True)
        except ValueError as exc:
            if str(exc) != "no canonical daily report is available":
                raise
            review = {
                "trigger_reference": "CANONICAL_DAILY_REPORT_MISSING",
                "blocker_count": 1,
                "semantic_review_warranted": False,
            }

        run_record = {
            "run_id": run_id,
            "run_key": f"foundation-contract-probe:{marker}",
            "agent_version": "foundation-research-gateway-probe-v1",
            "source_commit": "foundation-contract-probe",
            "trigger": "foundation_contract_probe",
            "trigger_reference": marker,
            "started_at": now,
            "completed_at": now,
            "evidence_cutoff": (
                evidence.evidence_cutoff.isoformat()
                if evidence.evidence_cutoff
                else None
            ),
            "input_artifacts": [],
            "input_fingerprint": "probe-" + marker,
            "proposed_actions": [],
            "actions_taken": [],
            "tools_invoked": [],
            "output_artifact": {
                "synthetic": True,
                "execution_authority": False,
                "broker_calls": 0,
            },
            "approval_required": False,
            "authorization_reference": None,
            "status": "NOOP",
            "error_summary": None,
            "rationale_summary": {
                "conclusion": "Synthetic gateway contract verification only.",
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "uncertainties": [],
            },
            "llm_usage": {"invoked": False},
            "operator_identity": "foundation-contract-probe",
        }
        written = await http.post(
            gateway,
            headers=headers,
            json={"action": "record_run", "run": run_record},
        )
        written.raise_for_status()
        written_body = written.json()
        if written_body.get("inserted") is not True:
            raise RuntimeError(f"research run persistence failed: {written_body}")

        ledger = {
            "ledger_version": "math001-search-ledger-v1",
            "proposal_id": f"foundation-contract-probe-{marker}",
            "proposal_revision": 1,
            "proposal_hash": "a" * 64,
            "source_agent_run_id": run_id,
            "source_commit": "foundation-contract-probe",
            "family": {
                "family_id": family_id,
                "family_key": f"probe:{family_id}",
                "family_hash": "b" * 64,
                "normalized_name": "foundation contract probe",
                "display_name": "Foundation Contract Probe",
            },
            "hypotheses": [],
            "events": [],
            "multiplicity_plan": {
                "plan_id": str(uuid4()),
                "policy_status": "STUDY_ONLY_UNFROZEN",
                "production_authority": False,
                "protected_stage_authority": False,
            },
            "dependence_plan": {
                "plan_id": str(uuid4()),
                "policy_status": "STUDY_ONLY_UNFROZEN",
                "production_authority": False,
                "protected_stage_authority": False,
            },
            "candidate_variant_count": 0,
            "search_generation": 0,
            "production_authority": False,
            "protected_stage_authority": False,
        }
        ledger_write = await http.post(
            gateway,
            headers=headers,
            json={
                "action": "record_run_and_search_ledger",
                "run": run_record,
                "search_ledger": ledger,
            },
        )
        ledger_write.raise_for_status()
        ledger_body = ledger_write.json()
        if not ledger_body.get("search_ledger_recorded"):
            raise RuntimeError(f"search ledger persistence failed: {ledger_body}")
        if not (ledger_body.get("search_ledger") or {}).get("inserted"):
            raise RuntimeError(f"search ledger insert was not confirmed: {ledger_body}")

        after = await http.get(gateway, headers=headers)
        after.raise_for_status()
        after_evidence = after.json().get("evidence") or {}
        if any(
            row.get("run_key") == run_record["run_key"]
            for row in after_evidence.get("agent_runs") or []
            if isinstance(row, dict)
        ):
            raise RuntimeError("contract probe leaked into canonical agent-run evidence")
        exposure = (after_evidence.get("search_ledger") or {}).get("exposure") or {}
        if exposure.get("production_authority") is not False:
            raise RuntimeError("search ledger authority contract changed")

    print(
        "FOUNDATION_RESEARCH_AGENT_GATEWAY_PROBE_PASSED",
        {
            "strategy_version": version,
            "strategy_name": name,
            "daily_trigger_reference": review.get("trigger_reference"),
            "daily_blocker_count": review.get("blocker_count"),
            "run_persisted": True,
            "ledger_persisted": True,
            "probe_hidden_from_evidence": True,
            "model_invoked": False,
            "execution_authority": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
