from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.execution import ExecutionEngine
from app.strategy import Signal


NY = ZoneInfo("America/New_York")


def engine_with_settings(**overrides):
    values = dict(
        max_bar_age_seconds=90,
        max_spread_pct=Decimal("0.002"),
        min_confirmations=1,
    )
    values.update(overrides)
    engine = object.__new__(ExecutionEngine)
    engine.settings = SimpleNamespace(**values)
    return engine


def signal(symbol, momentum, vwap_edge, confirmations):
    return Signal(
        action="buy",
        symbol=symbol,
        metadata={
            "momentum_pct": str(momentum),
            "vwap_edge_pct": str(vwap_edge),
            "confirmation_passes": confirmations,
            "confirmations": {
                "QQQ": {"ok": True},
                "SMH": {"ok": False},
            },
        },
    )


def bar(minute, close="100"):
    return {
        "t": f"2026-09-24T{minute}:00-04:00",
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": "1000",
        "vw": close,
    }


def test_signal_rank_prefers_stronger_momentum_first():
    weak = signal("SPY", Decimal("0.0006"), Decimal("0.002"), 2)
    strong = signal("NVDA", Decimal("0.0012"), Decimal("0.001"), 1)

    assert ExecutionEngine._signal_rank(strong) > ExecutionEngine._signal_rank(weak)


def test_signal_rank_uses_vwap_edge_as_tiebreaker():
    a = signal("SPY", Decimal("0.001"), Decimal("0.001"), 1)
    b = signal("QQQ", Decimal("0.001"), Decimal("0.002"), 1)

    assert ExecutionEngine._signal_rank(b) > ExecutionEngine._signal_rank(a)


def test_latest_bot_exit_uses_actual_fill_time():
    today = datetime.now(NY)
    first_submitted = today.replace(hour=10, minute=0, second=0, microsecond=0)
    first_filled = first_submitted.replace(second=5)
    second_submitted = today.replace(hour=11, minute=0, second=0, microsecond=0)
    second_filled = second_submitted.replace(second=3)
    orders = [
        {
            "side": "sell",
            "client_order_id": "anevum-spy-time-123",
            "submitted_at": first_submitted.isoformat(),
            "filled_at": first_filled.isoformat(),
        },
        {
            "side": "sell",
            "client_order_id": "anevum-spy-target-456",
            "submitted_at": second_submitted.isoformat(),
            "filled_at": second_filled.isoformat(),
        },
    ]
    latest = ExecutionEngine._latest_bot_exit_today(orders, "SPY")
    assert latest == second_filled


def test_unfilled_sell_does_not_start_reentry_cooldown():
    orders = [
        {
            "side": "sell",
            "client_order_id": "anevum-spy-time-123",
            "submitted_at": "2026-09-24T15:00:00Z",
            "filled_at": None,
        }
    ]
    assert ExecutionEngine._latest_bot_exit_today(orders, "SPY") is None


def test_market_quality_rejects_stale_candidate_bar():
    engine = engine_with_settings(max_bar_age_seconds=90)
    s = signal("SPY", Decimal("0.001"), Decimal("0.001"), 1)
    now = datetime(2026, 9, 24, 13, 37, 10, tzinfo=NY)
    ok, reason, details = engine._market_quality(
        s,
        [bar("13:08")],
        {"bp": "100", "ap": "100.05", "t": "2026-09-24T13:37:09-04:00"},
        {"QQQ": [bar("13:36")]},
        now,
    )
    assert not ok
    assert "stale" in reason
    assert details["bar_age_seconds"] > 90


def test_market_quality_rejects_wide_spread():
    engine = engine_with_settings(max_spread_pct=Decimal("0.002"))
    s = signal("SPY", Decimal("0.001"), Decimal("0.001"), 1)
    now = datetime(2026, 9, 24, 13, 37, 10, tzinfo=NY)
    ok, reason, details = engine._market_quality(
        s,
        [bar("13:36")],
        {"bp": "100", "ap": "100.30", "t": "2026-09-24T13:37:09-04:00"},
        {"QQQ": [bar("13:36")]},
        now,
    )
    assert not ok
    assert "spread" in reason
    assert Decimal(details["spread_pct"]) > Decimal("0.002")


def test_market_quality_accepts_fresh_tight_market():
    engine = engine_with_settings()
    s = signal("SPY", Decimal("0.001"), Decimal("0.001"), 1)
    now = datetime(2026, 9, 24, 13, 37, 10, tzinfo=NY)
    ok, reason, details = engine._market_quality(
        s,
        [bar("13:36")],
        {"bp": "100", "ap": "100.05", "t": "2026-09-24T13:37:09-04:00"},
        {"QQQ": [bar("13:36")]},
        now,
    )
    assert ok
    assert reason == "market quality checks passed"
    assert details["fresh_confirmation_passes"] == 1
