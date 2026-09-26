from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.entry_exit_surface import (
    consistent_surface_cells,
    simulate_surface_exit,
)


NY = ZoneInfo("America/New_York")


def bar(at, *, high, low, close):
    return {
        "t": at.isoformat(),
        "o": str(close),
        "h": str(high),
        "l": str(low),
        "c": str(close),
        "v": "100",
        "vw": str(close),
    }


def test_surface_exit_uses_stop_first_when_one_bar_touches_both():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    result = simulate_surface_exit(
        future_bars=[
            bar(
                start + timedelta(minutes=1),
                high="100.7",
                low="99.5",
                close="100.1",
            )
        ],
        entry_price=Decimal("100"),
        stop_pct=Decimal("0.0035"),
        target_pct=Decimal("0.005"),
        horizon=1,
        spread_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
    )

    assert result is not None
    assert result["exit_reason"] == "stop"
    assert Decimal(result["net_return"]) < Decimal("-0.0035")


def test_surface_time_exit_includes_sell_friction():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=NY)
    result = simulate_surface_exit(
        future_bars=[
            bar(
                start + timedelta(minutes=1),
                high="100.1",
                low="99.9",
                close="100",
            )
        ],
        entry_price=Decimal("100"),
        stop_pct=Decimal("0.01"),
        target_pct=Decimal("0.01"),
        horizon=1,
        spread_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
    )

    assert result is not None
    assert result["exit_reason"] == "time"
    assert Decimal(result["net_return"]) == Decimal("-0.00045")


def test_consistent_surface_requires_positive_mean_in_every_period():
    positive = {
        "cells": [
            {
                "horizon_minutes": 15,
                "stop_pct": "0.0035",
                "target_pct": "0.005",
                "summary": {"mean_return": "0.001", "n": 30},
            }
        ]
    }
    negative = {
        "cells": [
            {
                "horizon_minutes": 15,
                "stop_pct": "0.0035",
                "target_pct": "0.005",
                "summary": {"mean_return": "-0.001", "n": 30},
            }
        ]
    }

    rejected = consistent_surface_cells([positive, negative])
    assert rejected["positive_in_all_periods"] == []
    assert (
        rejected["current_signal_family_rescued_by_simple_exit_geometry"]
        is False
    )

    accepted = consistent_surface_cells([positive, positive])
    assert accepted["positive_in_all_periods"]
    assert (
        accepted["current_signal_family_rescued_by_simple_exit_geometry"]
        is True
    )
