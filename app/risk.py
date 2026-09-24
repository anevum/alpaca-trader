from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import Settings


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


def d(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _account_can_trade(account: dict[str, Any]) -> RiskDecision | None:
    if bool(account.get("account_blocked")):
        return RiskDecision(False, "account is blocked")
    if bool(account.get("trading_blocked")):
        return RiskDecision(False, "trading is blocked")
    return None


def _execution_gate(settings: Settings, symbol: str) -> RiskDecision | None:
    symbol = symbol.upper()
    if not settings.execution_authorized:
        return RiskDecision(False, "execution is not authorized")
    if not settings.allowed_symbols:
        return RiskDecision(False, "no symbols are allowlisted")
    if symbol not in settings.allowed_symbols:
        return RiskDecision(False, f"{symbol} is not allowlisted")
    if settings.normalized_strategy_symbol != symbol:
        return RiskDecision(False, "signal symbol does not match STRATEGY_SYMBOL")
    return None


def validate_buy(
    settings: Settings,
    symbol: str,
    notional: Decimal,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    orders_today: int,
) -> RiskDecision:
    blocked = _execution_gate(settings, symbol) or _account_can_trade(account)
    if blocked:
        return blocked

    if notional <= 0:
        return RiskDecision(False, "order notional must be positive")
    if notional > settings.max_order_notional:
        return RiskDecision(False, "order exceeds MAX_ORDER_NOTIONAL")
    if orders_today >= settings.max_daily_orders:
        return RiskDecision(False, "daily entry-order limit reached")

    cash = d(account.get("cash"))
    if cash < notional:
        return RiskDecision(False, "insufficient cash")

    equity = d(account.get("equity"))
    last_equity = d(account.get("last_equity"))
    if last_equity > 0 and (last_equity - equity) >= settings.max_daily_loss:
        return RiskDecision(False, "daily loss circuit breaker is active")

    existing = Decimal("0")
    for position in positions:
        if str(position.get("symbol", "")).upper() == symbol.upper():
            existing = abs(d(position.get("market_value")))
            break
    if existing + notional > settings.max_position_notional:
        return RiskDecision(False, "position would exceed MAX_POSITION_NOTIONAL")

    return RiskDecision(True, "risk checks passed")


def validate_sell_to_flat(
    settings: Settings,
    symbol: str,
    account: dict[str, Any],
    position: dict[str, Any] | None,
) -> RiskDecision:
    blocked = _execution_gate(settings, symbol) or _account_can_trade(account)
    if blocked:
        return blocked
    if not position:
        return RiskDecision(False, "no position to exit")
    qty = d(position.get("qty"))
    if qty <= 0:
        return RiskDecision(False, "short or zero positions are not supported")
    # A sell-to-flat is risk reducing, so the daily entry limit and daily-loss
    # circuit breaker do not prevent an exit.
    return RiskDecision(True, "risk-reducing exit allowed")
