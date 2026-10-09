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


def test_replay_quality_gate_matches_production_order_of_checks(monkeypatch):
    from app import replay as replay_module

    def fixed_quality(_settings, _signal, _bars, _quality):
        return {
            "score": 82.0,
            "components": {},
            "relative_volume_ratio": 1.0,
            "trend_persistence": 0.6,
        }

    monkeypatch.setattr(replay_module, "score_opportunity", fixed_quality)
    data = {"SPY": [bar(30), bar(31)], "QQQ": [bar(30), bar(31)]}

    def run(floor):
        return ReplayEngine(settings(MIN_QUALITY_SCORE=str(floor)), AlwaysBuy()).run(
            data, initial_equity=Decimal("100"),
            spread_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )

    accepted, rejected = run(80), run(85)
    assert accepted["summary"]["entries"] == 1
    assert rejected["summary"]["entries"] == 0
    assert accepted["summary"]["quality_blocks"] == 0
    assert rejected["summary"]["quality_blocks"] > 0
    assert rejected["summary"]["signals_qualified"] == 0
    assert rejected["summary"]["signals_scored"] > 0
    assert accepted["assumptions"]["same_strategy_logic"] is False
    assert accepted["assumptions"]["live_execution_parity"] == "UNVERIFIED"


def test_replay_rolling_target_and_max_hold_do_not_force_exit():
    from app.replay import ReplayPosition
    from app.strategy_lab import build_strategy

    cfg = settings(STRATEGY_NAME="rolling_momentum_vwap",
                   TARGET_PCT="0.01", MAX_HOLD_MINUTES="1")
    position = ReplayPosition(
        symbol="SPY", qty=Decimal("1"),
        entry_price=Decimal("100"), entry_reference=Decimal("100"),
        entry_at=datetime(2026, 9, 21, 9, 30, tzinfo=NY),
        notional=Decimal("100"), quality_score=80.0,
    )
    engine = ReplayEngine(cfg, build_strategy(cfg))
    decision = engine._exit_decision(
        position, bar(45, close="101.2", low="100.5", high="101.5"),
        datetime(2026, 9, 21, 9, 46, tzinfo=NY),
    )
    assert decision is None
    assert position.target_reached is True


def test_replay_thesis_failures_use_two_distinct_completed_bars():
    from app.replay import ReplayPosition
    from app.strategy_lab import build_strategy

    cfg = settings(STRATEGY_NAME="rolling_momentum_vwap",
                   THESIS_EXIT_ENABLED="true", THESIS_FAILURE_CYCLES="2",
                   THESIS_EXIT_MAX_RETURN_PCT="0.0005")
    strategy = build_strategy(cfg)
    strategy.position_health = lambda **kw: {
        "strong_failure": True,
        "bar_time": kw["bars"][-1]["t"],
        "data_ready": True,
    }
    engine = ReplayEngine(cfg, strategy)
    position = ReplayPosition(
        symbol="SPY", qty=Decimal("1"),
        entry_price=Decimal("100"), entry_reference=Decimal("100"),
        entry_at=datetime(2026, 9, 21, 9, 30, tzinfo=NY),
        notional=Decimal("100"), quality_score=80.0,
    )
    first = bar(40, close="100", low="99.9", high="100.1")
    first_now = datetime(2026, 9, 21, 9, 41, tzinfo=NY)
    visible = {"SPY": [first], "QQQ": [bar(40)]}
    assert engine._exit_decision(position, first, first_now, visible=visible) is None
    assert engine._exit_decision(position, first, first_now, visible=visible) is None
    assert position.thesis_failure_count == 1
    second = bar(41, close="100", low="99.9", high="100.1")
    visible["SPY"].append(second)
    result = engine._exit_decision(
        position, second, datetime(2026, 9, 21, 9, 42, tzinfo=NY),
        visible=visible,
    )
    assert result is not None and result[0] == "thesis"
    assert position.thesis_failure_count == 2


def test_replay_profit_protection_uses_previous_completed_bar_floor():
    from app.replay import ReplayPosition
    from app.strategy_lab import build_strategy

    cfg = settings(STRATEGY_NAME="rolling_momentum_vwap",
                   TARGET_PCT="0.01", PROFIT_PROTECT_ENABLED="true")
    engine = ReplayEngine(cfg, build_strategy(cfg))
    position = ReplayPosition(
        symbol="SPY", qty=Decimal("1"),
        entry_price=Decimal("100"), entry_reference=Decimal("100"),
        entry_at=datetime(2026, 9, 21, 9, 30, tzinfo=NY),
        notional=Decimal("100"), quality_score=80.0,
    )
    first = bar(40, close="101.5", low="100", high="102")
    assert engine._exit_decision(
        position, first, datetime(2026, 9, 21, 9, 41, tzinfo=NY),
    ) is None
    assert position.profit_protection_active is True
    assert position.protected_floor_pct == Decimal("0.01")
    second = dict(bar(41, close="100.9", low="100.8", high="101.6"))
    second["o"] = "101.5"
    exit_result = engine._exit_decision(
        position, second, datetime(2026, 9, 21, 9, 42, tzinfo=NY),
    )
    assert exit_result is not None and exit_result[0] == "protect"


def test_dynamic_replay_fails_closed_without_asof_universe_snapshots():
    import pytest

    cfg = settings(DYNAMIC_UNIVERSE_ENABLED="true")
    engine = ReplayEngine(cfg, AlwaysBuy())
    data = {"SPY": [bar(30)], "QQQ": [bar(30)]}
    with pytest.raises(ValueError, match="as-of universe_snapshots"):
        engine.run(
            data, initial_equity=Decimal("100"),
            spread_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )


def test_dynamic_replay_does_not_use_future_universe_membership():
    cfg = settings(DYNAMIC_UNIVERSE_ENABLED="true")
    engine = ReplayEngine(cfg, AlwaysBuy())
    data = {
        "SPY": [bar(30), bar(31), bar(32)],
        "QQQ": [bar(30), bar(31), bar(32)],
    }
    result = engine.run(
        data,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("0"), slippage_bps=Decimal("0"),
        universe_snapshots={"2026-09-21T09:32:00-04:00": ["SPY"]},
    )
    assert result["summary"]["entries"] == 1
    assert result["summary"]["universe_snapshot_missing_cycles"] == 1
    assert result["assumptions"]["dynamic_universe_asof_supplied"] is True
    assert len(result["assumptions"]["universe_snapshot_fingerprint"]) == 64
    assert result["trades"][0]["entry_at"].startswith("2026-09-21T09:32:")
