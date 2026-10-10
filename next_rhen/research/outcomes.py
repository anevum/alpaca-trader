"""RHEN V5 Research R1: deterministic PAPER-only counterfactual maturation.

Never fetches broker data, sends an order, promotes a strategy, or asserts that
input price observations are independently complete/archived. Qualified,
rejected and unmeasurable candidates receive identical maturation treatment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from next_rhen.evidence_journal import validate_cycle, EvidenceContractError

SCHEMA = "anevum.rhen.paper-outcomes.v1"
WINDOWS = (5, 15, 60)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OBS_KEYS = frozenset({"observed_at", "close", "source_sha256"})
MAX_PRICE = Decimal("1000000000000")


class OutcomeContractError(ValueError):
    """Untrusted or temporally inconsistent paper-market observation."""


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise OutcomeContractError(f"{field} requires ISO timezone timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutcomeContractError(f"{field} timestamp invalid") from exc
    if stamp.tzinfo is None:
        raise OutcomeContractError(f"{field} timezone missing")
    return stamp.astimezone(timezone.utc)


def _price(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeContractError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise OutcomeContractError(f"{field} price invalid") from exc
    if not result.is_finite() or not Decimal(0) < result < MAX_PRICE:
        raise OutcomeContractError(f"{field} price nonfinite or nonpositive")
    return result


def _point(record: Any, field: str) -> tuple[datetime, Decimal, str]:
    if not isinstance(record, dict) or set(record) != OBS_KEYS:
        raise OutcomeContractError(f"{field} observation requires exact source fields")
    at = _utc(record["observed_at"], field)
    close = _price(record["close"], field)
    ref = record["source_sha256"]
    if not isinstance(ref, str) or not HEX64.fullmatch(ref):
        raise OutcomeContractError(f"{field} immutable source SHA is invalid")
    return at, close, ref


def mature_cycle(
    cycle: Mapping[str, Any],
    *,
    anchor_prices: Mapping[str, Any],
    horizon_prices: Mapping[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    """Derive 5/15/60m gross long-reference returns on supplied source records.

    The cycle's candidate population is the only permissible symbol universe.
    Input points are external, UNVERIFIED observations with exact timestamp and
    SHA references. An output of OBSERVED_UNATTESTED is NOT valid broker P&L,
    not a proved historical quote tape, and not a validation/holdout result.
    """
    try:
        cycle_bytes = validate_cycle(cycle)
    except EvidenceContractError as exc:
        raise OutcomeContractError("source decision cycle contract rejected") from exc
    observed = _utc(evaluated_at, "evaluated_at")
    decided = _utc(cycle["occurred_at"], "cycle.occurred_at")
    if observed < decided:
        raise OutcomeContractError("evaluation timestamp precedes decision")
    symbols = set(cycle["universe_symbols"])
    if not isinstance(anchor_prices, dict) or not isinstance(horizon_prices, dict):
        raise OutcomeContractError("anchor/horizon collections must be records")
    if set(anchor_prices) - symbols or set(horizon_prices) - symbols:
        raise OutcomeContractError("input contains symbol outside recorded full population")

    anchor: dict[str, tuple[datetime, Decimal, str]] = {}
    for symbol, raw in anchor_prices.items():
        point = _point(raw, f"anchor:{symbol}")
        if not decided - timedelta(minutes=3) <= point[0] <= decided:
            raise OutcomeContractError("anchor timestamp is future or too stale")
        anchor[symbol] = point
    horizons: dict[str, dict[int, tuple[datetime, Decimal, str]]] = {}
    for symbol, values in horizon_prices.items():
        if not isinstance(values, dict) or set(values) - {str(w) for w in WINDOWS}:
            raise OutcomeContractError("horizon set is not one of 5,15,60m")
        parsed = {}
        for k, record in values.items():
            window = int(k)
            stamp, price, ref = _point(record, f"horizon:{symbol}:{window}")
            target = decided + timedelta(minutes=window)
            if stamp != target:
                raise OutcomeContractError("future price not from exact matured horizon")
            if stamp > observed:
                raise OutcomeContractError("future price not yet observable at evaluated_at")
            parsed[window] = (stamp, price, ref)
        horizons[symbol] = parsed

    results = []
    counts = {
        "OBSERVED_UNATTESTED": 0,
        "NOT_YET_DUE": 0,
        "MISSING_ANCHOR": 0,
        "MISSING_HORIZON": 0,
    }
    for candidate in cycle["candidates"]:
        symbol = candidate["symbol"]
        entry = anchor.get(symbol)
        for minutes in WINDOWS:
            target = decided + timedelta(minutes=minutes)
            forward = horizons.get(symbol, {}).get(minutes)
            status = (
                "NOT_YET_DUE" if observed < target
                else "MISSING_ANCHOR" if entry is None
                else "MISSING_HORIZON" if forward is None
                else "OBSERVED_UNATTESTED"
            )
            result = {
                "symbol": symbol,
                "recorded_decision": candidate["decision"],
                "horizon_minutes": minutes,
                "due_at": target.isoformat().replace("+00:00", "Z"),
                "status": status,
                "gross_long_reference_bps": None,
                "anchor_sha256": entry[2] if entry else None,
                "horizon_sha256": forward[2] if forward else None,
            }
            if status == "OBSERVED_UNATTESTED":
                value = (forward[1] / entry[1] - Decimal(1)) * Decimal(10000)
                result["gross_long_reference_bps"] = float(round(value, 6))
            results.append(result)
            counts[status] += 1

    envelope = {
        "schema_version": SCHEMA,
        "workspace_id": cycle["workspace_id"],
        "run_id": cycle["run_id"],
        "cycle_id": cycle["cycle_id"],
        "sequence_no": cycle["sequence_no"],
        "decision_cycle_sha256": sha256(cycle_bytes).hexdigest(),
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "evaluated_at": observed.isoformat().replace("+00:00", "Z"),
        "source_quality": "AWAITING_INDEPENDENT_ATTESTATION",
        "execution_costs_included": False,
        "orders_or_positions": False,
        "alpha_validated": False,
        "strategy_promotion_allowed": False,
        "horizons": list(WINDOWS),
        "candidate_count": len(cycle["candidates"]),
        "status_counts": counts,
        "results": results,
    }
    # Immutable content digest allows offline comparison. This is not evidence
    # that an external broker, archive, scheduler or replay engine verified it.
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    envelope["content_sha256"] = sha256(raw).hexdigest()
    return envelope
