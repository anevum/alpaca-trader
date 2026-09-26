from app.edge_development_decisive import irreversible_development_rejections


def period(**expectancies):
    return {
        "summary_by_family": {
            family: {
                "events": 1 if value != 0 else 0,
                "expectancy_pct": str(value),
            }
            for family, value in expectancies.items()
        }
    }


def test_worst_period_failure_is_irreversible():
    result = irreversible_development_rejections(
        [
            period(
                controlled_continuation=-0.002,
                pullback_reclaim=0.001,
                compression_breakout=0.001,
                relative_strength_impulse=0.001,
                opening_breakout_retest=0,
            )
        ],
        total_periods=6,
    )

    item = result["families"]["controlled_continuation"]
    assert item["irreversibly_rejected"] is True
    assert item["reason"] == "worst_period_below_floor"


def test_positive_period_impossibility_rejects_after_three_nonpositive_periods():
    periods = [
        period(
            controlled_continuation=0,
            pullback_reclaim=0,
            compression_breakout=0,
            relative_strength_impulse=0,
            opening_breakout_retest=0,
        )
        for _ in range(3)
    ]
    result = irreversible_development_rejections(
        periods,
        total_periods=6,
    )

    item = result["families"]["opening_breakout_retest"]
    assert item["positive_periods_seen"] == 0
    assert item["max_possible_positive_periods"] == 3
    assert item["irreversibly_rejected"] is True
    assert item["reason"] == "insufficient_possible_positive_periods"


def test_two_positive_periods_after_three_windows_can_still_recover():
    periods = [
        period(
            controlled_continuation=0.001,
            pullback_reclaim=0.001,
            compression_breakout=0.001,
            relative_strength_impulse=0.001,
            opening_breakout_retest=0.001,
        ),
        period(
            controlled_continuation=0.001,
            pullback_reclaim=0.001,
            compression_breakout=0.001,
            relative_strength_impulse=0.001,
            opening_breakout_retest=0.001,
        ),
        period(
            controlled_continuation=0,
            pullback_reclaim=0,
            compression_breakout=0,
            relative_strength_impulse=0,
            opening_breakout_retest=0,
        ),
    ]
    result = irreversible_development_rejections(
        periods,
        total_periods=6,
    )

    item = result["families"]["opening_breakout_retest"]
    assert item["max_possible_positive_periods"] == 5
    assert item["irreversibly_rejected"] is False
