import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.btc_day_strategy import BtcDayTradeStrategy
from app.btc_day_preview import BtcDayPreview
from app.crypto_execution import CryptoExecutionEngine
from app.crypto_symbols import normalized_crypto_row
from app.risk import validate_crypto_buy
from app.state import RuntimeState
from test_crypto_execution import (AlwaysBuyStrategy, FakeBroker, FakeMarketData,
                                  FakeUniverse, _engine_settings, _risk_settings)

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def bars(now=NOW):
    result = []
    for i in range(96):
        close = Decimal("100") + Decimal(i) / 10
        result.append({"t": (now - timedelta(minutes=15 * (96-i))).isoformat(),
                       "o": str(close - Decimal(".04")), "h": str(close + Decimal(".02")),
                       "l": str(close - Decimal(".06")), "c": str(close)})
    return result


def signal(rows=None, **kwargs):
    return BtcDayTradeStrategy().evaluate(bars=bars() if rows is None else rows,
        confirmation_bars={}, symbol="BTC/USD", has_position=False,
        order_notional=Decimal("5"), now=NOW, **kwargs)


def quote(**kwargs):
    return {"bp": "109.49", "ap": "109.51", "as": "10", "t": NOW.isoformat(), **kwargs}


def test_intraday_completed_breakout_and_no_future_leakage():
    baseline = signal()
    assert baseline.action == "buy"
    future = {"t": NOW.isoformat(), "o": "10000", "h": "10001", "l": "9999", "c": "10000"}
    assert signal([*bars(), future]) == baseline
    assert baseline.metadata["live_execution_authorized"] is False
    assert baseline.metadata["validation_state"] == "UNVALIDATED_DESIGN"


@pytest.mark.parametrize("mutation", ["gap", "stale", "duplicate", "nan", "warmup"])
def test_intraday_bad_history_fails_closed(mutation):
    rows = bars()
    if mutation == "gap": rows.pop(60)
    if mutation == "stale": rows = bars(NOW - timedelta(minutes=15))
    if mutation == "duplicate": rows.append(dict(rows[-1]))
    if mutation == "nan": rows[-1]["c"] = "NaN"
    if mutation == "warmup": rows = rows[-95:]
    assert signal(rows).action == "hold"


def test_parameters_are_fixed_and_friction_cannot_be_zeroed():
    strategy = BtcDayTradeStrategy()
    with pytest.raises(TypeError): strategy.parameters["fast_ema"] = 1
    settings = _engine_settings(crypto_estimated_round_trip_fee_pct=Decimal("0"),
                               crypto_estimated_round_trip_slippage_pct=Decimal("0"))
    result = strategy.friction_check(signal(), quote(), settings, NOW)
    assert result.action == "buy"
    assert result.reference_price == Decimal("109.51")
    assert Decimal(result.metadata["cost_model"]["round_trip_fee_pct"]) == Decimal(".005")
    assert Decimal(result.metadata["cost_model"]["slippage_pct"]) == Decimal(".001")
    assert result.metadata["cost_model"]["basis"] == "movement_budget_only_not_measured_expectancy"


@pytest.mark.parametrize("changes", [{"as": "0"}, {"t": (NOW-timedelta(minutes=1)).isoformat()},
                                    {"bp": "100", "ap": "109.51"}, {"ap": "112"}, {"ap": "NaN"}])
def test_intraday_unexecutable_quote_holds(changes):
    assert BtcDayTradeStrategy.friction_check(signal(), quote(**changes), _engine_settings(), NOW).action == "hold"


def test_intraday_reversal_exit_uses_completed_data():
    rows = bars()
    for row in rows[-20:]:
        row.update(o="98", h="99", l="97", c="98")
    assert BtcDayTradeStrategy().position_exit_reason(rows, [], now=NOW) == "intraday trend reversal"


@pytest.mark.parametrize("mode", ["validated", "btc_direct_paper", "btc_direct_live_signal"])
def test_unvalidated_day_design_cannot_get_broker_authority(mode):
    class ForbiddenBroker:
        def __getattr__(self, key): raise AssertionError("preview must never contact broker")
    engine = CryptoExecutionEngine(_engine_settings(crypto_execution_mode=mode), ForbiddenBroker(),
                                  None, BtcDayTradeStrategy(), RuntimeState(), None)
    assert asyncio.run(engine.run_once())["action"] == "blocked"
    with pytest.raises(ValueError): engine._client_order_id("BTC/USD", "buy")


