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



def test_v12_signal_cache_reuses_raw_decisions_without_carrying_cooldown(monkeypatch):
    spec = v12.candidate_specs()[0]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    calls = []

    def fake_opportunity(series, candidate, symbol, stamp):
        calls.append(stamp)
        return None

    monkeypatch.setattr(v12, "opportunity_at", fake_opportunity)
    cache = {}
    first = v12.collect_opportunities(
        {},
        spec,
        start=start,
        end=end,
        signal_cache=cache,
    )
    first_call_count = len(calls)
    second = v12.collect_opportunities(
        {},
        spec,
        start=start + timedelta(minutes=5),
        end=end,
        signal_cache=cache,
    )

    assert first == []
    assert second == []
    assert first_call_count == 6
    assert len(calls) == first_call_count
    assert len(cache) == 6


def test_v12_prepared_series_skips_rebuild(monkeypatch):
    spec = v12.candidate_specs()[0]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=30)

    def fail_build(*args, **kwargs):
        raise AssertionError("prepared series must be reused")

    monkeypatch.setattr(v12, "build_series", fail_build)
    monkeypatch.setattr(v12, "collect_opportunities", lambda *args, **kwargs: [])
    monkeypatch.setattr(v12, "simulate", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        v12,
        "summarize",
        lambda *args, **kwargs: {
            "trade_count": 0,
            "independent_day_blocks": 0,
            "trades_per_day": 0.0,
            "expectancy_per_trade": 0.0,
            "profit_factor": None,
        },
    )

    result = v12.evaluate_candidate(
        {},
        spec=spec,
        start=start,
        end=end,
        scenario="high",
        seed=1,
        prepared_series={},
        signal_cache={},
    )
    assert result["opportunity_count"] == 0
    assert result["primary"]["trade_count"] == 0
