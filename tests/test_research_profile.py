from decimal import Decimal

from app.config import Settings
from app.research_profile import (
    STRATEGY_005_PROFILE_ID,
    apply_strategy_005_entry_profile,
    strategy_005_profile_hash,
    strategy_005_profile_manifest,
)


def test_strategy_005_profile_overrides_ambient_strategy_settings():
    ambient = Settings(
        STRATEGY_NAME="opening_range_vwap",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY",
        CONFIRMATION_SYMBOLS="SPY",
        FAST_WINDOW="9",
        SLOW_WINDOW="30",
        MIN_MOMENTUM_PCT="0.01",
        MIN_VWAP_EDGE_PCT="0.02",
        MIN_CONFIRMATIONS="0",
        REGIME_MIN_CONFIRMATIONS="0",
        MAX_VWAP_EXTENSION_PCT="0.05",
        STOP_PCT="0.02",
        TARGET_PCT="0.03",
        ENTRY_START="10:30",
        ENTRY_CUTOFF="11:00",
        MAX_BAR_AGE_SECONDS="5",
        MAX_SPREAD_PCT="0.05",
        MIN_QUALITY_SCORE="0",
        DATA_FEED="sip",
    )

    locked = apply_strategy_005_entry_profile(ambient)

    assert locked.strategy_name == "rolling_momentum_vwap"
    assert locked.scan_symbols == ("AAPL", "MSFT", "SMCI", "TQQQ", "SOXL")
    assert locked.confirmation_symbols == ("QQQ", "SMH")
    assert locked.dynamic_universe_enabled is False
    assert locked.fast_window == 3
    assert locked.slow_window == 8
    assert locked.min_momentum_pct == Decimal("0.0005")
    assert locked.min_vwap_edge_pct == Decimal("0")
    assert locked.min_confirmations == 1
    assert locked.regime_min_confirmations == 1
    assert locked.max_vwap_extension_pct == Decimal("0.008")
    assert locked.stop_pct == Decimal("0.0035")
    assert locked.target_pct == Decimal("0.005")
    assert locked.min_quality_score == Decimal("80")
    assert locked.data_feed == "iex"


def test_strategy_005_profile_hash_is_stable_and_manifest_is_copy():
    first = strategy_005_profile_hash()
    second = strategy_005_profile_hash()
    manifest = strategy_005_profile_manifest()

    assert first == second
    assert len(first) == 64
    assert manifest["profile_id"] == STRATEGY_005_PROFILE_ID

    manifest["scan_symbols"].append("FAKE")
    assert "FAKE" not in strategy_005_profile_manifest()["scan_symbols"]
