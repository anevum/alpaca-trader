from app.config import Settings
from app.realtime_market import select_hot_symbols
from app.state import RuntimeState


def settings(**overrides):
    base = {
        "ALPACA_API_KEY": "key",
        "ALPACA_API_SECRET": "secret",
        "STRATEGY_NAME": "rolling_momentum_vwap",
        "STRATEGY_SYMBOL": "SPY",
        "SCAN_SYMBOLS": "SPY,QQQ",
        "ALLOWED_SYMBOLS": "SPY,QQQ",
        "CONFIRMATION_SYMBOLS": "QQQ,SMH",
        "DYNAMIC_UNIVERSE_ENABLED": "true",
        "UNIVERSE_SIZE": "10",
        "UNIVERSE_CANDIDATE_POOL_SIZE": "10",
        "POLL_SECONDS": "15",
        "REALTIME_MARKET_ENABLED": "true",
        "REALTIME_SHADOW_ONLY": "true",
        "REALTIME_EXECUTION_ENABLED": "false",
        "REALTIME_SYMBOL_LIMIT": "4",
    }
    base.update(overrides)
    return Settings(**base)


def test_hot_set_prioritizes_required_context_and_respects_limit():
    state = RuntimeState()
    state.universe_active_symbols = ["AAPL", "MSFT", "NVDA", "AMD"]
    selected = select_hot_symbols(settings(), state)
    assert selected == ["SPY", "QQQ", "SMH", "AAPL"]


def test_realtime_execution_cannot_be_enabled_while_shadow_only():
    try:
        settings(
            REALTIME_EXECUTION_ENABLED="true",
            REALTIME_SHADOW_ONLY="true",
        )
    except ValueError as exc:
        assert "REALTIME_EXECUTION_ENABLED requires REALTIME_SHADOW_ONLY=false" in str(exc)
    else:
        raise AssertionError("unsafe real-time execution configuration was accepted")