def test_preview_reads_existing_data_only_and_reports_actual_hypothesis(monkeypatch):
    monkeypatch.setenv("BTC_DAY_PREVIEW_ENABLED", "true")
    class Data:
        async def bars_many(self, symbols, *, timeframe, lookback_minutes):
            assert symbols == ["BTC/USD"] and timeframe == "15Min" and lookback_minutes == 4320
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            return {"BTC/USD": bars(now)}
        async def latest_quotes(self, symbols): return {"BTC/USD": quote()}
    preview = BtcDayPreview(Data(), _engine_settings())
    asyncio.run(preview.observe())
    assert preview.snapshot["status"] == "UNVALIDATED_PREVIEW"
    assert preview.snapshot["broker_writes_allowed"] is False
    assert preview.snapshot["account_assumption"] == "flat_hypothesis_only"
    assert preview.snapshot["activation_blockers"]


def test_compact_broker_positions_block_duplicate_entries_but_leave_equities():
    account = asyncio.run(FakeBroker().account())
    position = {"symbol": "BTCUSD", "asset_class": "crypto", "qty": ".01", "market_value": "5"}
    decision = validate_crypto_buy(_risk_settings(), "BTC/USD", Decimal("5"), account, [position], 0)
    assert not decision.allowed and "already open" in decision.reason
    assert CryptoExecutionEngine._owned_symbols([position], [{"symbol": "BTCUSD", "side": "buy",
        "status": "filled", "client_order_id": "anevum-crypto-owned"}]) == {"BTC/USD"}
    equity = {"symbol": "BTCUSD", "asset_class": "us_equity", "qty": "1"}
    assert normalized_crypto_row(equity) == equity
    assert CryptoExecutionEngine._crypto_positions([equity]) == []


class ExitBroker(FakeBroker):
    def __init__(self, position=None, orders=None):
        super().__init__()
        self.position, self.orders = position, orders or []
        self.canceled = []
    async def positions(self): return [self.position] if self.position else []
    async def open_orders(self): return self.orders
    async def cancel_order(self, order_id): self.canceled.append(order_id)
    async def submit_crypto_market_sell(self, **kwargs):
        self.submissions.append(kwargs)
        return {"id": "exit", "side": "sell", "status": "accepted", **kwargs}
    async def submit_crypto_stop_limit_sell(self, **kwargs):
        self.submissions.append(kwargs)
        return {"id": "stop", "side": "sell", "type": "stop_limit", "status": "new", **kwargs}


POSITION = {"symbol": "BTC/USD", "qty": ".01", "avg_entry_price": "100", "current_price": "103"}
STOP = {"symbol": "BTC/USD", "id": "stop", "side": "sell", "type": "stop_limit",
        "status": "new", "client_order_id": "anevum-crypto-btc-hardstop-test"}


def engine_for(broker, strategy=None):
    return CryptoExecutionEngine(_engine_settings(), broker, FakeMarketData(),
                                 strategy or AlwaysBuyStrategy(), RuntimeState(), FakeUniverse())


@pytest.mark.parametrize("remaining,orders,expected", [
    (None, [], "flat"),
    ({**POSITION, "qty": ".004", "qty_available": ".00399"}, [], "submitted"),
    (POSITION, [STOP], "hold"),
    ({**POSITION, "qty_available": "0"}, [], "hold")])
def test_exit_reconciles_fill_and_reservation_after_stop_cancel(remaining, orders, expected):
    broker = ExitBroker(remaining, orders)
    result = asyncio.run(engine_for(broker)._exit_position(asyncio.run(broker.account()), POSITION,
                              [STOP], reason="intraday target", now=NOW))
    assert result["action"] == expected
    assert broker.canceled == ["stop"]
    if expected == "submitted": assert broker.submissions[0]["qty"] == "0.00399"
    else: assert not broker.submissions


def test_existing_exit_order_prevents_duplicate_sell():
    broker = ExitBroker(POSITION)
    pending = {"symbol": "BTCUSD", "side": "sell", "type": "market", "status": "new"}
    result = asyncio.run(engine_for(broker)._exit_position(asyncio.run(broker.account()), POSITION,
                              [pending], reason="target", now=NOW))
    assert result["action"] == "hold" and not broker.submissions


