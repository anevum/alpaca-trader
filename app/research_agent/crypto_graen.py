from __future__ import annotations

from collections.abc import Mapping
from typing import Any


METHODOLOGY_VERSION = "graen-crypto-promotion-v1"
INITIAL_CANDIDATE_FLOOR = 1000
INITIAL_EXECUTION_FLOOR = 100
REQUIRED_METRICS = (
    "net_expectancy_after_costs",
    "brier_score",
    "log_loss",
    "calibration_intercept",
    "calibration_slope",
    "discrimination",
    "max_drawdown",
    "tail_loss",
    "mfe",
    "mae",
    "slippage",
    "spread_sensitivity",
    "regime_stability",
    "time_of_week_stability",
)


def assess_crypto_promotion(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    packet = dict(evidence or {})
    resolved = int(packet.get("resolved_candidate_predictions") or 0)
    round_trips = int(packet.get("paper_round_trips") or 0)
    hours = {int(x) for x in packet.get("utc_hours_covered") or [] if str(x).isdigit()}
    weekdays = {int(x) for x in packet.get("weekdays_covered") or [] if str(x).isdigit()}
    volatility_regimes = set(packet.get("volatility_regimes") or [])
    liquidity_regimes = set(packet.get("liquidity_regimes") or [])
    pairs = {str(x).upper() for x in packet.get("pairs_covered") or []}
    secondary_pairs = {p for p in pairs if p not in {"BTC/USD", "ETH/USD"}}
    metrics = dict(packet.get("metrics") or {})
    missing_metrics = [name for name in REQUIRED_METRICS if metrics.get(name) is None]

    gates = {
        "candidate_floor": resolved >= INITIAL_CANDIDATE_FLOOR,
        "execution_floor": round_trips >= INITIAL_EXECUTION_FLOOR,
        "all_hours": hours.issuperset(range(24)),
        "all_weekdays": weekdays.issuperset(range(7)),
        "multiple_volatility_regimes": len(volatility_regimes) >= 2,
        "multiple_liquidity_regimes": len(liquidity_regimes) >= 2,
        "major_pairs": {"BTC/USD", "ETH/USD"}.issubset(pairs),
        "secondary_pairs": bool(secondary_pairs),
        "required_metrics": not missing_metrics,
        "net_expectancy_positive_after_high_costs": bool(packet.get("net_expectancy_positive_after_high_costs") is True),
        "walk_forward_passed": bool(packet.get("walk_forward_passed") is True),
        "holdout_passed": bool(packet.get("holdout_passed") is True),
        "dependence_adjusted": bool(packet.get("dependence_adjusted") is True),
        "multiplicity_adjusted": bool(packet.get("multiplicity_adjusted") is True),
        "no_lookahead_verified": bool(packet.get("no_lookahead_verified") is True),
    }
    failed = [name for name, passed in gates.items() if not passed]
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "market_lane": "crypto",
        "status": "PROMOTION_READY" if not failed else "GATED",
        "promotion_ready": not failed,
        "gates": gates,
        "reason_codes": [name.upper() for name in failed],
        "evidence": {
            "resolved_candidate_predictions": resolved,
            "paper_round_trips": round_trips,
            "utc_hours_covered": sorted(hours),
            "weekdays_covered": sorted(weekdays),
            "volatility_regimes": sorted(volatility_regimes),
            "liquidity_regimes": sorted(liquidity_regimes),
            "pairs_covered": sorted(pairs),
            "missing_metrics": missing_metrics,
        },
        "dependence_policy": "existing_rhen_dependence_controls_required",
        "multiplicity_policy": "existing_rhen_multiplicity_controls_required",
        "fail_closed": True,
        "execution_authority": False,
        "live_activation_requires_explicit_user_control": True,
    }
