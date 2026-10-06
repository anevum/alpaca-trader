from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
    "app/crypto_layer.py",
    "app/crypto_execution.py",
):
    py_compile.compile(str(ROOT / target), doraise=True)

from app.config import get_settings
from app.crypto_execution import CryptoExecutionEngine
from app.state import RuntimeState
from app.strategy import Signal


class FakeBroker:
    def __init__(self) -> None:
        self.submissions: list[str] = []

    async def account(self):
        return {
            "cash": "100",
            "equity": "100",
            "last_equity": "100",
            "buying_power": "100",
            "trading_blocked": False,
            "account_blocked": False,
        }

    async def positions(self):
        return []

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        return []

    async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
        self.submissions.append(symbol)
        return {
            "id": f"paper-{len(self.submissions)}",
            "symbol": symbol,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "status": "accepted",
            "client_order_id": client_order_id,
        }

    async def order_by_client_order_id(self, client_order_id):
        return None


class FakeUniverse:
    async def active_symbols(self, *, now=None):
        return ("BTC/USD", "ETH/USD", "SOL/USD")


class FakeMarketData:
    async def bars_many(self, symbols, **kwargs):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        output = {}
        for symbol in symbols:
            rows = []
            for index in range(70):
                price = Decimal("100") + Decimal(index) * Decimal("0.05")
                rows.append({
                    "t": (now - timedelta(minutes=69-index)).isoformat(),
                    "o": str(price - Decimal("0.01")),
                    "h": str(price + Decimal("0.03")),
                    "l": str(price - Decimal("0.03")),
                    "c": str(price),
                    "v": "100",
                    "n": 10,
                })
            output[symbol] = rows
        return output

    async def latest_quotes(self, symbols):
        now = datetime.now(timezone.utc).isoformat()
        return {
            symbol: {
                "bp": "99.97",
                "ap": "100.03",
                "bs": "10",
                "as": "10",
                "t": now,
            }
            for symbol in symbols
        }


class RankedPaperStrategy:
    strategy_version_id = "CRYPTO-XSECT-PAPER-VERIFY"

    def evaluate(
        self,
        bars,
        confirmation_bars,
        symbol,
        has_position,
        order_notional,
        now=None,
    ):
        expected = {
            "BTC/USD": Decimal("0.030"),
            "ETH/USD": Decimal("0.025"),
            "SOL/USD": Decimal("0.020"),
        }[symbol]
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=Decimal("100"),
            stop_price=Decimal("99"),
            take_profit_price=Decimal("102"),
            reason="synthetic multi-asset paper verifier",
            metadata={
                "market": "crypto",
                "session_model": "24x7",
                "expected_gross_move_pct": str(expected),
                "opportunity_score": str(expected),
            },
        )


def paper_settings():
    settings = get_settings().model_copy(deep=True)
    settings.trading_mode = "paper"
    settings.execution_enabled = True
    settings.live_trading = False
    settings.acknowledge_live = "NO"
    settings.bot_armed = True
    settings.crypto_lane_enabled = True
    settings.crypto_execution_enabled = True
    settings.crypto_execution_mode = "multi_asset_paper"
    settings.crypto_multi_asset_paper_acknowledge = "YES"
    settings.crypto_order_notional = Decimal("5")
    settings.crypto_max_order_notional = Decimal("5")
    settings.crypto_max_total_position_notional = Decimal("15")
    settings.crypto_max_concurrent_positions = 3
    settings.crypto_max_new_entries_per_cycle = 2
    settings.crypto_max_entries_24h = 12
    settings.crypto_max_spread_pct = Decimal("0.0015")
    settings.crypto_estimated_round_trip_fee_pct = Decimal("0.005")
    settings.crypto_estimated_round_trip_slippage_pct = Decimal("0.001")
    settings.crypto_min_net_edge_pct = Decimal("0.002")
    settings.crypto_max_quote_age_seconds = 5
    settings.crypto_min_quoted_depth = Decimal("0")
    settings.crypto_min_trade_activity = Decimal("0")
    settings.crypto_reentry_cooldown_minutes = 15
    settings.crypto_stop_limit_buffer_pct = Decimal("0.0025")
    settings.max_total_position_notional = Decimal("1000")
    settings.max_daily_loss = Decimal("100")
    assert settings.paper_execution_authorized
    assert settings.crypto_multi_asset_paper_authorized
    assert not settings.live_execution_authorized
    return settings


async def main() -> None:
    broker = FakeBroker()
    state = RuntimeState()
    state.begin_crypto_cycle("multi-asset-paper-verifier")
    engine = CryptoExecutionEngine(
        settings=paper_settings(),
        client=broker,
        market_data=FakeMarketData(),
        strategy=RankedPaperStrategy(),
        state=state,
        universe=FakeUniverse(),
        ledger=None,
    )
    result = await engine.run_once()
    assert result["action"] == "submitted", result
    assert broker.submissions == ["BTC/USD", "ETH/USD"], broker.submissions
    assert len(result["orders"]) == 2, result
    assert result["orders"][0]["estimated_net_edge_pct"] is not None, result
    print("RHEN multi-asset crypto paper verifier passed")


if __name__ == "__main__":
    asyncio.run(main())
