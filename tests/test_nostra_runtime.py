from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.nostra import (
    NostraLedger,
    build_forecast,
    build_snapshot,
    score_direction_forecast,
    uniform_direction_baseline,
)
from app.nostra.service import NostraRuntime


NOW = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)


def test_nostra_contracts_are_deterministic_and_research_only():
    snapshot = build_snapshot(
        symbol="ETH/USD",
        market_lane="crypto",
        as_of_timestamp=NOW,
        feature_set_version="test-v1",
        raw_features={"x": 1.0},
    )
    baseline = uniform_direction_baseline()
    forecast = build_forecast(
        snapshot_id=snapshot["snapshot_id"],
        symbol="ETH/USD",
        market_lane="crypto",
        as_of_timestamp=NOW,
        generated_at=NOW,
        horizon_minutes=10,
        target_kind="direction",
        model_id=baseline["baseline_id"],
        model_version=baseline["baseline_version"],
        feature_set_version="test-v1",
        forecast_payload={"probabilities": baseline["probabilities"]},
    )
    assert snapshot["research_only"] is True
    assert snapshot["execution_authority"] is False
    assert forecast["research_only"] is True
    assert forecast["execution_authority"] is False


def test_nostra_scoring_retains_baseline_relative_skill():
    baseline = uniform_direction_baseline()
    score = score_direction_forecast(
        {"up": 0.7, "neutral": 0.2, "down": 0.1},
        "up",
        baseline_probabilities=baseline["probabilities"],
    )
    assert score["skill"]["brier"] > 0
    assert score["execution_authority"] is False


def test_nostra_ledger_fails_closed_without_foundation():
    ledger = NostraLedger(url="", token="")
    assert ledger.configured is False
    snapshot = build_snapshot(
        symbol="BTC/USD",
        market_lane="crypto",
        as_of_timestamp=NOW,
        feature_set_version="test-v1",
        raw_features={},
    )
    assert asyncio.run(ledger.append_snapshot(snapshot)) is False


def test_nostra_runtime_has_no_execution_authority(monkeypatch):
    monkeypatch.setenv("FOUNDATION_EVENTS_URL", "http://foundation/v1/events")
    monkeypatch.setenv("FOUNDATION_INGEST_TOKEN", "x" * 32)
    health = NostraRuntime().health()
    assert health["system"] == "NOSTRA"
    assert health["program"] == "FORWARD"
    assert health["research_only"] is True
    assert health["execution_authority"] is False


def test_nostra_runtime_source_has_no_broker_or_order_client_imports():
    source = (Path(__file__).resolve().parents[1] / "app" / "nostra" / "service.py").read_text()
    lowered = source.lower()
    assert "alpaca" not in lowered
    assert "submit_order" not in lowered
    assert "broker_client" not in lowered
