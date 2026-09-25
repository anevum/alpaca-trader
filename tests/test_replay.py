from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.replay import ReplayEngine
from app.strategy import Signal


NY = ZoneInfo("America/New_York")


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY,QQQ",
        CONFIRMATION_SYMBOLS="QQQ",
        MIN_CONFIRMATIONS="1",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_TOTAL_POSITION_NOTIONAL="80",
        MAX_CONCURRENT_POSITIONS="1",
        MAX_NEW_ENTRIES_PER_CYCLE="1",
        MAX_DAILY_ORDERS="2",
        MAX_DAILY_LOSS="5",
        STOP_PCT="0.01",
        TARGET_PCT="0.01",
        ENTRY_START="09:31",
        ENTRY_CUTOFF="15:30",
        FORCE_FLAT_TIME="15:55",
        MAX_HOLD_MINUTES="15",
        SIZING_MODE="fixed",
        MAX_SPREAD_PCT="0.002",
        MAX_PAIRWISE_CORRELATION="0.85",
        CORRELATION_LOOKBACK_BARS="30",
        CORRELATION_MIN_OBSERVATIONS="8",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


class AlwaysBuy:
    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now):
        if has_position or not bars:
            return Signal(action="hold", symbol=symbol, reason="already open")
        return Signal(
            action="buy",
            symbol=symbol,
            notional=order_notional,
            reference_price=Decimal(str(bars[-1]["c"])),
            metadata={
                "momentum_pct": "0.002",
                "vwap_edge_pct": "0.003",
                "confirmation_passes": 1,
                "confirmations": {"QQQ": {"ok": True}},
            },
        )


def bar(minute, close="100", low="99.9", high="100.1", volume="1000"):
    at = datetime(2026, 9, 21, 9, minute, tzinfo=NY)
    return {
        "t": at.isoformat(),
        "o": close,
        "h": high,
        "l": low,
        "c": close,
        "v": volume,
        "vw": close,
    }


def test_replay_is_stop_first_when_one_bar_touches_stop_and_target():
    engine = ReplayEngine(settings(), AlwaysBuy())
    data = {
        "SPY": [
            bar(30),
            bar(31, close="100", low="98.5", high="101.5"),
        ],
        "QQQ": [bar(30, close="50"), bar(31, close="50")],
    }

    result = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result["summary"]["trades"] == 1
    assert result["trades"][0]["exit_reason"] == "stop"
    assert Decimal(result["trades"][0]["net_pnl"]) < 0
    assert result["assumptions"]["broker_orders_possible"] is False


def test_replay_models_spread_and_slippage_as_cost():
    engine = ReplayEngine(settings(MAX_HOLD_MINUTES="1"), AlwaysBuy())
    data = {
        "SPY": [bar(30), bar(31)],
        "QQQ": [bar(30, close="50"), bar(31, close="50")],
    }

    frictionless = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    friction = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )

    assert Decimal(friction["summary"]["net_pnl"]) < Decimal(frictionless["summary"]["net_pnl"])


def test_replay_rejects_empty_history():
    engine = ReplayEngine(settings(), AlwaysBuy())
    try:
        engine.run(
            {},
            initial_equity=Decimal("100"),
            spread_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
        )
    except ValueError as exc:
        assert "regular-session" in str(exc)
    else:
        raise AssertionError("expected empty history to be rejected")


def test_replay_enforces_live_quality_threshold():
    engine = ReplayEngine(
        settings(MIN_QUALITY_SCORE="100"),
        AlwaysBuy(),
    )
    data = {
        "SPY": [bar(30), bar(31), bar(32)],
        "QQQ": [
            bar(30, close="50"),
            bar(31, close="50"),
            bar(32, close="50"),
        ],
    }

    result = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result["summary"]["trades"] == 0
    assert result["summary"]["quality_blocks"] > 0


class DynamicStopBuy(AlwaysBuy):
    def evaluate(self, bars, confirmation_bars, symbol, has_position, order_notional, now):
        signal = super().evaluate(
            bars,
            confirmation_bars,
            symbol,
            has_position,
            order_notional,
            now,
        )
        if signal.action == "buy":
            signal.metadata = dict(signal.metadata or {})
            signal.metadata["effective_stop_pct"] = "0.005"
        return signal


def test_replay_uses_signal_effective_stop_distance():
    engine = ReplayEngine(
        settings(
            STOP_PCT="0.01",
            TARGET_PCT="0.02",
            MAX_HOLD_MINUTES="10",
        ),
        DynamicStopBuy(),
    )
    data = {
        "SPY": [
            bar(30, close="100"),
            bar(31, close="99.8", low="99.4", high="100.0"),
        ],
        "QQQ": [
            bar(30, close="50"),
            bar(31, close="50"),
        ],
    }

    result = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result["summary"]["trades"] == 1
    assert result["trades"][0]["exit_reason"] == "stop"
    assert Decimal(result["trades"][0]["exit_price"]) == Decimal("99.500")


def test_replay_models_profit_protection_and_excursions_without_same_bar_lookahead():
    engine = ReplayEngine(
        settings(
            STOP_PCT="0.01",
            TARGET_PCT="0.02",
            MAX_HOLD_MINUTES="10",
            PROFIT_PROTECT_ENABLED="true",
            PROFIT_PROTECT_ACTIVATION_PCT="0.001",
            PROFIT_PROTECT_RETAIN_FRACTION="0.50",
            PROFIT_PROTECT_MIN_PCT="0.0003",
        ),
        AlwaysBuy(),
    )
    data = {
        "SPY": [
            bar(30, close="100"),
            bar(31, close="100.30", low="99.95", high="100.40"),
            bar(32, close="100.18", low="100.15", high="100.32"),
        ],
        "QQQ": [
            bar(30, close="50"),
            bar(31, close="50"),
            bar(32, close="50"),
        ],
    }

    result = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result["summary"]["trades"] == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "protect"
    assert Decimal(trade["net_pnl"]) > 0
    assert Decimal(trade["max_favorable_excursion"]) >= Decimal("0.004")
    assert Decimal(trade["max_adverse_excursion"]) <= Decimal("-0.0005")
    assert result["summary"]["average_mfe_pct"] >= 0.004
    assert result["summary"]["average_mae_pct"] <= -0.0005
    assert result["assumptions"]["new_profit_protection_same_bar"] is False
