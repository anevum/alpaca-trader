from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.btc_direct_strategy import BtcDirectSwingStrategy


def _bars(*, breakout: bool = True):
    strategy = BtcDirectSwingStrategy()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(strategy.long_trend_bars + 15):
        close = Decimal("50000") + Decimal(index * 40)
        rows.append(
            {
                "t": (start + timedelta(hours=index)).isoformat(),
                "o": str(close - Decimal("15")),
                "h": str(close + Decimal("20")),
                "l": str(close - Decimal("25")),
                "c": str(close),
            }
        )

    if not breakout:
        prior = Decimal(rows[-2]["c"])
        close = prior + Decimal("5")
        rows[-1] = {
            "t": rows[-1]["t"],
            "o": str(close - Decimal("5")),
            "h": str(close + Decimal("10")),
            "l": str(close - Decimal("15")),
            "c": str(close),
        }

    now = start + timedelta(hours=len(rows) + 1)
    return rows, now


def test_direct_btc_strategy_enters_on_completed_hourly_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )

    assert signal.action == "buy"
    assert signal.symbol == "BTC/USD"
    assert signal.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-003"
    assert signal.metadata["strategy_family"] == "btc_direct_intraday_breakout"
    assert signal.metadata["research_dependency"] is False
    assert signal.metadata["fee_aware"] is True
    assert signal.metadata["timeframe"] == "1Hour"
    assert signal.metadata["regime_timeframe"] == "1Hour"
    assert signal.stop_price > 0
    assert signal.take_profit_price > signal.reference_price


def test_direct_btc_strategy_holds_without_breakout():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=False)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )

    assert signal.action == "hold"
    assert "breakout" in signal.reason.lower()


def test_direct_btc_strategy_is_btc_only():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=True)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )

    assert signal.action == "hold"
    assert "BTC/USD only" in signal.reason


def test_direct_btc_strategy_uses_only_completed_hourly_bars():
    strategy = BtcDirectSwingStrategy()
    bars, now = _bars(breakout=False)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    bars.append(
        {
            "t": current_hour.isoformat(),
            "o": "90000",
            "h": "95000",
            "l": "89900",
            "c": "94900",
        }
    )

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=current_hour + timedelta(minutes=20),
    )

    assert signal.action == "hold"
