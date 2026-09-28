"""Explicit, dated external-cash-flow accounting; no broker operations.

This is a manual reconciliation bridge, not automatic transfer discovery. A
reviewed manifest must describe ALL external flows since the prior close.
Broker equity/last_equity remain untouched; trading P&L is never reset to zero.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


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


def annotate_account(
    account: dict[str, Any],
    adjustment: SessionCashFlow | None,
    *,
    run_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Add an auditable reference without modifying raw broker balances.

    No adjustment is inferred from flat positions, a drawdown, or lifetime
    withdrawals. A stale manifest expires at the New York date boundary.
    Active manifest mismatches block new entries but never protective exits.
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
        "source": "operator_reconciled_session_manifest",
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
