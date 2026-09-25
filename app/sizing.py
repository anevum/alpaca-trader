from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from .config import Settings


CENT = Decimal("0.01")


def d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def base_equity(account: dict[str, Any]) -> Decimal:
    """Use prior-close equity for stable intraday sizing, then fall back to equity."""
    prior = d(account.get("last_equity"))
    if prior > 0:
        return prior
    return d(account.get("equity"))


def long_exposure(positions: list[dict[str, Any]]) -> Decimal:
    return sum(
        (
            abs(d(position.get("market_value")))
            for position in positions
            if d(position.get("qty")) > 0
        ),
        Decimal("0"),
    )


def long_position_count(positions: list[dict[str, Any]]) -> int:
    return sum(1 for position in positions if d(position.get("qty")) > 0)


def effective_gross_limit(
    settings: Settings,
    account: dict[str, Any],
) -> Decimal:
    hard_limit = settings.max_total_position_notional
    if settings.sizing_mode != "equity_risk":
        return hard_limit

    equity = base_equity(account)
    if equity <= 0:
        return Decimal("0")

    percent_limit = equity * settings.max_gross_exposure_pct
    return min(hard_limit, percent_limit)


def calculate_entry_notional(
    settings: Settings,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
) -> Decimal:
    """Return a bounded entry notional without changing the strategy signal.

    In equity_risk mode the allocator caps each entry by:
    - risk budget divided by configured stop distance;
    - an equal share of remaining gross-exposure capacity;
    - remaining cash;
    - hard per-order and per-position limits.

    The gross-exposure limit itself is the lower of the hard dollar ceiling and
    MAX_GROSS_EXPOSURE_PCT of prior-close equity.
    """
    if settings.sizing_mode == "fixed":
        return settings.order_notional

    equity = base_equity(account)
    if equity <= 0 or settings.stop_pct <= 0:
        return Decimal("0")

    used_slots = long_position_count(positions)
    slots_remaining = settings.max_concurrent_positions - used_slots
    if slots_remaining <= 0:
        return Decimal("0")

    gross_limit = effective_gross_limit(settings, account)
    exposure = long_exposure(positions)
    remaining_gross = max(gross_limit - exposure, Decimal("0"))
    if remaining_gross <= 0:
        return Decimal("0")

    cash = max(d(account.get("cash")), Decimal("0"))
    if cash <= 0:
        return Decimal("0")

    risk_budget = equity * settings.risk_per_trade_pct
    risk_notional = risk_budget / settings.stop_pct
    slot_budget = remaining_gross / Decimal(slots_remaining)

    notional = min(
        risk_notional,
        slot_budget,
        cash,
        settings.max_order_notional,
        settings.max_position_notional,
    ).quantize(CENT, rounding=ROUND_DOWN)

    if notional < settings.min_order_notional:
        return Decimal("0")
    return notional


def sizing_snapshot(
    settings: Settings,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
) -> dict[str, str]:
    equity = base_equity(account)
    gross_limit = effective_gross_limit(settings, account)
    exposure = long_exposure(positions)
    recommended = calculate_entry_notional(settings, account, positions)
    return {
        "mode": settings.sizing_mode,
        "base_equity": str(equity),
        "gross_limit": str(gross_limit),
        "current_exposure": str(exposure),
        "risk_per_trade_pct": str(settings.risk_per_trade_pct),
        "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
        "recommended_notional": str(recommended),
    }
