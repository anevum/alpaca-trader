from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def d(value: Any, default: Decimal = ZERO) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def parse_stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _flatten_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    stack = list(orders)
    while stack:
        order = stack.pop()
        output.append(order)
        legs = order.get("legs") or []
        if isinstance(legs, list):
            stack.extend(item for item in legs if isinstance(item, dict))
    return output


def is_bot_order(order: dict[str, Any], owner_tag: str = "") -> bool:
    client_order_id = str(order.get("client_order_id") or "")
    if not client_order_id.startswith("anevum-"):
        return False
    tag = owner_tag.strip()
    return not tag or f"-{tag}-" in client_order_id


def bot_order_index(
    orders: list[dict[str, Any]],
    owner_tag: str = "",
) -> dict[str, dict[str, Any]]:
    return {
        str(order.get("id")): order
        for order in _flatten_orders(orders)
        if order.get("id") and is_bot_order(order, owner_tag)
    }


def infer_exit_reason(order: dict[str, Any] | None) -> str:
    if not order:
        return "unknown"
    client_order_id = str(order.get("client_order_id") or "").lower()
    tagged_reasons = (
        ("-hardstop-", "broker_protective_stop"),
        ("-protect-", "profit_protection"),
        ("-target-", "take_profit"),
        ("-thesis-", "thesis_failure"),
        ("-time-", "max_hold"),
        ("-eod-", "end_of_day"),
        ("-stop-", "software_stop"),
    )
    for marker, reason in tagged_reasons:
        if marker in client_order_id:
            return reason
    order_type = str(order.get("type") or "").lower()
    if order_type == "stop":
        return "stop_order"
    return "market_exit"


def _fill_key(fill: dict[str, Any]) -> tuple[datetime, str]:
    stamp = parse_stamp(
        fill.get("transaction_time")
        or fill.get("filled_at")
        or fill.get("timestamp")
    ) or datetime.min.replace(tzinfo=timezone.utc)
    return stamp, str(fill.get("id") or "")


