from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import Settings
from .cash_flow import risk_reference_equity
from .sizing import (
    effective_gross_limit,
    effective_position_limit,
    effective_portfolio_stop_risk_limit,
    portfolio_stop_risk,
)


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


def _execution_gate(
    settings: Settings,
    symbol: str,
    *,
    require_scan: bool = True,
    entry_symbols: set[str] | None = None,
) -> RiskDecision | None:
    symbol = symbol.upper()
    if not settings.execution_authorized:
        return RiskDecision(False, "execution is not authorized")
    if settings.dynamic_universe_enabled:
        if require_scan:
            if not entry_symbols:
                return RiskDecision(False, "dynamic entry universe is unavailable")
            if symbol not in entry_symbols:
                return RiskDecision(False, f"{symbol} is not in active dynamic universe")
        return None

    if not settings.allowed_symbols:
        return RiskDecision(False, "no symbols are allowlisted")
    if symbol not in settings.allowed_symbols:
        return RiskDecision(False, f"{symbol} is not allowlisted")
    if require_scan and symbol not in settings.scan_symbols:
        return RiskDecision(False, f"{symbol} is not in SCAN_SYMBOLS")
    return None


def validate_buy(
    settings: Settings,
    symbol: str,
    notional: Decimal,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    entry_orders_today: int,
    entry_symbols: set[str] | None = None,
    stop_pct_override: Decimal | None = None,
) -> RiskDecision:
    blocked = (
        _execution_gate(
            settings,
            symbol,
            entry_symbols=entry_symbols,
        )
        or _account_can_trade(account)
    )
    if blocked:
        return blocked

    if account.get("cash_flow_error"):
        return RiskDecision(False, str(account["cash_flow_error"]))

    normalized_symbol = symbol.upper()
    if any(
        str(position.get("symbol", "")).upper() == normalized_symbol
        and d(position.get("qty")) > 0
        for position in positions
    ):
        return RiskDecision(False, f"{normalized_symbol} position is already open")

    long_positions = [
        position for position in positions
        if d(position.get("qty")) > 0
    ]
    if (
        settings.max_concurrent_positions > 0
        and len(long_positions) >= settings.max_concurrent_positions
    ):
        return RiskDecision(False, "maximum concurrent-position limit reached")
    if notional <= 0:
        return RiskDecision(False, "order notional must be positive")
    if notional > settings.max_order_notional:
        return RiskDecision(False, "order exceeds MAX_ORDER_NOTIONAL")
    if notional > settings.max_position_notional:
        return RiskDecision(False, "order exceeds MAX_POSITION_NOTIONAL")

    current_exposure = sum(
        (abs(d(position.get("market_value"))) for position in long_positions),
        Decimal("0"),
    )
    if current_exposure + notional > settings.max_total_position_notional:
        return RiskDecision(False, "order exceeds MAX_TOTAL_POSITION_NOTIONAL")

    if settings.sizing_mode == "equity_risk":
        gross_limit = effective_gross_limit(settings, account)
        if gross_limit <= 0:
            return RiskDecision(False, "equity-based gross-exposure limit is unavailable")
        if current_exposure + notional > gross_limit:
            return RiskDecision(False, "order exceeds MAX_GROSS_EXPOSURE_PCT")

    if settings.portfolio_limit_mode == "risk":
        position_limit = effective_position_limit(settings, account)
        if position_limit <= 0 or notional > position_limit:
            return RiskDecision(False, "order exceeds MAX_POSITION_GROSS_PCT")
        risk_limit = effective_portfolio_stop_risk_limit(settings, account)
        effective_stop_pct = stop_pct_override or settings.stop_pct
        projected_stop_risk = (
            portfolio_stop_risk(settings, long_positions)
            + (notional * effective_stop_pct)
        )
        if risk_limit <= 0 or projected_stop_risk > risk_limit:
            return RiskDecision(False, "order exceeds MAX_PORTFOLIO_STOP_RISK_PCT")

    if (
        settings.max_daily_orders > 0
        and entry_orders_today >= settings.max_daily_orders
    ):
        return RiskDecision(False, "daily entry-order limit reached")

    cash = d(account.get("cash"))
    if cash < notional:
        return RiskDecision(False, "insufficient cash")

    equity = d(account.get("equity"))
    last_equity = risk_reference_equity(account)
    if last_equity > 0 and (last_equity - equity) >= settings.max_daily_loss:
        return RiskDecision(False, "daily loss circuit breaker is active")

    return RiskDecision(True, "risk checks passed")


