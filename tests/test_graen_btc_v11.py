from datetime import datetime, timedelta, timezone

import graen.crypto.btc_trend_pullback_v11 as v11


UTC = timezone.utc


def _summary(*, trades=60, days=45, expectancy=0.002, pf=1.5):
    return {
        "trade_count": trades,
        "independent_day_blocks": days,
        "trades_per_day": 0.5,
        "expectancy_per_trade": expectancy,
        "profit_factor": pf,
        "symbol_concentration": {"max_share": 1.0},
        "dependence_adjusted_null": {"p_value": 0.01},
    }


def test_v11_is_btc_only_and_has_six_frozen_candidates():
    specs = v11.candidate_specs()
    assert v11.UNIVERSE == ("BTC/USD",)
    assert len(specs) == 6
    assert all(spec.candidate_id.startswith("V11-BTC-TPR-") for spec in specs)
    assert all(spec.concentration_limit == 1.0 for spec in specs)


def test_v11_corpus_verifier_is_btc_only_and_fail_closed():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=25)
    bars = {
        "BTC/USD": [
            {"t": (start + timedelta(minutes=5 * index)).isoformat()}
            for index in range(5)
        ],
        "SOL/USD": [],
    }
    result = v11.verify_development_corpus(
        bars,
        start=start,
        end=end,
        min_fraction=1.0,
    )
    assert result["passed"] is True
    assert result["symbol"] == "BTC/USD"
    assert result["evidence_role"] == "DEVELOPMENT_ONLY"

    try:
        v11.verify_development_corpus(
            {"BTC/USD": bars["BTC/USD"][:2]},
            start=start,
            end=end,
            min_fraction=1.0,
        )
    except ValueError as exc:
        assert str(exc) == "v11_btc_development_corpus_incomplete:BTC/USD"
    else:
        raise AssertionError("incomplete BTC development corpus must fail closed")


def test_v11_development_gate_requires_temporal_robustness():
    aggregate = {
        "primary": _summary(),
        "one_bar_delay": _summary(expectancy=0.001),
    }
    passing_fold = {"passed": True}
    failing_fold = {"passed": False}

    passed, reasons = v11.development_gate(
        aggregate,
        [passing_fold] * 5 + [failing_fold] * 2,
    )
    assert passed is True
    assert reasons == []

    passed, reasons = v11.development_gate(
        aggregate,
        [passing_fold] * 4 + [failing_fold] * 3,
    )
    assert passed is False
    assert "development_positive_temporal_folds_below_5_of_7" in reasons
