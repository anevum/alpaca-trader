import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import app.execution as execution_module
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.execution import ExecutionEngine
from app.state import RuntimeState
from app.strategy import Signal


NY = ZoneInfo("America/New_York")
TEST_NOW = datetime.now(NY).replace(hour=13, minute=37, second=10, microsecond=0)


@pytest.fixture(autouse=True)
def fixed_execution_clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return TEST_NOW.replace(tzinfo=None)
            return TEST_NOW.astimezone(tz)

    monkeypatch.setattr(execution_module, "datetime", FixedDateTime)


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        EXECUTION_ENABLED="true",
        BOT_ARMED="true",
        STRATEGY_NAME="rolling_momentum_vwap",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY,QQQ",
        ALLOWED_SYMBOLS="SPY,QQQ",
        CONFIRMATION_SYMBOLS="SMH",
        MIN_CONFIRMATIONS="1",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="20",
        MAX_POSITION_NOTIONAL="20",
        MAX_CONCURRENT_POSITIONS="3",
        MAX_NEW_ENTRIES_PER_CYCLE="2",
        MAX_TOTAL_POSITION_NOTIONAL="60",
        MAX_DAILY_ORDERS="12",
        MAX_DAILY_LOSS="1",
        STOP_PCT="0.0035",
        TARGET_PCT="0.005",
        MAX_HOLD_MINUTES="15",
        REENTRY_COOLDOWN_MINUTES="2",
        ENTRY_START="09:31",
        ENTRY_CUTOFF="23:00",
        FORCE_FLAT_TIME="23:59",
        MAX_BAR_AGE_SECONDS="90",
        MAX_SPREAD_PCT="0.002",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


