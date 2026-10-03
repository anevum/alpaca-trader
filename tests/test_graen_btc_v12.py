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



def _five_minute_bars(start: datetime, count: int):
    rows = []
    for index in range(count):
        stamp = start + timedelta(minutes=5 * index)
        drift = 0.02 * index
        pulse = 0.5 if index % 37 == 0 else 0.0
        price = 100.0 + drift + pulse
        rows.append({
            "t": stamp.isoformat(),
            "o": price,
            "h": price + 0.25,
            "l": price - 0.25,
            "c": price + (0.05 if index % 3 else -0.03),
            "v": 10.0 + (index % 11),
            "n": 5 + (index % 7),
        })
    return rows


def test_v12_shared_series_matches_standalone_candidate_evaluation():
    fold_start = datetime(2025, 6, 3, tzinfo=UTC)
    fold_end = fold_start + timedelta(hours=8)
    data_start = fold_start - timedelta(hours=32)
    bars = {"BTC/USD": _five_minute_bars(data_start, 520)}
    spec = v12.candidate_specs()[0]

    standalone = v12.evaluate_candidate(
        bars,
        spec=spec,
        start=fold_start,
        end=fold_end,
        scenario="high",
        seed=777,
    )
    shared = v12.build_series(
        bars,
        start=fold_start - timedelta(hours=4),
        end=fold_end + timedelta(hours=4),
        warmup_hours=26,
    )
    optimized = v12.evaluate_candidate_from_series(
        shared,
        spec=spec,
        start=fold_start,
        end=fold_end,
        scenario="high",
        seed=777,
    )

    assert optimized == standalone


def test_v12_development_builds_market_series_once(monkeypatch):
    calls = {"build": 0, "evaluate": 0}
    spec = v12.candidate_specs()[0]

    def fake_build(*args, **kwargs):
        calls["build"] += 1
        return {"BTC/USD": {}}

    def fake_evaluate(series, *, spec, start, end, scenario, seed):
        calls["evaluate"] += 1
        return {
            "candidate": spec.to_dict(),
            "primary": {
                "trade_count": 0,
                "independent_day_blocks": 0,
                "expectancy_per_trade": 0.0,
                "profit_factor": None,
            },
            "one_bar_delay": {"expectancy_per_trade": 0.0},
        }

    monkeypatch.setattr(v12, "candidate_specs", lambda: (spec,))
    monkeypatch.setattr(v12, "build_series", fake_build)
    monkeypatch.setattr(v12, "evaluate_candidate_from_series", fake_evaluate)

    result = v12.evaluate_development({"BTC/USD": []})

    assert calls["build"] == 1
    assert calls["evaluate"] == 1 + len(v12.DEVELOPMENT_FOLDS)
    assert result["candidate_count"] == 1
    assert result["selected_candidate_id"] is None
