from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.strategy import RollingMomentumVwapStrategy


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
    return RollingMomentumVwapStrategy(
        fast_window=3,
        slow_window=8,
        min_momentum_pct=Decimal("0.0005"),
        min_vwap_edge_pct=Decimal("0"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        entry_start=datetime.strptime("09:31", "%H:%M").time(),
        entry_cutoff=datetime.strptime("15:30", "%H:%M").time(),
        confirmation_symbols=("QQQ", "SMH"),
        min_confirmations=1,
    )


def rising_bars(base=100):
    rows = []
    price = Decimal(str(base))
    for minute in range(30, 40):
        close = price + Decimal("0.10")
        rows.append(
            bar(
                f"09:{minute}",
                price,
                close + Decimal("0.03"),
                price - Decimal("0.03"),
                close,
            )
        )
        price = close
    return rows


def weak_bars(base=200):
    rows = []
    price = Decimal(str(base))
    for minute in range(30, 40):
        close = price - Decimal("0.10")
        rows.append(
            bar(
                f"09:{minute}",
                price,
                price + Decimal("0.03"),
                close - Decimal("0.03"),
                close,
            )
        )
        price = close
    return rows


def test_rolling_momentum_generates_entry_with_one_confirmation():
    s = strategy()
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": rising_bars(200), "SMH": weak_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )
    assert signal.action == "buy"
    assert signal.reason == "rolling momentum above VWAP with constructive market regime"
    assert signal.metadata["confirmation_passes"] == 1
    assert signal.metadata["checks"]["fast_above_slow"] is True
    assert signal.metadata["checks"]["momentum_ok"] is True
    assert signal.metadata["checks"]["vwap_ok"] is True


def test_rolling_signal_can_remain_eligible_without_fresh_daily_breakout():
    s = strategy()
    bars = rising_bars(100)
    first = s.evaluate(
        bars=bars,
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )
    second = s.evaluate(
        bars=bars,
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 20, tzinfo=NY),
    )
    assert first.action == "buy"
    assert second.action == "buy"


def test_both_confirmations_weak_blocks_entry():
    s = strategy()
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": weak_bars(200), "SMH": weak_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert signal.reason == "not enough market confirmations passed"


def test_existing_position_blocks_additional_entry():
    s = strategy()
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=True,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert "position already open" in signal.reason
    assert signal.metadata["evidence_reference_only"] is True
    assert signal.metadata["bar_time"] == "2026-09-24T09:39:00-04:00"
    assert signal.metadata["current_close"] == "101.00"


