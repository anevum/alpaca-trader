from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.crypto_layer import (
    CryptoCrossSectionalPaperStrategy,
    CryptoRollingMomentumStrategy,
    CryptoScanner,
    CryptoUniverse,
)
from app.state import RuntimeState
from app.strategy import Signal


NY = ZoneInfo("America/New_York")


def _bars(start: datetime, closes: list[str]) -> list[dict]:
    rows = []
    for index, raw in enumerate(closes):
        close = Decimal(raw)
        stamp = start + timedelta(minutes=index)
        rows.append({
            "t": stamp.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
            "o": str(close - Decimal("0.10")),
            "h": str(close + Decimal("0.10")),
            "l": str(close - Decimal("0.20")),
            "c": str(close),
            "v": "100",
            "vw": str(close - Decimal("0.02")),
        })
    return rows


def test_crypto_strategy_evaluates_on_weekend():
    strategy = CryptoRollingMomentumStrategy(
        fast_window=2,
        slow_window=3,
        min_momentum_pct=Decimal("0"),
        min_vwap_edge_pct=Decimal("0"),
        stop_pct=Decimal("0.005"),
        target_pct=Decimal("0.01"),
        entry_start=datetime.strptime("09:30", "%H:%M").time(),
        entry_cutoff=datetime.strptime("15:30", "%H:%M").time(),
        confirmation_symbols=("BTC/USD",),
        min_confirmations=1,
        regime_window=3,
        regime_min_confirmations=1,
        regime_min_return_pct=Decimal("0"),
        max_vwap_extension_pct=Decimal("0.05"),
    )
    start = datetime(2026, 9, 26, 12, 0, tzinfo=NY)  # Saturday
    candidate = _bars(start, ["100", "101", "102", "103", "104", "105"])
    confirmation = _bars(start, ["200", "201", "202", "203", "204", "205"])

    signal = strategy.evaluate(
        bars=candidate,
        confirmation_bars={"BTC/USD": confirmation},
        symbol="SOL/USD",
        has_position=False,
        order_notional=Decimal("10"),
        now=start + timedelta(minutes=7),
    )

    assert signal.action == "buy"
    assert signal.metadata["market"] == "crypto"
    assert signal.metadata["session_model"] == "24x7"


def test_crypto_universe_filters_to_configured_quote_and_excludes_stables():
    class FakeClient:
        async def assets(self, *, status: str, asset_class: str):
            assert status == "active"
            assert asset_class == "crypto"
            return [
                {"symbol": "BTC/USD", "status": "active", "tradable": True, "fractionable": True},
                {"symbol": "ETH/USDT", "status": "active", "tradable": True, "fractionable": True},
                {"symbol": "USDC/USD", "status": "active", "tradable": True, "fractionable": True},
                {"symbol": "SOL/USD", "status": "inactive", "tradable": True, "fractionable": True},
                {"symbol": "XRP/USD", "status": "active", "tradable": True, "fractionable": True},
            ]

    settings = SimpleNamespace(
        crypto_quote_currencies={"USD"},
        crypto_excluded_bases={"USDC", "USDT", "USDG"},
    )
    universe = CryptoUniverse(settings, FakeClient(), None, RuntimeState())
    eligible = asyncio.run(universe._eligible())
    assert eligible == ["BTC/USD", "XRP/USD"]


def test_crypto_scanner_does_not_overwrite_equity_scan_state():
    class FakeUniverse:
        async def active_symbols(self, *, now=None):
            return ("BTC/USD",)

    class FakeMarketData:
        async def bars_many(self, symbols):
            return {symbol: [] for symbol in symbols}

    class FakeStrategy:
        def evaluate(self, **kwargs):
            return Signal(
                action="hold",
                symbol=kwargs["symbol"],
                reason="test hold",
                metadata={},
            )

    settings = SimpleNamespace(
        crypto_confirmation_symbols=("ETH/USD",),
        order_notional=Decimal("10"),
    )
    state = RuntimeState()
    scanner = CryptoScanner(
        settings,
        FakeMarketData(),
        FakeStrategy(),
        state,
        FakeUniverse(),
    )
    result = asyncio.run(scanner.scan_once())

    assert result["action"] == "hold"
    assert "BTC/USD" in state.crypto_last_completed_scan
    assert state.last_completed_scan == {}



def test_cross_sectional_paper_strategy_emits_rankable_cost_candidate():
    strategy = CryptoCrossSectionalPaperStrategy(
        fast_window=3,
        slow_window=8,
        min_momentum_pct=Decimal("0.0005"),
        min_vwap_edge_pct=Decimal("0"),
        stop_pct=Decimal("0.008"),
        target_pct=Decimal("0.012"),
        entry_start=datetime.strptime("00:00", "%H:%M").time(),
        entry_cutoff=datetime.strptime("23:59", "%H:%M").time(),
        confirmation_symbols=(),
        min_confirmations=0,
        regime_window=5,
        regime_min_confirmations=0,
        regime_min_return_pct=Decimal("0"),
        max_vwap_extension_pct=Decimal("0.08"),
        strategy_version_id="CRYPTO-XSECT-PAPER-TEST",
    )
    start = datetime(2026, 10, 6, 0, 0, tzinfo=NY)
    closes = [
        str(Decimal("100") + Decimal(index) * Decimal("0.05"))
        for index in range(70)
    ]
    bars = _bars(start, closes)

    signal = strategy.evaluate(
        bars=bars,
        confirmation_bars={},
        symbol="ETH/USD",
        has_position=False,
        order_notional=Decimal("5"),
        now=start + timedelta(minutes=72),
    )

    assert signal.action == "buy"
    assert signal.metadata["strategy_family"] == "cross_sectional_intraday_paper"
    assert Decimal(signal.metadata["expected_gross_move_pct"]) >= Decimal("0.012")
    assert Decimal(signal.metadata["opportunity_score"]) > 0
