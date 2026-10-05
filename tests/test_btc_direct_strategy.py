from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.btc_direct_strategy import BtcDirectSwingStrategy


def _bars(*, breakout: bool = True):
    strategy = BtcDirectSwingStrategy()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    daily = []
    for index in range(strategy.sma_window_bars + 2):
        close = Decimal("50000") + Decimal(index * 20)
        daily.append(
            {
                "t": (start + timedelta(days=index)).isoformat(),
                "o": str(close - Decimal("10")),
                "h": str(close + Decimal("20")),
                "l": str(close - Decimal("20")),
                "c": str(close),
            }
        )

    four_hour_start = start + timedelta(days=strategy.sma_window_bars - 8)
    rows = []
    for index in range(50):
        close = Decimal("55000") + Decimal(index * 10)
        rows.append(
            {
                "t": (four_hour_start + timedelta(hours=4 * index)).isoformat(),
                "o": str(close - Decimal("5")),
                "h": str(close + Decimal("5")),
                "l": str(close - Decimal("10")),
                "c": str(close),
            }
        )
    if breakout:
        prior_high = max(Decimal(row["h"]) for row in rows[-43:-1])
        rows[-1]["c"] = str(prior_high + Decimal("100"))
        rows[-1]["h"] = str(prior_high + Decimal("110"))
        rows[-1]["o"] = str(prior_high + Decimal("90"))
        rows[-1]["l"] = str(prior_high + Decimal("80"))
    else:
        rows[-1]["c"] = rows[-2]["c"]
        rows[-1]["h"] = str(Decimal(rows[-2]["h"]) + Decimal("1"))
        rows[-1]["o"] = rows[-2]["o"]
        rows[-1]["l"] = rows[-2]["l"]

    now = start + timedelta(days=strategy.sma_window_bars + 4)
    return rows, daily, now


def test_direct_btc_strategy_enters_only_on_completed_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "buy"
    assert signal.symbol == "BTC/USD"
    assert signal.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-001"
    assert signal.metadata["research_dependency"] is False
    assert signal.stop_price > 0
    assert signal.take_profit_price == 0


def test_direct_btc_strategy_holds_without_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(breakout=False)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "hold"
    assert "breakout" in signal.reason.lower()


def test_direct_btc_strategy_is_btc_only():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "hold"
    assert "BTC/USD only" in signal.reason
