"""Audit the *actual* event-time 15-minute candidate cohort.

The canonical report-read projection returns candidates with nested, measured
forward outcomes. This module provides a conservative summary, never replacing
event rows with guessed successes or filling missing prices with zero.
No broker or execution imports. No write capability.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any


AUDIT_VERSION = "rhen-candidate-cohort-audit-v1"
MIN_SCREEN_CANDIDATES = 20
MIN_OUTCOME_COVERAGE = Decimal("0.95")
MAX_SOURCE_CANDIDATES = 5000

# An internal compact RHEN Core projection preserves only a sample of
# rejected decisions. Valid 15-minute outcomes cannot certify that the
# underlying decision population is complete.
POPULATION_ATTESTATION_SCHEMA = "rhen-full-decision-population-v1"


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        item = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if item.tzinfo is None:
        return None
    return item.astimezone(timezone.utc)


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError, TypeError):
        return False


def audit_candidate_cohort(
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_strategy: str,
    population_attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return explicit completeness; no selection or strategy evaluation.

    Counts complete measured 15m outcomes with finite returns and at-decision
    identification. An existing candidate may have other outcome horizons;
    those do not count toward 15m coverage.
    """
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise ValueError("candidates must be a finite ordered array")
    if len(candidates) > MAX_SOURCE_CANDIDATES:
        raise ValueError("candidate cohort exceeds the canonical bounded export")
    identities: set[str] = set()
    complete = 0
    missing = 0
    invalid_identity = 0
    duplicate_identity = 0
    version_mismatch = 0
    missing_time = 0
    incomplete_or_error = 0
    invalid_forward = 0
    causal_time_error = 0
    incomplete_market_data = 0
    compacted_source_count = 0

    for item in candidates:
        if not isinstance(item, Mapping):
            invalid_identity += 1
            continue
        identity = str(item.get("candidate_id") or item.get("candidate_key") or "")
        if not identity:
            invalid_identity += 1
        elif identity in identities:
            duplicate_identity += 1
        else:
            identities.add(identity)
        if (
            not expected_strategy
            or str(item.get("strategy_version_id") or "") != expected_strategy
        ):
            version_mismatch += 1
        when = _moment(item.get("observed_at"))
        if when is None:
            missing_time += 1
        scan = item.get("scan_cycle")
        if not isinstance(scan, Mapping):
            incomplete_market_data += 1
        elif str(scan.get("data_status") or "").lower() not in {"ok", "complete"}:
            incomplete_market_data += 1
            if str(scan.get("data_status") or "").lower() == "compact_core_v3":
                compacted_source_count += 1

        # Core returns forward_outcomes={"15": {...}}. Foundation's older
        # export uses outcomes=[{"horizon_minutes": 15, ...}].
        native = item.get("outcomes")
        compacted = item.get("forward_outcomes")
        if isinstance(native, list):
            fifteen = [
                outcome for outcome in native
                if isinstance(outcome, Mapping)
                and str(outcome.get("horizon_minutes")) == "15"
            ]
        elif isinstance(compacted, Mapping):
            record = compacted.get("15")
            fifteen = [record] if isinstance(record, Mapping) else []
        else:
            fifteen = []
        if len(fifteen) != 1:
            missing += 1
            continue
        result = fifteen[0]
        if str(result.get("status") or "").lower() != "complete":
            incomplete_or_error += 1
            continue
        if not _finite(result.get("forward_return")):
            invalid_forward += 1
            continue
        observed_after = _moment(result.get("computed_at"))
        if when is None or observed_after is None or observed_after < when:
            causal_time_error += 1
            continue
        complete += 1

    total = len(candidates)
    coverage = Decimal(complete) / Decimal(total) if total else Decimal("0")
    reasons: list[str] = []
    if not total:
        reasons.append("NO_CANDIDATES_EXPORTED")
    if total == MAX_SOURCE_CANDIDATES:
        reasons.append("CANONICAL_EXPORT_AT_RECORD_LIMIT")
    if total < MIN_SCREEN_CANDIDATES:
        reasons.append("INSUFFICIENT_CANDIDATE_COUNT")
    if coverage < MIN_OUTCOME_COVERAGE:
        reasons.append("INSUFFICIENT_15M_FORWARD_COVERAGE")
    for count, code in (
        (invalid_identity, "CANDIDATE_IDS_INVALID"),
        (duplicate_identity, "CANDIDATE_IDS_DUPLICATED"),
        (version_mismatch, "CANDIDATE_STRATEGY_PROVENANCE_MISMATCH"),
        (missing_time, "CANDIDATE_DECISION_TIME_MISSING"),
        (incomplete_market_data, "CANDIDATE_MARKET_DATA_NOT_COMPLETE"),
        (invalid_forward, "FORWARD_RETURN_INVALID"),
        (causal_time_error, "FORWARD_OUTCOME_CAUSAL_TIME_INVALID"),
    ):
        if count:
            reasons.append(code)

    digest_material = [
        {
            "candidate_id": str(item.get("candidate_id") or item.get("candidate_key") or ""),
            "observed_at": item.get("observed_at"),
            "strategy_version_id": item.get("strategy_version_id"),
            "outcomes": [
                {
                    "horizon_minutes": outcome.get("horizon_minutes"),
                    "status": outcome.get("status"),
                    "forward_return": outcome.get("forward_return"),
                    "computed_at": outcome.get("computed_at"),
                }
                for outcome in (item.get("outcomes") if isinstance(item.get("outcomes"), list) else [])
                if isinstance(outcome, Mapping)
                and str(outcome.get("horizon_minutes")) == "15"
            ],
        }
        for item in candidates
        if isinstance(item, Mapping)
    ]
    return {
        "audit_version": AUDIT_VERSION,
        "state": "READY" if not reasons else "PARTIAL",
        "candidate_count": total,
        "complete_15m": complete,
        "missing_15m": missing,
        "incomplete_or_error_15m": incomplete_or_error,
        "invalid_forward_return_15m": invalid_forward,
        "coverage_15m": str(coverage),
        "coverage_required_15m": str(MIN_OUTCOME_COVERAGE),
        "identity_errors": invalid_identity,
        "duplicate_identities": duplicate_identity,
        "strategy_mismatch": version_mismatch,
        "missing_decision_time": missing_time,
        "causal_time_errors": causal_time_error,
        "market_data_incomplete": incomplete_market_data,
        "blocking_reasons": sorted(set(reasons)),
        "cohort_fingerprint": sha256(
            json.dumps(
                digest_material, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest(),
        "asof_universe_proven": False,
        "broker_fill_parity_proven": False,
        "validated_alpha": False,
    }
