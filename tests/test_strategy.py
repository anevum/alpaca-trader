from decimal import Decimal

from app.strategy import SmaCrossStrategy


def bars(values):
    return [{"c": str(v)} for v in values]


def test_bullish_cross_generates_buy_when_flat():
    strategy = SmaCrossStrategy(fast_window=2, slow_window=3)
    signal = strategy.evaluate(
        bars([3, 2, 1, 4]),
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("5"),
    )
    assert signal.action == "buy"


def test_bearish_cross_generates_sell_when_long():
    strategy = SmaCrossStrategy(fast_window=2, slow_window=3)
    signal = strategy.evaluate(
        bars([1, 2, 3, 0]),
        symbol="SPY",
        has_position=True,
        order_notional=Decimal("5"),
    )
    assert signal.action == "sell"


def test_no_entry_without_fresh_cross():
    strategy = SmaCrossStrategy(fast_window=2, slow_window=3)
    signal = strategy.evaluate(
        bars([1, 2, 3, 4]),
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("5"),
    )
    assert signal.action == "hold"
