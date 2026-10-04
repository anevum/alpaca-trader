from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .models import ExperimentWorkflowState


CHARTER_SCHEMA_VERSION = "anevum.autonomy-charter.v1"
CHARTER_ID = "ANEVUM-AUTONOMY-CHARTER-V1"
CHARTER_VERSION = "1.0.0"
CHARTER_EFFECTIVE_AT = "2026-10-04T22:30:00Z"

AUTONOMOUS_RESEARCH_STAGES = frozenset(
    {
        ExperimentWorkflowState.DEVELOPMENT_RUNNING,
        ExperimentWorkflowState.VALIDATION_RUNNING,
        ExperimentWorkflowState.HOLDOUT_RUNNING,
    }
)

AUTONOMOUS_ACTIONS = frozenset(
    {
        "INGEST_DATA",
        "VERIFY_DATA",
        "GENERATE_HYPOTHESIS",
        "PREREGISTER_EXPERIMENT",
        "FREEZE_RESEARCH_METHODOLOGY",
        "OPEN_DEVELOPMENT",
        "OPEN_VALIDATION",
        "OPEN_HOLDOUT",
        "RUN_FALSIFICATION",
        "RUN_VELUM_REPLAY",
        "RUN_SHADOW",
        "RUN_PAPER",
        "REJECT_HYPOTHESIS",
        "ARCHIVE_HYPOTHESIS",
        "RETIRE_DEGRADED_STRATEGY",
        "DECREASE_LIVE_EXPOSURE",
        "RECOVER_IDEMPOTENT_OPERATION",
        "SCHEDULE_NEXT_EXPERIMENT",
    }
)

PROHIBITED_ACTIONS = frozenset(
    {
        "MODIFY_SOURCE_CODE",
        "CREATE_OR_MERGE_SOFTWARE_PR",
        "CHANGE_CREDENTIALS",
        "COMMIT_NEW_SPEND",
        "INCREASE_LIVE_RISK_LIMIT",
        "ADD_BROKER_OR_EXCHANGE",
        "WEAKEN_STATISTICAL_GUARDRAIL",
        "BYPASS_HOLDOUT",
        "PROMOTE_UNRESTRICTED_LIVE",
        "DESTRUCTIVE_INFRASTRUCTURE_CHANGE",
        "LEGAL_ACCEPTANCE",
        "IRREVERSIBLE_PUBLICATION",
        "EXTERNAL_CAPITAL_ACTION",
    }
)


class AutonomyCondition(str, Enum):
    OPERATING = "OPERATING"
    RESEARCHING = "RESEARCHING"
    ENGINEERING_REQUIRED = "ENGINEERING_REQUIRED"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
    BLOCKED = "BLOCKED"
    IDLE = "IDLE"


@dataclass(frozen=True, slots=True)
class AutonomyCharter:
    schema_version: str = CHARTER_SCHEMA_VERSION
    charter_id: str = CHARTER_ID
    version: str = CHARTER_VERSION
    effective_at: str = CHARTER_EFFECTIVE_AT
    autonomous_actions: frozenset[str] = AUTONOMOUS_ACTIONS
    prohibited_actions: frozenset[str] = PROHIBITED_ACTIONS
    code_mutation_authority: bool = False
    credential_mutation_authority: bool = False
    spending_authority: bool = False
    production_risk_increase_authority: bool = False
    statistical_guardrail_override_authority: bool = False
    unrestricted_live_promotion_authority: bool = False

    def allows_action(self, action: str) -> bool:
        normalized = str(action or "").strip().upper()
        return normalized in self.autonomous_actions and normalized not in self.prohibited_actions

    def allows_stage(self, target: ExperimentWorkflowState) -> bool:
        return target in AUTONOMOUS_RESEARCH_STAGES

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["autonomous_actions"] = sorted(self.autonomous_actions)
        payload["prohibited_actions"] = sorted(self.prohibited_actions)
        return payload


DEFAULT_CHARTER = AutonomyCharter()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def authorization_reference(kind: str, material: Mapping[str, Any]) -> str:
    normalized = str(kind or "").strip().upper()
    if not normalized:
        raise ValueError("authorization kind is required")
    return f"{CHARTER_ID}:{normalized}:{_digest(dict(material))[:20]}"


def standing_freeze_authorization(
    *,
    proposal_id: str,
    proposal_revision: int,
    proposal_hash: str,
    charter: AutonomyCharter = DEFAULT_CHARTER,
) -> dict[str, Any]:
    if not charter.allows_action("FREEZE_RESEARCH_METHODOLOGY"):
        raise ValueError("autonomy charter does not authorize methodology freeze")
    if not proposal_id or proposal_revision < 1 or not proposal_hash:
        raise ValueError("complete proposal identity is required")
    material = {
        "proposal_id": proposal_id,
        "proposal_revision": proposal_revision,
        "proposal_hash": proposal_hash,
        "charter_id": charter.charter_id,
        "charter_version": charter.version,
    }
    return {
        "decision_reference": authorization_reference("FREEZE", material),
        **material,
        "authorized_by": charter.charter_id,
        "authorized_at": charter.effective_at,
        "authorization_basis": "STANDING_RESEARCH_CHARTER",
        "human_approval_required": False,
        "production_authority": False,
    }


