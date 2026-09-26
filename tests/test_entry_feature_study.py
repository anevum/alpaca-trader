from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.entry_feature_study import (
    decision_path_features,
    forward_excursion,
)
from app.replay import stamp


NY = ZoneInfo("America/New_York")


def bar(at: datetime, *, o: str, h: str, l: str, c: str, v: str = "100"):
    return {
        "t": at.isoformat(),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "vw": c,
    }


def test_decision_path_features_use_only_bars_supplied():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    visible = [
        bar(start, o="100", h="100.2", l="99.9", c="100.1", v="100"),
        bar(start + timedelta(minutes=1), o="100.1", h="100.4", l="100", c="100.3", v="120"),
        bar(start + timedelta(minutes=2), o="100.3", h="100.5", l="100.2", c="100.4", v="150"),
    ]
    future = bar(
        start + timedelta(minutes=3),
        o="100.4",
        h="110",
        l="90",
        c="109",
        v="999999",
    )

    before = decision_path_features(
        visible,
        fast_window=3,
        slow_window=8,
    )
    after = decision_path_features(
        [*visible, future],
        fast_window=3,
        slow_window=8,
    )

    assert before["last_bar_return_pct"] != after["last_bar_return_pct"]
    assert before["volume_acceleration"] != after["volume_acceleration"]
    assert before["close_location_value"] != after["close_location_value"]


def test_forward_excursion_starts_after_decision_bar():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = [
        bar(start, o="100", h="200", l="50", c="100"),
        bar(start + timedelta(minutes=1), o="100", h="100.2", l="99.9", c="100.1"),
    ]

    result = forward_excursion(
        bars,
        decision_bar_time=stamp(bars[0]),
        entry_price=Decimal("100"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        horizons=(1,),
    )

    assert result["1"]["mfe_pct"] == Decimal("0.002").to_eng_string()
    assert result["1"]["mae_pct"] == Decimal("-0.001").to_eng_string()


def test_forward_excursion_is_conservative_when_stop_and_target_share_bar():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    bars = [
        bar(start, o="100", h="100", l="100", c="100"),
        bar(
            start + timedelta(minutes=1),
            o="100",
            h="100.7",
            l="99.5",
            c="100.2",
        ),
    ]

    result = forward_excursion(
        bars,
        decision_bar_time=stamp(bars[0]),
        entry_price=Decimal("100"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        horizons=(1,),
    )

    assert result["1"]["first_barrier"] == "stop"
    assert result["1"]["stop_before_target"] is True
    assert result["1"]["target_before_stop"] is False


def test_path_efficiency_separates_directional_from_choppy_path():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    directional = [
        bar(start, o="100", h="100.1", l="99.9", c="100"),
        bar(start + timedelta(minutes=1), o="100", h="100.2", l="100", c="100.1"),
        bar(start + timedelta(minutes=2), o="100.1", h="100.3", l="100.1", c="100.2"),
        bar(start + timedelta(minutes=3), o="100.2", h="100.4", l="100.2", c="100.3"),
    ]
    choppy = [
        bar(start, o="100", h="100.1", l="99.9", c="100"),
        bar(start + timedelta(minutes=1), o="100", h="100.4", l="100", c="100.3"),
        bar(start + timedelta(minutes=2), o="100.3", h="100.3", l="99.9", c="100"),
        bar(start + timedelta(minutes=3), o="100", h="100.4", l="100", c="100.3"),
    ]

    directional_features = decision_path_features(
        directional,
        fast_window=3,
        slow_window=8,
    )
    choppy_features = decision_path_features(
        choppy,
        fast_window=3,
        slow_window=8,
    )

    assert Decimal(directional_features["path_efficiency"]) > Decimal(
        choppy_features["path_efficiency"]
    )



def _study_row(feature_value: str, *, target: bool, stop: bool):
    return {
        "quality_allowed": True,
        "features": {"quality_score": feature_value},
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


def test_stable_rule_scan_requires_cross_period_improvement():
    period_a = {
        "period": "a",
        "observations": [
            _study_row("10", target=False, stop=True),
            _study_row("20", target=False, stop=True),
            _study_row("80", target=True, stop=False),
            _study_row("90", target=True, stop=False),
        ],
    }
    period_b = {
        "period": "b",
        "observations": [
            _study_row("10", target=False, stop=True),
            _study_row("20", target=False, stop=True),
            _study_row("80", target=True, stop=False),
            _study_row("90", target=True, stop=False),
        ],
    }

    result = stable_rule_scan(
        [period_a, period_b],
        horizon=15,
        min_per_period=2,
        top_n=10,
    )

    assert result["status"] == "research_only"
    assert result["candidate_frozen"] is False
    assert result["promotion_authorized"] is False
    assert result["rules"]
    assert any(
        any(
            condition["feature"] == "quality_score"
            and condition["operator"] == ">="
            and Decimal(condition["threshold"]) >= Decimal("80")
            for condition in rule["conditions"]
        )
        for rule in result["rules"]
    )
    for rule in result["rules"]:
        for label, metrics in rule["periods"].items():
            baseline = result["development_periods"][label]
            assert (
                metrics["target_before_stop_rate"]
                >= baseline["target_before_stop_rate"]
            )
            assert (
                metrics["stop_before_target_rate"]
                <= baseline["stop_before_target_rate"]
            )
