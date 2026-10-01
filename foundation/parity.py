from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def event_digest(event: dict[str, Any]) -> str:
    normalized = {
        "event_key": event.get("event_key"),
        "run_id": event.get("run_id"),
        "strategy_version_id": event.get("strategy_version_id"),
        "event_type": event.get("event_type"),
        "occurred_at": event.get("occurred_at"),
        "symbol": event.get("symbol"),
        "correlation_id": event.get("correlation_id"),
        "source": event.get("source"),
        "payload": event.get("payload") or {},
    }
    return hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParityResult:
    source_count: int
    target_count: int
    matched_count: int
    missing_in_target: list[str]
    unexpected_in_target: list[str]
    digest_mismatches: list[str]
    source_type_counts: dict[str, int]
    target_type_counts: dict[str, int]

    @property
    def complete(self) -> bool:
        return (
            not self.missing_in_target
            and not self.unexpected_in_target
            and not self.digest_mismatches
        )

    @property
    def match_ratio(self) -> float:
        if self.source_count == 0:
            return 1.0 if self.target_count == 0 else 0.0
        return self.matched_count / self.source_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "target_count": self.target_count,
            "matched_count": self.matched_count,
            "match_ratio": self.match_ratio,
            "complete": self.complete,
            "missing_in_target": self.missing_in_target,
            "unexpected_in_target": self.unexpected_in_target,
            "digest_mismatches": self.digest_mismatches,
            "source_type_counts": self.source_type_counts,
            "target_type_counts": self.target_type_counts,
        }


def compare_event_sets(
    source_events: Iterable[dict[str, Any]],
    target_events: Iterable[dict[str, Any]],
) -> ParityResult:
    source = {str(e["event_key"]): e for e in source_events}
    target = {str(e["event_key"]): e for e in target_events}

    source_keys = set(source)
    target_keys = set(target)
    missing = sorted(source_keys - target_keys)
    unexpected = sorted(target_keys - source_keys)

    mismatches: list[str] = []
    matched = 0
    for key in sorted(source_keys & target_keys):
        if event_digest(source[key]) == event_digest(target[key]):
            matched += 1
        else:
            mismatches.append(key)

    source_types = Counter(str(e.get("event_type") or "") for e in source.values())
    target_types = Counter(str(e.get("event_type") or "") for e in target.values())

    return ParityResult(
        source_count=len(source),
        target_count=len(target),
        matched_count=matched,
        missing_in_target=missing,
        unexpected_in_target=unexpected,
        digest_mismatches=mismatches,
        source_type_counts=dict(sorted(source_types.items())),
        target_type_counts=dict(sorted(target_types.items())),
    )


def promotion_gate(
    result: ParityResult,
    *,
    minimum_events: int = 100,
    minimum_match_ratio: float = 1.0,
) -> dict[str, Any]:
    reasons: list[str] = []

    if result.source_count < minimum_events:
        reasons.append(
            f"insufficient_sample:{result.source_count}<{minimum_events}"
        )
    if result.match_ratio < minimum_match_ratio:
        reasons.append(
            f"match_ratio_below_threshold:{result.match_ratio:.6f}<{minimum_match_ratio:.6f}"
        )
    if result.missing_in_target:
        reasons.append(f"missing_in_target:{len(result.missing_in_target)}")
    if result.unexpected_in_target:
        reasons.append(f"unexpected_in_target:{len(result.unexpected_in_target)}")
    if result.digest_mismatches:
        reasons.append(f"digest_mismatches:{len(result.digest_mismatches)}")

    return {
        "gate": "PASS" if not reasons else "FAIL",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "minimum_events": minimum_events,
        "minimum_match_ratio": minimum_match_ratio,
        "result": result.as_dict(),
        "reasons": reasons,
    }
