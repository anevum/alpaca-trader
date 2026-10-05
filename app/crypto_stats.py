from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _filled_at(order: dict[str, Any]) -> str:
    return str(
        order.get("filled_at")
        or order.get("updated_at")
        or order.get("submitted_at")
        or ""
    )


def _crypto_bot_order(order: dict[str, Any]) -> bool:
    return str(order.get("client_order_id") or "").startswith("anevum-crypto-")


def crypto_trade_stats(
    orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    *,
    strategy_version_id: str,
    strategy_family: str,
    start_at: datetime | None = None,
    include_manual_btc: bool = False,
) -> dict[str, Any]:
    """Build a broker-derived crypto scorecard from filled RHEN crypto orders.

    The direct BTC runtime allows one crypto position at a time, so average-cost
    inventory accounting is deterministic and sufficient for the live scorecard.
    """
    start_utc = start_at.astimezone(timezone.utc) if start_at else None

    def in_window(order: dict[str, Any]) -> bool:
        if start_utc is None:
            return True
        raw = _filled_at(order)
        if not raw:
            return False
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            return False
        return stamp.astimezone(timezone.utc) >= start_utc

    def eligible_order(order: dict[str, Any]) -> bool:
        if _crypto_bot_order(order):
            return True
        if not include_manual_btc:
            return False
        return str(order.get("symbol") or "").upper() == "BTC/USD"

    filled = [
        order
        for order in orders
        if eligible_order(order)
        and str(order.get("status") or "").lower() == "filled"
        and _d(order.get("filled_qty")) > 0
        and _d(order.get("filled_avg_price")) > 0
        and in_window(order)
    ]
    filled.sort(key=_filled_at)

    inventory_qty = Decimal("0")
    inventory_cost = Decimal("0")
    open_entry_at: str | None = None
    closed: list[dict[str, Any]] = []

    for order in filled:
        side = str(order.get("side") or "").lower()
        qty = _d(order.get("filled_qty"))
        price = _d(order.get("filled_avg_price"))
        stamp = _filled_at(order)

        if side == "buy":
            if inventory_qty <= 0:
                open_entry_at = stamp or None
            inventory_cost += qty * price
            inventory_qty += qty
            continue

        if side != "sell" or inventory_qty <= 0:
            continue

        matched_qty = min(qty, inventory_qty)
        average_cost = (
            inventory_cost / inventory_qty
            if inventory_qty > 0
            else Decimal("0")
        )
        cost_basis = average_cost * matched_qty
        proceeds = price * matched_qty
        pnl = proceeds - cost_basis
        return_pct = (
            pnl / cost_basis
            if cost_basis > 0
            else Decimal("0")
        )

        closed.append(
            {
                "symbol": str(order.get("symbol") or "BTC/USD"),
                "entry_at": open_entry_at,
                "exit_at": stamp or None,
                "qty": str(matched_qty),
                "entry_price": str(average_cost),
                "exit_price": str(price),
                "pnl": str(pnl),
                "return_pct": str(return_pct),
                "exit_client_order_id": order.get("client_order_id"),
            }
        )

        inventory_qty -= matched_qty
        inventory_cost -= cost_basis
        if inventory_qty <= Decimal("0.0000000001"):
            inventory_qty = Decimal("0")
            inventory_cost = Decimal("0")
            open_entry_at = None

    returns = [_d(row["return_pct"]) for row in closed]
    pnls = [_d(row["pnl"]) for row in closed]
    wins = sum(1 for value in returns if value > 0)
    losses = sum(1 for value in returns if value < 0)
    flats = len(returns) - wins - losses

    gross_profit = sum((value for value in pnls if value > 0), Decimal("0"))
    gross_loss_abs = -sum((value for value in pnls if value < 0), Decimal("0"))
    realized_pnl = sum(pnls, Decimal("0"))
    expectancy = (
        sum(returns, Decimal("0")) / Decimal(len(returns))
        if returns
        else Decimal("0")
    )
    win_rate = (
        Decimal(wins) / Decimal(len(returns))
        if returns
        else Decimal("0")
    )
    profit_factor = (
        gross_profit / gross_loss_abs
        if gross_loss_abs > 0
        else None
    )

    equity_curve = Decimal("1")
    peak = Decimal("1")
    max_drawdown = Decimal("0")
    loss_streak = 0
    max_loss_streak = 0
    for value in returns:
        equity_curve *= Decimal("1") + value
        peak = max(peak, equity_curve)
        if peak > 0:
            drawdown = equity_curve / peak - Decimal("1")
            max_drawdown = min(max_drawdown, drawdown)
        if value < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    crypto_positions = [
        position
        for position in positions
        if "/" in str(position.get("symbol") or "")
        or str(position.get("asset_class") or "").lower() == "crypto"
    ]

    return {
        "source": "alpaca_broker_orders",
        "strategy_version_id": strategy_version_id,
        "strategy_family": strategy_family,
        "start_at": start_utc.isoformat() if start_utc else None,
        "manual_btc_orders_included": include_manual_btc,
        "filled_orders": len(filled),
        "filled_entries": sum(
            1 for order in filled if str(order.get("side") or "").lower() == "buy"
        ),
        "filled_exits": sum(
            1 for order in filled if str(order.get("side") or "").lower() == "sell"
        ),
        "closed_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": str(win_rate),
        "realized_pnl": str(realized_pnl),
        "average_return_pct": str(expectancy),
        "expectancy_pct": str(expectancy),
        "profit_factor": str(profit_factor) if profit_factor is not None else None,
        "gross_profit": str(gross_profit),
        "gross_loss_abs": str(gross_loss_abs),
        "max_drawdown_pct": str(max_drawdown),
        "max_loss_streak": max_loss_streak,
        "open_inventory_qty": str(inventory_qty),
        "recent_closed_trades": closed[-20:],
        "open_positions": [
            {
                "symbol": position.get("symbol"),
                "qty": position.get("qty"),
                "avg_entry_price": position.get("avg_entry_price"),
                "current_price": position.get("current_price"),
                "market_value": position.get("market_value"),
                "unrealized_pl": position.get("unrealized_pl"),
                "unrealized_plpc": position.get("unrealized_plpc"),
            }
            for position in crypto_positions
        ],
    }
