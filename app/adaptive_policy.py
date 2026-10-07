"""Frozen policy primitives for disabled/shadow use; ACTIVE is release-blocked.

No imports from execution/broker, no environment mutation, no implicit promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import yaml

ORDER = ("NO_TRADE", "DEFENSIVE", "FAST_SCALP", "NORMAL", "TREND_EXTEND", "ASSERTIVE_TREND")
HARD_FALSE = ("allow_margin", "allow_short", "allow_crypto", "may_exceed_existing_risk_limits", "may_mutate_environment", "may_call_broker_directly")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def d(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("nonfinite policy/capital value")
    return result


@dataclass(frozen=True)
class PolicyLibrary:
    version: str
    fingerprint: str
    profiles: object

    @classmethod
    def load(cls, path=None):
        raw = yaml.safe_load(Path(path or Path(__file__).parent/"market_fabric/policy_library.yaml").read_text())
        rules = raw.get("hard_rules", {})
        if any(rules.get(k) is not False for k in HARD_FALSE) or rules.get("profile_switch_requires_approved_profile") is not True:
            raise ValueError("policy authority invariant violated")
        if raw.get("fallback") != "BASELINE_LOCKED" or set(raw.get("profiles", {})) != set(ORDER):
            raise ValueError("unknown policy library")
        profiles = raw["profiles"]
        for profile in profiles.values():
            for key in ("allocation_multiplier", "gross_envelope_fraction"):
                if not 0 <= d(profile[key]) <= 1:
                    raise ValueError("policy capital factor outside hard envelope")
            for key, value in profile.items():
                if key.endswith("_multiplier") and d(value) <= 0 and key != "allocation_multiplier":
                    raise ValueError("invalid profile multiplier")
        return cls(raw["schema_version"], fingerprint(raw), MappingProxyType({k: MappingProxyType(dict(v)) for k,v in profiles.items()}))


@dataclass(frozen=True)
class PolicyContext:
    observed_at: datetime
    feature_as_of: datetime
    session: str
    regime: str
    confidence: float
    unknown_probability: float
    familiarity: float
    evidence_healthy: bool
    strategy_healthy: bool
    reconciliation_safe: bool
    loss_cooldown: bool
    configuration_fingerprint: str
    library_fingerprint: str


@dataclass(frozen=True)
class EffectivePolicySnapshot:
    mode: str
    profile: str
    proposed_profile: str
    library_fingerprint: str
    configuration_fingerprint: str
    execution_values: object
    counterfactual_values: object
    reasons: tuple[str, ...]
    snapshot_fingerprint: str
    entry_authority: bool = False


class AdaptivePolicyController:
    def __init__(self, library, *, baseline, hard_limits, configuration_fingerprint,
                 min_dwell_seconds=900, confirmations=2):
        self.library = library
        self.baseline = dict(baseline)
        self.hard_limits = dict(hard_limits)
        self.configuration_fingerprint = configuration_fingerprint
        self.profile = "BASELINE_LOCKED"
        self.since = None
        self.pending = None
        self.confirmed = 0
        self.last_observation = None
        self.min_dwell_seconds, self.confirmations = min_dwell_seconds, confirmations

    def resolve(self, profile):
        result = dict(self.baseline)
        if profile == "BASELINE_LOCKED":
            return result
        p = self.library.profiles[profile]
        for key, mult in (("stop_pct", "stop_multiplier"), ("target_pct", "target_multiplier"),
                          ("max_hold_minutes", "hold_multiplier"), ("reentry_cooldown_minutes", "reentry_cooldown_multiplier"),
                          ("max_spread_pct", "spread_limit_multiplier"), ("net_edge_hurdle_bps", "net_edge_hurdle_multiplier")):
            if key in result and mult in p:
                result[key] = str(d(result[key])*d(p[mult]))
        if "min_quality_score" in result:
            result["min_quality_score"] = str(max(d(p.get("minimum_quality_score_floor", 0)), d(result["min_quality_score"])+d(p.get("quality_score_delta", 0))))
        # Hard ceilings and minimum edge floor cannot be relaxed by any profile.
        for key, cap in self.hard_limits.items():
            if key in result and key != "minimum_net_edge_hurdle_bps":
                result[key] = str(min(d(result[key]), d(cap)))
        if "net_edge_hurdle_bps" in result:
            result["net_edge_hurdle_bps"] = str(max(d(result["net_edge_hurdle_bps"]), d(self.hard_limits.get("minimum_net_edge_hurdle_bps", self.baseline["net_edge_hurdle_bps"]))))
        result.update(allocation_multiplier=str(p["allocation_multiplier"]), gross_envelope_fraction=str(p["gross_envelope_fraction"]),
                      allow_new_entries=p["allow_new_entries"], allow_margin=False)
        return result

    def observe(self, context: PolicyContext, *, enabled=False, mode="shadow", approved_profiles=None):
        if mode not in {"disabled", "shadow"}:
            raise ValueError("ACTIVE requires a later protected profile release; unavailable pre-crossover")
        reasons = []
        selected = "BASELINE_LOCKED"
        actual_mode = "SHADOW" if enabled and mode == "shadow" else "DISABLED"
        if actual_mode == "SHADOW":
            if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (context.confidence, context.unknown_probability, context.familiarity)):
                raise ValueError("invalid regime probability/confidence")
            age = (context.observed_at-context.feature_as_of).total_seconds()
            safety = not context.evidence_healthy or not context.strategy_healthy or not context.reconciliation_safe or context.loss_cooldown
            invalid = context.library_fingerprint != self.library.fingerprint or context.configuration_fingerprint != self.configuration_fingerprint or not 0 <= age <= 600
            if invalid:
                reasons.append("INVALID_OR_STALE_LINEAGE")
                actual_mode = "FALLBACK"
            elif safety:
                selected = "NO_TRADE"
                reasons.append("SAFETY_DOWNGRADE")
            elif context.session != "REGULAR" or context.regime == "UNKNOWN" or context.unknown_probability >= .25 or context.familiarity < .5:
                reasons.append("SESSION_OR_UNCERTAINTY_VETO")
            else:
                selected = {"TREND_EXPANSION": "ASSERTIVE_TREND", "BROAD_ADVANCE": "ASSERTIVE_TREND",
                            "LATE_SESSION_EXPANSION": "TREND_EXTEND", "TREND_DECAY": "NORMAL", "ROTATION": "NORMAL",
                            "MIDDAY_COMPRESSION": "DEFENSIVE", "HIGH_VOLATILITY": "DEFENSIVE", "CHOP": "DEFENSIVE",
                            "BROAD_DECLINE": "NO_TRADE"}.get(context.regime, "BASELINE_LOCKED")
            if approved_profiles is not None:
                approved = frozenset(str(p) for p in approved_profiles if str(p) in ORDER)
                # NO_TRADE is an unconditional safety reduction. Any other
                # counterfactual profile requires exact trusted release lineage.
                if selected not in {"BASELINE_LOCKED","NO_TRADE"} and selected not in approved:
                    selected = "BASELINE_LOCKED"
                    reasons.append("PROFILE_NOT_APPROVED")
            fresh_observation = self.last_observation is None or context.feature_as_of > self.last_observation
            current_rank = ORDER.index(self.profile) if self.profile in ORDER else ORDER.index("NORMAL")
            rank = ORDER.index(selected) if selected in ORDER else ORDER.index("NORMAL")
            upgrade = rank > current_rank
            dwell = self.since is None or (context.observed_at-self.since).total_seconds() >= self.min_dwell_seconds
            if selected != self.profile and selected != "BASELINE_LOCKED" and not safety:
                if fresh_observation:
                    self.confirmed = min(self.confirmations, self.confirmed+1) if self.pending == selected else 1
                    self.pending = selected
                if self.confirmed < self.confirmations or (upgrade and (not dwell or context.confidence < .75)):
                    selected = self.profile
                    reasons.append("HYSTERESIS_OR_DWELL")
            if selected != self.profile:
                self.since = context.observed_at
                self.profile = selected
                self.pending, self.confirmed = None, 0
            self.last_observation = context.feature_as_of if fresh_observation else self.last_observation
        values = self.resolve(selected)
        identity = {"mode": actual_mode, "proposed_profile": selected, "at": context.observed_at.isoformat(),
                    "library": self.library.fingerprint, "configuration": context.configuration_fingerprint, "values": values}
        return EffectivePolicySnapshot(actual_mode, "BASELINE_LOCKED", selected, self.library.fingerprint,
                                       context.configuration_fingerprint, MappingProxyType(dict(self.baseline)),
                                       MappingProxyType(values), tuple(reasons), fingerprint(identity))
