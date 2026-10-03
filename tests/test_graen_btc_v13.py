from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_hypotheses_v13 import (
    CAMPAIGN_ID,
    FAMILY,
    METHODOLOGY_VERSION,
    candidate_specs,
    holdout_may_open,
    hypothesis_registry,
    spec_from_dict,
)


UTC = timezone.utc


def _bars(start: datetime, count: int = 420):
    rows = []
    price = 100000.0
    for index in range(count):
        # Deterministic alternating microstructure with a slowly varying drift.
        drift = 0.00020 if (index // 24) % 2 == 0 else -0.00012
        shock = 0.0010 if index % 37 == 0 else (-0.0008 if index % 29 == 0 else 0.0)
        open_ = price
        close = max(open_ * (1.0 + drift + shock), 1.0)
        high = max(open_, close) * 1.0005
        low = min(open_, close) * 0.9995
        volume = 8.0 + (index % 11)
        rows.append({
            "t": (start + timedelta(minutes=5 * index)).isoformat(),
            "o": open_,
            "h": high,
            "l": low,
            "c": close,
            "v": volume,
            "n": 20 + (index % 17),
            "vw": (open_ + close) / 2.0,
        })
        price = close
    return rows


def test_v13_is_new_frozen_hypothesis_tournament():
    specs = candidate_specs()
    assert CAMPAIGN_ID == "btc-hypothesis-tournament-v13"
    assert METHODOLOGY_VERSION == "graen-btc-hypothesis-tournament-v13"
    assert FAMILY == "btc_new_hypothesis_tournament"
    assert len(specs) == 8
    assert len({row.candidate_id for row in specs}) == 8
    assert {row.mechanism for row in specs} == {
        "serial_dependence_reversion",
        "fair_value_dislocation_reversion",
        "return_acceleration",
        "volatility_of_volatility_transition",
    }
    exhausted = {
        "compression_breakout",
        "vol_normalized_trend",
        "downshock_reclaim",
        "activity_confirmed_trend_pullback_recovery",
    }
    assert not ({row.mechanism for row in specs} & exhausted)


def test_v13_registry_records_mechanism_and_cost_contract():
    registry = hypothesis_registry()
    assert len(registry) == len(candidate_specs())
    for row in registry:
        assert row["mechanism_hypothesis"]
        assert row["why_it_could_exist"]
        assert row["observable_inputs"]
        assert row["entry_condition"]
        assert row["exit_logic"]
        assert row["invalidation_condition"]
        assert row["transaction_cost_assumptions"]["selection_scenario"] == "high"
        assert row["transaction_cost_assumptions"]["delayed_entry_minutes"] == 5


def test_v13_spec_round_trip_is_deterministic():
    for spec in candidate_specs():
        assert spec_from_dict(spec.to_dict()) == spec
    assert candidate_specs() == candidate_specs()
    assert hypothesis_registry() == hypothesis_registry()


def test_v13_holdout_guard_fails_closed():
    assert holdout_may_open(validation_passed=False, velum_passed=False) is False
    assert holdout_may_open(validation_passed=True, velum_passed=False) is False
    assert holdout_may_open(validation_passed=False, velum_passed=True) is False
    assert holdout_may_open(validation_passed=True, velum_passed=True) is True


def test_v13_definitions_never_grant_execution_authority():
    registry = hypothesis_registry()
    assert all("execution_authority" not in row for row in registry)
    # Strategy definitions contain signals only; broker configuration is not part of the spec.
    forbidden = {"broker", "position_size", "capital_allocation", "live_execution"}
    assert all(not (forbidden & set(row)) for row in registry)
