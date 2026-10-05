from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.btc_direct_strategy import BtcDirectSwingStrategy


def make_fixture(*, breakout: bool):
    strategy = BtcDirectSwingStrategy()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(strategy.long_trend_bars + 15):
        close = Decimal("50000") + Decimal(index * 40)
        rows.append({
            "t": (start + timedelta(hours=index)).isoformat(),
            "o": str(close - Decimal("15")),
            "h": str(close + Decimal("20")),
            "l": str(close - Decimal("25")),
            "c": str(close),
        })

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


def main():
    strategy = BtcDirectSwingStrategy()

    bars, now = make_fixture(breakout=True)
    buy = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )
    assert buy.action == "buy", buy
    assert buy.stop_price > 0
    assert buy.take_profit_price > buy.reference_price
    assert buy.metadata["strategy_version_id"] == "RHEN-BTC-DIRECT-003"
    assert buy.metadata["timeframe"] == "1Hour"

    bars, now = make_fixture(breakout=False)
    hold = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="BTC/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )
    assert hold.action == "hold", hold

    wrong = strategy.evaluate(
        bars=bars,
        confirmation_bars={"BTC/USD": bars},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=now,
    )
    assert wrong.action == "hold"

    print("RHEN BTC direct verifier passed")


if __name__ == "__main__":
    main()
