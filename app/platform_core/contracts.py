from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


ACTIVE_ENTITLEMENT_STATES = {"ACTIVE", "TRIAL"}
EXECUTABLE_RELEASE_STATES = {"CANARY", "STABLE"}


@dataclass(frozen=True)
class TradingEligibilityInput:
    """Pure account-level inputs required before RHEN may create a new entry.

    This contract is intentionally stricter than the current single-account
    execution gates. It does not submit orders or read secrets.
    """

    environment: str
    tenant_status: str
    entitlement_status: str
    broker_account_status: str
    broker_crypto_enabled: bool
    broker_trading_blocked: bool
    broker_reconciled: bool
    allocation_active: bool
    allocation_positive: bool
    risk_profile_active: bool
    strategy_assignment_active: bool
    strategy_release_state: str
    customer_trading_consent: bool
    customer_bot_enabled: bool
    iren_fleet_healthy: bool
    live_customer_authority: bool = False


@dataclass(frozen=True)
class ExecutionEligibility:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_execution_eligibility(
    value: TradingEligibilityInput,
) -> ExecutionEligibility:
    """Fail closed unless every required account/platform gate is satisfied."""

    reasons: list[str] = []
    environment = str(value.environment or "").upper()
    tenant_status = str(value.tenant_status or "").upper()
    entitlement_status = str(value.entitlement_status or "").upper()
    broker_status = str(value.broker_account_status or "").upper()
    release_state = str(value.strategy_release_state or "").upper()

    if environment not in {"PAPER", "LIVE"}:
        reasons.append("invalid_environment")
    if tenant_status != "ACTIVE":
        reasons.append("tenant_not_active")
    if entitlement_status not in ACTIVE_ENTITLEMENT_STATES:
        reasons.append("entitlement_not_active")
    if broker_status != "ACTIVE":
        reasons.append("broker_account_not_active")
    if not value.broker_crypto_enabled:
        reasons.append("broker_crypto_not_enabled")
    if value.broker_trading_blocked:
        reasons.append("broker_trading_blocked")
    if not value.broker_reconciled:
        reasons.append("broker_not_reconciled")
    if not value.allocation_active or not value.allocation_positive:
        reasons.append("capital_allocation_unavailable")
    if not value.risk_profile_active:
        reasons.append("risk_profile_not_active")
    if not value.strategy_assignment_active:
        reasons.append("strategy_assignment_not_active")
    if release_state not in EXECUTABLE_RELEASE_STATES:
        reasons.append("strategy_release_not_executable")
    if not value.customer_trading_consent:
        reasons.append("customer_consent_missing")
    if not value.customer_bot_enabled:
        reasons.append("customer_bot_disabled")
    if not value.iren_fleet_healthy:
        reasons.append("iren_fleet_gate_closed")
    if environment == "LIVE" and not value.live_customer_authority:
        reasons.append("live_customer_authority_missing")

    return ExecutionEligibility(
        eligible=not reasons,
        reasons=tuple(reasons),
    )


def deterministic_order_identity(
    *,
    tenant_id: str,
    broker_account_id: str,
    strategy_release_id: str,
    signal_id: str,
    symbol: str,
    side: str,
) -> tuple[str, str]:
    """Return retry-stable intent and broker client-order identities.

    The digest contains no broker secret or customer PII. Equivalent inputs
    always produce the same identity so an ambiguous submission can reconcile
    before any retry.
    """

    normalized = "|".join(
        (
            str(tenant_id).strip().lower(),
            str(broker_account_id).strip().lower(),
            str(strategy_release_id).strip(),
            str(signal_id).strip(),
            str(symbol).strip().upper(),
            str(side).strip().upper(),
        )
    )
    if any(not part for part in normalized.split("|")):
        raise ValueError("order identity fields must be non-empty")

    digest = sha256(normalized.encode("utf-8")).hexdigest()
    order_intent_id = f"rhen-intent-{digest}"
    client_order_id = f"anevum-rhen-{digest[:48]}"
    return order_intent_id, client_order_id
