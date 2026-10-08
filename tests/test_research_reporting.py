from datetime import datetime, timezone
from decimal import Decimal

from app.research_reporting import (
    classify_daily,
    next_research_action,
    enrich_excursions,
    reconstruct_closed_trades,
    trade_metrics,
)


def test_reconstruct_closed_trades_excludes_manual_and_labels_exit():
    orders = [
        {
            "id": "buy-1",
            "client_order_id": "anevum-aapl-buy-primary-abc",
            "symbol": "AAPL",
            "side": "buy",
        },
        {
            "id": "sell-1",
            "client_order_id": "anevum-aapl-target-primary-def",
            "symbol": "AAPL",
            "side": "sell",
            "type": "market",
        },
        {
            "id": "manual-1",
            "client_order_id": "manual-order",
            "symbol": "MSFT",
            "side": "buy",
        },
    ]
    fills = [
        {
            "id": "f1",
            "order_id": "buy-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "2",
            "price": "100",
            "transaction_time": "2026-09-25T14:00:00Z",
        },
        {
            "id": "f2",
            "order_id": "sell-1",
            "symbol": "AAPL",
            "side": "sell",
            "qty": "2",
            "price": "101",
            "transaction_time": "2026-09-25T14:10:00Z",
        },
        {
            "id": "f3",
            "order_id": "manual-1",
            "symbol": "MSFT",
            "side": "buy",
            "qty": "1",
            "price": "200",
            "transaction_time": "2026-09-25T14:11:00Z",
        },
    ]

    result = reconstruct_closed_trades(fills, orders, owner_tag="primary")

    assert result["included_fill_count"] == 2
    assert result["excluded_fill_count"] == 1
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["symbol"] == "AAPL"
    assert trade["realized_pnl"] == Decimal("2")
    assert trade["return_pct"] == Decimal("1.00")
    assert trade["hold_minutes"] == Decimal("10.0")
    assert trade["exit_reason"] == "take_profit"


def test_mfe_mae_and_metrics_are_measured_from_trade_window():
    trade = {
        "symbol": "AAPL",
        "qty": Decimal("1"),
        "entry_price": Decimal("100"),
        "exit_price": Decimal("101"),
        "entry_at": datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc),
        "exit_at": datetime(2026, 9, 25, 14, 2, tzinfo=timezone.utc),
        "realized_pnl": Decimal("1"),
        "return_pct": Decimal("1"),
        "hold_minutes": Decimal("2"),
        "exit_reason": "take_profit",
        "mfe_pct": None,
        "mae_pct": None,
    }
    bars = {
        "AAPL": [
            {"t": "2026-09-25T14:00:00Z", "h": "101", "l": "99.5"},
            {"t": "2026-09-25T14:01:00Z", "h": "103", "l": "99"},
            {"t": "2026-09-25T14:02:00Z", "h": "102", "l": "100"},
            {"t": "2026-09-25T14:03:00Z", "h": "110", "l": "90"},
        ]
    }

    enrich_excursions([trade], bars)
    metrics = trade_metrics([trade])

    assert trade["mfe_pct"] == Decimal("3.00")
    assert trade["mae_pct"] == Decimal("-1.00")
    assert metrics["realized_pnl"] == Decimal("1")
    assert metrics["wins"] == 1
    assert metrics["losses"] == 0
    assert metrics["average_mfe_pct"] == Decimal("3.00")
    assert metrics["average_mae_pct"] == Decimal("-1.00")


def test_daily_classification_never_promotes_small_sample():
    metrics = trade_metrics(
        [
            {
                "realized_pnl": Decimal("1"),
                "hold_minutes": Decimal("5"),
                "symbol": "AAPL",
                "exit_reason": "take_profit",
                "mfe_pct": Decimal("1.2"),
                "mae_pct": Decimal("-0.2"),
            }
        ]
    )
    runtime = {
        "last_error": None,
        "reconciliation_safe": True,
        "persistence_error": None,
    }
    result = classify_daily(metrics, runtime)
    assert result["classification"] == "INVESTIGATE"


def test_daily_classification_prioritizes_runtime_defect():
    metrics = trade_metrics([])
    runtime = {
        "last_error": "reconciliation timeout",
        "reconciliation_safe": False,
        "persistence_error": None,
    }
    result = classify_daily(metrics, runtime)
    assert result["classification"] == "CHANGE"
    assert result["defects"]


def test_weak_session_routes_to_entry_selectivity_research():
    action = next_research_action(
        {
            "trade_count": 24,
            "wins": 13,
            "losses": 11,
            "expectancy": Decimal("-0.01"),
            "profit_factor": Decimal("0.95"),
        },
        {"bounded_history": False},
        {"classification": "INVESTIGATE"},
    )
    assert "entry-selectivity" in action
    assert "quality score" in action
    assert "re-entry churn" in action
