"""Frozen, finite BTC research grammar. This module grants no execution authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import product
import json
import math
from typing import Any

from .btc_direct_strategy import BtcDirectParameters, BtcDirectSwingStrategy

VERSION = "graen-btc-direct-discovery-v2"
LIVE_ID = "RHEN-BTC-DIRECT-003"
FROZEN_AT = datetime(2026, 10, 6, tzinfo=timezone.utc)
DEVELOPMENT = (
    datetime(2025, 8, 3, tzinfo=timezone.utc),
    datetime(2026, 3, 12, 17, tzinfo=timezone.utc),
)
VALIDATION = (
    datetime(2026, 4, 17, tzinfo=timezone.utc),
    datetime(2026, 6, 8, tzinfo=timezone.utc),
)
HOLDOUT = (
    datetime(2026, 6, 8, tzinfo=timezone.utc),
    FROZEN_AT,
)
KNOWN_SOURCE_GAPS = (
    datetime(2025, 6, 28, 12, tzinfo=timezone.utc),
    datetime(2026, 3, 12, 17, tzinfo=timezone.utc),
)
STAGES = ("DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY", "FORWARD_PAPER", "ELIGIBLE_FOR_REVIEW")
COSTS = {
    "LOW": {"fee_bps": 25, "spread_bps": 10, "slippage_bps": 5},
    "BASE": {"fee_bps": 35, "spread_bps": 20, "slippage_bps": 10},
    "HIGH": {"fee_bps": 50, "spread_bps": 40, "slippage_bps": 20},
}
DELAYS = (0, 1, 2)  # Next hourly open, plus zero/one/two additional completed bars.
GATES = {"min_trades": 20, "min_days": 10, "min_profit_factor": 1.2,
         "max_drawdown": .15, "min_expectancy": 0, "min_stability_ratio": .5,
         "paper_min_trades": 30, "paper_min_days": 30, "paper_elapsed_days": 30}


def fingerprint(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def catalog() -> dict[str, dict[str, Any]]:
    result = {}
    for breakout, momentum, target in product((48, 72), ("0.01", "0.015"), ("0.03", "0.045")):
        parameters = BtcDirectParameters(breakout_lookback_bars=breakout,
                                         min_momentum_pct=momentum, take_profit_pct=target).payload()
        digest = fingerprint({"methodology": VERSION, "parameters": parameters})
        identity = "GRAEN-BTC-DIRECT-" + digest[:16].upper()
        result[identity] = {"candidate_id": identity, "parameters": parameters,
                            "fingerprint": digest, "methodology_version": VERSION}
    return result


def candidate_strategy(candidate: dict[str, Any]) -> BtcDirectSwingStrategy:
    known = catalog().get(candidate.get("candidate_id"))
    if not known or any(candidate.get(key) != known[key] for key in ("parameters", "fingerprint", "methodology_version")):
        raise ValueError("unknown_or_invalid_btc_candidate")
    return BtcDirectSwingStrategy(BtcDirectParameters(**known["parameters"]), candidate_id=known["candidate_id"])


def chrono_contract(end: datetime) -> dict[str, Any]:
    """Return the frozen v2 chronology selected only from source completeness.

    v1 reached no strategy evidence: Alpaca's US crypto hourly source has genuine
    holes at 2025-06-28T12:00Z and 2026-03-12T17:00Z, with no underlying trades
    or quotes available for reconstruction. v2 therefore excludes those gaps
    before any candidate is evaluated. The scored windows remain chronological,
    half-open, and non-overlapping; validation begins only after a fresh 35-day
    source-complete warmup following the March gap.
    """
    requested = end.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if requested != FROZEN_AT:
        raise ValueError("btc_discovery_v2_frozen_end_required")
    return {
        "development": [DEVELOPMENT[0].isoformat(), DEVELOPMENT[1].isoformat()],
        "validation": [VALIDATION[0].isoformat(), VALIDATION[1].isoformat()],
        "holdout": [HOLDOUT[0].isoformat(), HOLDOUT[1].isoformat()],
        "warmup_days": 35,
        "interval": "1Hour",
        "half_open": True,
        "frozen_at": FROZEN_AT.isoformat(),
        "data_quality_policy": {
            "basis": "pre_candidate_source_completeness",
            "provider": "alpaca",
            "feed": "us",
            "required_interval": "1Hour",
            "known_source_gaps_excluded": [stamp.isoformat() for stamp in KNOWN_SOURCE_GAPS],
            "development_source_start": (DEVELOPMENT[0] - timedelta(days=35)).isoformat(),
            "validation_source_start": (VALIDATION[0] - timedelta(days=35)).isoformat(),
            "holdout_source_start": (HOLDOUT[0] - timedelta(days=35)).isoformat(),
            "inter_stage_scoring_embargo": [DEVELOPMENT[1].isoformat(), VALIDATION[0].isoformat()],
            "selection_used_strategy_results": False,
        },
    }


def gate(result: dict[str, Any], previous: dict[str, Any] | None = None, *, paper: bool = False) -> list[str]:
    reasons = []
    scenarios = result.get("scenarios") or {}
    for cost in COSTS:
        for delay in DELAYS:
            key = f"{cost}:delay_{delay}"
            row = scenarios.get(key)
            if not isinstance(row, dict):
                reasons.append(key + ":missing_evidence")
                continue
            expected_cost = COSTS[cost]
            if row.get("costs") != expected_cost or row.get("delay_bars") != delay:
                reasons.append(key + ":invalid_friction_contract")
            metrics = row.get("metrics") or {}
            requirements = (("trade_count", GATES["paper_min_trades"] if paper else GATES["min_trades"], ">="),
                            ("independent_days", GATES["paper_min_days"] if paper else GATES["min_days"], ">="),
                            ("net_expectancy", 0, ">"), ("profit_factor", GATES["min_profit_factor"], ">="),
                            ("max_drawdown", GATES["max_drawdown"], "<="))
            for field, bound, op in requirements:
                raw = metrics.get(field)
                try:
                    value = float(raw)
                    passed = math.isfinite(value) and ({">=": value >= bound, ">": value > bound, "<=": value <= bound}[op])
                except (ValueError, TypeError):
                    passed = False
                if not passed:
                    reasons.append(key + ":" + field)
            if previous:
                prior = (((previous.get("scenarios") or {}).get(key) or {}).get("metrics") or {}).get("net_expectancy")
                if prior is None or float(metrics.get("net_expectancy") or 0) < float(prior) * GATES["min_stability_ratio"]:
                    reasons.append(key + ":stage_stability")
    return reasons


def valid_assignment(assignment: Any) -> dict[str, Any] | None:
    if assignment is None:
        return None
    if not isinstance(assignment, dict) or assignment.get("stage") not in {"FORWARD_PAPER", "ELIGIBLE_FOR_REVIEW"}:
        raise ValueError("btc_candidate_not_forward_paper_approved")
    candidate_strategy(assignment)
    if assignment.get("live_authority") is not False or not assignment.get("approval_fingerprint"):
        raise ValueError("invalid_paper_approval")
    if assignment.get("lifecycle_history") != list(STAGES[:4]) or assignment.get("approval_gates") != GATES:
        raise ValueError("btc_assignment_lifecycle_gates_invalid")
    contract = assignment.get("approval_contract") or {}
    try:
        if chrono_contract(datetime.fromisoformat(contract["holdout"][1])) != contract:
            raise ValueError("btc_assignment_chronology_invalid")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("btc_assignment_chronology_invalid") from exc
    results = assignment.get("approval_results") or {}
    previous = None
    for stage in STAGES[:3]:
        result = results.get(stage) or {}
        if gate(result, previous):
            raise ValueError("btc_assignment_statistical_gate_failed")
        previous = result
    verification = results.get("VELUM_REPLAY") or {}
    if verification.get("verified") is not True or verification.get("candidate_fingerprint") != assignment["fingerprint"]:
        raise ValueError("btc_assignment_velum_gate_failed")
    for stage in STAGES[:3]:
        receipt = (verification.get("receipts") or {}).get(stage) or {}
        if receipt.get("dataset_fingerprint") != results[stage].get("dataset_fingerprint") or receipt.get("result_fingerprint") != fingerprint(results[stage]):
            raise ValueError("btc_assignment_velum_receipt_invalid")
    digest = fingerprint({"candidate": assignment["fingerprint"], "results": results,
                          "contract": assignment.get("approval_contract"), "gates": GATES, "version": VERSION})
    if digest != assignment["approval_fingerprint"]:
        raise ValueError("btc_assignment_full_approval_fingerprint_invalid")
    return assignment


def candidate_order_tag(candidate_id: str) -> str:
    known = catalog().get(candidate_id)
    if not known:
        raise ValueError("unknown_btc_order_candidate")
    return "c" + known["fingerprint"][:10]


def candidate_order_matches(order: dict[str, Any], candidate_id: str) -> bool:
    client_id = str(order.get("client_order_id") or "")
    return client_id.startswith("anevum-crypto-") and ("-" + candidate_order_tag(candidate_id) + "-") in client_id


def resolve_strategy(trading_mode: str, assignment: Any = None) -> BtcDirectSwingStrategy:
    # Live ignores all research state, including malformed state. Never consult it.
    if trading_mode == "live":
        return BtcDirectSwingStrategy()
    if trading_mode != "paper":
        raise ValueError("unknown_btc_lane")
    candidate = valid_assignment(assignment)
    return candidate_strategy(candidate) if candidate else BtcDirectSwingStrategy()