@pytest.mark.parametrize("current,age,reason", [("103", 5, "target"), ("98", 5, "stop"), ("100", 361, "max hold")])
def test_intraday_target_stop_and_deadline_use_existing_execution_engine(current, age, reason):
    # Simulated execution proves order mechanics, not forward-paper profitability.
    broker = ExitBroker({**POSITION, "current_price": current})
    strategy = SimpleNamespace(hard_stop_pct=Decimal(".0125"), take_profit_pct=Decimal(".018"), max_hold_minutes=360)
    recent = [{"symbol": "BTCUSD", "side": "buy", "status": "filled", "client_order_id": "anevum-crypto-test",
               "filled_at": (NOW-timedelta(minutes=age)).isoformat()}]
    result = asyncio.run(engine_for(broker, strategy)._manage_positions(asyncio.run(broker.account()),
        [{**POSITION, "current_price": current}], [], recent, {}, NOW))
    assert result[0]["action"] == "submitted" and reason in result[0]["reason"]
    assert Decimal(broker.submissions[0]["qty"]) == Decimal(".01")


def test_restart_restores_ownership_and_protects_fee_adjusted_position():
    broker = ExitBroker({**POSITION, "qty": ".009975", "current_price": "100"})
    recent = [{"symbol": "BTCUSD", "side": "buy", "status": "filled", "client_order_id": "anevum-crypto-test",
               "filled_at": NOW.isoformat()}]
    # Fresh engine has no in-memory entry state; broker evidence establishes ownership.
    result = asyncio.run(engine_for(broker)._manage_positions(asyncio.run(broker.account()),
        [broker.position], [], recent, {}, NOW))
    assert result[0]["action"] == "submitted"
    assert Decimal(broker.submissions[0]["qty"]) == Decimal(".009975")


def test_command_and_health_report_preview_without_changing_live_identity(monkeypatch):
    from app import main
    preview = {"status": "UNVALIDATED_PREVIEW", "broker_writes_allowed": False}
    monkeypatch.setattr(main, "btc_day_preview", SimpleNamespace(snapshot=preview))
    monkeypatch.setattr(main, "client", FakeBroker())
    lane = asyncio.run(main.crypto_command_lane_snapshot())
    health = asyncio.run(main.health())
    assert lane["intraday_preview"] == preview
    assert health["crypto"]["intraday_preview"] == preview
    assert lane["strategy_version_id"] == main.settings.crypto_strategy_version_id


def test_simulated_buy_protect_restart_target_exit_cycle():
    class Broker(ExitBroker):
        def __init__(self):
            super().__init__()
            self.history = []
        async def recent_orders(self, limit=100): return self.history
        async def submit_crypto_market_buy(self, symbol, qty, client_order_id):
            order = {"id": "buy", "symbol": "BTCUSD", "side": "buy", "status": "filled",
                     "client_order_id": client_order_id, "filled_at": datetime.now(timezone.utc).isoformat()}
            self.history.append(order)
            self.position = {**POSITION, "symbol": "BTCUSD", "asset_class": "crypto", "current_price": "100",
                             "qty": str(Decimal(qty) * Decimal(".9975")), "market_value": "4.98"}
            self.submissions.append(order)
            return order
        async def submit_crypto_stop_limit_sell(self, **kwargs):
            order = await super().submit_crypto_stop_limit_sell(**kwargs)
            self.orders.append(order)
            return order
        async def cancel_order(self, order_id):
            await super().cancel_order(order_id)
            self.orders = [row for row in self.orders if row["id"] != order_id]
        async def submit_crypto_market_sell(self, **kwargs):
            order = await super().submit_crypto_market_sell(**kwargs)
            self.position = None
            return order
    broker = Broker()
    engine = engine_for(broker)
    engine.state.crypto_graen_promotion = {"status": "PROMOTION_READY", "promotion_ready": True}
    assert asyncio.run(engine.run_once())["action"] == "submitted"
    fee_adjusted_qty = Decimal(broker.position["qty"])
    asyncio.run(engine.run_once())
    assert len([row for row in broker.submissions if row.get("side") == "buy"]) == 1
    assert broker.orders[0]["type"] == "stop_limit"
    assert Decimal(broker.orders[0]["qty"]) == fee_adjusted_qty
    # Reconstruct the engine and recover ownership from compact-symbol broker orders.
    engine = engine_for(broker)
    broker.position["current_price"] = "103"
    results = asyncio.run(engine._manage_positions(asyncio.run(broker.account()),
        [broker.position], broker.orders, broker.history, {}, datetime.now(timezone.utc)))
    assert results[0]["action"] == "submitted"
    assert broker.position is None and broker.canceled == ["stop"]
    assert Decimal(broker.submissions[-1]["qty"]) == fee_adjusted_qty
