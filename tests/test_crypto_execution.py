from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.alpaca_client import AlpacaClient
from app.crypto_execution import CryptoExecutionEngine
from app.risk import validate_crypto_buy
from app.state import RuntimeState
from app.strategy import Signal


class CapturingAlpacaClient(AlpacaClient):
    def __init__(self):
        super().__init__(SimpleNamespace())
        self.calls = []

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"id": "order-1", **(kwargs.get("json") or {})}


def test_crypto_order_adapters_use_gtc_and_stop_limit():
    client = CapturingAlpacaClient()

    asyncio.run(client.submit_crypto_market_buy("BTC/USD", "0.001", "cid-buy"))
    asyncio.run(client.submit_crypto_market_sell("BTC/USD", "0.001", "cid-sell"))
    asyncio.run(
        client.submit_crypto_stop_limit_sell(
            "BTC/USD",
            "0.001",
            "100",
            "99",
            "cid-stop",
        )
    )

    buy = client.calls[0][2]["json"]
    sell = client.calls[1][2]["json"]
    stop = client.calls[2][2]["json"]
    assert buy["time_in_force"] == "gtc"
    assert sell["time_in_force"] == "gtc"
    assert stop["time_in_force"] == "gtc"
    assert stop["type"] == "stop_limit"
    assert stop["stop_price"] == "100"
    assert stop["limit_price"] == "99"


def _risk_settings(**overrides):
    values = dict(
        crypto_lane_enabled=True,
        crypto_execution_enabled=True,
        execution_authorized=True,
        crypto_max_concurrent_positions=1,
        crypto_max_new_entries_per_cycle=1,
        crypto_max_order_notional=Decimal("5"),
        crypto_max_total_position_notional=Decimal("10"),
        max_total_position_notional=Decimal("250"),
        crypto_max_entries_24h=4,
        max_daily_loss=Decimal("10"),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_crypto_risk_isolated_from_equity_position_count():
    settings = _risk_settings()
    account = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "account_blocked": False,
        "trading_blocked": False,
    }
    equity_position = {
        "symbol": "SPY",
        "qty": "1",
        "market_value": "20",
    }
    decision = validate_crypto_buy(
        settings,
        "BTC/USD",
        Decimal("5"),
        account,
        [equity_position],
        0,
        entry_symbols={"BTC/USD"},
    )
    assert decision.allowed is True


def test_crypto_risk_blocks_duplicate_crypto_position():
    settings = _risk_settings()
    account = {
        "cash": "100",
        "equity": "100",
        "last_equity": "100",
        "account_blocked": False,
        "trading_blocked": False,
    }
    decision = validate_crypto_buy(
        settings,
        "BTC/USD",
        Decimal("5"),
        account,
        [{"symbol": "BTC/USD", "qty": "0.001", "market_value": "5"}],
        0,
        entry_symbols={"BTC/USD"},
    )
    assert decision.allowed is False
    assert "already open" in decision.reason


