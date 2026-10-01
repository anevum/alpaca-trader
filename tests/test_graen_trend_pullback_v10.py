from graen.crypto.trend_pullback_v10 import (
    FAMILY,
    METHODOLOGY_VERSION,
    candidate_specs,
    development_gate,
    holdout_gate,
    validation_gate,
)


def _summary(
    *,
    trades=40,
    days=24,
    per_day=0.5,
    expectancy=0.001,
    pf=1.4,
    p=0.01,
    concentration=0.5,
):
    return {
        "trade_count": trades,
        "independent_day_blocks": days,
        "trades_per_day": per_day,
        "expectancy_per_trade": expectancy,
        "profit_factor": pf,
        "dependence_adjusted_null": {"p_value": p},
        "symbol_concentration": {"max_share": concentration},
    }


def test_v10_family_is_frozen_and_materially_distinct():
    specs = candidate_specs()
    assert METHODOLOGY_VERSION == "graen-crypto-trend-pullback-v10"
    assert FAMILY == "activity_confirmed_trend_pullback_recovery"
    assert len(specs) == 6
    assert len({row.candidate_id for row in specs}) == 6
    assert {row.trend_minutes for row in specs} >= {60, 120, 240, 360}
    assert all(row.pullback_minutes < row.trend_minutes for row in specs)
    assert all(row.hold_minutes > 0 for row in specs)


def test_v10_development_requires_frequency_cost_edge_and_delay():
    result = {
        "primary": _summary(),
        "one_bar_delay": _summary(expectancy=0.0005),
    }
    passed, reasons = development_gate(result)
    assert passed is True
    assert reasons == []

    result["primary"]["trades_per_day"] = 0.2
    result["one_bar_delay"]["expectancy_per_trade"] = -0.0001
    passed, reasons = development_gate(result)
    assert passed is False
    assert "development_frequency_below_0.35_per_day" in reasons
    assert "development_delay_expectancy_nonpositive" in reasons


def test_v10_validation_and_holdout_fail_closed():
    spec = candidate_specs()[0]
    validation = {
        "primary": _summary(trades=25, days=15, per_day=0.5),
        "one_bar_delay": _summary(expectancy=0.0005),
    }
    passed, reasons = validation_gate(spec, validation)
    assert passed is True
    assert reasons == []

    validation["primary"]["dependence_adjusted_null"]["p_value"] = 0.2
    passed, reasons = validation_gate(spec, validation)
    assert passed is False
    assert "validation_dependence_p_above_0.05" in reasons

    scenarios = {
        "low": {
            "primary": _summary(expectancy=0.0015),
            "one_bar_delay": _summary(expectancy=0.001),
        },
        "base": {
            "primary": _summary(expectancy=0.0012),
            "one_bar_delay": _summary(expectancy=0.0008),
        },
        "high": {
            "primary": _summary(trades=30, days=20, per_day=0.4, expectancy=0.001),
            "one_bar_delay": _summary(expectancy=0.0004),
        },
    }
    passed, reasons = holdout_gate(spec, scenarios)
    assert passed is True
    assert reasons == []
