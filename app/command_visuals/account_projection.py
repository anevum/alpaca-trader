"""Read-only broker reconciliation projections; no fill/position inference."""
from app.market_fabric.contracts import number, utc


def account_projection(snapshot, observed_at):
    stamp = utc(observed_at).isoformat()
    account = snapshot["account"]
    points = {}
    for key in ("equity", "cash", "buying_power", "portfolio_value"):
        if account.get(key) is not None:
            points[key] = {"timestamp": stamp, "value": number(account[key]), "provenance": "OBSERVED",
                "source": "ALPACA/REST_RECONCILIATION", "quality_state": "LIVE"}
    positions = []
    for p in snapshot["positions"]:
        if not p.get("symbol"):
            raise ValueError("broker position symbol missing")
        positions.append({"symbol": p["symbol"], "quantity": number(p["qty"]),
            "average_entry_price": number(p["avg_entry_price"], positive=True),
            "market_value": number(p["market_value"]), "unrealized_pl": number(p["unrealized_pl"]) if p.get("unrealized_pl") is not None else None,
            "observed_at": stamp, "provenance": "OBSERVED", "source": "ALPACA/REST_RECONCILIATION"})
    overlays = []
    def order_overlay(order):
        if not order.get("id") or not order.get("symbol"):
            raise ValueError("broker order identity missing")
        if order.get("status") in {"filled", "canceled", "expired", "rejected"}:
            return
        for field, kind in (("stop_price", "BROKER_STOP"), ("limit_price", "BROKER_LIMIT")):
            if order.get(field) is not None:
                overlays.append({"symbol": order["symbol"], "order_ref": order["id"], "kind": kind,
                    "value": number(order[field], positive=True), "side": order.get("side"), "observed_at": stamp,
                    "source": "ALPACA/REST_RECONCILIATION", "provenance": "OBSERVED"})
        for leg in order.get("legs") or []:
            order_overlay(leg)
    for order in snapshot["open_orders"]:
        order_overlay(order)
    return {"points": points, "positions": positions, "overlays": overlays,
            "observed_at": stamp, "source": "ALPACA/REST_RECONCILIATION", "provenance": "OBSERVED",
            "quality_state": "LIVE", "ledger_reconciliation_state": "CHAMPION_OWNED"}