class FakeBroker:
    def __init__(self):
        self.submissions = []

    async def account(self):
        return {
            "cash": "100",
            "equity": "100",
            "last_equity": "100",
            "account_blocked": False,
            "trading_blocked": False,
        }

    async def positions(self):
        return []

    async def open_orders(self):
        return []

    async def recent_orders(self, limit=100):
        return []

    async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
        self.submissions.append((symbol, qty, client_order_id))
        return {
            "id": "crypto-buy-1",
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
        return ("BTC/USD",)


class FakeMarketData:
    async def bars_many(self, symbols):
        now = datetime.now(timezone.utc)
        return {
            symbol: [
                {
                    "t": now.isoformat(),
                    "o": "100",
                    "h": "100.1",
                    "l": "99.9",
                    "c": "100",
                    "v": "10",
                    "n": 5,
                }
            ]
            for symbol in symbols
        }

    async def latest_quotes(self, symbols):
        now = datetime.now(timezone.utc).isoformat()
        return {
            symbol: {
                "bp": "99.95",
                "ap": "100.05",
                "bs": "2",
                "as": "2",
                "t": now,
            }
            for symbol in symbols
        }


class AlwaysBuyStrategy:
    def evaluate(self, **kwargs):
        return Signal(
            action="buy",
            symbol=kwargs["symbol"],
            notional=kwargs["order_notional"],
            reference_price=Decimal("100"),
            stop_price=Decimal("99"),
            take_profit_price=Decimal("101"),
            reason="test crypto signal",
            metadata={"market": "crypto", "session_model": "24x7"},
        )


def _engine_settings(**overrides):
    values = dict(
        crypto_lane_enabled=True,
        crypto_execution_enabled=True,
        execution_authorized=True,
        crypto_max_concurrent_positions=1,
        crypto_max_new_entries_per_cycle=1,
        crypto_max_order_notional=Decimal("5"),
        crypto_max_total_position_notional=Decimal("10"),
        max_total_position_notional=Decimal("250"),
        crypto_max_entries_24h=4,
        max_daily_loss=Decimal("10"),
        crypto_order_notional=Decimal("5"),
        crypto_confirmation_symbols=("ETH/USD",),
        crypto_max_spread_pct=Decimal("0.005"),
        crypto_estimated_round_trip_fee_pct=Decimal("0.005"),
        crypto_estimated_round_trip_slippage_pct=Decimal("0.001"),
        crypto_min_net_edge_pct=Decimal("0.002"),
        crypto_reentry_cooldown_minutes=15,
        crypto_stop_pct=Decimal("0.0035"),
        crypto_target_pct=Decimal("0.005"),
        crypto_max_hold_minutes=60,
        crypto_stop_limit_buffer_pct=Decimal("0.0025"),
        crypto_fast_window=3,
        crypto_volatility_lookback_bars=30,
        crypto_strategy_family="rolling_momentum_vwap",
        crypto_strategy_version_id="CRYPTO-2026-09-29-001",
        crypto_model_version="crypto-rmvwap-model-v1",
        crypto_calibration_version="crypto-calibration-test-v1",
        crypto_regime_version="nostra-crypto-regime-v1",
        crypto_execution_adapter_version="alpaca-crypto-execution-v1",
        crypto_execution_mode="validated",
        crypto_calibration_promoted=True,
        crypto_max_quote_age_seconds=15,
        crypto_min_quoted_depth=Decimal("0"),
        crypto_min_trade_activity=Decimal("0"),
        crypto_ads_threshold=Decimal("-20"),
        crypto_promotion_evidence={
            "resolved_candidate_predictions": 1000,
            "paper_round_trips": 100,
            "utc_hours_covered": list(range(24)),
            "weekdays_covered": list(range(7)),
            "volatility_regimes": ["low", "high"],
            "liquidity_regimes": ["low", "high"],
            "pairs_covered": ["BTC/USD", "ETH/USD", "SOL/USD"],
            "metrics": {
                "net_expectancy_after_costs": 0.01,
                "brier_score": 0.2,
                "log_loss": 0.5,
                "calibration_intercept": 0.0,
                "calibration_slope": 1.0,
                "discrimination": 0.6,
                "max_drawdown": -0.05,
                "tail_loss": -0.02,
                "mfe": 0.01,
                "mae": -0.005,
                "slippage": 0.001,
                "spread_sensitivity": 0.1,
                "regime_stability": 0.8,
                "time_of_week_stability": 0.8,
            },
            "net_expectancy_positive_after_high_costs": True,
            "walk_forward_passed": True,
            "holdout_passed": True,
            "dependence_adjusted": True,
            "multiplicity_adjusted": True,
            "no_lookahead_verified": True,
        },
        order_owner_tag="deadbeef",
        crypto_multi_asset_paper_authorized=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_crypto_execution_engine_submits_crypto_only_order():
    state = RuntimeState()
    state.crypto_graen_promotion = {
        "status": "PROMOTION_READY",
        "promotion_ready": True,
        "reason_codes": [],
    }
    state.begin_crypto_cycle("crypto-test-cycle")
    engine = CryptoExecutionEngine(
        _engine_settings(),
        FakeBroker(),
        FakeMarketData(),
        AlwaysBuyStrategy(),
        state,
        FakeUniverse(),
        ledger=None,
    )
    result = asyncio.run(engine.run_once())
    assert result["action"] == "submitted"
    assert result["symbol"] == "BTC/USD"
    assert result["order"]["time_in_force"] == "gtc"
    assert state.last_order is None
    assert state.crypto_last_order["symbol"] == "BTC/USD"


def test_crypto_position_filter_excludes_equities():
    positions = [
        {"symbol": "SPY", "qty": "1"},
        {"symbol": "BTC/USD", "qty": "0.001"},
    ]
    selected = CryptoExecutionEngine._crypto_positions(positions)
    assert [position["symbol"] for position in selected] == ["BTC/USD"]



class MultiAssetUniverse:
    async def active_symbols(self, *, now=None):
        return ("BTC/USD", "ETH/USD", "SOL/USD")


class MultiAssetMarketData(FakeMarketData):
    async def bars_many(self, symbols):
        now = datetime.now(timezone.utc)
        rows = []
        for i in range(70):
            price = Decimal("100") + Decimal(i) * Decimal("0.03")
            rows.append({
                "t": (now.replace(microsecond=0)).isoformat(),
                "o": str(price - Decimal("0.01")),
                "h": str(price + Decimal("0.03")),
                "l": str(price - Decimal("0.03")),
                "c": str(price),
                "v": "100",
                "n": 10,
            })
        return {symbol: list(rows) for symbol in symbols}


class RankedBuyStrategy:
    strategy_version_id = "CRYPTO-XSECT-PAPER-TEST"

    def evaluate(self, **kwargs):
        symbol = kwargs["symbol"]
        scores = {
            "BTC/USD": (Decimal("0.030"), Decimal("0.030")),
            "ETH/USD": (Decimal("0.025"), Decimal("0.025")),
            "SOL/USD": (Decimal("0.020"), Decimal("0.020")),
        }
        expected_move, score = scores[symbol]
        return Signal(
            action="buy",
            symbol=symbol,
            notional=kwargs["order_notional"],
            reference_price=Decimal("100"),
            stop_price=Decimal("99"),
            take_profit_price=Decimal("102"),
            reason="ranked paper candidate",
            metadata={
                "market": "crypto",
                "session_model": "24x7",
                "expected_gross_move_pct": str(expected_move),
                "opportunity_score": str(score),
            },
        )


def test_multi_asset_paper_submits_top_two_and_counts_virtual_exposure():
    state = RuntimeState()
    state.begin_crypto_cycle("crypto-multi-test")
    broker = FakeBroker()
    settings = _engine_settings(
        crypto_execution_mode="multi_asset_paper",
        crypto_multi_asset_paper_authorized=True,
        crypto_calibration_promoted=False,
        crypto_max_concurrent_positions=3,
        crypto_max_new_entries_per_cycle=2,
        crypto_max_total_position_notional=Decimal("15"),
        crypto_max_entries_24h=12,
    )
    engine = CryptoExecutionEngine(
        settings,
        broker,
        FakeMarketData(),
        RankedBuyStrategy(),
        state,
        MultiAssetUniverse(),
        ledger=None,
    )
    result = asyncio.run(engine.run_once())
    assert result["action"] == "submitted"
    assert [row["symbol"] for row in result["orders"]] == ["BTC/USD", "ETH/USD"]
    assert [row[0] for row in broker.submissions] == ["BTC/USD", "ETH/USD"]


def test_multi_asset_paper_cost_gate_blocks_target_that_cannot_clear_costs():
    state = RuntimeState()
    state.begin_crypto_cycle("crypto-cost-test")
    settings = _engine_settings(
        crypto_execution_mode="multi_asset_paper",
        crypto_multi_asset_paper_authorized=True,
        crypto_max_concurrent_positions=3,
        crypto_max_new_entries_per_cycle=2,
        crypto_estimated_round_trip_fee_pct=Decimal("0.015"),
        crypto_estimated_round_trip_slippage_pct=Decimal("0.005"),
        crypto_min_net_edge_pct=Decimal("0.010"),
    )
    engine = CryptoExecutionEngine(
        settings,
        FakeBroker(),
        FakeMarketData(),
        RankedBuyStrategy(),
        state,
        MultiAssetUniverse(),
        ledger=None,
    )
    result = asyncio.run(engine.run_once())
    assert result["action"] == "hold"
    assert all(
        "estimated crypto move does not clear fees" in reason
        for reason in result["scan_reasons"].values()
    )


def test_multi_asset_paper_requires_explicit_paper_authority():
    state = RuntimeState()
    state.begin_crypto_cycle("crypto-auth-test")
    settings = _engine_settings(
        crypto_execution_mode="multi_asset_paper",
        crypto_multi_asset_paper_authorized=False,
    )
    engine = CryptoExecutionEngine(
        settings,
        FakeBroker(),
        FakeMarketData(),
        RankedBuyStrategy(),
        state,
        MultiAssetUniverse(),
        ledger=None,
    )
    result = asyncio.run(engine.run_once())
    assert result["action"] == "blocked"
    assert "not explicitly authorized" in result["reason"]