def validate_sell_to_flat(
    settings: Settings,
    symbol: str,
    account: dict[str, Any],
    position: dict[str, Any] | None,
) -> RiskDecision:
    blocked = _execution_gate(
        settings,
        symbol,
        require_scan=False,
    ) or _account_can_trade(account)
    if blocked:
        return blocked
    if not position:
        return RiskDecision(False, "no position to exit")
    qty = d(position.get("qty"))
    if qty <= 0:
        return RiskDecision(False, "short or zero positions are not supported")
    return RiskDecision(True, "risk-reducing exit allowed")


def validate_crypto_buy(
    settings: Settings,
    symbol: str,
    notional: Decimal,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    entries_24h: int,
    *,
    entry_symbols: set[str] | None = None,
    require_execution_authorized: bool = True,
) -> RiskDecision:
    symbol = symbol.upper()
    if not settings.crypto_lane_enabled:
        return RiskDecision(False, "crypto lane is disabled")
    if not settings.crypto_execution_enabled:
        return RiskDecision(False, "crypto execution is disabled")
    if require_execution_authorized and not settings.execution_authorized:
        return RiskDecision(False, "account execution is not authorized")
    blocked = _account_can_trade(account)
    if blocked:
        return blocked
    if account.get("cash_flow_error"):
        return RiskDecision(False, str(account["cash_flow_error"]))
    if "/" not in symbol:
        return RiskDecision(False, "crypto symbol must be a trading pair")
    if entry_symbols is not None and symbol not in entry_symbols:
        return RiskDecision(False, f"{symbol} is not in active crypto universe")

    crypto_positions = [
        position
        for position in positions
        if "/" in str(position.get("symbol", ""))
        and d(position.get("qty")) > 0
    ]
    if any(
        str(position.get("symbol", "")).upper() == symbol
        for position in crypto_positions
    ):
        return RiskDecision(False, f"{symbol} crypto position is already open")

    if len(crypto_positions) >= settings.crypto_max_concurrent_positions:
        return RiskDecision(False, "maximum crypto concurrent-position limit reached")
    if notional <= 0:
        return RiskDecision(False, "crypto order notional must be positive")
    if notional > settings.crypto_max_order_notional:
        return RiskDecision(False, "crypto order exceeds CRYPTO_MAX_ORDER_NOTIONAL")

    crypto_exposure = sum(
        (abs(d(position.get("market_value"))) for position in crypto_positions),
        Decimal("0"),
    )
    if crypto_exposure + notional > settings.crypto_max_total_position_notional:
        return RiskDecision(
            False,
            "crypto order exceeds CRYPTO_MAX_TOTAL_POSITION_NOTIONAL",
        )

    all_long_positions = [
        position for position in positions if d(position.get("qty")) > 0
    ]
    shared_exposure = sum(
        (abs(d(position.get("market_value"))) for position in all_long_positions),
        Decimal("0"),
    )
    if shared_exposure + notional > settings.max_total_position_notional:
        return RiskDecision(False, "order exceeds shared MAX_TOTAL_POSITION_NOTIONAL")

    if settings.crypto_max_entries_24h > 0 and entries_24h >= settings.crypto_max_entries_24h:
        return RiskDecision(False, "crypto 24-hour entry limit reached")

    cash = d(account.get("cash"))
    if cash < notional:
        return RiskDecision(False, "insufficient cash")

    equity = d(account.get("equity"))
    last_equity = risk_reference_equity(account)
    if last_equity > 0 and (last_equity - equity) >= settings.max_daily_loss:
        return RiskDecision(False, "shared daily loss circuit breaker is active")

    return RiskDecision(True, "crypto risk checks passed")


def validate_crypto_sell_to_flat(
    settings: Settings,
    symbol: str,
    account: dict[str, Any],
    position: dict[str, Any] | None,
    *,
    require_execution_authorized: bool = True,
) -> RiskDecision:
    if not settings.crypto_lane_enabled:
        return RiskDecision(False, "crypto lane is disabled")
    if require_execution_authorized and not settings.execution_authorized:
        return RiskDecision(False, "account execution is not authorized")
    blocked = _account_can_trade(account)
    if blocked:
        return blocked
    if "/" not in symbol:
        return RiskDecision(False, "crypto symbol must be a trading pair")
    if not position:
        return RiskDecision(False, "no crypto position to exit")
    qty = d(position.get("qty"))
    if qty <= 0:
        return RiskDecision(False, "short or zero crypto positions are not supported")
    return RiskDecision(True, "crypto risk-reducing exit allowed")
