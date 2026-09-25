from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .strategy import Signal


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


@dataclass(frozen=True)
class Strategy004EntryDecision:
    allowed: bool
    lane: str
    reason: str
    details: dict[str, Any]


def strategy_004_entry_gate(signal: Signal) -> Strategy004EntryDecision:
    """Research-only entry-shape gate derived from the 2026-09-25 live sample.

    Strategy 004 deliberately has two accepted structures:

    1. impulse: exceptional, fresh momentum with strong VWAP separation,
       relative volume, quality, and independent confirmations;
    2. continuation: established VWAP trend with moderate momentum and a small
       incremental latest-bar move, avoiding stale/low-participation chase
       entries.

    This function is intentionally not wired into live execution. It is used by
    the offline strategy laboratory until out-of-sample and shadow validation
    justify promotion.
    """

    metadata = dict(signal.metadata or {})
    market_quality = dict(metadata.get("market_quality") or {})

    quality = _d(metadata.get("quality_score"))
    momentum = _d(metadata.get("momentum_pct"))
    vwap_edge = _d(metadata.get("vwap_edge_pct"))
    relative_volume = _d(metadata.get("relative_volume_ratio"))
    confirmations = int(metadata.get("confirmation_passes", 0) or 0)
    bar_age_seconds = _d(market_quality.get("bar_age_seconds"))

    current_close = _d(metadata.get("current_close"))
    previous_close = _d(metadata.get("previous_close"))
    if current_close <= 0 or previous_close <= 0:
        return Strategy004EntryDecision(
            allowed=False,
            lane="reject",
            reason="strategy 004 requires current and previous completed-bar closes",
            details={},
        )

    last_bar_return = (current_close / previous_close) - Decimal("1")

    impulse = (
        quality >= Decimal("88")
        and vwap_edge >= Decimal("0.0075")
        and relative_volume >= Decimal("1.5")
        and last_bar_return >= Decimal("0.005")
        and confirmations >= 2
        and bar_age_seconds <= Decimal("15")
    )

    continuation = (
        vwap_edge >= Decimal("0.0065")
        and Decimal("0.0012") <= momentum <= Decimal("0.0045")
        and Decimal("0") <= last_bar_return <= Decimal("0.0015")
        and confirmations >= 2
        and bar_age_seconds <= Decimal("30")
        and (
            relative_volume >= Decimal("0.3")
            or bar_age_seconds <= Decimal("10")
        )
    )

    details = {
        "quality_score": str(quality),
        "momentum_pct": str(momentum),
        "vwap_edge_pct": str(vwap_edge),
        "relative_volume_ratio": str(relative_volume),
        "confirmation_passes": confirmations,
        "bar_age_seconds": str(bar_age_seconds),
        "last_bar_return_pct": str(last_bar_return),
        "impulse": impulse,
        "continuation": continuation,
    }

    if impulse:
        return Strategy004EntryDecision(
            allowed=True,
            lane="impulse",
            reason="strategy 004 impulse structure accepted",
            details=details,
        )
    if continuation:
        return Strategy004EntryDecision(
            allowed=True,
            lane="continuation",
            reason="strategy 004 continuation structure accepted",
            details=details,
        )
    return Strategy004EntryDecision(
        allowed=False,
        lane="reject",
        reason="strategy 004 rejected nonvalidated entry structure",
        details=details,
    )