def reconstruct_closed_trades(
    fills: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    *,
    owner_tag: str = "",
) -> dict[str, Any]:
    """Reconstruct long-only RHEN round trips from broker fills.

    Manual orders are excluded by requiring each fill's order_id to map to an
    RHEN-owned client_order_id in the legacy `anevum-` namespace. Buys are matched FIFO to later sells per symbol.
    """

    indexed = bot_order_index(orders, owner_tag)
    lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    matched: list[dict[str, Any]] = []
    unmatched_sell_qty: dict[str, Decimal] = defaultdict(lambda: ZERO)
    included_fills = 0
    excluded_fills = 0

    for fill in sorted(fills, key=_fill_key):
        order_id = str(fill.get("order_id") or "")
        order = indexed.get(order_id)
        if order is None:
            excluded_fills += 1
            continue
        side = str(fill.get("side") or order.get("side") or "").lower()
        symbol = str(fill.get("symbol") or order.get("symbol") or "").upper()
        qty = d(fill.get("qty"))
        price = d(fill.get("price"))
        stamp = parse_stamp(
            fill.get("transaction_time")
            or fill.get("filled_at")
            or order.get("filled_at")
        )
        if not symbol or qty <= ZERO or price <= ZERO or stamp is None:
            excluded_fills += 1
            continue
        included_fills += 1

        if side == "buy":
            lots[symbol].append(
                {
                    "qty": qty,
                    "price": price,
                    "at": stamp,
                    "order_id": order_id,
                    "client_order_id": order.get("client_order_id"),
                }
            )
            continue

        if side != "sell":
            continue

        remaining = qty
        while remaining > ZERO and lots[symbol]:
            lot = lots[symbol][0]
            take = min(remaining, lot["qty"])
            matched.append(
                {
                    "symbol": symbol,
                    "qty": take,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "entry_at": lot["at"],
                    "exit_at": stamp,
                    "entry_order_id": lot["order_id"],
                    "exit_order_id": order_id,
                    "entry_client_order_id": lot["client_order_id"],
                    "exit_client_order_id": order.get("client_order_id"),
                    "exit_reason": infer_exit_reason(order),
                }
            )
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] <= ZERO:
                lots[symbol].popleft()
        if remaining > ZERO:
            unmatched_sell_qty[symbol] += remaining

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in matched:
        key = (
            item["symbol"],
            item["entry_order_id"],
            item["exit_order_id"],
        )
        bucket = grouped.setdefault(
            key,
            {
                "symbol": item["symbol"],
                "qty": ZERO,
                "entry_notional": ZERO,
                "exit_notional": ZERO,
                "entry_at": item["entry_at"],
                "exit_at": item["exit_at"],
                "entry_order_id": item["entry_order_id"],
                "exit_order_id": item["exit_order_id"],
                "entry_client_order_id": item["entry_client_order_id"],
                "exit_client_order_id": item["exit_client_order_id"],
                "exit_reason": item["exit_reason"],
            },
        )
        bucket["qty"] += item["qty"]
        bucket["entry_notional"] += item["qty"] * item["entry_price"]
        bucket["exit_notional"] += item["qty"] * item["exit_price"]
        bucket["entry_at"] = min(bucket["entry_at"], item["entry_at"])
        bucket["exit_at"] = max(bucket["exit_at"], item["exit_at"])

    trades: list[dict[str, Any]] = []
    for bucket in grouped.values():
        qty = bucket["qty"]
        entry_price = bucket.pop("entry_notional") / qty
        exit_price = bucket.pop("exit_notional") / qty
        pnl = (exit_price - entry_price) * qty
        return_pct = ((exit_price / entry_price) - Decimal("1")) * HUNDRED
        hold_minutes = Decimal(
            str((bucket["exit_at"] - bucket["entry_at"]).total_seconds() / 60)
        )
        trades.append(
            {
                **bucket,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "realized_pnl": pnl,
                "return_pct": return_pct,
                "hold_minutes": hold_minutes,
                "mfe_pct": None,
                "mae_pct": None,
            }
        )

    trades.sort(key=lambda item: (item["exit_at"], item["symbol"]))
    open_lots = {
        symbol: sum((lot["qty"] for lot in queue), ZERO)
        for symbol, queue in lots.items()
        if queue
    }
    return {
        "trades": trades,
        "included_fill_count": included_fills,
        "excluded_fill_count": excluded_fills,
        "open_lot_qty": open_lots,
        "unmatched_sell_qty": dict(unmatched_sell_qty),
    }


def enrich_excursions(
    trades: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> None:
    for trade in trades:
        symbol = trade["symbol"]
        entry_at = trade["entry_at"]
        exit_at = trade["exit_at"]
        entry = trade["entry_price"]
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        for bar in bars_by_symbol.get(symbol, []):
            stamp = parse_stamp(bar.get("t"))
            if stamp is None or stamp < entry_at or stamp > exit_at:
                continue
            high = d(bar.get("h"))
            low = d(bar.get("l"))
            if high > ZERO:
                highs.append(high)
            if low > ZERO:
                lows.append(low)
        if highs:
            trade["mfe_pct"] = ((max(highs) / entry) - Decimal("1")) * HUNDRED
        if lows:
            trade["mae_pct"] = ((min(lows) / entry) - Decimal("1")) * HUNDRED


def _decimal_mean(values: list[Decimal]) -> Decimal | None:
    return (sum(values, ZERO) / Decimal(len(values))) if values else None


def _decimal_median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(str(median(values)))


def trade_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [d(item.get("realized_pnl")) for item in trades]
    wins = [value for value in pnls if value > ZERO]
    losses = [value for value in pnls if value < ZERO]
    flats = [value for value in pnls if value == ZERO]
    gross_profit = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    expectancy = _decimal_mean(pnls) or ZERO
    profit_factor = (
        (gross_profit / gross_loss) if gross_loss > ZERO else None
    )
    holds = [d(item.get("hold_minutes")) for item in trades]
    mfes = [d(item.get("mfe_pct")) for item in trades if item.get("mfe_pct") is not None]
    maes = [d(item.get("mae_pct")) for item in trades if item.get("mae_pct") is not None]

    cumulative = ZERO
    peak = ZERO
    max_drawdown = ZERO
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)

    by_symbol: dict[str, dict[str, Any]] = {}
    by_exit_reason: dict[str, dict[str, Any]] = {}
    for field, target in (("symbol", by_symbol), ("exit_reason", by_exit_reason)):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            grouped[str(trade.get(field) or "unknown")].append(trade)
        for key, values in grouped.items():
            values_pnl = [d(item.get("realized_pnl")) for item in values]
            target[key] = {
                "trades": len(values),
                "realized_pnl": sum(values_pnl, ZERO),
                "wins": sum(1 for value in values_pnl if value > ZERO),
                "losses": sum(1 for value in values_pnl if value < ZERO),
                "expectancy": _decimal_mean(values_pnl) or ZERO,
            }

    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flats),
        "win_rate": (Decimal(len(wins)) / Decimal(len(trades))) if trades else None,
        "realized_pnl": sum(pnls, ZERO),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "average_hold_minutes": _decimal_mean(holds),
        "median_hold_minutes": _decimal_median(holds),
        "average_mfe_pct": _decimal_mean(mfes),
        "average_mae_pct": _decimal_mean(maes),
        "max_realized_drawdown": max_drawdown,
        "by_symbol": by_symbol,
        "by_exit_reason": by_exit_reason,
    }


