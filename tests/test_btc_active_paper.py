from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import pytest

from app.config import Settings
from app.crypto_execution import CryptoExecutionEngine
from app.crypto_layer import CryptoRollingMomentumStrategy
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
    return _config(
        CRYPTO_EXECUTION_MODE=mode,
        I_ACKNOWLEDGE_BTC_ACTIVE_PAPER=("YES" if authorized else "NO"),
        CRYPTO_ORDER_NOTIONAL="63",
        CRYPTO_MAX_ORDER_NOTIONAL="63",
        CRYPTO_MAX_TOTAL_POSITION_NOTIONAL="63",
        CRYPTO_MAX_ENTRIES_24H="24",
        CRYPTO_MAX_SPREAD_PCT="0.003",
        CRYPTO_REENTRY_COOLDOWN_MINUTES="5",
        CRYPTO_STOP_PCT="0.005",
        CRYPTO_TARGET_PCT="0.010",
        CRYPTO_MAX_HOLD_MINUTES="120",
        CRYPTO_CALIBRATION_PROMOTED="false",
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



def test_active_paper_confirmation_gates_can_be_observation_only():
    settings = _config(
        CRYPTO_MIN_CONFIRMATIONS="0",
        CRYPTO_REGIME_MIN_CONFIRMATIONS="0",
    )
    assert settings.crypto_min_confirmations == 0
    assert settings.crypto_regime_min_confirmations == 0


def test_negative_vwap_edge_allows_small_below_vwap_tolerance():
    now = datetime.now(timezone.utc)
    bars = []
    closes = [
        Decimal("99.80"),
        Decimal("99.84"),
        Decimal("99.88"),
        Decimal("99.90"),
        Decimal("99.92"),
        Decimal("99.94"),
        Decimal("99.96"),
        Decimal("99.98"),
        Decimal("100.00"),
    ]
    for index, close in enumerate(closes):
        stamp = now - timedelta(minutes=len(closes) - index + 1)
        bars.append(
            {
                "t": stamp.isoformat(),
                "o": str(close - Decimal("0.01")),
                "h": str(close + Decimal("0.02")),
                "l": str(close - Decimal("0.02")),
                "c": str(close),
                "v": "10",
                "vw": "100.10",
                "n": 5,
            }
        )

    strategy = CryptoRollingMomentumStrategy(
        fast_window=3,
        slow_window=8,
        min_momentum_pct=Decimal("0"),
        min_vwap_edge_pct=Decimal("-0.002"),
        stop_pct=Decimal("0.005"),
        target_pct=Decimal("0.010"),
        entry_start=time(0, 0),
        entry_cutoff=time(23, 59),
        confirmation_symbols=(),
        min_confirmations=0,
        regime_window=5,
        regime_min_confirmations=0,
        regime_min_return_pct=Decimal("0"),
    )
    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "buy"
    assert signal.metadata["checks"]["vwap_ok"] is True
    assert Decimal(signal.metadata["vwap_edge_pct"]) < 0
