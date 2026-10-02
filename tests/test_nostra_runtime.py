from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.nostra import (
    NostraLedger,
    build_evaluation,
    build_forecast,
    build_snapshot,
    score_direction_forecast,
    uniform_direction_baseline,
)
from app.nostra.service import NostraGateway, NostraRuntime, require_nostra_api_token


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



def test_nostra_api_token_required(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("NOSTRA_API_TOKEN", "a" * 32)
    require_nostra_api_token("a" * 32)
    try:
        require_nostra_api_token("wrong")
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("invalid token must be rejected")



class _FakeLedger:
    configured = True

    def __init__(self):
        self.snapshots = []
        self.forecasts = []
        self.outcomes = []
        self.scores = []
        self.evaluations = []

    async def append_snapshot(self, record, *, correlation_id=None):
        self.snapshots.append((record, correlation_id))
        return True

    async def append_forecast(self, record, *, correlation_id=None):
        self.forecasts.append((record, correlation_id))
        return True

    async def append_outcome(self, record, *, correlation_id=None):
        self.outcomes.append((record, correlation_id))
        return True

    async def append_score(self, record, *, correlation_id=None):
        self.scores.append((record, correlation_id))
        return True

    async def append_evaluation(self, record, *, correlation_id=None):
        self.evaluations.append((record, correlation_id))
        return True


class _FakeGateway:
    configured = True

    def __init__(self, payload):
        self.payload = payload

    async def work(self):
        return self.payload


def _candidate():
    return {
        "candidate_identity": "cycle-1:BTC/USD",
        "symbol": "BTC/USD",
        "market_lane": "crypto",
        "observed_at": NOW.isoformat(),
        "run_id": "run-1",
        "strategy_version_id": "CRYPTO-TEST",
        "features": {
            "feature_state": {
                "methodology_version": "crypto-features-v1",
                "raw": {
                    "momentum_return": 0.001,
                    "realized_volatility": 0.002,
                    "spread_bps": 5.0,
                },
                "normalized": {
                    "volatility_normalized_momentum": 0.5,
                },
                "time_state": {"hour_utc": 18.0},
            }
        },
        "scan_cycle": {
            "scan_cycle_id": "cycle-1",
            "data_status": "healthy",
            "data_feed": "alpaca",
            "bar_interval": "1Min",
        },
        "research_attribution": {"market": "crypto"},
    }


def test_live_baseline_issues_truthful_research_only_return_forecast():
    ledger = _FakeLedger()
    gateway = _FakeGateway(
        {
            "ok": True,
            "research_only": True,
            "execution_authority": False,
            "forecast_candidates": [_candidate()],
            "score_outcomes": [],
            "counts": {},
        }
    )
    runtime = NostraRuntime(ledger=ledger, gateway=gateway)

    result = asyncio.run(runtime.process_once())

    assert result["forecasts_persisted"] == 1
    snapshot = ledger.snapshots[0][0]
    forecast = ledger.forecasts[0][0]
    assert snapshot["provenance"]["candidate_identity"] == "cycle-1:BTC/USD"
    assert forecast["target_kind"] == "return"
    assert forecast["horizon_minutes"] == 10
    assert forecast["model_id"] == "zero_return"
    assert forecast["forecast_payload"]["expected_return"] == 0.0
    assert forecast["research_only"] is True
    assert forecast["execution_authority"] is False
    issued = datetime.fromisoformat(forecast["generated_at"])
    assert issued >= NOW
    assert forecast["generated_at"] != forecast["as_of_timestamp"]


def test_live_baseline_scores_only_gateway_matched_prior_forecast():
    ledger = _FakeLedger()
    gateway = _FakeGateway(
        {
            "ok": True,
            "research_only": True,
            "execution_authority": False,
            "forecast_candidates": [],
            "score_outcomes": [
                {
                    "forecast_id": "nsf_prior",
                    "candidate_identity": "cycle-1:BTC/USD",
                    "symbol": "BTC/USD",
                    "observed_at": "2026-10-02T18:10:00+00:00",
                    "realized_return": 0.01,
                    "max_favorable_return": "0.015",
                    "max_adverse_return": "-0.004",
                    "outcome_methodology_version": "candidate-forward-crypto-v1",
                }
            ],
            "counts": {},
        }
    )
    runtime = NostraRuntime(ledger=ledger, gateway=gateway)

    result = asyncio.run(runtime.process_once())

    assert result["scores_persisted"] == 1
    outcome = ledger.outcomes[0][0]
    score = ledger.scores[0][0]
    assert outcome["forecast_id"] == "nsf_prior"
    assert outcome["realized_payload"]["candidate_identity"] == "cycle-1:BTC/USD"
    assert score["forecast_id"] == "nsf_prior"
    assert score["metrics"]["absolute_error"] == 0.01
    assert score["skill"]["absolute_error"] == 0.0
    assert score["execution_authority"] is False


def test_nostra_gateway_derives_scoped_read_url(monkeypatch):
    monkeypatch.setenv(
        "FOUNDATION_EVENTS_URL",
        "http://foundation.railway.internal:8080/v1/events",
    )
    monkeypatch.setenv("NOSTRA_GATEWAY_TOKEN", "g" * 32)
    gateway = NostraGateway()
    assert gateway.configured is True
    assert gateway.url.endswith("/v1/nostra-gateway")



def test_nostra_evaluation_identity_is_stable_across_runtime_restarts():
    common = {
        "model_id": "zero_return",
        "model_version": "nostra-baselines-v1",
        "horizon_minutes": 10,
        "target_kind": "return",
        "window_start": datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc),
        "window_end": datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc),
        "sample_count": 48,
        "through_score_id": "nsc_through",
        "metrics": {
            "mean_absolute_error": 0.001,
            "root_mean_squared_error": 0.002,
        },
        "calibration": {
            "residual_mean": 0.0001,
            "residual_stddev": 0.0015,
        },
    }
    first = build_evaluation(
        **common,
        evaluated_at=datetime(2026, 10, 2, 19, 1, tzinfo=timezone.utc),
        provenance={"deployment_id": "deploy-a"},
    )
    second = build_evaluation(
        **common,
        evaluated_at=datetime(2026, 10, 2, 19, 2, tzinfo=timezone.utc),
        provenance={"deployment_id": "deploy-b"},
    )

    assert first["evaluation_id"] == second["evaluation_id"]
    assert first["research_only"] is True
    assert first["execution_authority"] is False


