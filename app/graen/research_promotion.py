"""Durable, bounded research-code handoff in the GRAEN control loop."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from graen.engineering import (
    CANONICAL_BRANCH, MAX_ATTEMPTS, TRIGGERS, IntegrityError, ProtectedChange,
    compile_bundle, digest, validate_spec, verify_bundle, verify_ci, verify_exposure, stamp,
)
from .research_repository import GitHubRepository, AuthorizationDenied, TransportUnavailable


class PromotionStore:
    def __init__(self, gateway):
        self.gateway = gateway

    async def claim(self, problem_id, owner):
        return await self.gateway._request("POST", {
            "action": "research_promotion_claim", "problem_id": problem_id, "owner": owner,
        })

    async def save(self, problem_id, owner, revision, state):
        return await self.gateway._request("POST", {
            "action": "research_promotion_save", "problem_id": problem_id,
            "owner": owner, "expected_revision": revision, "state": state,
        })


class ResearchPromotion:
    def __init__(self, gateway, repository=None):
        self.store = PromotionStore(gateway)
        self.repository = repository or GitHubRepository()

    async def tick(self, problem_id):
        owner = str(uuid.uuid4())
        claim = await self.store.claim(problem_id, owner)
        if not claim.get("claimed"):
            return {"state": "NOT_CLAIMED"}
        revision = claim["revision"]
        state = dict(claim.get("state") or {})
        state.setdefault("phase", "FREEZE")
        try:
            spec = claim.get("prespec")
            if not isinstance(spec, dict):
                raise IntegrityError("complete_frozen_prespec_required")
            spec_hash = validate_spec(spec)
            verify_exposure(spec, claim.get("exposure") or {})
            if state.get("spec_hash") and state["spec_hash"] != spec_hash:
                raise IntegrityError("frozen_prespec_mutation")
            phase = state["phase"]
            if phase == "FREEZE":
                # save is an atomic durable artifact + state transaction. Branch
                # creation happens on a later tick, after read-back verification.
                state.update({
                    "phase": "BRANCH", "prespec": spec, "spec_hash": spec_hash,
                    "epoch": spec["epoch"], "attempt": 0, "history": [],
                    "research_only": True, "execution_authority": False,
                })
            else:
                if not claim.get("prespec_artifact_id"):
                    raise IntegrityError("durable_prespec_missing")
                if not self.repository.configured:
                    raise AuthorizationDenied("runtime_github_authorization_not_configured")
                files = compile_bundle(spec)
                verify_bundle(spec, files)
                if phase == "BRANCH":
                    if state["attempt"] >= MAX_ATTEMPTS:
                        raise IntegrityError("bounded_engineering_attempts_exhausted")
                    base = await self.repository.head()
                    branch = "research-code/" + spec["hypothesis_id"].lower() + "-" + spec_hash[:12] + "-" + base[:12]
                    await self.repository.create_branch(branch, base)
                    state.update(phase="WRITE", base_sha=base, branch=branch, attempt=state["attempt"] + 1)
                elif phase == "WRITE":
                    # Canonical movement creates a fresh branch; never rebase or
                    # force-write over another researcher's branch.
                    if await self.repository.head() != state["base_sha"]:
                        state["history"].append({key: state.get(key) for key in ("branch", "base_sha", "head_sha", "pr")})
                        state["phase"] = "BRANCH"
                    else:
                        head = await self.repository.head(state["branch"])
                        if head != state["base_sha"]:
                            if not await self.repository.bundle_matches(head, files):
                                raise IntegrityError("unexpected_branch_contents")
                            transport = "reconciled"
                        else:
                            head, transport = await self.repository.write(
                                state["branch"], state["base_sha"], files,
                                "Research: compile " + spec["hypothesis_id"],
                            )
                        state.update(phase="PR", head_sha=head, write_transport=transport)
                elif phase == "PR":
                    number = await self.repository.open_pr(
                        state["branch"], "Research: " + spec["hypothesis_id"],
                        "Frozen specification: " + spec_hash + "\nEpoch: " + spec["epoch"] +
                        "\nResearch-only bounded compiler output. No execution authority.",
                    )
                    state.update(phase="CI", pr=number)
                elif phase == "CI":
                    info = await self.repository.pr(state["pr"])
                    if info["head"]["sha"] != state["head_sha"] or info["base"]["ref"] != CANONICAL_BRANCH:
                        raise IntegrityError("pull_request_identity_changed")
                    if info.get("merged"):
                        # Recover a merge whose response/state write was lost.
                        state.update(phase="DEPLOY", merge_sha=info["merge_commit_sha"])
                    elif info.get("state") != "open":
                        raise IntegrityError("pull_request_closed_without_merge")
                    elif await self.repository.head() != state["base_sha"]:
                        state["history"].append({key: state.get(key) for key in ("branch", "base_sha", "head_sha", "pr")})
                        state["phase"] = "BRANCH"
                    else:
                        diff = await self.repository.diff(state["pr"])
                        if {row["filename"] for row in diff} != set(files) or any(
                            row.get("status") not in {"added", "modified"} or row.get("previous_filename")
                            for row in diff
                        ):
                            raise ProtectedChange("protected_or_unexpected_pull_request_diff")
                        if not await self.repository.bundle_matches(state["head_sha"], files):
                            raise ProtectedChange("pull_request_bytes_not_compiler_output")
                        runs = await self.repository.ci(state["head_sha"])
                        if verify_ci(runs, head=state["head_sha"], pr_number=state["pr"]):
                            state["ci"] = [{
                                "id": row["id"], "head_sha": row["head_sha"],
                                "run_attempt": row.get("run_attempt", 1),
                                "conclusion": row["conclusion"],
                            } for row in runs if row["head_sha"] == state["head_sha"]]
                            result = await self.repository.merge(state["pr"], state["head_sha"])
                            if result.get("merged") is not True or not result.get("sha"):
                                raise IntegrityError("merge_not_verified")
                            state.update(phase="DEPLOY", merge_sha=result["sha"])
                        elif any(row.get("conclusion") == "failure" for row in runs):
                            # Exact compiler output cannot safely repair arbitrary
                            # compiler/CI defects or modify methodology parameters.
                            # The failure and unchanged prespec remain durable.
                            state["blocked_reason"] = "trusted_compiler_or_ci_repair_required"
                elif phase == "DEPLOY":
                    heartbeat = claim.get("executor_heartbeat") or {}
                    fresh = heartbeat.get("heartbeat_at") and 0 <= (
                        datetime.now(timezone.utc) - stamp(heartbeat["heartbeat_at"])
                    ).total_seconds() <= 180
                    if (
                        heartbeat.get("source_commit") == state["merge_sha"]
                        and heartbeat.get("deployment_id") and fresh
                        and not heartbeat.get("last_error")
                    ):
                        state.update(
                            phase="RESUME", deployment_id=heartbeat["deployment_id"],
                            executor_heartbeat_at=heartbeat["heartbeat_at"],
                        )
                elif phase == "RESUME":
                    # The gateway atomically verifies no competing active run,
                    # writes provenance and queues only frozen DEVELOPMENT.
                    state["phase"] = "COMPLETE"
                    state["resume_stage"] = "CRYPTO_COMPILED_DEVELOPMENT"
                elif phase != "COMPLETE":
                    raise IntegrityError("unknown_promotion_phase")
            if state.get("blocked_reason") == "runtime_github_authorization_not_configured" and self.repository.configured:
                state.pop("blocked_reason", None)
        except AuthorizationDenied as exc:
            state["blocked_reason"] = str(exc)
        except TransportUnavailable as exc:
            # Retried on the next bounded tick, without changing specification.
            state["blocked_reason"] = "transient_repository_transport:" + str(exc)
        except (IntegrityError, KeyError, ValueError) as exc:
            state["blocked_reason"] = str(exc)
            state["protected_decision_required"] = isinstance(exc, ProtectedChange)
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
        summary = latest.get(str(problem.get("problem_id")), {})
        triggered = any(summary.get(key) in TRIGGERS for key in ("state", "decision", "next_action"))
        if promotion.get("phase") == "COMPLETE":
            continue
        if triggered or promotion or metadata.get("research_stage") == "RESEARCH_IMPLEMENTATION_REQUIRED":
            yield str(problem["problem_id"])
