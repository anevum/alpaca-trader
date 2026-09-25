from decimal import Decimal

import pytest

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


def test_risk_mode_zero_count_caps_do_not_block_entry():
    s = settings(
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="0.80",
        MAX_POSITION_GROSS_PCT="0.25",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
        STOP_PCT="0.0035",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("20"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [
            {"symbol": "SPY", "qty": "0.05", "market_value": "5"},
            {"symbol": "AAPL", "qty": "0.05", "market_value": "5"},
            {"symbol": "MSFT", "qty": "0.05", "market_value": "5"},
            {"symbol": "NVDA", "qty": "0.05", "market_value": "5"},
        ],
        999,
    )
    assert result.allowed


def test_risk_mode_blocks_projected_portfolio_stop_risk():
    s = settings(
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="1",
        MAX_POSITION_GROSS_PCT="1",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.002",
        STOP_PCT="0.01",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [{"symbol": "SPY", "qty": "1", "market_value": "18"}],
        0,
    )
    assert not result.allowed
    assert "MAX_PORTFOLIO_STOP_RISK_PCT" in result.reason


def test_dynamic_universe_authorizes_active_symbol_not_in_static_allowlist():
    s = settings(
        DYNAMIC_UNIVERSE_ENABLED="true",
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="0.80",
        MAX_POSITION_GROSS_PCT="0.25",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
        STOP_PCT="0.0035",
    )
    result = validate_buy(
        s,
        "AAPL",
        Decimal("20"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [],
        0,
        entry_symbols={"AAPL", "MSFT"},
    )
    assert result.allowed


def test_dynamic_universe_rejects_symbol_outside_current_snapshot():
    s = settings(
        DYNAMIC_UNIVERSE_ENABLED="true",
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="0.80",
        MAX_POSITION_GROSS_PCT="0.25",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
        STOP_PCT="0.0035",
    )
    result = validate_buy(
        s,
        "TSLA",
        Decimal("20"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [],
        0,
        entry_symbols={"AAPL", "MSFT"},
    )
    assert not result.allowed
    assert "active dynamic universe" in result.reason


def test_risk_mode_nonzero_concurrency_cap_is_enforced():
    s = settings(
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="2",
        MAX_NEW_ENTRIES_PER_CYCLE="1",
        MAX_DAILY_ORDERS="0",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="1",
        MAX_POSITION_GROSS_PCT="1",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.05",
        STOP_PCT="0.0035",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [
            {"symbol": "SPY", "qty": "1", "market_value": "10"},
            {"symbol": "AAPL", "qty": "1", "market_value": "10"},
        ],
        0,
    )
    assert not result.allowed
    assert "concurrent-position" in result.reason


def test_risk_mode_nonzero_daily_order_cap_is_enforced():
    s = settings(
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="2",
        MAX_NEW_ENTRIES_PER_CYCLE="1",
        MAX_DAILY_ORDERS="3",
        MAX_ORDER_NOTIONAL="100",
        MAX_POSITION_NOTIONAL="100",
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_GROSS_EXPOSURE_PCT="1",
        MAX_POSITION_GROSS_PCT="1",
        MAX_PORTFOLIO_STOP_RISK_PCT="0.05",
        STOP_PCT="0.0035",
    )
    result = validate_buy(
        s,
        "QQQ",
        Decimal("5"),
        {"cash": "100", "equity": "100", "last_equity": "100"},
        [],
        3,
    )
    assert not result.allowed
    assert "daily entry-order limit" in result.reason


def test_live_persistent_runtime_requires_live_strategy_identity():
    with pytest.raises(
        ValueError,
        match="STRATEGY_VERSION_ID prefixed LIVE-",
    ):
        settings(
            TRADING_MODE="live",
            EXECUTION_ENABLED="true",
            LIVE_TRADING="true",
            I_ACKNOWLEDGE_LIVE_TRADING="YES",
            BOT_ARMED="true",
            TRADING_INGEST_URL="https://example.invalid/functions/v1/trading-ingest",
            TRADING_INGEST_TOKEN="x" * 32,
            TRADING_RUN_ID="00000000-0000-4000-8000-000000000001",
            TRADING_RUN_STARTED_AT="2026-09-25T13:30:00Z",
            STRATEGY_VERSION_ID="SHADOW-WRONG-ID",
        )


def test_live_persistent_runtime_accepts_live_strategy_identity():
    s = settings(
        TRADING_MODE="live",
        EXECUTION_ENABLED="true",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="YES",
        BOT_ARMED="true",
        TRADING_INGEST_URL="https://example.invalid/functions/v1/trading-ingest",
        TRADING_INGEST_TOKEN="x" * 32,
        TRADING_RUN_ID="00000000-0000-4000-8000-000000000001",
        TRADING_RUN_STARTED_AT="2026-09-25T13:30:00Z",
        STRATEGY_VERSION_ID="LIVE-2026-09-25-004",
    )
    assert s.persistence_environment == "live"
