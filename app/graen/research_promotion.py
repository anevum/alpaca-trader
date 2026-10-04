"""Manual engineering handoff for GRAEN research capabilities.

Runtime research may freeze specifications and request missing software, but it may
not edit source, create branches/PRs, merge, or deploy. After an operator/Codex
change is deployed, this controller verifies that the deployed implementation
matches the frozen specification and resumes research automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
import uuid
from typing import Any, Mapping

from graen.engineering import (
    IntegrityError,
    TRIGGERS,
    compile_bundle,
    digest,
    validate_spec,
    verify_bundle,
    verify_exposure,
)
from app.research_agent.autonomy import build_engineering_requirement

UTC = timezone.utc


class PromotionStore:
    def __init__(self, gateway):
        self.gateway = gateway

    async def claim(self, problem_id, owner):
        return await self.gateway._request(
            "POST",
            {"action": "research_promotion_claim", "problem_id": problem_id, "owner": owner},
        )

    async def save(self, problem_id, owner, revision, state):
        return await self.gateway._request(
            "POST",
            {
                "action": "research_promotion_save",
                "problem_id": problem_id,
                "owner": owner,
                "expected_revision": revision,
                "state": state,
            },
        )


def _module_name(spec: Mapping[str, Any]) -> str:
    return str(spec["hypothesis_id"]).lower().replace("-", "_")


def _manual_resolution(
    spec: Mapping[str, Any],
    spec_hash: str,
    heartbeat: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return deployed provenance only when exact frozen implementation is importable."""
    try:
        implementation = import_module("graen.crypto.generated." + _module_name(spec))
    except (ImportError, ModuleNotFoundError):
        return None

    if getattr(implementation, "SPEC_HASH", None) != spec_hash:
        return None
    deployed_spec = getattr(implementation, "SPEC", None)
    if not isinstance(deployed_spec, Mapping) or digest(dict(deployed_spec)) != spec_hash:
        return None
    if not callable(getattr(implementation, "evaluate", None)):
        return None

    source_commit = str(heartbeat.get("source_commit") or "").strip()
    deployment_id = str(heartbeat.get("deployment_id") or "").strip()
    heartbeat_at = str(heartbeat.get("heartbeat_at") or "").strip()
    if not source_commit or not deployment_id or not heartbeat_at:
        return None
    try:
        stamp = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if not (0 <= (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds() <= 300):
            return None
    except ValueError:
        return None

    return {
        "verified": True,
        "source_commit": source_commit,
        "deployment_id": deployment_id,
        "heartbeat_at": heartbeat_at,
        "module": "graen.crypto.generated." + _module_name(spec),
        "spec_hash": spec_hash,
        "verification": "deployed_module_matches_frozen_prespec",
    }


def _requirement(spec: Mapping[str, Any], spec_hash: str) -> dict[str, Any]:
    files = compile_bundle(dict(spec))
    verify_bundle(dict(spec), files)
    hypothesis_id = str(spec["hypothesis_id"])
    expected_paths = sorted(files)
    return build_engineering_requirement(
        requirement_id="ENG-" + spec_hash[:12].upper(),
        requested_by="GRAEN",
        title=f"Implement frozen research hypothesis {hypothesis_id}",
        reason=(
            "GRAEN produced a frozen research specification that cannot execute until "
            "the required trusted software implementation exists in the deployed runtime."
        ),
        capability_required=(
            f"Trusted deterministic implementation for {hypothesis_id} matching frozen "
            f"specification hash {spec_hash}."
        ),
        affected_components=["GRAEN research compiler", "GRAEN research executor", "research tests"],
        blocked_research=[hypothesis_id],
        acceptance_tests=[
            "Deployed implementation exposes SPEC and SPEC_HASH matching the frozen specification.",
            "Deployed implementation exposes deterministic evaluate(...) behavior.",
            "Protected trading/risk/broker code remains unchanged.",
            "Relevant research and CI suites pass.",
            "GRAEN research executor reports a fresh deployment heartbeat and automatically resumes DEVELOPMENT.",
        ],
        suggested_paths=expected_paths,
        continuation_policy="Continue every independent research branch while this hypothesis waits for software.",
        risk="LOW",
        implementation_notes=[
            "Use the frozen prespec exactly; do not tune parameters while implementing.",
            "Do not open validation or holdout data during software implementation.",
            "Do not grant the runtime GitHub write, merge, deployment, or live-trading authority.",
        ],
    )


class ResearchPromotion:
    """Converts implementation gaps into manual engineering work packages."""

    def __init__(self, gateway, repository=None):
        # repository is intentionally accepted for backwards constructor
        # compatibility but is never used. Runtime source mutation is forbidden.
        self.store = PromotionStore(gateway)
        self.repository = None

    async def tick(self, problem_id):
        owner = str(uuid.uuid4())
        claim = await self.store.claim(problem_id, owner)
        if not claim.get("claimed"):
            return {"state": "NOT_CLAIMED"}

        revision = claim["revision"]
        state = dict(claim.get("state") or {})
        try:
            spec = claim.get("prespec")
            if not isinstance(spec, dict):
                raise IntegrityError("complete_frozen_prespec_required")
            spec_hash = validate_spec(spec)
            verify_exposure(spec, claim.get("exposure") or {})
            if state.get("spec_hash") and (
                state.get("spec_hash") != spec_hash or state.get("prespec") != spec
            ):
                raise IntegrityError("frozen_prespec_mutation")

            phase = str(state.get("phase") or "FREEZE")
            if phase == "FREEZE":
                state = {
                    "phase": "ENGINEERING_REQUIRED",
                    "prespec": spec,
                    "spec_hash": spec_hash,
                    "epoch": spec["epoch"],
                    "engineering_requirement": _requirement(spec, spec_hash),
                    "research_only": True,
                    "execution_authority": False,
                    "runtime_code_mutation_authorized": False,
                    "runtime_git_write_authorized": False,
                    "runtime_merge_authorized": False,
                    "runtime_deploy_authorized": False,
                }
            elif phase == "ENGINEERING_REQUIRED":
                resolved = _manual_resolution(
                    spec,
                    spec_hash,
                    claim.get("executor_heartbeat") or {},
                )
                if resolved is not None:
                    state.update(
                        phase="COMPLETE",
                        manual_resolution=resolved,
                        resume_stage="CRYPTO_COMPILED_DEVELOPMENT",
                        blocked_reason=None,
                    )
            elif phase != "COMPLETE":
                raise IntegrityError("unknown_manual_engineering_phase")
        except (IntegrityError, KeyError, ValueError) as exc:
            state["blocked_reason"] = str(exc)

        await self.store.save(problem_id, owner, revision, state)
        return state


def engineering_problem_ids(snapshot):
    runs = snapshot.get("runs") or []
    latest = {}
    for run in runs:
        latest.setdefault(str(run.get("problem_id")), run.get("result_summary") or {})
    for problem in snapshot.get("problems") or []:
        if problem.get("status") not in {"WAITING", "BLOCKED"}:
            continue
        metadata = problem.get("metadata") or {}
        promotion = metadata.get("code_promotion") or {}
        research_stage = str(metadata.get("research_stage") or "")

        if research_stage.startswith("CRYPTO_") and not research_stage.startswith("CRYPTO_COMPILED_"):
            if research_stage != "RESEARCH_IMPLEMENTATION_REQUIRED":
                continue

        summary = latest.get(str(problem.get("problem_id")), {})
        triggered = any(
            summary.get(key) in TRIGGERS
            for key in ("state", "decision", "next_action")
        )
        phase = str(promotion.get("phase") or "")
        if phase == "COMPLETE":
            continue
        if triggered or promotion or research_stage == "RESEARCH_IMPLEMENTATION_REQUIRED":
            yield str(problem["problem_id"])
