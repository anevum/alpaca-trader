from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.btc_direct_strategy import BtcDirectSwingStrategy


def _bars(*, breakout: bool = True):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    total_hours = 260 * 24
    rows = []
    for index in range(total_hours):
        close = Decimal("50000") + (Decimal(index) / Decimal("100"))
        rows.append(
            {
                "t": (start + timedelta(hours=index)).isoformat(),
                "o": str(close - Decimal("1")),
                "h": str(close + Decimal("2")),
                "l": str(close - Decimal("2")),
                "c": str(close),
            }
        )

    if breakout:
        prior_high = max(Decimal(row["h"]) for row in rows[-(42 * 4 + 4):-4])
        for offset in range(4):
            close = prior_high + Decimal("100") + Decimal(offset)
            rows[-4 + offset] = {
                "t": rows[-4 + offset]["t"],
                "o": str(close - Decimal("1")),
                "h": str(close + Decimal("2")),
                "l": str(close - Decimal("2")),
                "c": str(close),
            }

    now = start + timedelta(hours=total_hours + 1)
    return rows, now


def test_direct_btc_strategy_enters_only_on_completed_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "buy"
    assert signal.symbol == "BTC/USD"
    assert signal.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-001"
    assert signal.metadata["research_dependency"] is False
    assert signal.metadata["timeframe"] == "4Hour"
    assert signal.metadata["source_timeframe"] == "1Hour"
    assert signal.stop_price > 0
    assert signal.take_profit_price == 0


def test_direct_btc_strategy_holds_without_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=False)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "hold"
    assert "breakout" in signal.reason.lower()


def test_direct_btc_strategy_is_btc_only():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "hold"
    assert "BTC/USD only" in signal.reason
