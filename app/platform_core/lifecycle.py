from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


BLOCKING_TENANT_STATES = {
    "RESTRICTED",
    "BROKER_BLOCKED",
    "PAYMENT_PAST_DUE",
    "RISK_HALTED",
    "SYSTEM_HALTED",
    "CLOSING",
    "CLOSED",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def paper_funding_projection(
    account: dict[str, Any] | None,
    *,
    observed_at: Any = None,
) -> dict[str, Any]:
    """Project broker-owned paper funding without inventing an ANEVUM balance."""

    row = account or {}
    equity = _decimal(row.get("equity"))
    cash = _decimal(row.get("cash"))
    buying_power = _decimal(row.get("buying_power"))
    non_marginable_buying_power = _decimal(row.get("non_marginable_buying_power"))
    available_for_crypto = (
        non_marginable_buying_power
        if non_marginable_buying_power is not None
        else cash
    )

    known = available_for_crypto is not None
    funded = bool(available_for_crypto is not None and available_for_crypto > 0)

    return {
        "source": "ALPACA",
        "environment": "PAPER",
        "known": known,
        "funded": funded if known else False,
        "equity": str(equity) if equity is not None else None,
        "cash": str(cash) if cash is not None else None,
        "buying_power": str(buying_power) if buying_power is not None else None,
        "non_marginable_buying_power": (
            str(non_marginable_buying_power)
            if non_marginable_buying_power is not None
            else None
        ),
        "available_for_crypto": (
            str(available_for_crypto)
            if available_for_crypto is not None
            else None
        ),
        "observed_at": observed_at,
        "external_money_movement_enabled": False,
    }


@dataclass(frozen=True)
class PaperCustomerFacts:
    tenant_status: str
    broker_connected: bool
    broker_active: bool
    reconciliation_success: bool
    funded: bool
    allocation_active: bool
    risk_active: bool
    strategy_assigned: bool
    customer_consented: bool
    bot_enabled: bool
    execution_eligible: bool
    execution_reasons: tuple[str, ...] = ()


def derive_paper_customer_lifecycle(value: PaperCustomerFacts) -> dict[str, Any]:
    """Derive the customer lifecycle from canonical facts.

    This is a projection. It does not mutate tenant status and therefore cannot
    diverge from the underlying broker/configuration evidence.
    """

    tenant_status = str(value.tenant_status or "REGISTERED").upper()
    reasons = tuple(str(reason) for reason in value.execution_reasons if reason)

    if tenant_status in BLOCKING_TENANT_STATES:
        return {
            "state": tenant_status,
            "setup_ready": False,
            "execution_ready": False,
            "next_action": "Resolve account restriction",
        }

    if not value.broker_connected:
        return {
            "state": "BROKER_SETUP_REQUIRED",
            "setup_ready": False,
            "execution_ready": False,
            "next_action": "Connect Alpaca Paper",
        }

    if not value.broker_active or not value.reconciliation_success:
        return {
            "state": "BROKER_PENDING",
            "setup_ready": False,
            "execution_ready": False,
            "next_action": "Verify Alpaca account state",
        }

    if not value.funded:
        return {
            "state": "FUNDING_REQUIRED",
            "setup_ready": False,
            "execution_ready": False,
            "next_action": "Add or reset Alpaca Paper funds",
        }

    missing: list[str] = []
    if not value.allocation_active:
        missing.append("allocation")
    if not value.risk_active:
        missing.append("risk profile")
    if not value.strategy_assigned:
        missing.append("approved strategy release")
    if not value.customer_consented:
        missing.append("paper automation consent")

    if missing:
        return {
            "state": "TRADING_CONFIGURATION_REQUIRED",
            "setup_ready": False,
            "execution_ready": False,
            "next_action": "Complete " + missing[0],
            "missing": tuple(missing),
        }

    if value.execution_eligible:
        return {
            "state": "ACTIVE",
            "setup_ready": True,
            "execution_ready": True,
            "next_action": "Monitor RHEN",
        }

    if not value.bot_enabled:
        return {
            "state": "READY",
            "setup_ready": True,
            "execution_ready": False,
            "next_action": "Enable RHEN paper automation",
        }

    if reasons == ("tenant_execution_runtime_unavailable",):
        next_action = "Await tenant execution runtime"
    elif reasons:
        next_action = "Resolve execution gate: " + reasons[0].replace("_", " ")
    else:
        next_action = "Await execution readiness"

    return {
        "state": "READY",
        "setup_ready": True,
        "execution_ready": False,
        "next_action": next_action,
    }
