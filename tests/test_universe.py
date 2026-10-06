from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.state import RuntimeState
from app.universe import DynamicUniverse


NY = ZoneInfo("America/New_York")


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY,QQQ,SMH",
        ALLOWED_SYMBOLS="SPY,QQQ,SMH",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
        DYNAMIC_UNIVERSE_ENABLED="true",
        UNIVERSE_SIZE="10",
        UNIVERSE_CANDIDATE_POOL_SIZE="12",
        UNIVERSE_REFRESH_SECONDS="300",
        UNIVERSE_DAILY_LOOKBACK="5",
        UNIVERSE_DATA_BATCH_SIZE="50",
        UNIVERSE_MIN_PRICE="2",
        UNIVERSE_MIN_AVG_VOLUME="10000",
        UNIVERSE_MIN_AVG_DOLLAR_VOLUME="500000",
        UNIVERSE_EXCHANGES="NASDAQ,NYSE,ARCA",
        UNIVERSE_ALWAYS_INCLUDE="SPY,QQQ,SMH",
        SIZING_MODE="equity_risk",
        PORTFOLIO_LIMIT_MODE="risk",
        MAX_CONCURRENT_POSITIONS="0",
        MAX_NEW_ENTRIES_PER_CYCLE="0",
        MAX_DAILY_ORDERS="0",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_TOTAL_POSITION_NOTIONAL="250",
        STOP_PCT="0.0035",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


class FakeClient:
    async def assets(self, **kwargs):
        symbols = [
            "SPY", "QQQ", "SMH", "AAPL", "MSFT", "NVDA",
            "AMD", "META", "GOOGL", "AMZN", "TSLA", "NFLX",
            "JPM", "XOM",
            "OTCX", "BADF",
        ]
        assets = []
        for symbol in symbols:
            assets.append({
                "symbol": symbol,
                "exchange": "NASDAQ" if symbol not in {"SPY", "QQQ", "SMH"} else "ARCA",
                "status": "active",
                "tradable": True,
                "fractionable": symbol != "BADF",
            })
        assets[-2]["exchange"] = "OTC"
        return assets


class FailingClient:
    async def assets(self, **kwargs):
        raise RuntimeError("asset endpoint unavailable")


class FakeMarketData:
    def __init__(self):
        self.daily_requested = []

    async def stock_screener_symbols(self, **kwargs):
        return ["NVDA", "TSLA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "AMZN", "NFLX"]

    async def daily_bars_many(self, symbols, **kwargs):
        self.daily_requested = list(symbols)
        now = datetime(2026, 9, 24, 16, 0, tzinfo=NY)
        output = {}
        for index, symbol in enumerate(symbols):
            base = Decimal("20") + Decimal(index)
            output[symbol] = [
                {
                    "t": (now - timedelta(days=offset)).isoformat(),
                    "o": str(base),
                    "h": str(base * Decimal("1.03")),
                    "l": str(base * Decimal("0.98")),
                    "c": str(base * Decimal("1.01")),
                    "v": str(50000 + index * 5000),
                }
                for offset in range(5, 0, -1)
            ]
        return output

    async def bars_many(self, symbols):
        now = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
        output = {}
        for index, symbol in enumerate(symbols):
            base = Decimal("20") + Decimal(index)
            output[symbol] = [
                {
                    "t": (now - timedelta(minutes=2)).isoformat(),
                    "o": str(base),
                    "h": str(base * Decimal("1.01")),
                    "l": str(base * Decimal("0.995")),
                    "c": str(base),
                    "v": str(10000 + index * 1000),
                },
                {
                    "t": (now - timedelta(minutes=1)).isoformat(),
                    "o": str(base),
                    "h": str(base * Decimal("1.02")),
                    "l": str(base),
                    "c": str(base * Decimal("1.01")),
                    "v": str(20000 + index * 2000),
                },
            ]
        return output


def test_dynamic_universe_filters_assets_and_builds_bounded_active_set():
    state = RuntimeState()
    manager = DynamicUniverse(
        settings(),
        FakeClient(),
        FakeMarketData(),
        state,
    )
    now = datetime(2026, 9, 25, 10, 1, tzinfo=NY)

    import asyncio
    active = asyncio.run(manager.active_symbols(now=now))

    assert 1 <= len(active) <= 10
    assert {"SPY", "QQQ", "SMH"} <= set(active)
    assert "OTCX" not in active
    assert "BADF" not in active
    assert state.universe_source == "hierarchical_screener"
    assert state.universe_eligible_count >= 12
    assert state.universe_candidate_count >= 10


def test_dynamic_universe_uses_cached_snapshot_inside_refresh_interval():
    state = RuntimeState()
    market = FakeMarketData()
    manager = DynamicUniverse(settings(), FakeClient(), market, state)
    now = datetime(2026, 9, 25, 10, 1, tzinfo=NY)

    import asyncio
    first = asyncio.run(manager.active_symbols(now=now))
    second = asyncio.run(
        manager.active_symbols(now=now + timedelta(seconds=60))
    )

    assert second == first
    assert state.universe_source == "hierarchical_screener"


def test_dynamic_universe_falls_back_to_static_symbols_on_refresh_failure():
    state = RuntimeState()
    manager = DynamicUniverse(
        settings(),
        FailingClient(),
        FakeMarketData(),
        state,
    )
    now = datetime(2026, 9, 25, 10, 1, tzinfo=NY)

    import asyncio
    active = asyncio.run(manager.active_symbols(now=now))

    assert {"SPY", "QQQ", "SMH"} <= set(active)
    assert state.universe_source == "fallback"
    assert "asset endpoint unavailable" in str(state.universe_error)


def test_hierarchical_discovery_does_not_pull_daily_history_for_full_catalog():
    state = RuntimeState()
    market = FakeMarketData()
    manager = DynamicUniverse(settings(), FakeClient(), market, state)
    now = datetime(2026, 9, 25, 10, 1, tzinfo=NY)

    import asyncio
    asyncio.run(manager.active_symbols(now=now))

    assert set(market.daily_requested) <= {
        "SPY", "QQQ", "SMH", "NVDA", "TSLA", "AAPL", "MSFT",
        "AMD", "META", "GOOGL", "AMZN", "NFLX",
    }
    assert "OTCX" not in market.daily_requested
    assert "BADF" not in market.daily_requested
    assert state.universe_eligible_count > len(market.daily_requested)
    assert state.universe_source == "hierarchical_screener"


def test_hierarchical_discovery_falls_back_when_screeners_unavailable():
    class NoScreenerMarket(FakeMarketData):
        async def stock_screener_symbols(self, **kwargs):
            return []

    state = RuntimeState()
    market = NoScreenerMarket()
    manager = DynamicUniverse(settings(), FakeClient(), market, state)
    now = datetime(2026, 9, 25, 10, 1, tzinfo=NY)

    import asyncio
    active = asyncio.run(manager.active_symbols(now=now))

    assert active
    assert state.universe_source == "full_market_fallback"
    assert len(market.daily_requested) == state.universe_eligible_count
