from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.crypto_execution import CryptoExecutionEngine
from app.state import RuntimeState
from app.strategy import Signal


def _config(**overrides):
    values = {
        "ALPACA_API_KEY": "x",
        "ALPACA_API_SECRET": "y",
        "TRADING_MODE": "paper",
        "EXECUTION_ENABLED": "true",
        "LIVE_TRADING": "false",
        "BOT_ARMED": "true",
        "I_ACKNOWLEDGE_LIVE_TRADING": "NO",
        "STRATEGY_SYMBOL": "SPY",
        "SCAN_SYMBOLS": "SPY",
        "ALLOWED_SYMBOLS": "SPY",
        "CRYPTO_LANE_ENABLED": "true",
        "CRYPTO_EXECUTION_ENABLED": "true",
        "CRYPTO_EXECUTION_MODE": "experimental_active_paper",
        "I_ACKNOWLEDGE_BTC_ACTIVE_PAPER": "YES",
        "CRYPTO_ALWAYS_INCLUDE": "BTC/USD",
        "CRYPTO_CONFIRMATION_SYMBOLS": "ETH/USD",
    }
    values.update(overrides)
    return Settings(**values)


def test_active_paper_authority_is_explicit_and_paper_only():
    settings = _config()
    assert settings.btc_active_paper_authorized is True
    assert settings.live_execution_authorized is False

    with pytest.raises(ValueError, match="TRADING_MODE=paper"):
        _config(
            TRADING_MODE="live",
            LIVE_TRADING="true",
            I_ACKNOWLEDGE_LIVE_TRADING="YES",
        )


class Broker:
    def __init__(self):
        self.buy_calls = []

    async def account(self):
        return {
            "cash": "100",
            "equity": "100",
            "last_equity": "100",
            "account_blocked": False,
            "trading_blocked": False,
        }

    async def positions(self):
        return []

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        return []

    async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
        self.buy_calls.append((symbol, qty, client_order_id))
        return {
            "id": "active-paper-buy-1",
            "symbol": symbol,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "status": "accepted",
            "client_order_id": client_order_id,
        }

    async def order_by_client_order_id(self, client_order_id):
        return None


class Universe:
    async def active_symbols(self, *, now=None):
        return ("ETH/USD", "SOL/USD")


class MarketData:
    async def bars_many(self, symbols):
        now = datetime.now(timezone.utc)
        return {
            symbol: [
                {
                    "t": now.isoformat(),
                    "o": "100",
                    "h": "101",
                    "l": "99",
                    "c": "100",
                    "v": "100",
                    "n": 10,
                }
            ]
            for symbol in symbols
        }

    async def latest_quotes(self, symbols):
        now = datetime.now(timezone.utc).isoformat()
        return {
            symbol: {
                "bp": "99.99",
                "ap": "100.01",
                "bs": "5",
                "as": "5",
                "t": now,
            }
            for symbol in symbols
        }


class AlwaysBuy:
    def evaluate(self, **kwargs):
        return Signal(
            action="buy",
            symbol=kwargs["symbol"],
            notional=kwargs["order_notional"],
            reference_price=Decimal("100"),
            stop_price=Decimal("99.65"),
            take_profit_price=Decimal("100.50"),
            reason="active paper test signal",
            metadata={"market": "crypto", "session_model": "24x7"},
        )


class Ledger:
    def __init__(self):
        self.intents = []
        self.orders = []
        self.cycles = []

    async def persist_entry_intent(self, **kwargs):
        self.intents.append(kwargs)
        return {
            "signal_id": "signal-1",
            "intent_id": "intent-1",
            "position_id": "position-1",
        }

    def record_broker_order(self, order, **kwargs):
        self.orders.append((order, kwargs))

    def record_decision_cycle(self, **kwargs):
        self.cycles.append(kwargs)


def _engine_settings(*, mode="experimental_active_paper", authorized=True):
    return SimpleNamespace(
        crypto_execution_mode=mode,
        btc_active_paper_authorized=authorized,
        crypto_lane_enabled=True,
        crypto_execution_enabled=True,
        execution_authorized=True,
        crypto_max_concurrent_positions=1,
        crypto_max_order_notional=Decimal("63"),
        crypto_max_total_position_notional=Decimal("63"),
        max_total_position_notional=Decimal("80.35"),
        crypto_max_entries_24h=24,
        max_daily_loss=Decimal("10"),
        crypto_order_notional=Decimal("63"),
        crypto_confirmation_symbols=("ETH/USD",),
        crypto_max_spread_pct=Decimal("0.003"),
        crypto_reentry_cooldown_minutes=5,
        crypto_stop_pct=Decimal("0.0035"),
        crypto_target_pct=Decimal("0.005"),
        crypto_max_hold_minutes=60,
        crypto_stop_limit_buffer_pct=Decimal("0.0025"),
        crypto_calibration_promoted=False,
        crypto_max_quote_age_seconds=15,
        crypto_min_quoted_depth=Decimal("0"),
        crypto_min_trade_activity=Decimal("0"),
        crypto_ads_threshold=Decimal("0"),
        order_owner_tag="active01",
    )


def test_active_paper_bypasses_research_promotion_but_keeps_broker_risk_path():
    broker = Broker()
    state = RuntimeState()
    state.crypto_graen_promotion = {
        "status": "GATED",
        "promotion_ready": False,
        "reason_codes": ["PAPER_SAMPLE"],
    }
    state.begin_crypto_cycle("active-paper-test")
    ledger = Ledger()
    engine = CryptoExecutionEngine(
        _engine_settings(),
        broker,
        MarketData(),
        AlwaysBuy(),
        state,
        Universe(),
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert result["symbol"] == "BTC/USD"
    assert len(broker.buy_calls) == 1
    assert broker.buy_calls[0][0] == "BTC/USD"
    assert len(ledger.intents) == 1
    assert state.crypto_last_execution_context["execution_class"] == (
        "EXPERIMENTAL_ACTIVE_PAPER"
    )
    assert state.crypto_last_execution_context["active_universe"] == ["BTC/USD"]


def test_active_paper_refuses_to_trade_without_explicit_acknowledgement():
    broker = Broker()
    state = RuntimeState()
    state.begin_crypto_cycle("active-paper-no-ack")
    engine = CryptoExecutionEngine(
        _engine_settings(authorized=False),
        broker,
        MarketData(),
        AlwaysBuy(),
        state,
        Universe(),
        ledger=Ledger(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert "not explicitly authorized" in result["reason"]
    assert broker.buy_calls == []
