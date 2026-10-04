from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.crypto_canary import (
    BTC_CANARY_MOMENTUM_BARS,
    BTC_CANARY_SMA_BARS,
    BTC_CANARY_SOURCE_CANDIDATE_ID,
    BTC_CANARY_STRATEGY_VERSION_ID,
    completed_4h_bars,
    evaluate_btc_canary_state,
    signal_from_canary_state,
)
from graen.crypto.btc_4h_consensus_v14_r2h import candidate_spec


UTC = timezone.utc


def _bars(count: int, *, start: datetime, price_fn):
    return [
        {
            "t": (start + timedelta(hours=4 * index)).isoformat(),
            "c": str(price_fn(index)),
        }
        for index in range(count)
    ]


def test_canary_constants_are_frozen_to_r2h_candidate():
    spec = candidate_spec()
    assert BTC_CANARY_SOURCE_CANDIDATE_ID == spec.candidate_id
    assert BTC_CANARY_MOMENTUM_BARS == spec.momentum_lookback_bars
    assert BTC_CANARY_SMA_BARS == spec.sma_window_bars
    assert BTC_CANARY_STRATEGY_VERSION_ID == "BTC-CANARY-001"


def test_incomplete_4h_bar_is_never_used():
    now = datetime(2026, 10, 4, 16, 30, tzinfo=UTC)
    rows = [
        {"t": "2026-10-04T08:00:00+00:00", "c": "100"},
        {"t": "2026-10-04T12:00:00+00:00", "c": "101"},
        {"t": "2026-10-04T16:00:00+00:00", "c": "999"},
    ]
    completed = completed_4h_bars(rows, now=now)
    assert [row["close"] for row in completed] == [Decimal("100"), Decimal("101")]


def test_canary_is_long_when_slow_momentum_is_positive():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = _bars(
        BTC_CANARY_SMA_BARS + 10,
        start=start,
        price_fn=lambda index: Decimal("100") + Decimal(index) / Decimal("100"),
    )
    now = start + timedelta(hours=4 * (len(rows) + 1))
    state = evaluate_btc_canary_state(rows, now=now)
    assert state.available is True
    assert state.momentum_return is not None and state.momentum_return > 0
    assert state.desired_long is True


def test_canary_is_flat_when_momentum_and_sma_are_both_negative():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = _bars(
        BTC_CANARY_SMA_BARS + 10,
        start=start,
        price_fn=lambda index: Decimal("300") - Decimal(index) / Decimal("10"),
    )
    now = start + timedelta(hours=4 * (len(rows) + 1))
    state = evaluate_btc_canary_state(rows, now=now)
    assert state.available is True
    assert state.momentum_return is not None and state.momentum_return < 0
    assert state.sma is not None and state.signal_close < state.sma
    assert state.desired_long is False


def test_canary_requires_full_frozen_warmup():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = _bars(
        BTC_CANARY_SMA_BARS - 1,
        start=start,
        price_fn=lambda _index: Decimal("100"),
    )
    now = start + timedelta(hours=4 * (len(rows) + 1))
    state = evaluate_btc_canary_state(rows, now=now)
    assert state.available is False
    assert state.desired_long is False


def test_canary_signal_is_explicitly_experimental_and_has_no_profit_target():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = _bars(
        BTC_CANARY_SMA_BARS + 10,
        start=start,
        price_fn=lambda index: Decimal("100") + Decimal(index) / Decimal("100"),
    )
    now = start + timedelta(hours=4 * (len(rows) + 1))
    state = evaluate_btc_canary_state(rows, now=now)
    signal = signal_from_canary_state(
        state,
        reference_price=Decimal("50000"),
        order_notional=Decimal("1"),
        stop_pct=Decimal("0.05"),
    )
    assert signal.action == "buy"
    assert signal.notional == Decimal("1")
    assert signal.take_profit_price == Decimal("0")
    assert signal.metadata["experimental_canary"] is True
    assert signal.metadata["research_status"] == "NOT_PROMOTED"