def test_live_runtime_persists_rolling_baseline_evaluation_once_per_score_cut():
    ledger = _FakeLedger()
    evaluation = {
        "schema_version": "nostra-baseline-evaluation-work-v1",
        "model_id": "zero_return",
        "model_version": "nostra-baselines-v1",
        "horizon_minutes": 10,
        "target_kind": "return",
        "window_start": "2026-10-02T18:10:00+00:00",
        "window_end": "2026-10-02T18:20:00+00:00",
        "sample_count": 48,
        "through_score_id": "nsc_through",
        "metrics": {
            "mean_expected_return": 0.0,
            "mean_realized_return": 0.0002,
            "bias": -0.0002,
            "mean_absolute_error": 0.001,
            "mean_squared_error": 0.000002,
            "root_mean_squared_error": 0.001414213562,
        },
        "calibration": {
            "residual_mean": 0.0002,
            "residual_stddev": 0.0014,
            "realized_return_quantiles": {
                "p05": -0.002,
                "p25": -0.0005,
                "p50": 0.0001,
                "p75": 0.0008,
                "p95": 0.0025,
            },
            "absolute_error_quantiles": {
                "p50": 0.0008,
                "p90": 0.0022,
                "p95": 0.0028,
            },
        },
        "research_only": True,
        "execution_authority": False,
    }
    gateway = _FakeGateway(
        {
            "ok": True,
            "research_only": True,
            "execution_authority": False,
            "forecast_candidates": [],
            "score_outcomes": [],
            "baseline_evaluation": evaluation,
            "counts": {},
        }
    )
    runtime = NostraRuntime(ledger=ledger, gateway=gateway)

    first = asyncio.run(runtime.process_once())
    second = asyncio.run(runtime.process_once())

    assert first["evaluations_persisted"] == 1
    assert second["evaluations_persisted"] == 0
    assert len(ledger.evaluations) == 1
    persisted = ledger.evaluations[0][0]
    assert persisted["sample_count"] == 48
    assert persisted["metrics"]["mean_absolute_error"] == 0.001
    assert persisted["calibration"]["residual_stddev"] == 0.0014
    assert persisted["research_only"] is True
    assert persisted["execution_authority"] is False
