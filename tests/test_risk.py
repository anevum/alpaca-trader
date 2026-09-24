from decimal import Decimal
from app.config import Settings
from app.risk import validate_buy


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        LIVE_TRADING="false",
        BOT_ARMED="true",
        ALLOWED_SYMBOLS="SPY",
        MAX_ORDER_NOTIONAL="5",
        MAX_POSITION_NOTIONAL="10",
        MAX_DAILY_ORDERS="2",
        MAX_DAILY_LOSS="2",
    )
    base.update(overrides)
    return Settings(**base)


def test_allowlisted_small_buy_passes():
    s = settings()
    result = validate_buy(
        s, "SPY", Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [], 0,
    )
    assert result.allowed


def test_disallowed_symbol_is_blocked():
    s = settings()
    result = validate_buy(
        s, "QQQ", Decimal("5"),
        {"cash": "10", "equity": "10", "last_equity": "10"},
        [], 0,
    )
    assert not result.allowed


def test_live_account_can_be_monitored_while_execution_is_disabled():
    s = settings(TRADING_MODE="live", LIVE_TRADING="false", BOT_ARMED="false")
    assert s.base_url == "https://api.alpaca.markets"
    assert not s.live_execution_authorized
