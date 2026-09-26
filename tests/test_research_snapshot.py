from app.config import Settings
from app.research_snapshot import research_config_snapshot


def settings():
    return Settings(
        ALPACA_API_KEY="do-not-persist-key",
        ALPACA_API_SECRET="do-not-persist-secret",
        ADMIN_TOKEN="do-not-persist-admin",
        TRADING_MODE="paper",
        STRATEGY_NAME="rolling_momentum_vwap",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="AAPL,MSFT",
        ALLOWED_SYMBOLS="AAPL,MSFT,QQQ,SMH",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
        MIN_CONFIRMATIONS="1",
        ENTRY_START="09:31",
        ENTRY_CUTOFF="15:30",
        STOP_PCT="0.0035",
        TARGET_PCT="0.005",
        FAST_WINDOW="3",
        SLOW_WINDOW="8",
        MIN_MOMENTUM_PCT="0.0005",
        MIN_VWAP_EDGE_PCT="0",
        DYNAMIC_UNIVERSE_ENABLED="true",
        UNIVERSE_SIZE="100",
        UNIVERSE_CANDIDATE_POOL_SIZE="300",
        DATA_FEED="iex",
    )


def test_research_snapshot_contains_replay_critical_identity():
    snapshot = research_config_snapshot(settings())

    assert snapshot["strategy"]["strategy_name"] == "rolling_momentum_vwap"
    assert snapshot["strategy"]["confirmation_symbols"] == ["QQQ", "SMH"]
    assert snapshot["strategy"]["entry_start"] == "09:31"
    assert snapshot["strategy"]["stop_pct"] == "0.0035"
    assert snapshot["market_data"]["data_feed"] == "iex"
    assert snapshot["universe"]["dynamic_universe_enabled"] is True
    assert snapshot["universe"]["universe_size"] == 100
    assert snapshot["universe"]["candidate_pool_size"] == 300


def test_research_snapshot_excludes_credentials_and_admin_secrets():
    snapshot = research_config_snapshot(settings())
    rendered = repr(snapshot)

    assert "do-not-persist-key" not in rendered
    assert "do-not-persist-secret" not in rendered
    assert "do-not-persist-admin" not in rendered
    assert "alpaca_api_key" not in rendered.lower()
    assert "alpaca_api_secret" not in rendered.lower()
    assert "admin_token" not in rendered.lower()
