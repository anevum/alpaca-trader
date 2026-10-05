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
    "app/risk.py",
    "app/state.py",
    "app/crypto_execution.py",
    "app/crypto_stats.py",
):
    py_compile.compile(str(ROOT / target), doraise=True)

from app.config import get_settings
from app.crypto_execution import CryptoExecutionEngine
from app.state import RuntimeState
from app.strategy import Signal


class NoWriteClient:
    def __init__(self, *, with_position: bool = False) -> None:
        self.with_position = with_position

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
        if not self.with_position:
            return []
        return [{
            "symbol": "BTC/USD",
            "asset_class": "crypto",
            "qty": "0.0001",
            "avg_entry_price": "50000",
            "current_price": "50500",
            "market_value": "5.05",
            "unrealized_pl": "0.05",
            "unrealized_plpc": "0.01",
        }]

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        if not self.with_position:
            return []
        return [{
            "id": "manual-buy-1",
            "client_order_id": "manual-btc-entry",
            "symbol": "BTC/USD",
            "side": "buy",
            "type": "market",
            "status": "filled",
            "filled_qty": "0.0001",
            "filled_avg_price": "50000",
            "submitted_at": "2026-10-05T20:00:00+00:00",
            "filled_at": "2026-10-05T20:00:01+00:00",
        }]

    async def submit_crypto_market_buy(self, *args, **kwargs):
        raise AssertionError("live-signal mode attempted a crypto BUY broker write")

    async def submit_crypto_market_sell(self, *args, **kwargs):
        raise AssertionError("live-signal mode attempted a crypto SELL broker write")

    async def submit_crypto_stop_limit_sell(self, *args, **kwargs):
        raise AssertionError("live-signal mode attempted a protective broker write")

    async def cancel_order(self, *args, **kwargs):
        raise AssertionError("live-signal mode attempted to cancel a broker order")

    async def order_by_client_order_id(self, *args, **kwargs):
        raise AssertionError("live-signal mode should not recover submitted orders")


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
            reason="synthetic live-signal verifier",
            metadata={"market": "crypto", "session_model": "24x7"},
        )


class DummyUniverse:
    async def active_symbols(self, now=None):
        return ["BTC/USD"]


def live_signal_settings():
    settings = get_settings().model_copy(deep=True)
    settings.trading_mode = "live"
    settings.execution_enabled = True
    settings.live_trading = True
    settings.acknowledge_live = "YES"
    settings.bot_armed = True
    settings.alpaca_api_key = "verifier"
    settings.alpaca_api_secret = "verifier"
    settings.crypto_lane_enabled = True
    settings.crypto_execution_enabled = True
    settings.crypto_execution_mode = "btc_direct_live_signal"
    settings.crypto_order_notional = Decimal("5")
    settings.crypto_max_order_notional = Decimal("5")
    settings.crypto_max_total_position_notional = Decimal("10")
    settings.crypto_max_concurrent_positions = 1
    settings.crypto_max_entries_24h = 4
    settings.crypto_max_spread_pct = Decimal("0.005")
    settings.crypto_max_quote_age_seconds = 30
    settings.crypto_min_quoted_depth = Decimal("0")
    settings.crypto_min_trade_activity = Decimal("0")
    settings.crypto_reentry_cooldown_minutes = 0
    settings.crypto_stop_limit_buffer_pct = Decimal("0.0025")
    settings.max_total_position_notional = Decimal("1000")
    settings.max_daily_loss = Decimal("100")
    settings.crypto_strategy_version_id = "RHEN-BTC-DIRECT-002"
    settings.crypto_strategy_family = "btc_direct_pullback"
    assert settings.execution_authorized
    assert settings.btc_direct_live_signal_authorized
    return settings


async def verify_entry_ticket() -> None:
    settings = live_signal_settings()
    state = RuntimeState()
    engine = CryptoExecutionEngine(
        settings=settings,
        client=NoWriteClient(with_position=False),
        market_data=FakeMarketData(),
        strategy=AlwaysBuyStrategy(),
        state=state,
        universe=DummyUniverse(),
        ledger=None,
    )
    result = await engine.run_once()
    assert result["action"] == "pending_approval", result
    ticket = result["ticket"]
    assert ticket["ticket_type"] == "ENTRY", ticket
    assert ticket["side"] == "buy", ticket
    assert ticket["broker_write_performed"] is False, ticket
    assert ticket["manual_action_required"] is True, ticket


async def verify_position_ticket() -> None:
    settings = live_signal_settings()
    state = RuntimeState()
    engine = CryptoExecutionEngine(
        settings=settings,
        client=NoWriteClient(with_position=True),
        market_data=FakeMarketData(),
        strategy=AlwaysBuyStrategy(),
        state=state,
        universe=DummyUniverse(),
        ledger=None,
    )
    result = await engine.run_once()
    assert result["action"] == "pending_approval", result
    ticket = result["ticket"]
    assert ticket["ticket_type"] == "PROTECT", ticket
    assert ticket["side"] == "sell", ticket
    assert ticket["broker_write_performed"] is False, ticket


async def main() -> None:
    await verify_entry_ticket()
    await verify_position_ticket()
    print("RHEN BTC live-account no-write verifier passed")


if __name__ == "__main__":
    asyncio.run(main())
