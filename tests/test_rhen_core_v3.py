from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from app.rhen_core.store import RhenCoreStore
from app.rhen_core.supervisor import ProcessSpec, _child_env


UTC = timezone.utc


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("RHEN_CORE_STORAGE_WARNING_MB", "500")
    monkeypatch.setenv("RHEN_CORE_STORAGE_SHED_MB", "750")
    return RhenCoreStore(tmp_path / "rhen-core.db")


def test_decision_cycle_is_compacted_and_candidate_rows_are_bounded(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    candidates = [
        {
            "candidate_id": f"c-{index}",
            "symbol": f"S{index}",
            "observed_at": now.isoformat(),
            "qualified": index == 0,
            "final_decision": "selected" if index == 0 else "rejected",
            "reason": "ok" if index == 0 else "weak",
            "features": {
                "momentum_pct": index / 100,
                "vwap_edge_pct": index / 1000,
                "huge_duplicate_blob": "x" * 5000,
            },
        }
        for index in range(10)
    ]
    result = store.ingest_events(
        [
            {
                "event_key": "cycle-1",
                "event_type": "decision_cycle",
                "occurred_at": now.isoformat(),
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "source": "test",
                "payload": {
                    "cycle_key": "cycle-1",
                    "market_lane": "crypto",
                    "candidate_count": 10,
                    "qualified_count": 1,
                    "rejected_count": 9,
                    "candidates": candidates,
                },
            }
        ]
    )
    assert result["inserted"] == 1
    assert result["candidate_rows"] == 4

    with store.connect() as conn:
        event = conn.execute(
            "select payload_json from events where event_key='cycle-1'"
        ).fetchone()
        rows = conn.execute(
            "select candidate_key,qualified,feature_json from candidates"
        ).fetchall()

    payload = json.loads(event[0])
    assert "candidates" not in payload
    assert len(rows) == 4
    assert any(row["candidate_key"] == "c-0" and row["qualified"] == 1 for row in rows)
    assert all("huge_duplicate_blob" not in row["feature_json"] for row in rows)


def test_position_metrics_are_bucketed_to_prevent_poll_rate_growth(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    stamp = datetime(2026, 10, 5, 14, 1, tzinfo=UTC)
    events = []
    for minute in (1, 3):
        observed = stamp.replace(minute=minute)
        events.append(
            {
                "event_key": f"raw-{minute}",
                "event_type": "position_metrics",
                "occurred_at": observed.isoformat(),
                "run_id": "run-1",
                "symbol": "BTC/USD",
                "source": "test",
                "payload": {"current_return_pct": minute / 1000},
            }
        )
    store.ingest_events(events)
    with store.connect() as conn:
        count = conn.execute(
            "select count(*) from events where event_type='position_metrics'"
        ).fetchone()[0]
    assert count == 1


def test_research_evidence_has_strategy_identity_without_postgres(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STRATEGY_VERSION_ID", "V15-R1")
    monkeypatch.setenv("STRATEGY_NAME", "btc_r2h_breakout_v15")
    monkeypatch.setenv("TRADING_RUN_ID", "run-v15")
    store = _store(tmp_path, monkeypatch)

    evidence = store.canonical_evidence()

    assert evidence["current_strategy"]["version_id"] == "V15-R1"
    assert evidence["current_strategy"]["strategy_name"] == "btc_r2h_breakout_v15"
    assert evidence["current_strategy"]["run_id"] == "run-v15"
    assert evidence["research_questions"] == []
    assert evidence["experiments"] == []


def test_nostra_uses_fresh_normalized_candidate_and_scores_forward_outcome(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    identity = "btc-candidate-1"
    store.ingest_events(
        [
            {
                "event_key": "cycle-nostra",
                "event_type": "decision_cycle",
                "occurred_at": now.isoformat(),
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "source": "test",
                "payload": {
                    "cycle_key": "cycle-nostra",
                    "market_lane": "crypto",
                    "candidates": [
                        {
                            "candidate_id": identity,
                            "symbol": "BTC/USD",
                            "market_lane": "crypto",
                            "observed_at": now.isoformat(),
                            "qualified": True,
                            "final_decision": "selected",
                            "features": {"momentum_pct": 0.01},
                        }
                    ],
                },
            }
        ]
    )
    work = store.nostra_work(now=now + timedelta(seconds=1))
    assert work["forecast_candidates"][0]["candidate_identity"] == identity
    assert work["forecast_candidates"][0]["missing_model_ids"] == ["zero_return"]

    store.ingest_events(
        [
            {
                "event_key": "nostra-forecast-1",
                "event_type": "nostra_forecast",
                "occurred_at": now.isoformat(),
                "source": "NOSTRA",
                "payload": {
                    "forecast_id": "forecast-1",
                    "symbol": "BTC/USD",
                    "model_id": "zero_return",
                    "model_version": "nostra-baselines-v1",
                    "generated_at": now.isoformat(),
                    "forecast_payload": {"expected_return": 0.0},
                    "provenance": {"candidate_identity": identity},
                },
            },
            {
                "event_key": "forward-outcome-1",
                "event_type": "candidate_forward_outcome",
                "occurred_at": (now + timedelta(minutes=10)).isoformat(),
                "symbol": "BTC/USD",
                "source": "test",
                "payload": {
                    "candidate_id": identity,
                    "status": "complete",
                    "horizon_minutes": 10,
                    "observation_end_at": (
                        now + timedelta(minutes=10)
                    ).isoformat(),
                    "forward_return": 0.01,
                },
            },
        ]
    )
    later = store.nostra_work(now=now + timedelta(minutes=10, seconds=1))
    assert later["forecast_candidates"] == []
    assert later["score_outcomes"][0]["forecast_id"] == "forecast-1"
    assert later["score_outcomes"][0]["realized_return"] == 0.01


def test_supervisor_strips_broker_credentials_from_pure_core(
    monkeypatch
):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setenv("TRADING_INGEST_TOKEN", "x" * 40)

    core = _child_env(ProcessSpec("nostra", "app.nostra.service:app", 8115))
    assert core["ALPACA_API_KEY"] == "DISABLED"
    assert core["ALPACA_API_SECRET"] == "DISABLED"
    assert core["EXECUTION_ENABLED"] == "false"
    assert core["LIVE_TRADING"] == "false"
    assert core["GRAEN_GATEWAY_TOKEN"] == "x" * 40

    replay = _child_env(
        ProcessSpec(
            "velum",
            "app.velum_service:app",
            8113,
            market_data_credentials=True,
        )
    )
    assert replay["ALPACA_API_KEY"] == "key"
    assert replay["ALPACA_API_SECRET"] == "secret"
    assert replay["EXECUTION_ENABLED"] == "false"
    assert replay["LIVE_TRADING"] == "false"
