from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from foundation.nostra_gateway import _baseline_evaluation, _point_in_time_candidate

from foundation.ingest.service import (
    EvidenceEvent,
    canonical_payload_hash,
    infer_run,
    nostra_gateway_authorized,
    project_event,
    reconciliation_result,
    require_foundation_token,
    require_nostra_gateway_token,
)


def event(**overrides):
    values = {
        "event_key": "run-1:test:1",
        "run_id": "run-1",
        "strategy_version_id": "strategy-1",
        "event_type": "test",
        "occurred_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "source": "test",
        "payload": {},
    }
    values.update(overrides)
    return EvidenceEvent(**values)


def test_payload_hash_is_canonical():
    left = canonical_payload_hash({"b": 2, "a": 1})
    right = canonical_payload_hash({"a": 1, "b": 2})
    assert left == right
    assert left.startswith("sha256:")


def test_infer_crypto_run():
    assert infer_run(event(payload={"market_lane":"crypto","execution_mode":"shadow"})) == ("crypto","shadow")


def test_infer_equity_run():
    assert infer_run(event(payload={"market_lane":"us_equity","trading_mode":"live"})) == ("equity","live")


def test_ingest_token_accepts_legacy_header_alias(monkeypatch):
    monkeypatch.setenv("FOUNDATION_INGEST_TOKEN", "foundation-secret")
    require_foundation_token("foundation-secret", None)
    require_foundation_token(None, "foundation-secret")
    with pytest.raises(HTTPException):
        require_foundation_token(None, "wrong")


def test_reconciliation_is_safe_only_without_blockers():
    observed = datetime(2026,10,1,tzinfo=timezone.utc)
    safe = reconciliation_result(
        unresolved_intents=[],
        unknown_open_orders=[],
        untracked_positions=[],
        observed_at=observed,
    )
    assert safe["safe_to_enter"] is True
    blocked = reconciliation_result(
        unresolved_intents=[{"client_order_id":"anevum-a"}],
        unknown_open_orders=[],
        untracked_positions=[],
        observed_at=observed,
    )
    assert blocked["safe_to_enter"] is False
    assert blocked["reason"] == "unresolved_intents:1"



def test_nostra_gateway_token_is_scoped_to_nostra_events(monkeypatch):
    monkeypatch.setenv("NOSTRA_GATEWAY_TOKEN", "n" * 32)

    nostra_batch = __import__("foundation.ingest.service", fromlist=["EvidenceBatch"]).EvidenceBatch(
        events=[event(source="NOSTRA", event_type="nostra_snapshot")]
    )
    assert nostra_gateway_authorized(nostra_batch, "n" * 32) is True
    assert nostra_gateway_authorized(nostra_batch, "wrong") is False

    mixed_batch = __import__("foundation.ingest.service", fromlist=["EvidenceBatch"]).EvidenceBatch(
        events=[
            event(source="NOSTRA", event_type="nostra_snapshot"),
            event(source="RHEN", event_type="test"),
        ]
    )
    assert nostra_gateway_authorized(mixed_batch, "n" * 32) is False



def test_nostra_read_gateway_requires_scoped_token(monkeypatch):
    monkeypatch.setenv("NOSTRA_GATEWAY_TOKEN", "z" * 32)
    require_nostra_gateway_token("z" * 32)
    with pytest.raises(HTTPException):
        require_nostra_gateway_token("wrong")


def test_nostra_gateway_candidate_is_point_in_time_and_rejects_future_fields():
    candidate = {
        "candidate_key": "cycle:BTC/USD",
        "symbol": "BTC/USD",
        "market_lane": "crypto",
        "observed_at": "2026-10-02T18:00:00+00:00",
        "run_id": "run-1",
        "strategy_version_id": "CRYPTO-TEST",
        "features": {"feature_state": {"raw": {"momentum_return": 0.01}}},
        "scan_cycle": {"scan_cycle_id": "cycle", "data_status": "healthy"},
    }
    projected = _point_in_time_candidate(candidate)
    assert projected is not None
    assert projected["candidate_identity"] == "cycle:BTC/USD"
    assert projected["features"]["feature_state"]["raw"]["momentum_return"] == 0.01
    assert "forward_outcomes" not in projected

    contaminated = dict(candidate)
    contaminated["forward_outcomes"] = {"10": {"forward_return": 0.02}}
    assert _point_in_time_candidate(contaminated) is None



class _EvaluationCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.args = None

    def execute(self, query, args=None):
        self.query = str(query)
        self.args = args

    def fetchall(self):
        return list(self.rows)


def test_nostra_baseline_evaluation_aggregates_realized_scores():
    generated = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)
    first_observed = datetime(2026, 10, 2, 18, 10, tzinfo=timezone.utc)
    second_observed = datetime(2026, 10, 2, 18, 20, tzinfo=timezone.utc)
    cur = _EvaluationCursor([
        (
            "nsc-1",
            "nostra-baselines-v1",
            generated,
            first_observed,
            "0",
            "0.01",
            "0.01",
            "0.0001",
        ),
        (
            "nsc-2",
            "nostra-baselines-v1",
            generated,
            second_observed,
            "0",
            "-0.01",
            "0.01",
            "0.0001",
        ),
    ])

    result = _baseline_evaluation(
        cur,
        current=datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc),
        lookback_hours=24,
    )

    assert result is not None
    assert result["sample_count"] == 2
    assert result["through_score_id"] == "nsc-2"
    assert result["window_start"] == first_observed.isoformat()
    assert result["window_end"] == second_observed.isoformat()
    assert result["metrics"]["mean_realized_return"] == pytest.approx(0.0)
    assert result["metrics"]["mean_absolute_error"] == pytest.approx(0.01)
    assert result["metrics"]["root_mean_squared_error"] == pytest.approx(0.01)
    assert result["calibration"]["residual_mean"] == pytest.approx(0.0)
    assert result["calibration"]["residual_stddev"] == pytest.approx(0.01)
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert "nostra.evidence_scores" in cur.query


class _ProjectionCursor:
    def __init__(self):
        self.query = ""
        self.args = None

    def execute(self, query, args=None):
        self.query = str(query)
        self.args = args


def test_nostra_evaluation_projects_to_append_only_evaluation_table():
    cur = _ProjectionCursor()
    payload = {
        "evaluation_id": "nse-1",
        "evaluated_at": "2026-10-02T19:01:00+00:00",
        "model_id": "zero_return",
        "model_version": "nostra-baselines-v1",
        "horizon_minutes": 10,
        "target_kind": "return",
        "window_start": "2026-10-02T18:10:00+00:00",
        "window_end": "2026-10-02T19:00:00+00:00",
        "sample_count": 48,
        "through_score_id": "nsc-through",
        "research_only": True,
        "execution_authority": False,
    }
    project_event(
        cur,
        event(
            run_id=None,
            strategy_version_id=None,
            source="NOSTRA",
            event_type="nostra_evaluation",
            payload=payload,
        ),
    )

    assert "insert into nostra.evidence_evaluations" in cur.query.lower()
    assert cur.args[0] == "nse-1"
    assert cur.args[8] == 48
    assert cur.args[9] == "nsc-through"
