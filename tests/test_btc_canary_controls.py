from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import Settings
from app.crypto_canary import BTC_CANARY_SMA_BARS
from app.crypto_canary_execution import BtcCanaryExecutionEngine
from app.risk import validate_btc_canary_buy
from app.research_scheduler import ResearchReportScheduler
from app.state import RuntimeState


def _settings(**overrides):
    values = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        EXECUTION_ENABLED="true",
        LIVE_TRADING="false",
        BOT_ARMED="true",
        I_ACKNOWLEDGE_LIVE_TRADING="NO",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY",
        CRYPTO_LANE_ENABLED="true",
        CRYPTO_EXECUTION_ENABLED="false",
        CRYPTO_EXECUTION_MODE="experimental_canary",
        BTC_CANARY_ENABLED="true",
        I_ACKNOWLEDGE_BTC_CANARY_EXPERIMENT="YES",
        BTC_CANARY_ORDER_NOTIONAL="1.00",
        BTC_CANARY_MAX_ORDER_NOTIONAL="1.00",
        BTC_CANARY_MAX_TOTAL_POSITION_NOTIONAL="1.00",
        BTC_CANARY_MAX_ENTRIES_24H="1",
        BTC_CANARY_MAX_SPREAD_PCT="0.003",
        BTC_CANARY_MAX_SLIPPAGE_PCT="0.005",
        BTC_CANARY_STOP_PCT="0.05",
        BTC_CANARY_HISTORY_DAYS="270",
    )
    values.update(overrides)
    return Settings(**values)


def test_canary_authorization_is_paper_only():
    settings = _settings()
    assert settings.paper_execution_authorized is True
    assert settings.btc_canary_execution_authorized is True

    live = _settings(
        TRADING_MODE="live",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="YES",
    )
    assert live.live_execution_authorized is True
    assert live.btc_canary_execution_authorized is False


def test_canary_mode_cannot_enable_validated_crypto_execution():
    with pytest.raises(ValueError, match="CRYPTO_EXECUTION_ENABLED"):
        _settings(CRYPTO_EXECUTION_ENABLED="true")


def test_canary_does_not_start_embedded_research_scheduler():
    scheduler = ResearchReportScheduler(
        _settings(),
        object(),
        object(),
        RuntimeState(),
        object(),
    )

    asyncio.run(scheduler.start())

    assert scheduler.task is None


def test_canary_risk_gate_is_btc_only_and_single_entry_per_24h():
    settings = _settings()
    account = {
        "cash": "10",
        "equity": "10",
        "last_equity": "10",
        "account_blocked": False,
        "trading_blocked": False,
    }

    allowed = validate_btc_canary_buy(
        settings,
        "BTC/USD",
        Decimal("1"),
        account,
        [],
        0,
    )
    assert allowed.allowed is True

    wrong_symbol = validate_btc_canary_buy(
        settings,
        "ETH/USD",
        Decimal("1"),
        account,
        [],
        0,
    )
    assert wrong_symbol.allowed is False
    assert "BTC/USD" in wrong_symbol.reason

    repeated = validate_btc_canary_buy(
        settings,
        "BTC/USD",
        Decimal("1"),
        account,
        [],
        1,
    )
    assert repeated.allowed is False
    assert "24-hour" in repeated.reason


class PaperBroker:
    def __init__(self):
        self.buy_calls = []

    async def account(self):
        return {
            "cash": "10",
            "equity": "10",
            "last_equity": "10",
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
            "id": "paper-canary-buy-1",
            "symbol": symbol,
            "qty": qty,
            "filled_qty": qty,
            "filled_avg_price": "100.01",
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "status": "filled",
            "client_order_id": client_order_id,
        }

    async def order_by_client_order_id(self, client_order_id):
        return None


class DurableLedger:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.entry_intents = []
        self.broker_orders = []
        self.cycles = []
        self.position_metrics = []

    async def persist_entry_intent(self, **kwargs):
        self.entry_intents.append(kwargs)
        return {
            "signal_id": "signal-1",
            "intent_id": "intent-1",
            "position_id": "position-1",
            "client_order_id": kwargs["client_order_id"],
        }

    def record_broker_order(self, order, **kwargs):
        self.broker_orders.append((order, kwargs))

    def record_decision_cycle(self, **kwargs):
        self.cycles.append(kwargs)

    def record_position_metrics(self, **kwargs):
        self.position_metrics.append(kwargs)


