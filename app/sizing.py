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


def effective_position_limit(
    settings: Settings,
    account: dict[str, Any],
) -> Decimal:
    if settings.portfolio_limit_mode != "risk":
        return settings.max_position_notional

    equity = base_equity(account)
    if equity <= 0:
        return Decimal("0")
    return min(
        settings.max_position_notional,
        equity * settings.max_position_gross_pct,
    )


def effective_portfolio_stop_risk_limit(
    settings: Settings,
    account: dict[str, Any],
) -> Decimal:
    equity = base_equity(account)
    if equity <= 0:
        return Decimal("0")
    return equity * settings.max_portfolio_stop_risk_pct


def position_stop_pct(
    settings: Settings,
    position: dict[str, Any],
) -> Decimal:
    explicit = d(position.get("risk_stop_pct"))
    if explicit > 0:
        return explicit
    if settings.volatility_stop_enabled:
        return settings.max_dynamic_stop_pct
    return settings.stop_pct


def portfolio_stop_risk(
    settings: Settings,
    positions: list[dict[str, Any]],
) -> Decimal:
    """Conservative nominal risk if every long position reaches its effective stop."""
    return sum(
        (
            abs(d(position.get("market_value")))
            * position_stop_pct(settings, position)
            for position in positions
            if d(position.get("qty")) > 0
        ),
        Decimal("0"),
    )


def calculate_entry_notional(
    settings: Settings,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    stop_pct_override: Decimal | None = None,
) -> Decimal:
    """Return a bounded entry notional without changing the strategy signal.

    Count mode preserves the original equal-slot allocator.

    Risk mode has no fixed position-count dependency. Each entry is bounded by:
    - per-trade risk budget;
    - per-position equity percentage;
    - remaining portfolio gross exposure;
    - remaining portfolio stop-risk budget;
    - remaining cash;
    - hard order/position dollar ceilings.

    This makes trade/position count an output of capital and risk capacity rather
    than a configured quota.
    """
    if settings.sizing_mode == "fixed":
        return settings.order_notional

    equity = base_equity(account)
    effective_stop_pct = stop_pct_override or settings.stop_pct
    if equity <= 0 or effective_stop_pct <= 0:
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
    risk_notional = risk_budget / effective_stop_pct

    if settings.portfolio_limit_mode == "risk":
        position_limit = effective_position_limit(settings, account)
        risk_limit = effective_portfolio_stop_risk_limit(settings, account)
        current_stop_risk = portfolio_stop_risk(settings, positions)
        remaining_stop_risk = max(risk_limit - current_stop_risk, Decimal("0"))
        remaining_risk_notional = remaining_stop_risk / effective_stop_pct

        notional = min(
            risk_notional,
            position_limit,
            remaining_gross,
            remaining_risk_notional,
            cash,
            settings.max_order_notional,
            settings.max_position_notional,
        ).quantize(CENT, rounding=ROUND_DOWN)
    else:
        used_slots = long_position_count(positions)
        slots_remaining = settings.max_concurrent_positions - used_slots
        if slots_remaining <= 0:
            return Decimal("0")
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
    stop_pct_override: Decimal | None = None,
) -> dict[str, str]:
    equity = base_equity(account)
    gross_limit = effective_gross_limit(settings, account)
    exposure = long_exposure(positions)
    effective_stop_pct = stop_pct_override or settings.stop_pct
    recommended = calculate_entry_notional(
        settings,
        account,
        positions,
        stop_pct_override=effective_stop_pct,
    )
    return {
        "mode": settings.sizing_mode,
        "portfolio_limit_mode": settings.portfolio_limit_mode,
        "base_equity": str(equity),
        "gross_limit": str(gross_limit),
        "current_exposure": str(exposure),
        "risk_per_trade_pct": str(settings.risk_per_trade_pct),
        "effective_stop_pct": str(effective_stop_pct),
        "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
        "max_position_gross_pct": str(settings.max_position_gross_pct),
        "position_limit": str(effective_position_limit(settings, account)),
        "max_portfolio_stop_risk_pct": str(settings.max_portfolio_stop_risk_pct),
        "portfolio_stop_risk_limit": str(
            effective_portfolio_stop_risk_limit(settings, account)
        ),
        "current_portfolio_stop_risk": str(portfolio_stop_risk(settings, positions)),
        "recommended_notional": str(recommended),
    }
