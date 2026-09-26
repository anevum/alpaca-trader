from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .strategy import Signal


MAX_MOMENTUM_PCT = Decimal("0.0029")
MAX_TREND_PERSISTENCE = Decimal("0.625")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


@dataclass(frozen=True)
class Strategy004CandidateCDecision:
    allowed: bool
    lane: str
    reason: str
    details: dict[str, Any]


def strategy_004_candidate_c_gate(
    signal: Signal,
) -> Strategy004CandidateCDecision:
    """Frozen Candidate C: controlled continuation without mature-momentum chase.

    The base production signal must already have passed the ordinary trend,
    momentum, VWAP, confirmation, regime, market-quality and quality-score
    gates. Candidate C adds only an upper bound on recent momentum and recent
    one-directional persistence.

    Thresholds were frozen from the two development windows before reserved
    holdout testing. This gate is research-only.
    """
    metadata = dict(signal.metadata or {})
    if (
        "momentum_pct" not in metadata
        or "trend_persistence" not in metadata
    ):
        return Strategy004CandidateCDecision(
            allowed=False,
            lane="reject",
            reason="candidate C requires momentum and trend-persistence metadata",
            details={},
        )

    momentum = _d(metadata.get("momentum_pct"))
    persistence = _d(metadata.get("trend_persistence"))

    momentum_ok = Decimal("0") < momentum <= MAX_MOMENTUM_PCT
    persistence_ok = (
        Decimal("0") <= persistence <= MAX_TREND_PERSISTENCE
    )
    allowed = momentum_ok and persistence_ok

    details = {
        "momentum_pct": str(momentum),
        "max_momentum_pct": str(MAX_MOMENTUM_PCT),
        "trend_persistence": str(persistence),
        "max_trend_persistence": str(MAX_TREND_PERSISTENCE),
        "momentum_ok": momentum_ok,
        "persistence_ok": persistence_ok,
    }

    if allowed:
        return Strategy004CandidateCDecision(
            allowed=True,
            lane="controlled_continuation",
            reason="candidate C controlled-continuation structure accepted",
            details=details,
        )
    return Strategy004CandidateCDecision(
        allowed=False,
        lane="reject",
        reason="candidate C rejected mature or overly persistent momentum",
        details=details,
    )