def scan_funnel(history: list[dict[str, Any]], capacity: int = 200) -> dict[str, Any]:
    scans = [item for item in history if item.get("kind") == "scan"]
    actions = Counter(str(item.get("action") or "unknown") for item in scans)
    reasons = Counter(str(item.get("reason") or item.get("message") or "unknown") for item in scans)
    errors = [
        item
        for item in history
        if item.get("kind") in {"runtime_error", "persistence"}
        or (item.get("kind") == "reconciliation" and item.get("action") in {"error", "blocked"})
        or item.get("action") in {"error", "warning"}
    ]
    return {
        "scan_transition_events": len(scans),
        "actions": dict(actions),
        "top_reasons": reasons.most_common(12),
        "error_or_warning_events": len(errors),
        "bounded_history": len(history) >= capacity,
        "history_events_available": len(history),
    }


def classify_daily(metrics: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    defects: list[str] = []
    if runtime.get("last_error"):
        defects.append(str(runtime["last_error"]))
    if runtime.get("reconciliation_safe") is False:
        defects.append("broker/canonical reconciliation is not safe")
    if runtime.get("persistence_error"):
        defects.append(str(runtime["persistence_error"]))
    if defects:
        return {
            "classification": "CHANGE",
            "reason": "implementation or safety defect requires correction",
            "defects": defects,
        }

    count = int(metrics.get("trade_count") or 0)
    if count == 0:
        return {
            "classification": "INVESTIGATE",
            "reason": "no closed RHEN trades; inspect opportunity/rejection evidence before changing strategy",
            "defects": [],
        }
    if count < 20:
        return {
            "classification": "INVESTIGATE",
            "reason": "sample is too small for strategy-parameter changes",
            "defects": [],
        }
    expectancy = d(metrics.get("expectancy"))
    profit_factor = metrics.get("profit_factor")
    pf = d(profit_factor) if profit_factor is not None else None
    if expectancy > ZERO and (pf is None or pf > Decimal("1")):
        return {
            "classification": "KEEP",
            "reason": "sample is positive; preserve live parameters while accumulating evidence",
            "defects": [],
        }
    return {
        "classification": "INVESTIGATE",
        "reason": "sample is weak, but changes require broader offline evidence",
        "defects": [],
    }


def next_research_action(
    metrics: dict[str, Any],
    funnel: dict[str, Any],
    classification: dict[str, Any],
) -> str:
    if classification.get("classification") == "CHANGE":
        return "repair the identified implementation/safety defect and verify it before the next live session"
    if int(metrics.get("trade_count") or 0) == 0:
        return "compare the dominant rejection reasons with subsequent 15-minute outcomes to test whether the entry gate is excluding useful setups"
    losses = int(metrics.get("losses") or 0)
    wins = int(metrics.get("wins") or 0)
    if losses > wins:
        return "compare losing versus winning entry structure using pre-entry features and MFE/MAE before altering thresholds"
    if funnel.get("bounded_history"):
        return "expand durable candidate-decision telemetry so rejection-funnel analysis is not limited by the 200-event runtime buffer"
    return "append this session to the offline validation corpus and re-evaluate expectancy under identical friction assumptions"


def serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value