class ProtectedPositionBroker(PaperBroker):
    async def positions(self):
        return [
            {
                "asset_class": "crypto",
                "symbol": "BTC/USD",
                "qty": "0.00001",
                "market_value": "1.00",
                "avg_entry_price": "90.00",
                "current_price": "100.00",
            }
        ]

    async def open_orders(self):
        return [
            {
                "asset_class": "crypto",
                "symbol": "BTC/USD",
                "id": "paper-canary-stop-1",
                "client_order_id": "anevum-crypto-btc-usd-hardstop-existing",
                "side": "sell",
                "status": "new",
                "qty": "0.00001",
            }
        ]


class CanaryMarketData:
    async def historical_bars_many(self, symbols, *, start, end, timeframe="1Min"):
        assert timeframe == "4Hour"
        count = BTC_CANARY_SMA_BARS + 10
        first = end - timedelta(hours=4 * (count + 2))
        rows = [
            {
                "t": (first + timedelta(hours=4 * index)).isoformat(),
                "c": str(Decimal("80") + Decimal(index) / Decimal("100")),
            }
            for index in range(count)
        ]
        return {"BTC/USD": rows}

    async def latest_quotes(self, symbols):
        now = datetime.now(timezone.utc)
        return {
            "BTC/USD": {
                "bp": "99.99",
                "ap": "100.01",
                "bs": "2",
                "as": "2",
                "t": now.isoformat(),
            }
        }


def test_canary_engine_submits_only_paper_btc_order_after_durable_intent():
    settings = _settings()
    broker = PaperBroker()
    ledger = DurableLedger()
    state = RuntimeState()
    state.begin_crypto_cycle("btc-canary-test")
    engine = BtcCanaryExecutionEngine(
        settings,
        broker,
        CanaryMarketData(),
        state,
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert result["symbol"] == "BTC/USD"
    assert len(ledger.entry_intents) == 1
    assert len(broker.buy_calls) == 1
    assert broker.buy_calls[0][0] == "BTC/USD"
    assert state.crypto_last_execution_context["execution_class"] == "EXPERIMENTAL_PAPER"


def test_canary_blocks_new_entry_when_durable_persistence_is_not_configured():
    settings = _settings()
    broker = PaperBroker()
    state = RuntimeState()
    state.begin_crypto_cycle("btc-canary-persistence-block-test")
    engine = BtcCanaryExecutionEngine(
        settings,
        broker,
        CanaryMarketData(),
        state,
        ledger=DurableLedger(enabled=False),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert "persistence is not configured" in result["reason"]
    assert broker.buy_calls == []


def test_canary_protects_existing_position_even_when_entry_persistence_is_disabled():
    settings = _settings()
    broker = ProtectedPositionBroker()
    state = RuntimeState()
    state.begin_crypto_cycle("btc-canary-existing-position-test")
    engine = BtcCanaryExecutionEngine(
        settings,
        broker,
        CanaryMarketData(),
        state,
        ledger=DurableLedger(enabled=False),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "hold"
    assert result["symbol"] == "BTC/USD"
    assert "position protected" in result["reason"]
    assert result["protection"]["action"] == "protected"
    assert broker.buy_calls == []


def test_canary_engine_hard_blocks_live_mode_before_order_submission():
    settings = _settings(
        TRADING_MODE="live",
        LIVE_TRADING="true",
        I_ACKNOWLEDGE_LIVE_TRADING="YES",
    )
    broker = PaperBroker()
    state = RuntimeState()
    state.begin_crypto_cycle("btc-canary-live-block-test")
    engine = BtcCanaryExecutionEngine(
        settings,
        broker,
        CanaryMarketData(),
        state,
        ledger=None,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert "paper" in result["reason"].lower()
    assert broker.buy_calls == []



class HighSlippagePaperBroker(PaperBroker):
    async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
        order = await super().submit_crypto_market_buy(symbol, qty, client_order_id)
        order["filled_avg_price"] = "100.60"
        return order


def test_canary_opens_circuit_when_paper_fill_slippage_exceeds_threshold():
    settings = _settings()
    broker = HighSlippagePaperBroker()
    state = RuntimeState()
    state.begin_crypto_cycle("btc-canary-slippage-test")
    engine = BtcCanaryExecutionEngine(
        settings,
        broker,
        CanaryMarketData(),
        state,
        ledger=DurableLedger(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert result["circuit_open_reason"] == "entry_slippage_threshold_exceeded"
    assert (
        state.crypto_last_execution_context["circuit_open_reason"]
        == "entry_slippage_threshold_exceeded"
    )
