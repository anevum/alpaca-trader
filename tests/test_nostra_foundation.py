from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.nostra import (
    NostraLedger,
    build_forecast,
    build_outcome,
    build_snapshot,
    empirical_direction_baseline,
    score_direction_forecast,
    score_return_forecast,
    uniform_direction_baseline,
)


NOW = datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc)


def snapshot(**overrides):
    values = {
        "symbol": "ETH/USD",
        "market_lane": "crypto",
        "as_of_timestamp": NOW,
        "feature_set_version": "crypto-features-v1",
        "raw_features": {"momentum_return": 0.002, "spread_bps": 4.0},
        "normalized_features": {"momentum_z": 1.2},
        "market_state": {"regime": "trend"},
        "code_sha": "abc123",
    }
    values.update(overrides)
    return build_snapshot(**values)


def forecast(snapshot_id: str, **overrides):
    values = {
        "snapshot_id": snapshot_id,
        "symbol": "ETH/USD",
        "market_lane": "crypto",
        "as_of_timestamp": NOW,
        "generated_at": NOW + timedelta(seconds=2),
        "horizon_minutes": 10,
        "target_kind": "direction",
        "model_id": "direction-baseline",
        "model_version": "v1",
        "feature_set_version": "crypto-features-v1",
        "forecast_payload": {
            "probabilities": {"up": 0.55, "neutral": 0.20, "down": 0.25}
        },
        "authority_state": "LOW_SUPPORT",
    }
    values.update(overrides)
    return build_forecast(**values)


def test_snapshot_id_is_deterministic_and_contract_has_no_execution_authority():
    left = snapshot()
    right = snapshot()
    assert left["snapshot_id"] == right["snapshot_id"]
    assert left["research_only"] is True
    assert left["execution_authority"] is False
    assert "outcome" not in left
    assert "realized_return" not in left


def test_snapshot_requires_point_in_time_timezone():
    with pytest.raises(ValueError):
        snapshot(as_of_timestamp=datetime(2026, 9, 30, 2, 30))


def test_forecast_validates_probability_mass_and_time_direction():
    snap = snapshot()
    with pytest.raises(ValueError):
        forecast(
            snap["snapshot_id"],
            forecast_payload={
                "probabilities": {"up": 0.8, "neutral": 0.3, "down": 0.1}
            },
        )
    with pytest.raises(ValueError):
        forecast(
            snap["snapshot_id"],
            generated_at=NOW - timedelta(seconds=1),
        )


def test_forecast_and_outcome_are_separate_immutable_contracts():
    snap = snapshot()
    pred = forecast(snap["snapshot_id"])
    outcome = build_outcome(
        forecast_id=pred["forecast_id"],
        observed_at=NOW + timedelta(minutes=10),
        realized_payload={"forward_return": 0.0042, "direction": "up"},
    )
    assert pred["forecast_id"] != outcome["outcome_id"]
    assert "realized_payload" not in pred
    assert outcome["forecast_id"] == pred["forecast_id"]
    assert outcome["execution_authority"] is False


def test_empirical_baseline_uses_only_supplied_prior_labels():
    baseline = empirical_direction_baseline(
        ["up", "up", "down"],
        labels=("up", "neutral", "down"),
    )
    probabilities = baseline["probabilities"]
    assert probabilities["up"] == 0.5
    assert probabilities["neutral"] == 1 / 6
    assert probabilities["down"] == 1 / 3
    assert baseline["samples"] == 3


def test_direction_scoring_reports_skill_relative_to_baseline():
    baseline = uniform_direction_baseline()
    result = score_direction_forecast(
        {"up": 0.8, "neutral": 0.1, "down": 0.1},
        "up",
        baseline_probabilities=baseline["probabilities"],
    )
    assert result["brier"] < result["baseline"]["brier"]
    assert result["skill"]["brier"] > 0
    assert result["skill"]["log_loss"] > 0


def test_return_scoring_can_identify_negative_skill():
    result = score_return_forecast(
        0.02,
        0.001,
        baseline_expected_return=0.0,
    )
    assert result["skill"]["absolute_error"] < 0
    assert result["execution_authority"] is False


class FakeSink:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.events = []

    async def emit_critical(self, **event):
        self.events.append(event)
        return True


def test_ledger_uses_critical_durable_path_and_fails_closed_when_disabled():
    snap = snapshot()
    sink = FakeSink(enabled=True)
    ledger = NostraLedger(sink)

    assert asyncio.run(ledger.append_snapshot(snap)) is True
    assert sink.events[0]["event_type"] == "nostra_snapshot"
    assert sink.events[0]["event_key"].endswith(snap["snapshot_id"])

    disabled = NostraLedger(FakeSink(enabled=False))
    assert asyncio.run(disabled.append_snapshot(snap)) is False


def test_ledger_rejects_any_record_with_execution_authority():
    snap = snapshot()
    snap["execution_authority"] = True
    ledger = NostraLedger(FakeSink(enabled=True))
    with pytest.raises(ValueError):
        asyncio.run(ledger.append_snapshot(snap))
