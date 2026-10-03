from datetime import datetime, timedelta, timezone

import graen.crypto.btc_mechanisms_v12 as v12


UTC = timezone.utc


def _aggregate(*, trades=80, days=55, expectancy=0.0015, pf=1.3):
    primary = {
        "trade_count": trades,
        "independent_day_blocks": days,
        "expectancy_per_trade": expectancy,
        "profit_factor": pf,
    }
    delayed = {
        "expectancy_per_trade": max(expectancy / 2, 0.0001),
    }
    return {"primary": primary, "one_bar_delay": delayed}


def test_v12_is_btc_only_and_spans_three_orthogonal_mechanisms():
    specs = v12.candidate_specs()
    assert v12.UNIVERSE == ("BTC/USD",)
    assert len(specs) == 8
    assert {spec.mechanism for spec in specs} == {
        "compression_breakout",
        "vol_normalized_trend",
        "downshock_reclaim",
    }
    assert all(spec.candidate_id.startswith("V12-BTC-") for spec in specs)
    assert all(spec.concentration_limit == 1.0 for spec in specs)


def test_v12_development_gate_requires_positive_aggregate_and_five_of_seven_folds():
    aggregate = _aggregate()
    passed, reasons = v12.development_gate(
        aggregate,
        [{"passed": True}] * 5 + [{"passed": False}] * 2,
    )
    assert passed is True
    assert reasons == []

    passed, reasons = v12.development_gate(
        aggregate,
        [{"passed": True}] * 4 + [{"passed": False}] * 3,
    )
    assert passed is False
    assert "development_positive_temporal_folds_below_5_of_7" in reasons

    negative = _aggregate(expectancy=-0.001, pf=0.8)
    negative["one_bar_delay"]["expectancy_per_trade"] = -0.001
    passed, reasons = v12.development_gate(
        negative,
        [{"passed": True}] * 7,
    )
    assert passed is False
    assert "development_expectancy_nonpositive" in reasons
    assert "development_profit_factor_not_above_one" in reasons
    assert "development_delay_expectancy_nonpositive" in reasons


def test_v12_opportunity_rejects_non_btc_symbol():
    spec = v12.candidate_specs()[0]
    assert v12.opportunity_at({}, spec, "ETH/USD", datetime(2026, 10, 3, tzinfo=UTC)) is None
