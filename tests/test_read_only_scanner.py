import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.scanner import ReadOnlyScanner
from app.state import RuntimeState
from app.strategy import Signal


class ClockOnlyClient:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.forbidden_calls = []

    async def clock(self):
        return {"is_open": self.is_open}

    def __getattr__(self, name):
        if name in {
            "account",
            "positions",
            "open_orders",
            "recent_orders",
            "asset",
            "submit_bracket_market_buy",
            "submit_market_sell",
            "cancel_order",
        }:
            self.forbidden_calls.append(name)
            raise AssertionError(f"read-only scanner touched forbidden method: {name}")
        raise AttributeError(name)


class FakeMarketData:
    def __init__(self):
        self.calls = []

    async def bars_many(self, symbols):
        self.calls.append(list(symbols))
        return {symbol: [{"t": "2026-09-24T15:00:00-04:00"}] for symbol in symbols}


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
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=Decimal("100"),
            stop_price=Decimal("99.65"),
            take_profit_price=Decimal("100.50"),
            reason="qualified test signal",
        )


class HoldStrategy:
    def evaluate(
        self,
        bars,
        confirmation_bars,
        symbol,
        has_position,
        order_notional,
        now,
    ):
        return Signal(action="hold", symbol=symbol, reason="not qualified")


def settings():
    return SimpleNamespace(
        scan_symbols=("SPY", "QQQ"),
        confirmation_symbols=("QQQ", "SMH"),
        order_notional=Decimal("20"),
    )


def test_read_only_scanner_never_touches_account_or_order_methods():
    state = RuntimeState()
    clock = ClockOnlyClient(is_open=True)
    market_data = FakeMarketData()
    scanner = ReadOnlyScanner(
        settings(),
        clock,
        market_data,
        BuyStrategy(),
        state,
    )

    result = asyncio.run(scanner.scan_once())

    assert result["action"] == "shadow_buy"
    assert result["symbol"] == "SPY"
    assert state.last_order is None
    assert clock.forbidden_calls == []
    assert market_data.calls == [["SPY", "QQQ", "SMH"]]


def test_read_only_scanner_records_hold_without_execution_side_effects():
    state = RuntimeState()
    clock = ClockOnlyClient(is_open=True)
    market_data = FakeMarketData()
    scanner = ReadOnlyScanner(
        settings(),
        clock,
        market_data,
        HoldStrategy(),
        state,
    )

    result = asyncio.run(scanner.scan_once())

    assert result["action"] == "hold"
    assert "scan-only watching 2 symbols" in result["reason"]
    assert state.last_order is None
    assert clock.forbidden_calls == []


def test_read_only_scanner_stops_at_closed_market_without_fetching_bars():
    state = RuntimeState()
    clock = ClockOnlyClient(is_open=False)
    market_data = FakeMarketData()
    scanner = ReadOnlyScanner(
        settings(),
        clock,
        market_data,
        BuyStrategy(),
        state,
    )

    result = asyncio.run(scanner.scan_once())

    assert result == {"action": "hold", "reason": "market is closed"}
    assert market_data.calls == []
    assert state.last_order is None
    assert clock.forbidden_calls == []
