"""ANEVUM V5 safety: protect legacy exits while permanently retiring entries.

Test only. No real Alpaca or Railway access and no broker writes.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.risk import validate_buy, validate_extended_buy, validate_sell_to_flat


ACCOUNT = {"cash": "75", "equity": "75", "last_equity": "75"}
POSITION = {"symbol": "SPY", "qty": "0.25", "market_value": "10"}


def settings(**overrides):
    base = {
        "ALPACA_API_KEY": "fake-unit-key",
        "ALPACA_API_SECRET": "fake-unit-secret",
        "TRADING_MODE": "paper",
        "EXECUTION_ENABLED": "true",
        "LIVE_TRADING": "false",
        "BOT_ARMED": "true",
        "SCAN_ONLY": "false",
        "STRATEGY_SYMBOL": "SPY",
        "SCAN_SYMBOLS": "SPY,QQQ",
        "ALLOWED_SYMBOLS": "SPY,QQQ",
        "ORDER_NOTIONAL": "5",
        "MAX_ORDER_NOTIONAL": "5",
        "MAX_POSITION_NOTIONAL": "10",
        "MAX_DAILY_ORDERS": "2",
        "MAX_DAILY_LOSS": "2",
        "POLL_SECONDS": "60",
        "EXTENDED_EQUITY_LANE_ENABLED": "true",
        "EXTENDED_EQUITY_EXECUTION_ENABLED": "true",
        "RHEN_LEGACY_NEW_ENTRY_LOCK": "true",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_lock_defaults_off_and_is_explicit_opt_in():
    cfg = settings(RHEN_LEGACY_NEW_ENTRY_LOCK="false")
    assert not cfg.legacy_new_entries_locked
    result = validate_buy(cfg, "SPY", Decimal("5"), ACCOUNT, [], 0)
    assert result.allowed


def test_lock_blocks_regular_equity_buy_despite_authorized_execution():
    cfg = settings()
    assert cfg.paper_execution_authorized
    assert not validate_buy(cfg, "SPY", Decimal("5"), ACCOUNT, [], 0).allowed


def test_lock_blocks_extended_equity_buy_despite_authorized_execution():
    cfg = settings()
    assert cfg.extended_equity_execution_authorized
    result = validate_extended_buy(
        cfg, "SPY", Decimal("5"), ACCOUNT, [], 0, entry_symbols={"SPY"}
    )
    assert not result.allowed
    assert "migration new-entry lock" in result.reason


def test_lock_preserves_risk_reducing_sell_authority():
    cfg = settings()
    assert cfg.paper_execution_authorized
    assert cfg.legacy_new_entries_locked
    result = validate_sell_to_flat(cfg, "SPY", ACCOUNT, POSITION)
    assert result.allowed
    assert result.reason == "risk-reducing exit allowed"


def test_lock_does_not_disarm_live_execution_for_existing_exits():
    cfg = settings(
        TRADING_MODE="live",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="YES",
        I_ACKNOWLEDGE_EXTENDED_EQUITY_LIVE="YES",
    )
    assert cfg.live_execution_authorized
    assert cfg.extended_equity_execution_authorized
    assert not validate_buy(cfg, "SPY", Decimal("5"), ACCOUNT, [], 0).allowed
    assert not validate_extended_buy(
        cfg, "SPY", Decimal("5"), ACCOUNT, [], 0, entry_symbols={"SPY"}
    ).allowed
    assert validate_sell_to_flat(cfg, "SPY", ACCOUNT, POSITION).allowed


def test_zero_daily_order_limit_is_unlimited_not_a_stop_control():
    cfg = settings(
        RHEN_LEGACY_NEW_ENTRY_LOCK="false",
        MAX_DAILY_ORDERS="0",
        EXTENDED_EQUITY_MAX_ENTRIES_PER_SESSION="0",
    )
    assert validate_buy(cfg, "SPY", Decimal("5"), ACCOUNT, [], 1).allowed
    assert validate_extended_buy(
        cfg, "SPY", Decimal("5"), ACCOUNT, [], 2, entry_symbols={"SPY"}
    ).allowed


def test_command_cannot_reenable_entries_under_retirement_lock(monkeypatch):
    from app import main as service
    async def mock_auth(_authorization):
        return None

    monkeypatch.setattr(service, "require_command_admin", mock_auth)
    monkeypatch.setattr(service.settings, "legacy_new_entries_locked", True)
    service.runtime_state.entries_enabled = False

    async def invoke():
        with pytest.raises(HTTPException) as exc:
            await service.command_enable_entries(None)
        assert exc.value.status_code == 409
        assert "migration entry lock" in str(exc.value.detail)
    asyncio.run(invoke())
    assert service.runtime_state.entries_enabled is False


def test_no_changes_to_exit_policy_in_safety_patch():
    from pathlib import Path
    code = (Path(__file__).resolve().parents[1] / "app" / "risk.py").read_text()
    sell = code.split("def validate_sell_to_flat(", 1)[1]
    assert "legacy_new_entries_locked" not in sell
    assert "risk-reducing exit allowed" in sell
