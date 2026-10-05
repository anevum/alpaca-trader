from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.btc_direct_strategy import BtcDirectSwingStrategy


def make_fixture(reclaim: bool):
    strategy = BtcDirectSwingStrategy()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    daily = []
    for index in range(strategy.sma_window_bars + 2):
        close = Decimal("50000") + Decimal(index * 150)
        daily.append({
            "t": (start + timedelta(days=index)).isoformat(),
            "o": str(close - Decimal("50")),
            "h": str(close + Decimal("100")),
            "l": str(close - Decimal("100")),
            "c": str(close),
        })

    four_hour_start = start + timedelta(days=strategy.sma_window_bars - 8)
    rows = []
    for index in range(30):
        close = Decimal("60000") + Decimal(index * 80)
        rows.append({
            "t": (four_hour_start + timedelta(hours=4 * index)).isoformat(),
            "o": str(close - Decimal("20")),
            "h": str(close + Decimal("40")),
            "l": str(close - Decimal("40")),
            "c": str(close),
        })

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


def main():
    strategy = BtcDirectSwingStrategy()

    bars, daily, now = make_fixture(True)
    buy = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )
    assert buy.action == "buy", buy
    assert buy.stop_price > 0
    assert buy.take_profit_price > buy.reference_price
    assert buy.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-002"

    bars, daily, now = make_fixture(False)
    hold = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )
    assert hold.action == "hold", hold

    wrong = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": daily},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("63"),
        now=now,
    )
    assert wrong.action == "hold"

    print("RHEN BTC direct verifier passed")


if __name__ == "__main__":
    main()
