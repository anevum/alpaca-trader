from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.btc_direct_strategy import BtcDirectSwingStrategy


def _bars(*, reclaim: bool = True):
    strategy = BtcDirectSwingStrategy()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    daily = []
    for index in range(strategy.sma_window_bars + 2):
        close = Decimal("50000") + Decimal(index * 150)
        daily.append(
            {
                "t": (start + timedelta(days=index)).isoformat(),
                "o": str(close - Decimal("50")),
                "h": str(close + Decimal("100")),
                "l": str(close - Decimal("100")),
                "c": str(close),
            }
        )

    four_hour_start = start + timedelta(days=strategy.sma_window_bars - 8)
    rows = []
    for index in range(30):
        close = Decimal("60000") + Decimal(index * 80)
        rows.append(
            {
                "t": (four_hour_start + timedelta(hours=4 * index)).isoformat(),
                "o": str(close - Decimal("20")),
                "h": str(close + Decimal("40")),
                "l": str(close - Decimal("40")),
                "c": str(close),
            }
        )

    if reclaim:
        baseline = Decimal(rows[-3]["c"])
        dip_close = baseline * Decimal("0.982")
        rows[-2] = {
            "t": rows[-2]["t"],
            "o": str(dip_close * Decimal("1.003")),
            "h": str(dip_close * Decimal("1.004")),
            "l": str(dip_close * Decimal("0.994")),
            "c": str(dip_close),
        }
        reclaim_close = baseline * Decimal("1.004")
        rows[-1] = {
            "t": rows[-1]["t"],
            "o": str(reclaim_close * Decimal("0.997")),
            "h": str(reclaim_close * Decimal("1.002")),
            "l": str(reclaim_close * Decimal("0.996")),
            "c": str(reclaim_close),
        }

    now = start + timedelta(days=strategy.sma_window_bars + 4)
    return rows, daily, now


def test_direct_btc_strategy_enters_on_completed_pullback_reclaim():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(reclaim=True)

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
    assert signal.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-002"
    assert signal.metadata["research_dependency"] is False
    assert signal.metadata["timeframe"] == "4Hour"
    assert signal.metadata["regime_timeframe"] == "1Day"
    assert signal.stop_price > 0
    assert signal.take_profit_price > signal.reference_price


def test_direct_btc_strategy_holds_without_pullback_reclaim():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(reclaim=False)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )

    assert signal.action == "hold"
    assert (
        "pullback" in signal.reason.lower()
        or "reclaim" in signal.reason.lower()
    )


def test_direct_btc_strategy_is_btc_only():
    strategy = BtcDirectSwingStrategy()
    bars, daily, now = _bars(reclaim=True)

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
