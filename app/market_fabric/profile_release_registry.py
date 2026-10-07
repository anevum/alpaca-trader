"""Trusted ASC-008 profile-release adapter for the isolated RHEN 4.4 observer.

The adapter consumes only the authenticated canonical research evidence document.
It cannot write research decisions, deploy, change configuration, call a broker, or
grant ACTIVE execution authority. A passing release only makes its profile eligible
for counterfactual SHADOW selection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.adaptive_policy import fingerprint
from app.research_agent.policy_profile_release import evaluate_profile_release
from .contracts import utc


AUTHORIZED_ACTION = "authorize_policy_profile_release"
METHODOLOGY_VERSION = "asc008-trusted-profile-registry-v1"


def _rows(value):
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def _bindings(proposal):
    keys = (
        "release_id","profile_id","profile_version","source_strategy_version",
        "target_strategy_version","configuration_fingerprint",
        "policy_library_fingerprint","nostra_methodology_fingerprint",
    )
    result = {key:proposal.get(key) for key in keys}
    result["profile_fingerprint"] = fingerprint(_mapping(proposal.get("profile_values")))
    return result


class TrustedProfileReleaseRegistry:
    def __init__(self, *, library_fingerprint):
        self.library_fingerprint = str(library_fingerprint)

    @staticmethod
    def _collect(evidence):
        proposals, artifacts = [], {}

        def add_proposal(value):
            if isinstance(value, Mapping):
                proposals.append(dict(value))

        def add_artifacts(value):
            if isinstance(value, Mapping):
                values = value.values()
            else:
                values = _rows(value)
            for artifact in values:
                if not isinstance(artifact, Mapping):
                    continue
                artifact_id = str(artifact.get("artifact_id") or "").strip()
                if artifact_id and artifact_id not in artifacts:
                    artifacts[artifact_id] = dict(artifact)

        for proposal in _rows(evidence.get("profile_release_proposals")):
            add_proposal(proposal)
        add_artifacts(evidence.get("profile_release_artifacts"))

        for run in _rows(evidence.get("agent_runs")):
            if not isinstance(run, Mapping) or str(run.get("status") or "").upper() != "COMPLETED":
                continue
            output = _mapping(run.get("output_artifact"))
            add_proposal(output.get("profile_release_proposal"))
            for proposal in _rows(output.get("profile_release_proposals")):
                add_proposal(proposal)
            add_artifacts(output.get("profile_release_artifacts"))

        return proposals, artifacts

    @staticmethod
    def _authorization_decisions(evidence, proposal):
        matches = []
        expected = {
            key:str(proposal.get(key) or "")
            for key in (
                "release_id","profile_id","profile_version","source_strategy_version",
                "target_strategy_version","configuration_fingerprint",
                "policy_library_fingerprint","authorization_reference",
            )
        }
        proposal_fp = fingerprint(proposal)
        for decision in _rows(evidence.get("research_decisions")):
            if not isinstance(decision, Mapping):
                continue
            if str(decision.get("status") or "").lower() != "final":
                continue
            if decision.get("superseded_by_decision_id") is not None:
                continue
            if str(decision.get("decision_type") or "").lower() != "research_authorization":
                continue
            payload = _mapping(decision.get("evidence"))
            if payload.get("revoked") is True or payload.get("authorized_action") != AUTHORIZED_ACTION:
                continue
            if any(str(payload.get(key) or "") != value for key,value in expected.items()):
                continue
            supplied_fp = str(payload.get("release_fingerprint") or "")
            if supplied_fp and supplied_fp != proposal_fp:
                continue
            authorized_by = str(payload.get("authorized_by") or "").strip()
            authorized_at = str(payload.get("authorized_at") or "").strip()
            reference = str(decision.get("decision_key") or decision.get("decision_id") or "").strip()
            if not (authorized_by and authorized_at and reference):
                continue
            try:
                utc(authorized_at)
            except (TypeError, ValueError):
                continue
            matches.append({
                "decision_reference":reference,
                "authorized_by":authorized_by,
                "authorized_at":authorized_at,
                "authorization_reference":expected["authorization_reference"],
            })
        return matches

    def review(self, evidence, *, now, expected_configuration_fingerprint, source_strategy_version):
        now = utc(now)
        evidence = _mapping(evidence)
        proposals, canonical_artifacts = self._collect(evidence)
        results, approved = [], set()
        seen_release_ids = set()
        duplicate_release_ids = {
            str(p.get("release_id") or "")
            for p in proposals
            if str(p.get("release_id") or "") and
               sum(1 for other in proposals if str(other.get("release_id") or "") == str(p.get("release_id") or "")) > 1
        }

        for proposal in proposals:
            release_id = str(proposal.get("release_id") or "").strip()
            reasons = []
            if not release_id or release_id in duplicate_release_ids or release_id in seen_release_ids:
                reasons.append("DUPLICATE_OR_MISSING_RELEASE_ID")
            seen_release_ids.add(release_id)
            if proposal.get("configuration_fingerprint") != expected_configuration_fingerprint:
                reasons.append("CURRENT_CONFIGURATION_FINGERPRINT_MISMATCH")
            if proposal.get("policy_library_fingerprint") != self.library_fingerprint:
                reasons.append("CURRENT_POLICY_LIBRARY_FINGERPRINT_MISMATCH")
            if proposal.get("source_strategy_version") != source_strategy_version:
                reasons.append("CURRENT_SOURCE_STRATEGY_MISMATCH")

            auth = self._authorization_decisions(evidence, proposal)
            if len(auth) != 1:
                reasons.append("EXACT_OPERATOR_AUTHORIZATION_REQUIRED")
            artifacts = dict(canonical_artifacts)
            artifact_ids = _mapping(proposal.get("artifact_ids"))
            authorization_id = str(artifact_ids.get("authorization") or "").strip()
            if len(auth) == 1 and authorization_id:
                binding = _bindings(proposal)
                authorized_at = auth[0]["authorized_at"]
                artifacts[authorization_id] = {
                    "artifact_id":authorization_id,
                    "kind":"authorization",
                    "status":"PASSED",
                    "bindings":binding,
                    "frozen_at":authorized_at,
                    "evaluated_at":authorized_at,
                    "methodology_version":"asc008-operator-authorization-v1",
                    "execution_authority":False,
                    "reference":auth[0]["authorization_reference"],
                    "decision_reference":auth[0]["decision_reference"],
                    "authorized_by":auth[0]["authorized_by"],
                }

            review = None
            if not reasons:
                try:
                    # ASC-008 is a review-time contract. Evaluate against the exact
                    # authorization time so a valid, already-reviewed release does
                    # not become invalid merely because its future activation time
                    # later passes. Current configuration/library lineage is checked
                    # independently above on every runtime read.
                    review_at = utc(auth[0]["authorized_at"])
                    review = evaluate_profile_release(
                        proposal, resolve_artifact=artifacts.get, now=review_at)
                except (TypeError, ValueError, ArithmeticError):
                    reasons.append("PROFILE_RELEASE_REVIEW_ERROR")
            if review is not None and review.get("eligible_for_operator_activation") is not True:
                reasons.extend(review.get("reason_codes") or ())
            if review is not None and not reasons:
                approved.add(str(proposal.get("profile_id")))
            results.append({
                "release_id":release_id or None,
                "profile_id":proposal.get("profile_id"),
                "profile_version":proposal.get("profile_version"),
                "eligible_for_shadow_selection":review is not None and not reasons,
                "reason_codes":sorted(set(reasons)),
                "release_fingerprint":fingerprint(proposal) if proposal else None,
                "asc008_review":review,
                "entry_authority":False,
                "broker_write_authority":False,
                "active_mode_authorized":False,
            })

        approved_profiles=tuple(sorted(approved))
        if approved_profiles:
            quality, reason = "LIVE", "TRUSTED_PROFILE_RELEASES_AVAILABLE"
        elif proposals:
            quality, reason = "BLOCKED", "NO_PROFILE_RELEASE_PASSED_ASC008_AND_OPERATOR_AUTHORIZATION"
        else:
            quality, reason = "UNAVAILABLE", "NO_CANONICAL_PROFILE_RELEASE"
        return {
            "quality_state":quality,
            "reason":reason,
            "methodology_version":METHODOLOGY_VERSION,
            "proposal_count":len(proposals),
            "approved_profiles":list(approved_profiles),
            "evidence_healthy":bool(approved_profiles),
            "reviews":results[:20],
            "observed_at":now.isoformat(),
            "source":"RHEN/canonical_research_gateway",
            "provenance":"DERIVED",
            "entry_authority":False,
            "broker_write_authority":False,
            "active_mode_authorized":False,
            "automatic_application_authorized":False,
        }
