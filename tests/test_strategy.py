from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.strategy import OpeningRangeVwapStrategy


NY = ZoneInfo("America/New_York")


def bar(minute, o, h, l, c, v=1000, vw=None):
    return {
        "t": f"2026-09-24T{minute}:00-04:00",
        "o": str(o),
        "h": str(h),
        "l": str(l),
        "c": str(c),
        "v": str(v),
        "vw": str(vw if vw is not None else c),
    }


def strategy():
    return OpeningRangeVwapStrategy(
        opening_range_minutes=5,
        stop_pct=Decimal("0.006"),
        target_pct=Decimal("0.0108"),
        entry_start=datetime.strptime("09:35", "%H:%M").time(),
        entry_cutoff=datetime.strptime("11:30", "%H:%M").time(),
        confirmation_symbols=("QQQ", "SMH"),
    )


def spy_breakout_bars():
    return [
        bar("09:30", 100, 100.2, 99.9, 100.0),
        bar("09:31", 100, 100.3, 99.95, 100.1),
        bar("09:32", 100.1, 100.4, 100.0, 100.2),
        bar("09:33", 100.2, 100.5, 100.1, 100.3),
        bar("09:34", 100.3, 100.6, 100.2, 100.5),
        bar("09:35", 100.5, 101.0, 100.5, 100.9),
    ]


def confirm_bars(base):
    return [
        bar("09:30", base, base + 0.1, base - 0.1, base),
        bar("09:31", base, base + 0.1, base - 0.05, base + 0.02),
        bar("09:32", base + 0.02, base + 0.12, base, base + 0.04),
        bar("09:33", base + 0.04, base + 0.14, base + 0.02, base + 0.06),
        bar("09:34", base + 0.06, base + 0.16, base + 0.04, base + 0.08),
        bar("09:35", base + 0.08, base + 0.20, base + 0.06, base + 0.15),
    ]


def test_breakout_generates_bracket_prices_when_confirmed():
    s = strategy()
    signal = s.evaluate(
        bars=spy_breakout_bars(),
        confirmation_bars={"QQQ": confirm_bars(200), "SMH": confirm_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("75"),
        now=datetime(2026, 9, 24, 9, 36, 5, tzinfo=NY),
    )
    assert signal.action == "buy"
    assert signal.reference_price == Decimal("100.9")
    assert signal.stop_price == Decimal("100.29")
    assert signal.take_profit_price == Decimal("101.99")


def test_partial_current_bar_is_ignored():
    s = strategy()
    signal = s.evaluate(
        bars=spy_breakout_bars(),
        confirmation_bars={"QQQ": confirm_bars(200), "SMH": confirm_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("75"),
        now=datetime(2026, 9, 24, 9, 35, 30, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert "waiting" in signal.reason


def test_confirmation_failure_blocks_entry():
    s = strategy()
    weak = confirm_bars(200)
    weak[-1] = bar("09:35", 200.08, 200.10, 199.50, 199.60)
    signal = s.evaluate(
        bars=spy_breakout_bars(),
        confirmation_bars={"QQQ": weak, "SMH": confirm_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("75"),
        now=datetime(2026, 9, 24, 9, 36, 5, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert "QQQ confirmation failed" in signal.reason


def test_no_late_entry_after_cutoff():
    s = strategy()
    signal = s.evaluate(
        bars=spy_breakout_bars(),
        confirmation_bars={"QQQ": confirm_bars(200), "SMH": confirm_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("75"),
        now=datetime(2026, 9, 24, 11, 31, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert signal.reason == "entry window closed"


def test_existing_position_never_adds():
    s = strategy()
    signal = s.evaluate(
        bars=spy_breakout_bars(),
        confirmation_bars={"QQQ": confirm_bars(200), "SMH": confirm_bars(300)},
        symbol="SPY",
        has_position=True,
        order_notional=Decimal("75"),
        now=datetime(2026, 9, 24, 9, 36, 5, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert "position already open" in signal.reason
