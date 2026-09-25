from decimal import Decimal

from app.config import Settings
from app.sizing import calculate_entry_notional, effective_gross_limit


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY",
        CONFIRMATION_SYMBOLS="QQQ",
        ORDER_NOTIONAL="20",
        STOP_PCT="0.0035",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_TOTAL_POSITION_NOTIONAL="250",
        MAX_CONCURRENT_POSITIONS="3",
        MAX_NEW_ENTRIES_PER_CYCLE="2",
        SIZING_MODE="equity_risk",
        RISK_PER_TRADE_PCT="0.001",
        MAX_GROSS_EXPOSURE_PCT="0.80",
        MIN_ORDER_NOTIONAL="1",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


def account(equity="100", cash="100"):
    return {
        "cash": cash,
        "equity": equity,
        "last_equity": equity,
    }


def test_80_percent_gross_limit_tracks_prior_close_equity():
    s = settings()
    assert effective_gross_limit(s, account("100")) == Decimal("80.00")
    assert effective_gross_limit(s, account("125")) == Decimal("100.00")


def test_first_slot_uses_equal_share_of_gross_capacity():
    s = settings()
    assert calculate_entry_notional(s, account(), []) == Decimal("26.66")


def test_allocator_rebalances_remaining_capacity_across_slots():
    s = settings()
    positions = [{"symbol": "SPY", "qty": "0.2666", "market_value": "26.66"}]
    assert calculate_entry_notional(s, account(), positions) == Decimal("26.67")


def test_risk_budget_can_bind_before_gross_capacity():
    s = settings(RISK_PER_TRADE_PCT="0.0005")
    assert calculate_entry_notional(s, account(), []) == Decimal("14.28")


def test_cash_and_hard_caps_still_bind_dynamic_sizing():
    s = settings(ORDER_NOTIONAL="12", MAX_ORDER_NOTIONAL="12", MAX_POSITION_NOTIONAL="12")
    assert calculate_entry_notional(s, account(cash="9.50"), []) == Decimal("9.50")


def test_fixed_mode_preserves_legacy_notional():
    s = settings(SIZING_MODE="fixed", ORDER_NOTIONAL="20")
    assert calculate_entry_notional(s, account(), []) == Decimal("20")


def test_risk_mode_ignores_fixed_position_slots():
    s = settings(
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_POSITION_GROSS_PCT="0.25",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
    )
    positions = [
        {
            "symbol": f"S{i}",
            "qty": "1",
            "market_value": "5",
        }
        for i in range(7)
    ]
    assert calculate_entry_notional(s, account(), positions) == Decimal("25.00")


def test_risk_mode_uses_remaining_gross_capacity_for_last_position():
    s = settings(
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_POSITION_GROSS_PCT="0.25",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
    )
    positions = [
        {"symbol": "A", "qty": "1", "market_value": "25"},
        {"symbol": "B", "qty": "1", "market_value": "25"},
        {"symbol": "C", "qty": "1", "market_value": "25"},
    ]
    assert calculate_entry_notional(s, account(), positions) == Decimal("5.00")
