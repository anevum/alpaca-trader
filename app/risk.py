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


def validate_buy(
    settings: Settings,
    symbol: str,
    notional: Decimal,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    orders_today: int,
) -> RiskDecision:
    symbol = symbol.upper()
    if not settings.bot_armed:
        return RiskDecision(False, "bot is disarmed")
    if not settings.allowed_symbols:
        return RiskDecision(False, "no symbols are allowlisted")
    if symbol not in settings.allowed_symbols:
        return RiskDecision(False, f"{symbol} is not allowlisted")
    if notional <= 0:
        return RiskDecision(False, "order notional must be positive")
    if notional > settings.max_order_notional:
        return RiskDecision(False, "order exceeds MAX_ORDER_NOTIONAL")
    if orders_today >= settings.max_daily_orders:
        return RiskDecision(False, "daily order limit reached")

    cash = d(account.get("cash"))
    if cash < notional:
        return RiskDecision(False, "insufficient cash")

    equity = d(account.get("equity"))
    last_equity = d(account.get("last_equity"))
    if last_equity > 0 and (last_equity - equity) >= settings.max_daily_loss:
        return RiskDecision(False, "daily loss circuit breaker is active")

    existing = Decimal("0")
    for position in positions:
        if str(position.get("symbol", "")).upper() == symbol:
            existing = abs(d(position.get("market_value")))
            break
    if existing + notional > settings.max_position_notional:
        return RiskDecision(False, "position would exceed MAX_POSITION_NOTIONAL")

    return RiskDecision(True, "risk checks passed")
