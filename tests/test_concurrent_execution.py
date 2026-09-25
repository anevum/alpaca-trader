import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.execution import ExecutionEngine
from app.state import RuntimeState
from app.strategy import Signal


NY = ZoneInfo("America/New_York")


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
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


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
        now = datetime.now(NY)
        stamp = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
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
        now = datetime.now(NY).isoformat()
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
        RuntimeState(),
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
        RuntimeState(),
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
        RuntimeState(),
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

    def record_broker_order(self, order, **metadata):
        self.broker_orders.append((order, metadata))


def test_new_entry_fails_closed_when_durable_intent_cannot_be_written():
    client = FakeClient()
    ledger = FakeLedger(fail_entries=True)
    engine = ExecutionEngine(
        settings(),
        client,
        FakeMarketData(),
        BuyStrategy(),
        RuntimeState(),
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
