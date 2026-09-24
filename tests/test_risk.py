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
        ALLOWED_SYMBOLS="SPY",
        ORDER_NOTIONAL="5",
        MAX_ORDER_NOTIONAL="5",
        MAX_POSITION_NOTIONAL="10",
        MAX_DAILY_ORDERS="2",
        MAX_DAILY_LOSS="2",
        POLL_SECONDS="60",
    )
    base.update(overrides)
    return Settings(**base)


def test_allowlisted_small_paper_buy_passes():
    s = settings()
    result = validate_buy(
        s,
        "SPY",
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
        "QQQ",
        Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [],
        0,
    )
    assert not result.allowed


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


def test_sell_to_flat_is_risk_reducing():
    s = settings(MAX_DAILY_ORDERS="0")
    result = validate_sell_to_flat(
        s,
        "SPY",
        {"trading_blocked": False},
        {"symbol": "SPY", "qty": "0.25", "market_value": "100"},
    )
    assert result.allowed
