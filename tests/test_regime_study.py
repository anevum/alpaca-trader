from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.market_regime import market_regime_features
from app.regime_study import regime_conditioned_summary


NY = ZoneInfo("America/New_York")


def bar(at: datetime, close: float, *, volume: float = 1000):
    return {
        "t": at.isoformat(),
        "o": close,
        "h": close * 1.0005,
        "l": close * 0.9995,
        "c": close,
        "v": volume,
        "vw": close,
    }


def series(start: datetime, closes: list[float]):
    return [
        bar(start + timedelta(minutes=index), close)
        for index, close in enumerate(closes)
    ]


def test_market_regime_features_reports_full_agreement():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = {
        "QQQ": series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.5]),
        "SMH": series(start, [200, 200.1, 200.2, 200.3, 200.4, 200.5]),
    }

    result = market_regime_features(bars, regime_window=5)

    assert result["agreement_band"] == "full"
    assert result["references_available"] == 2
    assert result["constructive_count"] == 2
    assert result["features"]["market_constructive_fraction"] == "1"


def test_market_regime_features_reports_partial_agreement():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = {
        "QQQ": series(start, [100, 100.1, 100.2, 100.3, 100.4, 100.5]),
        "SMH": series(start, [200, 199.9, 199.8, 199.7, 199.6, 199.5]),
    }

    result = market_regime_features(bars, regime_window=5)

    assert result["agreement_band"] == "partial"
    assert result["constructive_count"] == 1
    assert result["references"]["QQQ"]["constructive"] is True
    assert result["references"]["SMH"]["constructive"] is False


def observation(
    agreement: str,
    decision_at: str,
    *,
    target: bool,
    stop: bool,
    momentum: str,
):
    return {
        "quality_allowed": True,
        "decision_at": decision_at,
        "market_regime": {"agreement_band": agreement},
        "features": {
            "momentum_pct": momentum,
            "vwap_edge_pct": "0.003",
            "last_bar_return_pct": "0.001",
            "relative_volume_ratio": "1.2",
            "volume_acceleration": "1.1",
            "trend_persistence": "0.625",
        },
        "forward": {
            "15": {
                "mfe_pct": "0.006" if target else "0.001",
                "mae_pct": "-0.001" if target else "-0.004",
                "close_return_pct": "0.002" if target else "-0.001",
                "target_before_stop": target,
                "stop_before_target": stop,
            }
        },
    }


def test_regime_conditioned_summary_is_descriptive_only():
    rows = []
    for index in range(8):
        rows.append(
            observation(
                "full",
                f"2026-09-25T10:{index:02d}:00-04:00",
                target=index < 6,
                stop=index >= 6,
                momentum=str(0.001 + index * 0.0001),
            )
        )
        rows.append(
            observation(
                "partial",
                f"2026-09-25T14:{index:02d}:00-04:00",
                target=index < 2,
                stop=index >= 2,
                momentum=str(0.001 + index * 0.0001),
            )
        )

    result = regime_conditioned_summary(
        [{"period": "development", "observations": rows}],
        horizon=15,
        min_group_n=8,
    )

    assert result["status"] == "research_only"
    assert result["candidate_defined"] is False
    assert result["promotion_authorized"] is False
    full = result["periods"]["development"]["agreement"]["full"]
    partial = result["periods"]["development"]["agreement"]["partial"]
    assert full["n"] == 8
    assert partial["n"] == 8
    assert full["target_before_stop_rate"] > partial["target_before_stop_rate"]
    assert "open" in result["periods"]["development"]["time_band"]
    assert "afternoon" in result["periods"]["development"]["time_band"]
