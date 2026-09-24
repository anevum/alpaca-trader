from decimal import Decimal

from app.config import Settings
from app.risk import validate_buy


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        EXECUTION_ENABLED="true",
        BOT_ARMED="true",
        STRATEGY_NAME="opening_range_vwap",
        STRATEGY_SYMBOL="SPY",
        ALLOWED_SYMBOLS="SPY",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
        ORDER_NOTIONAL="75",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_DAILY_ORDERS="2",
        MAX_DAILY_LOSS="1",
        BAR_TIMEFRAME="1Min",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


def test_small_account_entry_passes_when_flat():
    result = validate_buy(
        settings(),
        "SPY",
        Decimal("75"),
        {"cash": "89.28", "equity": "89.28", "last_equity": "89.28"},
        [],
        0,
    )
    assert result.allowed


def test_any_existing_position_blocks_entry():
    result = validate_buy(
        settings(),
        "SPY",
        Decimal("75"),
        {"cash": "89.28", "equity": "89.28", "last_equity": "89.28"},
        [{"symbol": "SPY", "qty": "0.1", "market_value": "76"}],
        0,
    )
    assert not result.allowed


def test_one_dollar_daily_loss_locks_new_entry():
    result = validate_buy(
        settings(),
        "SPY",
        Decimal("75"),
        {"cash": "88", "equity": "88", "last_equity": "89.28"},
        [],
        0,
    )
    assert not result.allowed
    assert "daily loss" in result.reason


def test_two_entry_attempts_locks_new_entry():
    result = validate_buy(
        settings(),
        "SPY",
        Decimal("75"),
        {"cash": "89.28", "equity": "89.28", "last_equity": "89.28"},
        [],
        2,
    )
    assert not result.allowed
    assert "daily entry-order limit" in result.reason
