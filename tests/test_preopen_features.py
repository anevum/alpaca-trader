from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.preopen_state.features import build_symbol_features
from app.preopen_state.models import SourceTier

NY = ZoneInfo("America/New_York")


def bar(stamp: str, o: float, h: float, l: float, c: float, v: int) -> dict:
    return {"t": stamp, "o": o, "h": h, "l": l, "c": c, "v": v, "vw": c}


def test_features_use_only_information_before_cutoff():
    cutoff = datetime(2026, 9, 27, 9, 25, tzinfo=NY)
    bars = [
        bar("2026-09-27T13:20:00Z", 101.0, 101.4, 100.9, 101.2, 100),
        bar("2026-09-27T13:24:00Z", 101.2, 102.2, 101.1, 102.0, 200),
        bar("2026-09-27T13:25:00Z", 102.0, 150.0, 100.0, 149.0, 99999),
    ]
    daily = [
        {"t": "2026-09-26T04:00:00Z", "c": 100.0},
    ]

    result = build_symbol_features(
        symbol="SPY",
        bars=bars,
        daily_bars=daily,
        cutoff=cutoff,
        tier=SourceTier.DIRECT,
    )
    metrics = result["metrics"]

    assert metrics["last_price"] == pytest.approx(102.0)
    assert metrics["gap_vs_prior_close_pct"] == pytest.approx(2.0)
    assert metrics["premarket_bar_count"] == 2
    assert metrics["premarket_volume"] == 300
    assert metrics["premarket_range_pct"] < 2.0


def test_feature_status_is_unavailable_without_premarket_bars():
    cutoff = datetime(2026, 9, 27, 9, 25, tzinfo=NY)
    result = build_symbol_features(
        symbol="FEZ",
        bars=[],
        daily_bars=[],
        cutoff=cutoff,
        tier=SourceTier.PROXY,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["metrics"]["last_price"] is None
