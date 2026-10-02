from datetime import datetime, timedelta, timezone

from graen.crypto.activity_shock_v9 import (
    FAMILY,
    METHODOLOGY_VERSION,
    candidate_specs,
    development_gate,
    holdout_gate,
    validation_gate,
    verify_stage_corpus,
)


def _summary(*, trades=50, days=30, per_day=0.7, expectancy=0.001, pf=1.4, p=0.01, concentration=0.5):
    return {
        "trade_count": trades,
        "independent_day_blocks": days,
        "trades_per_day": per_day,
        "expectancy_per_trade": expectancy,
        "profit_factor": pf,
        "dependence_adjusted_null": {"p_value": p},
        "symbol_concentration": {"max_share": concentration},
    }


def test_v9_prespec_is_materially_distinct_and_frozen():
    specs = candidate_specs()
    assert METHODOLOGY_VERSION == "graen-crypto-activity-shock-v9"
    assert FAMILY == "activity_confirmed_momentum_continuation"
    assert len(specs) == 6
    assert len({row.candidate_id for row in specs}) == 6
    assert {row.impulse_minutes for row in specs} >= {10, 15, 30, 60}
    assert all(row.hold_minutes > 0 for row in specs)
    assert all(row.cooldown_minutes >= row.hold_minutes for row in specs)


def test_v9_development_requires_frequency_and_delay_robustness():
    result = {
        "primary": _summary(trades=60, days=30, per_day=0.8),
        "one_bar_delay": _summary(expectancy=0.0005),
    }
    passed, reasons = development_gate(result)
    assert passed is True
    assert reasons == []

    result["primary"]["trades_per_day"] = 0.2
    result["one_bar_delay"]["expectancy_per_trade"] = -0.0001
    passed, reasons = development_gate(result)
    assert passed is False
    assert "development_frequency_below_0.50_per_day" in reasons
    assert "development_delay_expectancy_nonpositive" in reasons


def test_v9_validation_and_holdout_fail_closed():
    spec = candidate_specs()[0]
    validation = {
        "primary": _summary(trades=25, days=15, per_day=0.6, p=0.01),
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
            "primary": _summary(trades=30, days=20, per_day=0.5, expectancy=0.001, p=0.01),
            "one_bar_delay": _summary(expectancy=0.0004),
        },
    }
    passed, reasons = holdout_gate(spec, scenarios)
    assert passed is True
    assert reasons == []


def test_v9_corpus_gate_requires_complete_symbol_coverage():
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    bars = {}
    for symbol in ("BTC/USD", "ETH/USD", "SOL/USD"):
        rows = []
        stamp = start
        while stamp < end:
            rows.append({
                "t": stamp.isoformat(),
                "o": 1,
                "h": 1,
                "l": 1,
                "c": 1,
                "v": 1,
                "n": 1,
            })
            stamp += timedelta(minutes=5)
        bars[symbol] = rows

    report = verify_stage_corpus(bars, start=start, end=end)
    assert report["passed"] is True
    assert report["expected_bars_per_symbol"] == 24

    bars["SOL/USD"] = bars["SOL/USD"][:5]
    try:
        verify_stage_corpus(bars, start=start, end=end)
    except ValueError as exc:
        assert "SOL/USD" in str(exc)
    else:
        raise AssertionError("incomplete v9 corpus must fail closed")
