from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for target in (
    "app/main.py",
    "app/config.py",
    "app/crypto_stats.py",
    "app/crypto_execution.py",
):
    py_compile.compile(str(ROOT / target), doraise=True)

from app.config import get_settings
from app.crypto_execution import CryptoExecutionEngine
from app.crypto_stats import crypto_trade_stats
from app.state import RuntimeState
from app.strategy import Signal


class FakeClient:
    def __init__(self) -> None:
        self.buy_calls: list[dict] = []
        self.stop_calls: list[dict] = []
        self._position_open = False
        self._entry_order: dict | None = None

    async def account(self):
        return {
            "cash": "1000",
            "equity": "1000",
            "last_equity": "1000",
            "buying_power": "1000",
            "trading_blocked": False,
            "account_blocked": False,
        }

    async def positions(self):
        if not self._position_open:
            return []
        return [{
            "symbol": "BTC/USD",
            "asset_class": "crypto",
            "qty": "0.00126",
            "avg_entry_price": "50000",
            "current_price": "50000",
            "market_value": "63",
            "unrealized_pl": "0",
            "unrealized_plpc": "0",
        }]

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        return [self._entry_order] if self._entry_order else []

    async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
        order = {
            "id": "paper-buy-1",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "status": "filled",
            "qty": qty,
            "filled_qty": qty,
            "filled_avg_price": "50000",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        self.buy_calls.append(order)
        self._entry_order = order
        self._position_open = True
        return order

    async def submit_crypto_stop_limit_sell(
        self, symbol, qty, stop_price, limit_price, client_order_id
    ):
        order = {
            "id": "paper-stop-1",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "sell",
            "type": "stop_limit",
            "status": "new",
            "qty": qty,
            "stop_price": stop_price,
            "limit_price": limit_price,
        }
        self.stop_calls.append(order)
        return order

    async def order_by_client_order_id(self, client_order_id):
        return None

    async def cancel_order(self, order_id):
        return None


class FakeMarketData:
    async def bars_many(self, symbols, **kwargs):
        return {symbol: [] for symbol in symbols}

    async def latest_quotes(self, symbols):
        now = datetime.now(timezone.utc).isoformat()
        return {
            symbol: {
                "bp": "49990",
                "ap": "50010",
                "bs": "1",
                "as": "1",
                "t": now,
            }
            for symbol in symbols
        }


class AlwaysBuyStrategy:
    hard_stop_pct = Decimal("0.03")
    take_profit_pct = Decimal("0.05")
    max_hold_minutes = 4320
    manages_position_exits = False
    timeframe = "4Hour"
    required_history_minutes = 14 * 24 * 60
    regime_timeframe = "1Day"
    regime_history_minutes = 100 * 24 * 60

    def evaluate(
        self,
        bars,
        confirmation_bars,
        symbol,
        has_position,
        order_notional,
        now=None,
    ):
        if has_position:
            return Signal(action="hold", symbol=symbol, reason="position open")
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=Decimal("50000"),
            stop_price=Decimal("48500"),
            take_profit_price=Decimal("52500"),
            reason="synthetic verifier signal",
            metadata={"market": "crypto", "session_model": "24x7"},
        )


class DummyUniverse:
    async def active_symbols(self, now=None):
        return ["BTC/USD"]


async def main():
    settings = get_settings()
    assert settings.crypto_execution_mode == "btc_direct_paper"
    assert settings.btc_direct_paper_authorized

    client = FakeClient()
    state = RuntimeState()
    engine = CryptoExecutionEngine(
        settings=settings,
        client=client,
        market_data=FakeMarketData(),
        strategy=AlwaysBuyStrategy(),
        state=state,
        universe=DummyUniverse(),
        ledger=None,
    )

    first = await engine.run_once()
    assert first["action"] == "submitted", first
    assert len(client.buy_calls) == 1, client.buy_calls

    second = await engine.run_once()
    assert second["action"] == "submitted", second
    assert len(client.stop_calls) == 1, client.stop_calls
    assert Decimal(client.stop_calls[0]["stop_price"]) == Decimal("48500.000000000")

    buy_order = {
        "client_order_id": "anevum-crypto-btc-usd-buy-test-1",
        "symbol": "BTC/USD",
        "side": "buy",
        "status": "filled",
        "filled_qty": "0.001",
        "filled_avg_price": "50000",
        "filled_at": "2026-10-05T20:50:00+00:00",
    }
    sell_order = {
        "client_order_id": "anevum-crypto-btc-usd-sell-test-1",
        "symbol": "BTC/USD",
        "side": "sell",
        "status": "filled",
        "filled_qty": "0.001",
        "filled_avg_price": "52500",
        "filled_at": "2026-10-05T21:50:00+00:00",
    }
    scorecard = crypto_trade_stats(
        [buy_order, sell_order],
        [],
        strategy_version_id="RHEN-BTC-DIRECT-002",
        strategy_family="btc_direct_pullback",
        start_at=datetime(2026, 10, 5, 20, 46, 30, tzinfo=timezone.utc),
    )
    assert scorecard["closed_trades"] == 1, scorecard
    assert scorecard["wins"] == 1, scorecard
    assert Decimal(scorecard["realized_pnl"]) > 0, scorecard
    assert Decimal(scorecard["win_rate"]) == Decimal("1"), scorecard

    print("RHEN crypto execution path verifier passed")


if __name__ == "__main__":
    asyncio.run(main())
