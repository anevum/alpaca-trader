from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Protocol


ZERO = Decimal("0")
QTY_QUANTUM = Decimal("0.000000001")


def decimal_value(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO
    return parsed if parsed.is_finite() else ZERO


def canonical_crypto_symbol(value: Any, *, asset_class: Any = None) -> str:
    symbol = str(value or "").strip().upper()
    if "/" in symbol:
        return symbol
    if str(asset_class or "").lower() == "crypto" and symbol.endswith("USD") and len(symbol) > 3:
        return symbol[:-3] + "/USD"
    return symbol


@dataclass(frozen=True)
class TenantPaperSignal:
    signal_id: str
    strategy_release_id: str
    action: str
    symbol: str
    reference_price: Decimal
    target_allocation_fraction: Decimal | None
    stop_price: Decimal | None
    stop_limit_price: Decimal | None
    take_profit_price: Decimal | None
    metadata: dict[str, Any]

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "TenantPaperSignal":
        action = str(row.get("action") or "").upper()
        target_raw = row.get("target_allocation_fraction")
        stop_raw = row.get("stop_price")
        stop_limit_raw = row.get("stop_limit_price")
        take_raw = row.get("take_profit_price")
        return cls(
            signal_id=str(row.get("signal_id") or ""),
            strategy_release_id=str(row.get("strategy_release_id") or ""),
            action=action,
            symbol=canonical_crypto_symbol(row.get("symbol")),
            reference_price=decimal_value(row.get("reference_price")),
            target_allocation_fraction=(
                decimal_value(target_raw) if target_raw is not None else None
            ),
            stop_price=decimal_value(stop_raw) if stop_raw is not None else None,
            stop_limit_price=(
                decimal_value(stop_limit_raw) if stop_limit_raw is not None else None
            ),
            take_profit_price=(
                decimal_value(take_raw) if take_raw is not None else None
            ),
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TenantEntryRiskInput:
    allocation_fraction: Decimal
    absolute_cap: Decimal
    max_position_fraction: Decimal
    max_gross_exposure_fraction: Decimal
    max_daily_loss_fraction: Decimal
    max_drawdown_fraction: Decimal
    max_concurrent_positions: int
    high_water_equity: Decimal | None


@dataclass(frozen=True)
class TenantEntryRiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    authorized_allocation: Decimal
    requested_notional: Decimal
    approved_notional: Decimal
    approved_qty: Decimal
    crypto_capacity: Decimal
    gross_exposure: Decimal
    position_count: int
    daily_loss_fraction: Decimal
    drawdown_fraction: Decimal
    next_high_water_equity: Decimal

    def payload(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "authorized_allocation": str(self.authorized_allocation),
            "requested_notional": str(self.requested_notional),
            "approved_notional": str(self.approved_notional),
            "approved_qty": str(self.approved_qty),
            "crypto_capacity": str(self.crypto_capacity),
            "gross_exposure": str(self.gross_exposure),
            "position_count": self.position_count,
            "daily_loss_fraction": str(self.daily_loss_fraction),
            "drawdown_fraction": str(self.drawdown_fraction),
            "next_high_water_equity": str(self.next_high_water_equity),
        }


class TenantPaperBroker(Protocol):
    async def account_snapshot(self) -> dict[str, Any]: ...
    async def positions_snapshot(self) -> list[dict[str, Any]]: ...
    async def open_orders_snapshot(self) -> list[dict[str, Any]]: ...
    async def recent_orders_snapshot(self, *, limit: int = 100) -> list[dict[str, Any]]: ...
    async def order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None: ...
    async def submit_crypto_market_buy(
        self, *, symbol: str, qty: str, client_order_id: str
    ) -> dict[str, Any]: ...
    async def submit_crypto_market_sell(
        self, *, symbol: str, qty: str, client_order_id: str
    ) -> dict[str, Any]: ...
    async def submit_crypto_stop_limit_sell(
        self,
        *,
        symbol: str,
        qty: str,
        stop_price: str,
        limit_price: str,
        client_order_id: str,
    ) -> dict[str, Any]: ...
    async def cancel_order(self, order_id: str) -> None: ...


def _positive_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        position
        for position in positions
        if abs(decimal_value(position.get("qty"))) > ZERO
    ]


def position_for_symbol(
    positions: list[dict[str, Any]],
    symbol: str,
) -> dict[str, Any] | None:
    normalized = canonical_crypto_symbol(symbol)
    for position in positions:
        candidate = canonical_crypto_symbol(
            position.get("symbol"),
            asset_class=position.get("asset_class") or position.get("class"),
        )
        if candidate == normalized and abs(decimal_value(position.get("qty"))) > ZERO:
            return position
    return None


def evaluate_tenant_entry_risk(
    signal: TenantPaperSignal,
    risk: TenantEntryRiskInput,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
) -> TenantEntryRiskDecision:
    reasons: list[str] = []
    equity = decimal_value(account.get("equity"))
    last_equity = decimal_value(account.get("last_equity"))
    cash = decimal_value(account.get("cash"))
    non_marginable_raw = account.get("non_marginable_buying_power")
    crypto_capacity = (
        decimal_value(non_marginable_raw)
        if non_marginable_raw is not None
        else cash
    )

    prior_high = risk.high_water_equity or ZERO
    next_high = max(prior_high, equity)
    daily_loss = (
        max((last_equity - equity) / last_equity, ZERO)
        if last_equity > ZERO
        else ZERO
    )
    drawdown = (
        max((next_high - equity) / next_high, ZERO)
        if next_high > ZERO
        else ZERO
    )

    authorized = min(
        max(equity * risk.allocation_fraction, ZERO),
        max(risk.absolute_cap, ZERO),
    )
    target = signal.target_allocation_fraction or ZERO
    requested = max(authorized * target, ZERO)

    positive_positions = _positive_positions(positions)
    gross = sum(
        (abs(decimal_value(position.get("market_value"))) for position in positive_positions),
        ZERO,
    )
    position_count = len(positive_positions)

    same_position = position_for_symbol(positions, signal.symbol)
    same_open_buy = any(
        canonical_crypto_symbol(
            order.get("symbol"),
            asset_class=order.get("asset_class") or order.get("class"),
        ) == signal.symbol
        and str(order.get("side") or "").lower() == "buy"
        and str(order.get("status") or "").lower()
        not in {"filled", "canceled", "cancelled", "expired", "rejected"}
        for order in open_orders
    )

    if signal.action != "ENTER_LONG":
        reasons.append("signal_not_entry")
    if signal.reference_price <= ZERO:
        reasons.append("reference_price_invalid")
    if target <= ZERO or target > Decimal("1"):
        reasons.append("signal_target_invalid")
    if equity <= ZERO:
        reasons.append("account_equity_unavailable")
    if crypto_capacity <= ZERO:
        reasons.append("crypto_capacity_unavailable")
    if authorized <= ZERO:
        reasons.append("capital_allocation_unavailable")
    if same_position is not None:
        reasons.append("symbol_position_already_open")
    if same_open_buy:
        reasons.append("symbol_entry_order_already_open")
    if position_count >= max(int(risk.max_concurrent_positions), 1):
        reasons.append("max_concurrent_positions_reached")
    if daily_loss >= risk.max_daily_loss_fraction:
        reasons.append("daily_loss_limit_reached")
    if drawdown >= risk.max_drawdown_fraction:
        reasons.append("drawdown_limit_reached")

    position_limit = max(authorized * risk.max_position_fraction, ZERO)
    gross_limit = max(authorized * risk.max_gross_exposure_fraction, ZERO)
    gross_headroom = max(gross_limit - gross, ZERO)

    approved = min(
        requested,
        position_limit,
        gross_headroom,
        max(crypto_capacity, ZERO),
    )
    qty = (
        (approved / signal.reference_price).quantize(QTY_QUANTUM, rounding=ROUND_DOWN)
        if signal.reference_price > ZERO
        else ZERO
    )

    if gross_headroom <= ZERO:
        reasons.append("gross_exposure_limit_reached")
    if approved <= ZERO or qty <= ZERO:
        reasons.append("approved_order_size_zero")

    return TenantEntryRiskDecision(
        allowed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        authorized_allocation=authorized,
        requested_notional=requested,
        approved_notional=approved,
        approved_qty=qty,
        crypto_capacity=crypto_capacity,
        gross_exposure=gross,
        position_count=position_count,
        daily_loss_fraction=daily_loss,
        drawdown_fraction=drawdown,
        next_high_water_equity=next_high,
    )