def iso_now(offset_seconds=0):
    return (
        TEST_NOW.astimezone(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def reconciled_state():
    state = RuntimeState()
    state.startup_reconciled = True
    state.reconciliation_safe = True
    state.last_reconciliation = {"safe_to_enter": True}
    return state


class FakeClient:
    def __init__(self, positions=None, recent_orders=None):
        self._positions = list(positions or [])
        self._recent_orders = list(recent_orders or [])
        self.buy_orders = []
        self.sell_orders = []

    async def account(self):
        return {
            "cash": "100",
            "buying_power": "100",
            "equity": "100",
            "last_equity": "100",
            "trading_blocked": False,
            "account_blocked": False,
        }

    async def clock(self):
        return {"is_open": True}

    async def positions(self):
        return list(self._positions)

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        return list(self._recent_orders)

    async def asset(self, symbol):
        return {
            "symbol": symbol,
            "status": "active",
            "tradable": True,
            "fractionable": True,
        }

    async def order_by_client_order_id(self, client_order_id):
        return None

    async def submit_market_buy(self, symbol, qty, client_order_id):
        order = {
            "id": f"buy-{symbol}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "status": "accepted",
            "order_class": "",
            "submitted_at": iso_now(),
        }
        self.buy_orders.append(order)
        return order

    async def submit_market_sell(self, symbol, qty, client_order_id):
        order = {
            "id": f"sell-{symbol}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "sell",
            "qty": qty,
            "status": "accepted",
            "submitted_at": iso_now(),
        }
        self.sell_orders.append(order)
        return order

    async def cancel_order(self, order_id):
        return None


class FakeMarketData:
    def __init__(self):
        self.bar_calls = 0

    def _bar(self):
        stamp = TEST_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
        return {
            "t": stamp.isoformat(),
            "o": "100",
            "h": "100.1",
            "l": "99.9",
            "c": "100",
            "v": "1000",
            "vw": "100",
        }

    async def bars_many(self, symbols):
        self.bar_calls += 1
        return {symbol: [self._bar()] for symbol in symbols}

    async def latest_quotes_many(self, symbols):
        now = TEST_NOW.isoformat()
        return {
            symbol: {"bp": "100.00", "ap": "100.05", "t": now}
            for symbol in symbols
        }


class BuyStrategy:
    def evaluate(
        self,
        bars,
        confirmation_bars,
        symbol,
        has_position,
        order_notional,
        now,
    ):
        if has_position:
            return Signal(
                action="hold",
                symbol=symbol,
                reason="position already open",
            )
        momentum = "0.002" if symbol == "SPY" else "0.001"
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=Decimal("100"),
            stop_price=Decimal("99.65"),
            take_profit_price=Decimal("100.50"),
            reason="test buy",
            metadata={
                "momentum_pct": momentum,
                "vwap_edge_pct": "0.001",
                "confirmation_passes": 1,
                "confirmations": {"SMH": {"ok": True}},
            },
        )


def bot_buy(symbol):
    return {
        "symbol": symbol,
        "side": "buy",
        "client_order_id": f"anevum-{symbol.lower()}-buy-existing",
        "submitted_at": iso_now(-600),
        "filled_at": iso_now(-599),
    }


def bot_sell(symbol, seconds_ago=60):
    return {
        "symbol": symbol,
        "side": "sell",
        "client_order_id": f"anevum-{symbol.lower()}-time-existing",
        "submitted_at": iso_now(-seconds_ago - 1),
        "filled_at": iso_now(-seconds_ago),
    }


def position(symbol, entry="100", current="100", market_value="20"):
    return {
        "symbol": symbol,
        "qty": "0.2",
        "avg_entry_price": entry,
        "current_price": current,
        "market_value": market_value,
    }


def test_engine_can_submit_two_entries_in_one_cycle():
    client = FakeClient()
    market = FakeMarketData()
    engine = ExecutionEngine(
        settings(),
        client,
        market,
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert len(client.buy_orders) == 2
    assert {order["symbol"] for order in client.buy_orders} == {"SPY", "QQQ"}


def test_existing_managed_position_does_not_block_different_symbol_entry():
    client = FakeClient(
        positions=[position("SPY")],
        recent_orders=[bot_buy("SPY")],
    )
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert [order["symbol"] for order in client.buy_orders] == ["QQQ"]
    assert client.sell_orders == []


def test_two_positions_can_exit_in_same_cycle():
    client = FakeClient(
        positions=[
            position("SPY", current="100.60"),
            position("QQQ", current="100.70"),
        ],
        recent_orders=[bot_buy("SPY"), bot_buy("QQQ")],
    )
    market = FakeMarketData()
    engine = ExecutionEngine(
        settings(),
        client,
        market,
        BuyStrategy(),
        RuntimeState(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert {order["symbol"] for order in client.sell_orders} == {"SPY", "QQQ"}
    assert client.buy_orders == []
    assert market.bar_calls == 0


def test_cooling_top_candidate_falls_through_to_next_symbol():
    client = FakeClient(recent_orders=[bot_sell("SPY", seconds_ago=60)])
    engine = ExecutionEngine(
        settings(MAX_NEW_ENTRIES_PER_CYCLE="1"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert [order["symbol"] for order in client.buy_orders] == ["QQQ"]
    assert any(
        item["symbol"] == "SPY" and "cooldown" in item["reason"]
        for item in result["skipped"]
    )


class FakeLedger:
    def __init__(self, *, fail_entries=False, fail_exits=False):
        self.fail_entries = fail_entries
        self.fail_exits = fail_exits
        self.entry_intents = []
        self.exit_intents = []
        self.broker_orders = []

    async def persist_entry_intent(
        self,
        *,
        signal,
        qty,
        client_order_id,
        correlation_id,
        intended_at,
    ):
        if self.fail_entries:
            raise RuntimeError("persistence unavailable")
        self.entry_intents.append(
            {
                "symbol": signal.symbol,
                "qty": qty,
                "client_order_id": client_order_id,
            }
        )
        return {
            "signal_id": "00000000-0000-4000-8000-000000000201",
            "intent_id": "00000000-0000-4000-8000-000000000202",
            "position_id": "00000000-0000-4000-8000-000000000203",
            "client_order_id": client_order_id,
        }

    def persist_exit_intent(
        self,
        *,
        symbol,
        qty,
        client_order_id,
        exit_reason,
        correlation_id,
        intended_at,
    ):
        if self.fail_exits:
            raise RuntimeError("persistence unavailable")
        self.exit_intents.append(
            {
                "symbol": symbol,
                "qty": qty,
                "client_order_id": client_order_id,
                "exit_reason": exit_reason,
            }
        )
        return {
            "intent_id": "00000000-0000-4000-8000-000000000204",
            "exit_id": "00000000-0000-4000-8000-000000000205",
            "client_order_id": client_order_id,
        }

    async def persist_recovered_order(self, order, **metadata):
        self.broker_orders.append((order, metadata))
        return True

    def record_broker_order(self, order, **metadata):
        self.broker_orders.append((order, metadata))


class AmbiguousLostClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.submit_attempts = 0

    async def submit_market_buy(self, symbol, qty, client_order_id):
        self.submit_attempts += 1
        raise RuntimeError("simulated unknown broker submission outcome")


class AmbiguousLostSellClient(FakeClient):
    def __init__(self, positions=None, recent_orders=None):
        super().__init__(positions=positions, recent_orders=recent_orders)
        self.sell_attempts = 0

    async def submit_market_sell(self, symbol, qty, client_order_id):
        self.sell_attempts += 1
        raise RuntimeError("simulated unknown protective sell outcome")


class AmbiguousRecoveredSellClient(FakeClient):
    def __init__(self, positions=None, recent_orders=None):
        super().__init__(positions=positions, recent_orders=recent_orders)
        self.sell_attempts = 0
        self.recovered = {}

    async def submit_market_sell(self, symbol, qty, client_order_id):
        self.sell_attempts += 1
        order = {
            "id": f"recovered-sell-{symbol}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "sell",
            "qty": qty,
            "status": "accepted",
            "order_class": "",
            "submitted_at": iso_now(),
        }
        self.recovered[client_order_id] = order
        raise RuntimeError("simulated response loss after protective sell acceptance")

    async def order_by_client_order_id(self, client_order_id):
        return self.recovered.get(client_order_id)


class AmbiguousRecoveredClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.submit_attempts = 0
        self.recovered = {}

    async def submit_market_buy(self, symbol, qty, client_order_id):
        self.submit_attempts += 1
        order = {
            "id": f"recovered-{symbol}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "status": "accepted",
            "order_class": "",
            "submitted_at": iso_now(),
        }
        self.recovered[client_order_id] = order
        raise RuntimeError("simulated response loss after broker acceptance")

    async def order_by_client_order_id(self, client_order_id):
        return self.recovered.get(client_order_id)


def test_startup_reconciliation_blocks_new_entries():
    client = FakeClient()
    engine = ExecutionEngine(
        settings(MAX_NEW_ENTRIES_PER_CYCLE="1"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        RuntimeState(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert "startup reconciliation" in result["reason"]
    assert client.buy_orders == []


def test_reconciliation_mismatch_blocks_new_entries():
    client = FakeClient()
    state = RuntimeState()
    state.startup_reconciled = True
    state.reconciliation_safe = False
    state.last_reconciliation = {
        "safe_to_enter": False,
        "orphan_broker_positions": [{"symbol": "SPY", "qty": "0.2"}],
    }
    engine = ExecutionEngine(
        settings(MAX_NEW_ENTRIES_PER_CYCLE="1"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        state,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert "reconciliation" in result["reason"]
    assert result["reconciliation"]["orphan_broker_positions"][0]["symbol"] == "SPY"
    assert client.buy_orders == []


def test_ambiguous_submission_recovers_by_client_order_id_without_resubmit():
    client = AmbiguousRecoveredClient()
    ledger = FakeLedger()
    engine = ExecutionEngine(
        settings(MAX_NEW_ENTRIES_PER_CYCLE="1"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert result["symbol"] == "SPY"
    assert client.submit_attempts == 1
    assert any(
        order["id"] == "recovered-SPY"
        for order, _metadata in ledger.broker_orders
    )


def test_ambiguous_submission_blocks_future_entries_when_not_recoverable():
    client = AmbiguousLostClient()
    ledger = FakeLedger()
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(MAX_NEW_ENTRIES_PER_CYCLE="1"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        state,
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "error"
    assert client.submit_attempts == 1
    assert state.reconciliation_safe is False
    assert state.last_reconciliation["reason"] == "ambiguous broker submission"


def test_ambiguous_protective_sell_recovers_without_resubmit():
    client = AmbiguousRecoveredSellClient(
        positions=[position("SPY", current="100.60")],
        recent_orders=[bot_buy("SPY")],
    )
    ledger = FakeLedger()
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        state,
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert client.sell_attempts == 1
    assert any(
        order["id"] == "recovered-sell-SPY"
        and metadata.get("exit_reason")
        for order, metadata in ledger.broker_orders
    )


def test_ambiguous_protective_sell_suppresses_duplicate_retry():
    client = AmbiguousLostSellClient(
        positions=[position("SPY", current="100.60")],
        recent_orders=[bot_buy("SPY")],
    )
    ledger = FakeLedger()
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        state,
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "hold"
    assert client.sell_attempts == 1
    assert state.reconciliation_safe is False
    assert state.last_reconciliation["reason"] == "ambiguous protective exit submission"
    assert "reconciliation required" in result["results"][0]["reason"]


def test_new_entry_fails_closed_when_durable_intent_cannot_be_written():
    client = FakeClient()
    ledger = FakeLedger(fail_entries=True)
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "blocked"
    assert client.buy_orders == []
    assert all(
        item["reason"] == "durable entry intent persistence unavailable"
        for item in result["errors"]
    )


def test_protective_exit_fails_open_when_persistence_is_unavailable():
    client = FakeClient(
        positions=[position("SPY", current="100.60")],
        recent_orders=[bot_buy("SPY")],
    )
    ledger = FakeLedger(fail_exits=True)
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        RuntimeState(),
        ledger=ledger,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert [order["symbol"] for order in client.sell_orders] == ["SPY"]
    assert client.buy_orders == []


def test_equity_risk_allocator_compounds_and_uses_gross_capacity():
    client = FakeClient()
    engine = ExecutionEngine(
        settings(
            SIZING_MODE="equity_risk",
            RISK_PER_TRADE_PCT="0.001",
            MAX_GROSS_EXPOSURE_PCT="0.80",
            MIN_ORDER_NOTIONAL="1",
            MAX_ORDER_NOTIONAL="80.35",
            MAX_POSITION_NOTIONAL="80.35",
            MAX_TOTAL_POSITION_NOTIONAL="250",
            MAX_NEW_ENTRIES_PER_CYCLE="2",
        ),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert len(client.buy_orders) == 2
    notionals = [
        (Decimal(order["qty"]) * Decimal("100")).quantize(Decimal("0.01"))
        for order in client.buy_orders
    ]
    assert notionals == [Decimal("26.66"), Decimal("26.67")]
    assert sum(notionals) == Decimal("53.33")


class CorrelatedMarketData(FakeMarketData):
    def _bars(self, scale=1):
        now = TEST_NOW.replace(second=0, microsecond=0)
        closes = [
            Decimal("100.00"), Decimal("100.08"), Decimal("100.05"),
            Decimal("100.16"), Decimal("100.12"), Decimal("100.25"),
            Decimal("100.21"), Decimal("100.34"), Decimal("100.30"),
            Decimal("100.45"), Decimal("100.41"), Decimal("100.55"),
        ]
        result = []
        for index, close in enumerate(closes):
            adjusted = Decimal("100") + (close - Decimal("100")) * Decimal(str(scale))
            stamp = now - timedelta(minutes=len(closes) - index)
            result.append({
                "t": stamp.isoformat(),
                "o": str(adjusted),
                "h": str(adjusted + Decimal("0.05")),
                "l": str(adjusted - Decimal("0.05")),
                "c": str(adjusted),
                "v": str(1000 + index * 25),
                "vw": str(adjusted),
            })
        return result

    async def bars_many(self, symbols):
        self.bar_calls += 1
        return {symbol: self._bars(1) for symbol in symbols}


def test_allocator_blocks_second_highly_correlated_candidate():
    client = FakeClient()
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(
            MAX_PAIRWISE_CORRELATION="0.85",
            CORRELATION_LOOKBACK_BARS="30",
            CORRELATION_MIN_OBSERVATIONS="8",
        ),
        client,
        CorrelatedMarketData(),
        BuyStrategy(),
        state,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "submitted"
    assert [order["symbol"] for order in client.buy_orders] == ["SPY"]
    assert any(
        item["symbol"] == "QQQ" and "correlation" in item["reason"]
        for item in result["skipped"]
    )
    assert state.last_scan["SPY"]["metadata"]["quality_score"] > state.last_scan["QQQ"]["metadata"]["quality_score"]


def test_quality_gate_rejects_subthreshold_signal_before_execution():
    client = FakeClient()
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(
            MIN_QUALITY_SCORE="100",
            MAX_DAILY_ORDERS="4",
        ),
        client,
        FakeMarketData(),
        BuyStrategy(),
        state,
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "hold"
    assert client.buy_orders == []
    quality_holds = [
        payload
        for payload in state.last_scan.values()
        if "quality score" in str(payload.get("reason", ""))
    ]
    assert quality_holds
    assert all(payload["metadata"]["quality_score"] < 100 for payload in quality_holds)
