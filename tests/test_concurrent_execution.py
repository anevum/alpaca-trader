import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import app.execution as execution_module
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.execution import ExecutionEngine
from app.state import RuntimeState
from app.strategy import RollingMomentumVwapStrategy, Signal


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
    def __init__(self, positions=None, recent_orders=None, open_orders=None):
        self._positions = list(positions or [])
        self._recent_orders = list(recent_orders or [])
        self._open_orders = list(open_orders or [])
        self.buy_orders = []
        self.sell_orders = []
        self.stop_orders = []
        self.replaced_orders = []

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
        return list(self._open_orders)

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

    async def submit_stop_sell(self, symbol, qty, stop_price, client_order_id):
        order = {
            "id": f"stop-{symbol}-{len(self.stop_orders)+1}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "sell",
            "qty": qty,
            "type": "stop",
            "status": "new",
            "stop_price": stop_price,
            "submitted_at": iso_now(),
        }
        self.stop_orders.append(order)
        self._open_orders.append(order)
        return order

    async def replace_stop_order(self, order_id, stop_price):
        for index, order in enumerate(self._open_orders):
            if order.get("id") == order_id:
                replaced = dict(order)
                replaced["id"] = f"{order_id}-r{len(self.replaced_orders)+1}"
                replaced["stop_price"] = stop_price
                self._open_orders[index] = replaced
                self.replaced_orders.append(replaced)
                return replaced
        raise RuntimeError("stop order not found")

    async def cancel_order(self, order_id):
        self._open_orders = [
            order for order in self._open_orders
            if order.get("id") != order_id
        ]
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
        exit_metadata=None,
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
        exit_metadata=None,
    ):
        if self.fail_exits:
            raise RuntimeError("persistence unavailable")
        self.exit_intents.append(
            {
                "symbol": symbol,
                "qty": qty,
                "client_order_id": client_order_id,
                "exit_reason": exit_reason,
                "exit_metadata": exit_metadata or {},
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


def test_portfolio_risk_mode_submits_without_fixed_count_caps():
    client = FakeClient()
    engine = ExecutionEngine(
        settings(
            SIZING_MODE="equity_risk",
            PORTFOLIO_LIMIT_MODE="risk",
            MAX_CONCURRENT_POSITIONS="0",
            MAX_NEW_ENTRIES_PER_CYCLE="0",
            MAX_DAILY_ORDERS="0",
            RISK_PER_TRADE_PCT="0.001",
            MAX_GROSS_EXPOSURE_PCT="0.80",
            MAX_POSITION_GROSS_PCT="0.25",
            MAX_PORTFOLIO_STOP_RISK_PCT="0.01",
            MIN_ORDER_NOTIONAL="1",
            MAX_ORDER_NOTIONAL="80.35",
            MAX_POSITION_NOTIONAL="80.35",
            MAX_TOTAL_POSITION_NOTIONAL="250",
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
    assert notionals == [Decimal("25.00"), Decimal("25.00")]


def test_consecutive_stop_exits_trigger_temporary_entry_cooldown():
    recent_orders = [
        {
            "symbol": "SPY",
            "side": "sell",
            "client_order_id": "anevum-spy-stop-old",
            "submitted_at": iso_now(-181),
            "filled_at": iso_now(-180),
        },
        {
            "symbol": "QQQ",
            "side": "sell",
            "client_order_id": "anevum-qqq-stop-new",
            "submitted_at": iso_now(-61),
            "filled_at": iso_now(-60),
        },
    ]
    client = FakeClient(recent_orders=recent_orders)
    engine = ExecutionEngine(
        settings(
            LOSS_STREAK_LIMIT="2",
            LOSS_STREAK_COOLDOWN_MINUTES="10",
        ),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(engine.run_once())

    assert result["action"] == "hold"
    assert "loss-streak cooldown active" in result["reason"]
    assert client.buy_orders == []


def test_broker_hardstop_is_installed_for_managed_position():
    client = FakeClient()
    engine = ExecutionEngine(
        settings(BROKER_PROTECTIVE_STOP_ENABLED="true"),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(
        engine._ensure_protective_stop(
            position("SPY", entry="100", current="100"),
            [],
            TEST_NOW,
        )
    )

    assert result["action"] == "submitted"
    assert len(client.stop_orders) == 1
    assert client.stop_orders[0]["symbol"] == "SPY"
    assert Decimal(client.stop_orders[0]["stop_price"]) == Decimal("99.65")
    assert "-hardstop-" in client.stop_orders[0]["client_order_id"]


def test_profit_peak_ratchets_existing_broker_stop_upward():
    hardstop = {
        "id": "stop-SPY-1",
        "client_order_id": "anevum-spy-hardstop-existing",
        "symbol": "SPY",
        "side": "sell",
        "qty": "0.2",
        "type": "stop",
        "status": "new",
        "stop_price": "99.65",
        "submitted_at": iso_now(-60),
    }
    client = FakeClient(open_orders=[hardstop])
    engine = ExecutionEngine(
        settings(
            BROKER_PROTECTIVE_STOP_ENABLED="true",
            PROFIT_PROTECT_ENABLED="true",
            PROFIT_PROTECT_ACTIVATION_PCT="0.001",
            PROFIT_PROTECT_RETAIN_FRACTION="0.50",
            PROFIT_PROTECT_MIN_PCT="0.0003",
            PROFIT_STOP_STEP_PCT="0.0002",
        ),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    result = asyncio.run(
        engine._ensure_protective_stop(
            position("SPY", entry="100", current="100.20"),
            [hardstop],
            TEST_NOW,
        )
    )

    assert result["action"] == "replaced"
    assert len(client.replaced_orders) == 1
    assert Decimal(client.replaced_orders[0]["stop_price"]) == Decimal("100.10")
    state = engine.state.exit_states["SPY"]
    assert state["profit_protection_active"] is True
    assert Decimal(state["protected_floor_pct"]) == Decimal("0.0010")


def test_profit_floor_converts_reversal_into_positive_exit():
    engine = ExecutionEngine(
        settings(
            PROFIT_PROTECT_ENABLED="true",
            PROFIT_PROTECT_ACTIVATION_PCT="0.001",
            PROFIT_PROTECT_RETAIN_FRACTION="0.50",
            PROFIT_PROTECT_MIN_PCT="0.0003",
        ),
        FakeClient(),
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    engine._exit_state_for_position(
        position("SPY", entry="100", current="100.20")
    )
    exit_decision = engine._stateful_price_exit(
        position("SPY", entry="100", current="100.05")
    )

    assert exit_decision is not None
    tag, reason = exit_decision
    assert tag == "protect"
    assert "profit-protection floor" in reason
    assert Decimal(engine.state.exit_states["SPY"]["peak_return_pct"]) == Decimal("0.002")
    assert Decimal(engine.state.exit_states["SPY"]["protected_floor_pct"]) == Decimal("0.0010")


class FailingHealthStrategy(RollingMomentumVwapStrategy):
    def __init__(self):
        super().__init__(
            fast_window=3,
            slow_window=8,
            min_momentum_pct=Decimal("0.0005"),
            min_vwap_edge_pct=Decimal("0"),
            stop_pct=Decimal("0.0035"),
            target_pct=Decimal("0.005"),
            entry_start=datetime.strptime("09:31", "%H:%M").time(),
            entry_cutoff=datetime.strptime("15:30", "%H:%M").time(),
            confirmation_symbols=("SMH",),
            min_confirmations=1,
        )

    def position_health(self, **kwargs):
        now = kwargs["now"]
        return {
            "data_ready": True,
            "bar_time": now.replace(second=0, microsecond=0).isoformat(),
            "strong_failure": True,
            "reason": "market regime and position momentum both deteriorated",
            "candidate_failure_count": 3,
            "regime_ok": False,
        }


def test_thesis_failure_requires_two_cycles_before_exit():
    client = FakeClient(
        positions=[position("SPY", entry="100", current="99.95")],
        recent_orders=[bot_buy("SPY")],
    )
    state = reconciled_state()
    engine = ExecutionEngine(
        settings(
            THESIS_EXIT_ENABLED="true",
            THESIS_FAILURE_CYCLES="2",
            THESIS_EXIT_MAX_RETURN_PCT="0.0005",
        ),
        client,
        FakeMarketData(),
        FailingHealthStrategy(),
        state,
    )

    account_payload = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "trading_blocked": False,
        "account_blocked": False,
    }
    first = asyncio.run(
        engine._exit_managed_positions(
            account_payload,
            client._positions,
            [],
            client._recent_orders,
            TEST_NOW,
        )
    )
    assert first == []
    assert state.exit_states["SPY"]["thesis_failure_count"] == 1

    second = asyncio.run(
        engine._exit_managed_positions(
            account_payload,
            client._positions,
            [],
            client._recent_orders,
            TEST_NOW + timedelta(seconds=20),
        )
    )
    assert second == []
    assert state.exit_states["SPY"]["thesis_failure_count"] == 1

    third = asyncio.run(
        engine._exit_managed_positions(
            account_payload,
            client._positions,
            [],
            client._recent_orders,
            TEST_NOW + timedelta(seconds=61),
        )
    )

    assert len(third) == 1
    assert third[0]["action"] == "submitted"
    assert client.sell_orders[-1]["symbol"] == "SPY"
    assert "-thesis-" in client.sell_orders[-1]["client_order_id"]


def test_standing_hardstop_does_not_block_discretionary_profit_exit():
    hardstop = {
        "id": "stop-SPY-1",
        "client_order_id": "anevum-spy-hardstop-existing",
        "symbol": "SPY",
        "side": "sell",
        "qty": "0.2",
        "type": "stop",
        "status": "new",
        "stop_price": "99.65",
        "submitted_at": iso_now(-60),
    }
    client = FakeClient(
        positions=[position("SPY", entry="100", current="100.60")],
        recent_orders=[bot_buy("SPY")],
        open_orders=[hardstop],
    )
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    account_payload = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "trading_blocked": False,
        "account_blocked": False,
    }
    result = asyncio.run(
        engine._exit_managed_positions(
            account_payload,
            client._positions,
            [hardstop],
            client._recent_orders,
            TEST_NOW,
        )
    )

    assert len(result) == 1
    assert result[0]["action"] == "submitted"
    assert client._open_orders == []
    assert len(client.sell_orders) == 1
    assert "-target-" in client.sell_orders[0]["client_order_id"]


def test_exit_state_reconstructs_peak_and_trough_from_bar_path():
    engine = ExecutionEngine(
        settings(
            PROFIT_PROTECT_ENABLED="true",
            PROFIT_PROTECT_ACTIVATION_PCT="0.001",
            PROFIT_PROTECT_RETAIN_FRACTION="0.50",
            PROFIT_PROTECT_MIN_PCT="0.0003",
        ),
        FakeClient(),
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )
    entry_time = TEST_NOW - timedelta(minutes=4)
    bars = [
        {
            "t": (entry_time + timedelta(minutes=1)).isoformat(),
            "o": "100.00",
            "h": "100.30",
            "l": "99.95",
            "c": "100.10",
            "v": "1000",
            "vw": "100.10",
        },
        {
            "t": (entry_time + timedelta(minutes=2)).isoformat(),
            "o": "100.10",
            "h": "100.20",
            "l": "99.90",
            "c": "100.02",
            "v": "1000",
            "vw": "100.02",
        },
    ]

    state = engine._exit_state_for_position(
        position("SPY", entry="100", current="100.02"),
        bars=bars,
        entry_time=entry_time,
    )

    assert Decimal(state["peak_return_pct"]) == Decimal("0.003")
    assert Decimal(state["trough_return_pct"]) == Decimal("-0.001")
    assert state["profit_protection_active"] is True
    assert Decimal(state["protected_floor_pct"]) == Decimal("0.00150")


def test_profitable_hardstop_does_not_count_as_loss_streak():
    orders = [
        {
            "symbol": "SPY",
            "side": "buy",
            "client_order_id": "anevum-spy-buy-existing",
            "status": "filled",
            "filled_qty": "0.2",
            "filled_avg_price": "100.00",
            "submitted_at": iso_now(-240),
            "filled_at": iso_now(-239),
        },
        {
            "symbol": "SPY",
            "side": "sell",
            "client_order_id": "anevum-spy-hardstop-existing",
            "status": "filled",
            "filled_qty": "0.2",
            "filled_avg_price": "100.15",
            "stop_price": "100.10",
            "submitted_at": iso_now(-61),
            "filled_at": iso_now(-60),
        },
    ]
    engine = ExecutionEngine(
        settings(
            LOSS_STREAK_LIMIT="1",
            LOSS_STREAK_COOLDOWN_MINUTES="10",
        ),
        FakeClient(recent_orders=orders),
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    allowed, reason, detail = engine._loss_streak_gate(orders, TEST_NOW)

    assert allowed is True
    assert reason == ""
    assert detail["streak"] == 0


def test_losing_hardstop_counts_as_loss_streak():
    orders = [
        {
            "symbol": "SPY",
            "side": "buy",
            "client_order_id": "anevum-spy-buy-existing",
            "status": "filled",
            "filled_qty": "0.2",
            "filled_avg_price": "100.00",
            "submitted_at": iso_now(-240),
            "filled_at": iso_now(-239),
        },
        {
            "symbol": "SPY",
            "side": "sell",
            "client_order_id": "anevum-spy-hardstop-existing",
            "status": "filled",
            "filled_qty": "0.2",
            "filled_avg_price": "99.65",
            "stop_price": "99.65",
            "submitted_at": iso_now(-61),
            "filled_at": iso_now(-60),
        },
    ]
    engine = ExecutionEngine(
        settings(
            LOSS_STREAK_LIMIT="1",
            LOSS_STREAK_COOLDOWN_MINUTES="10",
        ),
        FakeClient(recent_orders=orders),
        FakeMarketData(),
        BuyStrategy(),
        reconciled_state(),
    )

    allowed, reason, detail = engine._loss_streak_gate(orders, TEST_NOW)

    assert allowed is False
    assert "loss-streak cooldown active" in reason
    assert detail["streak"] == 1
