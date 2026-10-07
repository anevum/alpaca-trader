"""ASC-008 profile-release review over resolved canonical artifacts.

This module evaluates evidence; it cannot mint trusted artifacts, change runtime
configuration, deploy, or grant broker authority. Resolvers belong to the existing
authenticated canonical evidence lane, not to a client-supplied pass flag.
"""
from decimal import Decimal
from collections.abc import Mapping

from app.adaptive_policy import ORDER, HARD_FALSE, PolicyLibrary, d, fingerprint
from app.market_fabric.contracts import utc
from .adaptive_shadow import aggregate_shadow_validation


REQUIRED_RUNTIME_GATES = (
    "automated_tests", "stream_equivalence", "live_shadow", "streams_reconnect",
    "rejection_accounting", "warm_start", "broker_reconciliation", "command_visuals",
    "command_latency", "kill_switches", "degraded_mode", "rollback_drill", "configuration_freeze",
)


def evaluate_profile_release(proposal, *, resolve_artifact, now):
    """Return review eligibility, never an activation. Missing evidence fails closed."""
    reasons, artifacts = [], {}
    if not isinstance(proposal, Mapping):
        proposal = {}
        reasons.append("INVALID_RELEASE_VALUES")
    if any(not isinstance(proposal.get(k, {}), Mapping) for k in ("profile_values", "hard_envelope", "artifact_ids")):
        proposal = {**proposal, "profile_values":{}, "hard_envelope":{}, "artifact_ids":{}}
        reasons.append("INVALID_RELEASE_VALUES")
    required = ("release_id", "profile_id", "profile_version", "source_strategy_version", "target_strategy_version",
        "configuration_fingerprint", "policy_library_fingerprint", "nostra_methodology_fingerprint",
        "profile_values", "hard_envelope", "rollback_profile", "rollback_version", "authorization_reference", "activation_at")
    if any(proposal.get(k) in (None, "", {}) for k in required):
        reasons.append("INCOMPLETE_RELEASE_LINEAGE")
    if proposal.get("profile_id") not in ORDER or proposal.get("rollback_profile") != "BASELINE_LOCKED":
        reasons.append("INVALID_PROFILE_OR_ROLLBACK")
    try:
        activation = utc(proposal["activation_at"])
        if activation < utc(now):
            reasons.append("ACTIVATION_TIME_IN_PAST")
        for key in ("allocation_multiplier", "gross_envelope_fraction"):
            if not 0 <= d(proposal["profile_values"][key]) <= 1:
                reasons.append("PROFILE_CAPITAL_ENVELOPE_VIOLATION")
        for key in HARD_FALSE:
            if key in proposal["profile_values"] and proposal["profile_values"][key] is not False:
                reasons.append("PROFILE_AUTHORITY_ENVELOPE_VIOLATION")
        for key in ("allow_margin", "allow_short", "allow_crypto", "may_exceed_existing_risk_limits"):
            if proposal["hard_envelope"].get(key) is not False:
                reasons.append("HARD_AUTHORITY_ENVELOPE_VIOLATION")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        reasons.append("INVALID_RELEASE_VALUES")
    # Releases describe bounded policy values, never new asset/session authority.
    allowed_values = set().union(*(profile.keys() for profile in PolicyLibrary.load().profiles.values())) | {
        "stop_pct", "target_pct", "max_hold_minutes", "reentry_cooldown_minutes",
        "max_spread_pct", "net_edge_hurdle_bps", "min_quality_score"}
    if set(proposal.get("profile_values", {})) - allowed_values:
        reasons.append("UNSUPPORTED_PROFILE_VALUE_OR_AUTHORITY")
    if any(proposal.get("hard_envelope", {}).get(k) is not False for k in (
        "options_broker_write_authority", "expanded_session_execution", "expanded_leverage")):
        reasons.append("FUTURE_AUTHORITY_ENVELOPE_VIOLATION")
    bindings = {k:proposal.get(k) for k in ("release_id", "profile_id", "profile_version", "source_strategy_version",
        "target_strategy_version", "configuration_fingerprint", "policy_library_fingerprint", "nostra_methodology_fingerprint")}
    bindings["profile_fingerprint"] = fingerprint(proposal.get("profile_values", {}))
    for kind in ("shadow", "velum", "holdout", "graen", "runtime", "authorization"):
        ref = proposal.get("artifact_ids", {}).get(kind)
        try:
            artifact = resolve_artifact(ref) if ref else None
        except (OSError, RuntimeError, ValueError):
            artifact = None
        if not isinstance(artifact, Mapping) or artifact.get("kind") != kind or artifact.get("artifact_id") != ref:
            reasons.append("MISSING_CANONICAL_"+kind.upper())
            continue
        # The resolver returns canonical rows with immutable lineage, not booleans
        # received from the public caller. No local fixture is used by the runtime.
        if artifact.get("bindings") != bindings or artifact.get("status") != "PASSED":
            reasons.append("ARTIFACT_LINEAGE_OR_STATUS_"+kind.upper())
            continue
        try:
            if not utc(artifact["frozen_at"]) <= utc(artifact["evaluated_at"]) <= utc(now):
                raise ValueError("artifact time")
            if not artifact.get("methodology_version") or artifact.get("execution_authority") is not False:
                raise ValueError("artifact authority")
        except (KeyError, ValueError, TypeError, ArithmeticError):
            reasons.append("INVALID_ARTIFACT_"+kind.upper())
            continue
        artifacts[kind] = artifact
    shadow = artifacts.get("shadow")
    validation = None
    if shadow:
        rows = shadow.get("session_results", [])
        sessions = [r.get("evaluation_session") for r in rows]
        if len(set(sessions)) != len(sessions):
            reasons.append("DUPLICATE_INDEPENDENT_SESSIONS")
        try:
            if any(type(r.get(k)) is not int or r[k] < 0 for r in rows for k in ("complete_candidates", "candidate_scope", "differential_decisions")):
                raise ValueError("invalid candidate counts")
            if any(r["complete_candidates"] > r["candidate_scope"] or r["differential_decisions"] > r["candidate_scope"] for r in rows):
                raise ValueError("invalid coverage")
            if any(not d(r["adaptive_minus_fixed_utility"]).is_finite() for r in rows):
                raise ValueError("invalid utility")
            if any(r.get("baseline_fingerprint") != proposal.get("configuration_fingerprint") for r in rows):
                raise ValueError("baseline mismatch")
            validation = aggregate_shadow_validation(rows)
            if not validation["validation_passed"] or validation["rejected_incompatible_results"]:
                reasons.extend(validation["reason_codes"])
        except (KeyError, ValueError, TypeError, ArithmeticError):
            reasons.append("INVALID_SHADOW_SESSION_EVIDENCE")
    holdout = artifacts.get("holdout")
    if holdout:
        try:
            sessions = holdout["independent_session_ids"]
            if len(set(sessions)) < 5 or len(set(sessions)) != len(sessions) or type(holdout["complete_candidates"]) is not int or holdout["complete_candidates"] < 50 or not Decimal(".95") <= d(holdout["coverage"]) <= 1:
                reasons.append("HOLDOUT_FLOOR_NOT_PASSED")
            if set(sessions) & set(r["evaluation_session"] for r in (shadow or {}).get("session_results", [])):
                reasons.append("HOLDOUT_OVERLAPS_FORWARD")
            if holdout.get("quarantine_accessed_during_development") is not False:
                reasons.append("HOLDOUT_QUARANTINE_BOUNDARY_VIOLATION")
        except (KeyError, TypeError, ValueError, ArithmeticError):
            reasons.append("INVALID_HOLDOUT_EVIDENCE")
    runtime = artifacts.get("runtime", {})
    for gate in REQUIRED_RUNTIME_GATES:
        if runtime.get("gates", {}).get(gate) != "PASSED":
            reasons.append("RUNTIME_GATE_"+gate.upper())
    if artifacts.get("velum", {}).get("no_lookahead_verified") is not True:
        reasons.append("VELUM_POINT_IN_TIME_NOT_VERIFIED")
    if artifacts.get("graen", {}).get("frozen_validation_passed") is not True:
        reasons.append("GRAEN_FROZEN_VALIDATION_NOT_PASSED")
    if artifacts.get("authorization", {}).get("reference") != proposal.get("authorization_reference"):
        reasons.append("AUTHORIZATION_RELEASE_MISMATCH")
    reasons = sorted(set(reasons))
    return {"contract_type":"policy_profile_release", "methodology_version":"asc008-profile-release-review-v1",
        "release_fingerprint":fingerprint(proposal), "eligible_for_operator_activation":not reasons,
        "reason_codes":reasons, "shadow_validation":validation,
        "artifact_ids":{k:v["artifact_id"] for k,v in artifacts.items()},
        "entry_authority":False, "broker_write_authority":False, "automatic_application_authorized":False,
        "provenance":"DERIVED", "source":"ASC008/canonical_artifacts", "observed_at":utc(now).isoformat()}