def test_entry_window_extends_into_afternoon_but_still_has_cutoff():
    s = strategy()
    bars = []
    price = Decimal("100")
    for hour, minute in [(15, 20), (15, 21), (15, 22), (15, 23), (15, 24), (15, 25), (15, 26), (15, 27), (15, 28), (15, 29)]:
        close = price + Decimal("0.10")
        bars.append(bar(f"{hour:02d}:{minute:02d}", price, close + Decimal("0.03"), price - Decimal("0.03"), close))
        price = close

    signal = s.evaluate(
        bars=bars,
        confirmation_bars={"QQQ": bars, "SMH": bars},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 15, 31, 0, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert signal.reason == "entry window closed"
    assert signal.metadata["evidence_reference_only"] is True
    assert signal.metadata["bar_time"] == "2026-09-24T15:29:00-04:00"
    assert signal.metadata["current_close"] == "101.00"


def test_deteriorating_market_regime_blocks_otherwise_valid_entry():
    s = strategy()
    candidate = rising_bars(100)
    qqq = rising_bars(200)
    smh = rising_bars(300)
    for rows in (qqq, smh):
        rows[-1]["c"] = str(Decimal(rows[-6]["c"]) - Decimal("0.50"))
        rows[-1]["l"] = str(Decimal(rows[-1]["c"]) - Decimal("0.05"))
        rows[-1]["vw"] = rows[-1]["c"]

    signal = s.evaluate(
        bars=candidate,
        confirmation_bars={"QQQ": qqq, "SMH": smh},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )

    assert signal.action == "hold"
    assert signal.reason in {
        "not enough market confirmations passed",
        "market regime is not constructive",
    }


def test_excessive_vwap_extension_blocks_chasing_entry():
    s = RollingMomentumVwapStrategy(
        fast_window=3,
        slow_window=8,
        min_momentum_pct=Decimal("0.0005"),
        min_vwap_edge_pct=Decimal("0"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        entry_start=datetime.strptime("09:31", "%H:%M").time(),
        entry_cutoff=datetime.strptime("15:30", "%H:%M").time(),
        confirmation_symbols=("QQQ", "SMH"),
        min_confirmations=1,
        max_vwap_extension_pct=Decimal("0.001"),
    )
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )
    assert signal.action == "hold"
    assert signal.reason == "price is too extended above session VWAP"


def test_volatility_stop_is_capped_and_recorded_in_signal():
    s = RollingMomentumVwapStrategy(
        fast_window=3,
        slow_window=8,
        min_momentum_pct=Decimal("0.0005"),
        min_vwap_edge_pct=Decimal("0"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        entry_start=datetime.strptime("09:31", "%H:%M").time(),
        entry_cutoff=datetime.strptime("15:30", "%H:%M").time(),
        confirmation_symbols=("QQQ", "SMH"),
        min_confirmations=1,
        volatility_stop_enabled=True,
        volatility_stop_multiplier=Decimal("10"),
        volatility_stop_lookback_bars=8,
        max_dynamic_stop_pct=Decimal("0.006"),
    )
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )

    assert signal.action == "buy"
    assert Decimal(signal.metadata["effective_stop_pct"]) == Decimal("0.006")
    assert signal.metadata["stop_model"]["mode"] == "volatility"


def test_position_health_flags_joint_market_and_candidate_failure():
    s = strategy()
    health = s.position_health(
        bars=weak_bars(100),
        confirmation_bars={"QQQ": weak_bars(200), "SMH": weak_bars(300)},
        symbol="SPY",
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )

    assert health["data_ready"] is True
    assert health["regime_ok"] is False
    assert health["candidate_failure_count"] >= 2
    assert health["strong_failure"] is True


def test_one_bar_pop_without_multi_bar_persistence_is_rejected():
    s = strategy()
    candidate = rising_bars(100)
    candidate[-3]["c"] = candidate[-4]["c"]
    candidate[-2]["c"] = str(Decimal(candidate[-3]["c"]) - Decimal("0.05"))
    candidate[-1]["c"] = str(Decimal(candidate[-2]["c"]) + Decimal("0.10"))
    candidate[-1]["h"] = str(Decimal(candidate[-1]["c"]) + Decimal("0.03"))
    candidate[-1]["l"] = str(Decimal(candidate[-1]["c"]) - Decimal("0.03"))
    candidate[-1]["vw"] = candidate[-1]["c"]

    signal = s.evaluate(
        bars=candidate,
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )

    assert signal.action == "hold"
    assert signal.reason == "multi-bar candidate trend is not persistent"


def test_market_confirmation_requires_multi_bar_persistence():
    s = strategy()
    confirmation = rising_bars(200)
    confirmation[-3]["c"] = confirmation[-4]["c"]
    confirmation[-2]["c"] = str(Decimal(confirmation[-3]["c"]) - Decimal("0.10"))
    confirmation[-1]["c"] = str(Decimal(confirmation[-2]["c"]) + Decimal("0.05"))
    confirmation[-1]["l"] = str(Decimal(confirmation[-2]["l"]) - Decimal("0.20"))
    confirmation[-1]["vw"] = confirmation[-1]["c"]

    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": confirmation, "SMH": weak_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 40, 5, tzinfo=NY),
    )

    assert signal.action == "hold"
    assert signal.reason == "not enough market confirmations passed"



def test_warmup_hold_retains_exact_completed_bar_reference_for_research():
    s = strategy()
    signal = s.evaluate(
        bars=rising_bars(100),
        confirmation_bars={"QQQ": rising_bars(200), "SMH": rising_bars(300)},
        symbol="SPY",
        has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 24, 9, 34, 5, tzinfo=NY),
    )

    assert signal.action == "hold"
    assert signal.reason == "not enough completed bars for rolling signal"
    assert signal.reference_price == Decimal("0")
    assert signal.metadata["evidence_reference_only"] is True
    assert signal.metadata["warmup_bar_count"] == 4
    assert signal.metadata["required_bar_count"] == 9
    assert signal.metadata["bar_time"] == "2026-09-24T09:33:00-04:00"
    assert signal.metadata["current_close"] == "100.40"
