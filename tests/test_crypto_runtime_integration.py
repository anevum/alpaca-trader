from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import main


def _paper_settings():
    settings = main.settings.model_copy(deep=True)
    settings.trading_mode = "paper"
    settings.execution_enabled = True
    settings.live_trading = False
    settings.acknowledge_live = "NO"
    settings.bot_armed = True
    settings.alpaca_api_key = "test-key"
    settings.alpaca_api_secret = "test-secret"
    settings.crypto_lane_enabled = True
    settings.crypto_execution_enabled = True
    settings.crypto_execution_mode = "multi_asset_paper"
    settings.crypto_multi_asset_paper_acknowledge = "YES"
    settings.crypto_only_runtime = True
    settings.crypto_poll_seconds = 15
    settings.crypto_strategy_version_id = "CRYPTO-XSECT-PAPER-001"
    settings.crypto_strategy_family = "cross_sectional_intraday_paper"
    assert settings.crypto_multi_asset_paper_authorized
    assert not settings.live_execution_authorized
    return settings


def test_multi_asset_paper_loop_does_not_require_promotion_lookup(monkeypatch):
    settings = _paper_settings()
    stop = asyncio.Event()
    calls = {"engine": 0}

    async def forbidden_promotion_lookup(_settings):
        raise AssertionError("multi_asset_paper must not depend on promotion status")

    class Engine:
        async def run_once(self):
            calls["engine"] += 1
            assert main.runtime_state.crypto_graen_promotion["status"] == "PAPER_DISCOVERY"
            assert (
                main.runtime_state.crypto_graen_promotion["execution_class"]
                == "MULTI_ASSET_PAPER_DISCOVERY"
            )
            assert main.runtime_state.crypto_graen_promotion["broker_writes_allowed"] is True
            stop.set()
            return {"action": "hold", "reason": "synthetic paper cycle"}

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "_stop", stop)
    monkeypatch.setattr(main, "crypto_engine", Engine())
    monkeypatch.setattr(main, "fetch_crypto_promotion_status", forbidden_promotion_lookup)

    asyncio.run(main.crypto_monitor_loop())

    assert calls["engine"] == 1


def test_command_reports_multi_asset_paper_as_authorized_without_live_promotion(monkeypatch):
    settings = _paper_settings()
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(
        main,
        "crypto_strategy",
        SimpleNamespace(
            strategy_version_id="CRYPTO-XSECT-PAPER-001",
            strategy_family="cross_sectional_intraday_paper",
        ),
    )
    monkeypatch.setattr(main.runtime_state, "paused", False)
    monkeypatch.setattr(
        main.runtime_state,
        "crypto_graen_promotion",
        {"promotion_ready": False, "status": "PAPER_DISCOVERY"},
    )

    crypto = next(
        item
        for item in main._command_active_strategies()
        if item["lane"] == "crypto"
    )

    assert crypto["trading_mode"] == "paper"
    assert crypto["execution_mode"] == "multi_asset_paper"
    assert crypto["signal_authorized"] is True
    assert crypto["signals_enabled"] is True
    assert crypto["execution_authorized"] is True
    assert crypto["broker_writes_allowed"] is True
    assert crypto["entries_enabled"] is True
    assert crypto["manual_approval_required"] is False


def test_preflight_accepts_authorized_btc_direct_live_signal(monkeypatch):
    settings = main.settings.model_copy(deep=True)
    settings.trading_mode = "live"
    settings.bot_armed = True
    settings.crypto_lane_enabled = True
    settings.crypto_execution_enabled = True
    settings.crypto_execution_mode = "btc_direct_live_signal"
    settings.alpaca_api_key = "test-key"
    settings.alpaca_api_secret = "test-secret"
    monkeypatch.setattr(main, "settings", settings)

    assert settings.btc_direct_live_signal_authorized is True
    assert main.crypto_preflight_policy_valid() is True


def test_preflight_rejects_enabled_crypto_without_mode_authorization(monkeypatch):
    settings = main.settings.model_copy(deep=True)
    settings.crypto_lane_enabled = True
    settings.crypto_execution_enabled = True
    settings.crypto_execution_mode = "btc_direct_paper"
    settings.btc_direct_paper_acknowledge = "NO"
    monkeypatch.setattr(main, "settings", settings)

    assert settings.btc_direct_paper_authorized is False
    assert main.crypto_preflight_policy_valid() is False
