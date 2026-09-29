"""Dated external-cash-flow accounting for trading P&L and risk.

Automatic detection uses Alpaca account activities for current-session cash
deposits/withdrawals. The operator-reviewed manifest remains a fallback and a
cross-check. Raw broker equity/last_equity are never rewritten.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
EXTERNAL_CASH_ACTIVITY_TYPES = frozenset({"CSD", "CSW"})


def money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("cash-flow amounts must be finite decimals") from exc
    if not result.is_finite():
        raise ValueError("cash-flow amounts must be finite decimals")
    return result


@dataclass(frozen=True)
class SessionCashFlow:
    session_date: date
    effective_at: datetime
    net_external_cash_flow: Decimal
    expected_last_equity: Decimal
    run_id: str
    evidence_ref: str
    source: str = "operator_reconciled_session_manifest"

    @classmethod
    def from_json(cls, raw: str) -> SessionCashFlow | None:
        if not raw.strip():
            return None
        fields = json.loads(raw)
        required = {
            "session_date", "effective_at", "net_external_cash_flow",
            "expected_last_equity", "run_id", "evidence_ref",
        }
        if not isinstance(fields, dict) or set(fields) != required:
            raise ValueError("SESSION_CASH_FLOW_ADJUSTMENT requires exactly the documented fields")
        for key in required:
            if not isinstance(fields[key], str) or not fields[key].strip():
                raise ValueError(f"cash-flow {key} must be a nonempty string")
        session = date.fromisoformat(fields["session_date"])
        effective = datetime.fromisoformat(fields["effective_at"].replace("Z", "+00:00"))
        if effective.tzinfo is None or effective.utcoffset() is None:
            raise ValueError("cash-flow effective_at requires a timezone")
        if effective.astimezone(NY).date() != session:
            raise ValueError("cash-flow effective_at must be in the New York session date")
        net = money(fields["net_external_cash_flow"])
        prior = money(fields["expected_last_equity"])
        if prior <= 0 or prior + net <= 0:
            raise ValueError("cash-flow reference equity must stay positive")
        return cls(session, effective, net, prior, fields["run_id"], fields["evidence_ref"])


def detected_session_cash_flow(
    activities: list[dict[str, Any]],
    *,
    session_date: date,
    expected_last_equity: Any,
    run_id: str,
    observed_at: datetime,
) -> SessionCashFlow | None:
    """Build a current-session adjustment from Alpaca CSD/CSW activities.

    Only explicit cash deposits (CSD) and cash withdrawals (CSW) are treated as
    owner capital movement. Dividends, fees, interest, fills, journals, and
    other account activities are intentionally excluded.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at requires a timezone")
    prior = money(expected_last_equity)
    if prior <= 0:
        raise ValueError("cash-flow prior-close equity must be positive")

    net = Decimal("0")
    activity_ids: list[str] = []
    for activity in activities:
        if not isinstance(activity, dict):
            raise ValueError("cash-flow activity must be an object")
        activity_type = str(activity.get("activity_type") or "").upper()
        if activity_type not in EXTERNAL_CASH_ACTIVITY_TYPES:
            continue

        raw_date = str(activity.get("date") or "").strip()
        if not raw_date:
            raise ValueError("cash-flow activity is missing date")
        try:
            activity_date = date.fromisoformat(raw_date[:10])
        except ValueError as exc:
            raise ValueError("cash-flow activity date is invalid") from exc
        if activity_date != session_date:
            continue

        activity_id = str(activity.get("id") or "").strip()
        if not activity_id:
            raise ValueError("cash-flow activity is missing id")
        amount = money(activity.get("net_amount"))
        if activity_type == "CSD" and amount < 0:
            raise ValueError("cash deposit activity has a negative net amount")
        if activity_type == "CSW" and amount > 0:
            raise ValueError("cash withdrawal activity has a positive net amount")

        net += amount
        activity_ids.append(activity_id)

    if not activity_ids:
        return None
    if prior + net <= 0:
        raise ValueError("cash-flow reference equity must stay positive")

    digest = hashlib.sha256("|".join(sorted(activity_ids)).encode()).hexdigest()[:16]
    return SessionCashFlow(
        session_date=session_date,
        effective_at=observed_at.astimezone(NY),
        net_external_cash_flow=net,
        expected_last_equity=prior,
        run_id=run_id,
        evidence_ref=f"alpaca-trans:{session_date.isoformat()}:{len(activity_ids)}:{digest}",
        source="alpaca_account_activities",
    )


def reconcile_cash_flow_adjustments(
    manual: SessionCashFlow | None,
    detected: SessionCashFlow | None,
    *,
    session_date: date,
) -> SessionCashFlow | None:
    """Prefer broker evidence while preventing manual/automatic double counting."""
    if detected is None:
        return manual
    if manual is None or manual.session_date != session_date:
        return detected
    if (
        manual.net_external_cash_flow != detected.net_external_cash_flow
        or manual.expected_last_equity != detected.expected_last_equity
        or manual.run_id != detected.run_id
    ):
        raise ValueError("manual and automatic cash-flow adjustments disagree")
    return detected


def annotate_account(
    account: dict[str, Any],
    adjustment: SessionCashFlow | None,
    *,
    run_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Add an auditable reference without modifying raw broker balances.

    No adjustment is inferred from flat positions, a drawdown, or lifetime
    withdrawals. A stale adjustment expires at the New York date boundary.
    Active mismatches block new entries but never protective exits.
    """
    result = dict(account)
    # Reapplying to an already annotated snapshot must never double count.
    for key in ("cash_flow_accounting", "risk_reference_equity", "cash_flow_error"):
        result.pop(key, None)
    if adjustment is None:
        return result
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at requires a timezone")
    meta = {
        "session_date": adjustment.session_date.isoformat(),
        "effective_at": adjustment.effective_at.isoformat(),
        "net_external_cash_flow": str(adjustment.net_external_cash_flow),
        "expected_last_equity": str(adjustment.expected_last_equity),
        "run_id": adjustment.run_id,
        "evidence_ref": adjustment.evidence_ref,
        "source": adjustment.source,
        "observed_at": observed_at.isoformat(),
    }
    result["cash_flow_accounting"] = meta
    if observed_at.astimezone(NY).date() != adjustment.session_date:
        meta["status"] = "out_of_session"
        return result
    error = None
    if run_id != adjustment.run_id:
        error = "cash-flow adjustment run does not match"
    elif observed_at < adjustment.effective_at:
        error = "cash-flow adjustment is not yet effective"
    else:
        try:
            prior = money(account.get("last_equity"))
            equity = money(account.get("equity"))
            if prior != adjustment.expected_last_equity:
                error = "cash-flow prior-close equity does not match"
        except ValueError:
            error = "cash-flow broker equity is invalid"
    if error:
        meta["status"] = "unreconciled"
        result["cash_flow_error"] = error
        return result
    reference = prior + adjustment.net_external_cash_flow
    result["risk_reference_equity"] = str(reference)
    meta.update({
        "status": "applied",
        "raw_equity_change": str(equity - prior),
        "risk_reference_equity": str(reference),
        "cash_flow_adjusted_day_pnl": str(equity - reference),
    })
    return result


def risk_reference_equity(account: dict[str, Any]) -> Decimal:
    return money(account.get("risk_reference_equity", account.get("last_equity", "0")))


def day_pnl(account: dict[str, Any]) -> Decimal:
    return money(account.get("equity", "0")) - risk_reference_equity(account)
