from decimal import Decimal

from app.config import Settings
from app.risk import validate_buy, validate_sell_to_flat


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        EXECUTION_ENABLED="true",
        LIVE_TRADING="false",
        BOT_ARMED="true",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY,QQQ",
        ALLOWED_SYMBOLS="SPY,QQQ",
        ORDER_NOTIONAL="5",
        MAX_ORDER_NOTIONAL="5",
        MAX_POSITION_NOTIONAL="10",
        MAX_DAILY_ORDERS="2",
        MAX_DAILY_LOSS="2",
        POLL_SECONDS="60",
    )
    base.update(overrides)
    return Settings(**base)


def test_allowlisted_scanner_symbol_passes():
    s = settings()
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [],
        0,
    )
    assert result.allowed


def test_disallowed_symbol_is_blocked():
    s = settings()
    result = validate_buy(
        s,
        "AAPL",
        Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [],
        0,
    )
    assert not result.allowed


def test_allowed_but_unscanned_symbol_is_blocked_for_entry():
    s = settings(ALLOWED_SYMBOLS="SPY,QQQ,AAPL")
    result = validate_buy(
        s,
        "AAPL",
        Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [],
        0,
    )
    assert not result.allowed
    assert "SCAN_SYMBOLS" in result.reason


def test_live_requires_explicit_acknowledgement():
    s = settings(
        TRADING_MODE="live",
        EXECUTION_ENABLED="true",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="NO",
        BOT_ARMED="true",
    )
    assert not s.live_execution_authorized


def test_live_all_gates_authorize_execution():
    s = settings(
        TRADING_MODE="live",
        EXECUTION_ENABLED="true",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="YES",
        BOT_ARMED="true",
    )
    assert s.live_execution_authorized


def test_sell_to_flat_can_exit_allowed_symbol_removed_from_scan():
    s = settings(
        MAX_DAILY_ORDERS="0",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY,QQQ",
    )
    result = validate_sell_to_flat(
        s,
        "QQQ",
        {"trading_blocked": False},
        {"symbol": "QQQ", "qty": "0.25", "market_value": "100"},
    )
    assert result.allowed


def test_concurrent_positions_allowed_below_configured_limit():
    s = settings(
        MAX_CONCURRENT_POSITIONS="3",
        MAX_TOTAL_POSITION_NOTIONAL="15",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "20", "equity": "20", "last_equity": "20"},
        [{"symbol": "SPY", "qty": "0.05", "market_value": "5"}],
        0,
    )
    assert result.allowed


def test_duplicate_symbol_is_blocked_even_with_free_concurrency_slot():
    s = settings(
        MAX_CONCURRENT_POSITIONS="3",
        MAX_TOTAL_POSITION_NOTIONAL="15",
    )
    result = validate_buy(
        s,
        "SPY",
        Decimal("5"),
        {"cash": "20", "equity": "20", "last_equity": "20"},
        [{"symbol": "SPY", "qty": "0.05", "market_value": "5"}],
        0,
    )
    assert not result.allowed
    assert "already open" in result.reason


def test_total_position_notional_caps_concurrent_entries():
    s = settings(
        MAX_CONCURRENT_POSITIONS="3",
        MAX_TOTAL_POSITION_NOTIONAL="10",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "20", "equity": "20", "last_equity": "20"},
        [{"symbol": "SPY", "qty": "0.05", "market_value": "6"}],
        0,
    )
    assert not result.allowed
    assert "MAX_TOTAL_POSITION_NOTIONAL" in result.reason