def standing_stage_authorization(
    *,
    experiment_id: str,
    experiment_key: str,
    stage: str,
    manifest_hash: str,
    source_commit: str,
    charter: AutonomyCharter = DEFAULT_CHARTER,
) -> dict[str, Any]:
    normalized_stage = str(stage or "").strip().casefold()
    target_by_stage = {
        "development": ExperimentWorkflowState.DEVELOPMENT_RUNNING,
        "validation": ExperimentWorkflowState.VALIDATION_RUNNING,
        "holdout": ExperimentWorkflowState.HOLDOUT_RUNNING,
    }
    target = target_by_stage.get(normalized_stage)
    if target is None or not charter.allows_stage(target):
        raise ValueError("autonomy charter does not authorize protected research stage")
    if not all(str(v or "").strip() for v in (experiment_id, experiment_key, manifest_hash, source_commit)):
        raise ValueError("complete experiment identity is required")
    action = "OPEN_" + normalized_stage.upper()
    if not charter.allows_action(action):
        raise ValueError("autonomy charter does not authorize stage action")
    material = {
        "experiment_id": experiment_id,
        "experiment_key": experiment_key,
        "stage": normalized_stage,
        "manifest_hash": manifest_hash,
        "source_commit": source_commit,
        "charter_id": charter.charter_id,
        "charter_version": charter.version,
    }
    return {
        "decision_reference": authorization_reference("STAGE", material),
        **material,
        "authorized_by": charter.charter_id,
        "authorized_at": charter.effective_at,
        "authorization_basis": "STANDING_RESEARCH_CHARTER",
        "human_approval_required": False,
        "production_authority": False,
    }


def classify_condition(
    *,
    code_change_required: bool = False,
    human_authority_required: bool = False,
    evidence_blocked: bool = False,
    active_research: bool = False,
    active_operations: bool = False,
) -> AutonomyCondition:
    if human_authority_required:
        return AutonomyCondition.HUMAN_DECISION_REQUIRED
    if code_change_required:
        return AutonomyCondition.ENGINEERING_REQUIRED
    if evidence_blocked:
        return AutonomyCondition.BLOCKED
    if active_research:
        return AutonomyCondition.RESEARCHING
    if active_operations:
        return AutonomyCondition.OPERATING
    return AutonomyCondition.IDLE


def build_engineering_requirement(
    *,
    requirement_id: str,
    requested_by: str,
    title: str,
    reason: str,
    capability_required: str,
    affected_components: Sequence[str],
    blocked_research: Sequence[str],
    acceptance_tests: Sequence[str],
    suggested_paths: Sequence[str] = (),
    continuation_policy: str = "Continue all independent research branches.",
    risk: str = "LOW",
    implementation_notes: Sequence[str] = (),
) -> dict[str, Any]:
    if not all(str(v or "").strip() for v in (requirement_id, requested_by, title, reason, capability_required)):
        raise ValueError("engineering requirement identity and reason are required")
    if not acceptance_tests:
        raise ValueError("engineering requirement must define acceptance tests")

    payload: dict[str, Any] = {
        "schema_version": "anevum.engineering-requirement.v1",
        "requirement_id": requirement_id,
        "condition": AutonomyCondition.ENGINEERING_REQUIRED.value,
        "requested_by": requested_by,
        "title": title,
        "reason": reason,
        "capability_required": capability_required,
        "affected_components": list(dict.fromkeys(str(v) for v in affected_components if str(v).strip())),
        "blocked_research": list(dict.fromkeys(str(v) for v in blocked_research if str(v).strip())),
        "acceptance_tests": list(dict.fromkeys(str(v) for v in acceptance_tests if str(v).strip())),
        "suggested_paths": list(dict.fromkeys(str(v) for v in suggested_paths if str(v).strip())),
        "continuation_policy": continuation_policy,
        "risk": str(risk or "LOW").upper(),
        "implementation_notes": list(dict.fromkeys(str(v) for v in implementation_notes if str(v).strip())),
        "runtime_code_mutation_authorized": False,
        "runtime_git_write_authorized": False,
        "runtime_merge_authorized": False,
        "runtime_deploy_authorized": False,
        "manual_chatgpt_workspace_required": True,
        "autonomy_charter_id": CHARTER_ID,
    }
    payload["handoff_prompt"] = render_engineering_prompt(payload)
    payload["requirement_digest"] = _digest({k: v for k, v in payload.items() if k not in {"handoff_prompt", "requirement_digest"}})
    return payload


def render_engineering_prompt(requirement: Mapping[str, Any]) -> str:
    affected = "\n".join(f"- {value}" for value in requirement.get("affected_components") or []) or "- Determine from current code."
    tests = "\n".join(f"- {value}" for value in requirement.get("acceptance_tests") or [])
    paths = "\n".join(f"- {value}" for value in requirement.get("suggested_paths") or []) or "- Inspect current implementation before choosing paths."
    notes = "\n".join(f"- {value}" for value in requirement.get("implementation_notes") or []) or "- Preserve the existing architecture and safety boundaries."
    blocked = ", ".join(str(v) for v in requirement.get("blocked_research") or []) or "none"

    return f"""Implement ANEVUM engineering requirement {requirement.get('requirement_id')}: {requirement.get('title')}

Reason:
{requirement.get('reason')}

Capability required:
{requirement.get('capability_required')}

Blocked research:
{blocked}

Affected components:
{affected}

Suggested paths:
{paths}

Acceptance tests:
{tests}

Implementation notes:
{notes}

Operating constraints:
- Work from current canonical main; do not restart or redesign the architecture.
- Preserve all research-search history and untouched holdout boundaries.
- Do not weaken statistical, risk, execution, credential, or spending safeguards.
- Do not enable live trading or increase live risk.
- Treat this as manual software engineering. Runtime services have no authority to edit source, create/merge PRs, or deploy this change themselves.
- Add focused tests and run the relevant full suite.
- Return the PR, exact CI evidence, affected deployment IDs, and any remaining blocker to IREN for verification.
"""


def charter_snapshot() -> dict[str, Any]:
    payload = DEFAULT_CHARTER.snapshot()
    payload["charter_digest"] = _digest(payload)
    return payload
