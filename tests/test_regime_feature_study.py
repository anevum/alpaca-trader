from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.regime_feature_study import (
    attach_regimes,
    classify_broad_market_regime,
    summarize_regimes,
)


NY = ZoneInfo("America/New_York")


def bar(at, close, *, volume="100"):
    close = Decimal(str(close))
    return {
        "t": at.isoformat(),
        "o": str(close),
        "h": str(close + Decimal("0.05")),
        "l": str(close - Decimal("0.05")),
        "c": str(close),
        "v": volume,
        "vw": str(close),
    }


def reference_series(start, closes):
    return [
        bar(start + timedelta(minutes=index), close)
        for index, close in enumerate(closes)
    ]


def test_classifies_broad_up_from_two_constructive_references():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = {
        "SPY": reference_series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.5]),
        "QQQ": reference_series(start, [200, 200.1, 200.2, 200.3, 200.4, 200.5]),
        "SMH": reference_series(start, [300, 299.9, 300.0, 300.0, 300.0, 300.0]),
    }

    decision = classify_broad_market_regime(
        bars,
        decision_bar_time=start + timedelta(minutes=5),
        window=5,
    )

    assert decision.available is True
    assert decision.label == "broad_up"
    assert decision.constructive_count >= 2
    assert decision.breadth_score > 0


def test_classifies_broad_down_from_two_weak_references():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = {
        "SPY": reference_series(start, [100, 99.9, 99.8, 99.7, 99.6, 99.5]),
        "QQQ": reference_series(start, [200, 199.9, 199.8, 199.7, 199.6, 199.5]),
        "SMH": reference_series(start, [300, 300.1, 300.0, 300.0, 300.0, 300.0]),
    }

    decision = classify_broad_market_regime(
        bars,
        decision_bar_time=start + timedelta(minutes=5),
        window=5,
    )

    assert decision.label == "broad_down"
    assert decision.weak_count >= 2
    assert decision.breadth_score < 0


def test_regime_classification_does_not_read_future_bars():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    decision_time = start + timedelta(minutes=5)
    base = {
        "SPY": reference_series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.5]),
        "QQQ": reference_series(start, [200, 200.1, 200.2, 200.3, 200.4, 200.5]),
        "SMH": reference_series(start, [300, 299.9, 300.0, 300.0, 300.0, 300.0]),
    }

    before = classify_broad_market_regime(
        base,
        decision_bar_time=decision_time,
        window=5,
    )

    with_future = {
        symbol: [
            *series,
            bar(start + timedelta(minutes=6), "1"),
        ]
        for symbol, series in base.items()
    }
    after = classify_broad_market_regime(
        with_future,
        decision_bar_time=decision_time,
        window=5,
    )

    assert before.label == after.label
    assert before.breadth_score == after.breadth_score
    assert before.details == after.details


def test_attach_regimes_preserves_entry_confirmation_contract():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = {
        "SPY": reference_series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.5]),
        "QQQ": reference_series(start, [200, 200.1, 200.2, 200.3, 200.4, 200.5]),
        "SMH": reference_series(start, [300, 300.1, 300.2, 300.3, 300.4, 300.5]),
    }
    entry_result = {
        "observations": [
            {
                "decision_bar_time": (start + timedelta(minutes=5)).isoformat(),
                "quality_allowed": True,
                "features": {},
                "forward": {},
            }
        ]
    }

    attached = attach_regimes(entry_result, bars, window=5)

    assert attached["regime_study"]["confirmation_symbols_unchanged"] is True
    assert attached["observations"][0]["market_regime"]["label"] == "broad_up"


def test_regime_summary_keeps_regimes_separate():
    rows = [
        {
            "quality_allowed": True,
            "market_regime": {"available": True, "label": "broad_up"},
            "forward": {
                "15": {
                    "target_before_stop": True,
                    "stop_before_target": False,
                    "mfe_pct": "0.006",
                    "mae_pct": "-0.001",
                    "close_return_pct": "0.002",
                }
            },
        },
        {
            "quality_allowed": True,
            "market_regime": {"available": True, "label": "broad_down"},
            "forward": {
                "15": {
                    "target_before_stop": False,
                    "stop_before_target": True,
                    "mfe_pct": "0.001",
                    "mae_pct": "-0.005",
                    "close_return_pct": "-0.002",
                }
            },
        },
    ]

    summary = summarize_regimes(rows, horizons=(15,))

    assert summary["broad_up"]["horizons"]["15"]["target_before_stop_rate"] == 1.0
    assert summary["broad_down"]["horizons"]["15"]["stop_before_target_rate"] == 1.0



def test_benchmark_alignment_uses_visible_candidate_and_reference_bars_only():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    decision_time = start + timedelta(minutes=5)
    bars = {
        "AAPL": reference_series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.6]),
        "QQQ": reference_series(start, [200, 200.02, 200.04, 200.06, 200.08, 200.10]),
    }
    rows = [
        {
            "symbol": "AAPL",
            "decision_bar_time": decision_time.isoformat(),
            "features": {},
        }
    ]

    attached = attach_benchmark_alignment(rows, bars, window=5)
    features = attached[0]["features"]

    assert features["benchmark_symbol"] == "QQQ"
    assert features["benchmark_alignment_available"] is True
    assert Decimal(features["relative_strength_pct"]) > 0

    future = {
        "AAPL": [
            *bars["AAPL"],
            bar(start + timedelta(minutes=6), "1"),
        ],
        "QQQ": [
            *bars["QQQ"],
            bar(start + timedelta(minutes=6), "999"),
        ],
    }
    after = attach_benchmark_alignment(rows, future, window=5)
    assert after[0]["features"] == features
