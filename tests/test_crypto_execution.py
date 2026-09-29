from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.alpaca_client import AlpacaClient
from app.crypto_execution import CryptoExecutionEngine
from app.risk import validate_crypto_buy
from app.state import RuntimeState
from app.strategy import Signal


class CapturingAlpacaClient(AlpacaClient):
    def __init__(self):
        super().__init__(SimpleNamespace())
        self.calls = []

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"id": "order-1", **(kwargs.get("json") or {})}


def test_crypto_order_adapters_use_gtc_and_stop_limit():
    client = CapturingAlpacaClient()

    asyncio.run(client.submit_crypto_market_buy("BTC/USD", "0.001", "cid-buy"))
    asyncio.run(client.submit_crypto_market_sell("BTC/USD", "0.001", "cid-sell"))
    asyncio.run(
        client.submit_crypto_stop_limit_sell(
            "BTC/USD",
            "0.001",
            "100",
            "99",
            "cid-stop",
        )
    )

    buy = client.calls[0][2]["json"]
    sell = client.calls[1][2]["json"]
    stop = client.calls[2][2]["json"]
    assert buy["time_in_force"] == "gtc"
    assert sell["time_in_force"] == "gtc"
    assert stop["time_in_force"] == "gtc"
    assert stop["type"] == "stop_limit"
    assert stop["stop_price"] == "100"
    assert stop["limit_price"] == "99"


def _risk_settings(**overrides):
    values = dict(
        crypto_lane_enabled=True,
        crypto_execution_enabled=True,
        execution_authorized=True,
        crypto_max_concurrent_positions=1,
        crypto_max_order_notional=Decimal("5"),
        crypto_max_total_position_notional=Decimal("10"),
        max_total_position_notional=Decimal("250"),
        crypto_max_entries_24h=4,
        max_daily_loss=Decimal("10"),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_crypto_risk_isolated_from_equity_position_count():
    settings = _risk_settings()
    account = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "account_blocked": False,
        "trading_blocked": False,
    }
    equity_position = {
        "symbol": "SPY",
        "qty": "1",
        "market_value": "20",
    }
    decision = validate_crypto_buy(
        settings,
        "BTC/USD",
        Decimal("5"),
        account,
        [equity_position],
        0,
        entry_symbols={"BTC/USD"},
    )
    assert decision.allowed is True


def test_crypto_risk_blocks_duplicate_crypto_position():
    settings = _risk_settings()
    account = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "account_blocked": False,
        "trading_blocked": False,
    }
    decision = validate_crypto_buy(
        settings,
        "BTC/USD",
        Decimal("5"),
        account,
        [{"symbol": "BTC/USD", "qty": "0.001", "market_value": "5"}],
        0,
        entry_symbols={"BTC/USD"},
    )
    assert decision.allowed is False
    assert "already open" in decision.reason


class FakeBroker:
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
        return {
            "id": "crypto-buy-1",
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


class FakeUniverse:
    async def active_symbols(self, *, now=None):
        return ("BTC/USD",)


class FakeMarketData:
    async def bars_many(self, symbols):
        return {symbol: [{"c": "100"}] for symbol in symbols}

    async def latest_quotes(self, symbols):
        return {
            symbol: {"bp": "99.95", "ap": "100.05", "t": "2026-09-29T23:00:00Z"}
            for symbol in symbols
        }


class AlwaysBuyStrategy:
    def evaluate(self, **kwargs):
        return Signal(
            action="buy",
            symbol=kwargs["symbol"],
            notional=kwargs["order_notional"],
            reference_price=Decimal("100"),
            stop_price=Decimal("99"),
            take_profit_price=Decimal("101"),
            reason="test crypto signal",
            metadata={"market": "crypto", "session_model": "24x7"},
        )


def _engine_settings():
    return SimpleNamespace(
        crypto_lane_enabled=True,
        crypto_execution_enabled=True,
        execution_authorized=True,
        crypto_max_concurrent_positions=1,
        crypto_max_order_notional=Decimal("5"),
        crypto_max_total_position_notional=Decimal("10"),
        max_total_position_notional=Decimal("250"),
        crypto_max_entries_24h=4,
        max_daily_loss=Decimal("10"),
        crypto_order_notional=Decimal("5"),
        crypto_confirmation_symbols=("ETH/USD",),
        crypto_max_spread_pct=Decimal("0.005"),
        crypto_reentry_cooldown_minutes=15,
        crypto_stop_pct=Decimal("0.0035"),
        crypto_target_pct=Decimal("0.005"),
        crypto_max_hold_minutes=60,
        crypto_stop_limit_buffer_pct=Decimal("0.0025"),
        order_owner_tag="deadbeef",
    )


def test_crypto_execution_engine_submits_crypto_only_order():
    state = RuntimeState()
    state.begin_crypto_cycle("crypto-test-cycle")
    engine = CryptoExecutionEngine(
        _engine_settings(),
        FakeBroker(),
        FakeMarketData(),
        AlwaysBuyStrategy(),
        state,
        FakeUniverse(),
        ledger=None,
    )
    result = asyncio.run(engine.run_once())
    assert result["action"] == "submitted"
    assert result["symbol"] == "BTC/USD"
    assert result["order"]["time_in_force"] == "gtc"
    assert state.last_order is None
    assert state.crypto_last_order["symbol"] == "BTC/USD"


def test_crypto_position_filter_excludes_equities():
    positions = [
        {"symbol": "SPY", "qty": "1"},
        {"symbol": "BTC/USD", "qty": "0.001"},
    ]
    selected = CryptoExecutionEngine._crypto_positions(positions)
    assert [position["symbol"] for position in selected] == ["BTC/USD"]
