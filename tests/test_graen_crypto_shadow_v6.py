import asyncio
from datetime import datetime, timedelta, timezone

from app.config import Settings
from graen.crypto.shadow_v6 import CryptoResidualReclaimShadow


SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "AVAX/USD", "LINK/USD")


def _panel():
    start = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    rows = {symbol: [] for symbol in SYMBOLS}
    prices = {
        "BTC/USD": 60000.0,
        "ETH/USD": 3000.0,
        "SOL/USD": 150.0,
        "XRP/USD": 0.60,
        "AVAX/USD": 25.0,
        "LINK/USD": 14.0,
    }
    opportunity_end = start + timedelta(minutes=480)
    for index in range(140):
        stamp = start + timedelta(minutes=5 * index)
        for symbol in SYMBOLS:
            previous = prices[symbol]
            step = 0.0001
            if symbol == "ETH/USD" and index in {93, 94, 95}:
                step = -0.004
            elif symbol == "ETH/USD" and index == 96:
                step = 0.003
            current = previous * (1.0 + step)
            rows[symbol].append(
                {
                    "t": stamp.isoformat().replace("+00:00", "Z"),
                    "o": previous,
                    "h": max(previous, current) * 1.0002,
                    "l": min(previous, current) * 0.9998,
                    "c": current,
                    "v": 1000 + index,
                    "n": 25 + index % 4,
                    "vw": (previous + current) / 2.0,
                }
            )
            prices[symbol] = current
    return rows, opportunity_end


class FakeCryptoMarketData:
    def __init__(self, bars):
        self.bars = bars

    async def bars_many(self, symbols, *, timeframe="1Min", lookback_minutes=None):
        return {
            symbol: list(self.bars.get(symbol, []))
            for symbol in symbols
        }


def _truncate(rows, count):
    return {symbol: values[:count] for symbol, values in rows.items()}


def test_shadow_engine_has_no_broker_execution_surface():
    shadow = CryptoResidualReclaimShadow(Settings(_env_file=None))
    assert shadow.broker_orders_possible is False
    assert shadow.settings.crypto_execution_enabled is False
    assert not hasattr(shadow, "client")
    status = shadow.status()
    assert status["mode"] == "shadow"
    assert status["execution_authority"] is False
    assert status["broker_orders_possible"] is False


def test_shadow_engine_tracks_opportunity_entry_and_exit_without_orders():
    raw, opportunity_end = _panel()
    shadow = CryptoResidualReclaimShadow(Settings(_env_file=None))
    fake = FakeCryptoMarketData(_truncate(raw, 96))
    shadow.market_data = fake

    opportunity_events = asyncio.run(shadow.cycle(now=opportunity_end))
    assert any(
        event["event_type"] == "crypto_shadow_v6_opportunity"
        and event["symbol"] == "ETH/USD"
        for event in opportunity_events
    )
    assert "ETH/USD" in shadow.pending
    assert shadow.entry_count == 0

    fake.bars = _truncate(raw, 98)
    entry_events = asyncio.run(
        shadow.cycle(now=opportunity_end + timedelta(minutes=10))
    )
    assert any(
        event["event_type"] == "crypto_shadow_v6_entry"
        and event["symbol"] == "ETH/USD"
        for event in entry_events
    )
    assert "ETH/USD" in shadow.positions
    assert "ETH/USD" not in shadow.pending
    assert shadow.entry_count == 1

    fake.bars = _truncate(raw, 122)
    exit_events = asyncio.run(
        shadow.cycle(now=opportunity_end + timedelta(minutes=130))
    )
    exits = [
        event
        for event in exit_events
        if event["event_type"] == "crypto_shadow_v6_exit"
        and event["symbol"] == "ETH/USD"
    ]
    assert exits
    assert "stressed_cost_net_return" in exits[0]["payload"]
    assert "ETH/USD" not in shadow.positions
    assert shadow.exit_count == 1
    assert shadow.broker_orders_possible is False
