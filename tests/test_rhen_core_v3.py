from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from app.rhen_core.store import RhenCoreStore
from app.rhen_core.supervisor import ProcessSpec, _child_env
from app.rhen_core.router import enabled_modules


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
    monkeypatch.setenv("ADMIN_TOKEN", "execution-admin-secret")
    monkeypatch.setenv("FOUNDATION_SHADOW_ENABLED", "true")

    core = _child_env(ProcessSpec("nostra", "app.nostra.service:app", 8115))
    assert core["ALPACA_API_KEY"] == ""
    assert core["ALPACA_API_SECRET"] == ""
    assert core["ADMIN_TOKEN"] == ""
    assert core["EXECUTION_ENABLED"] == "false"
    assert core["LIVE_TRADING"] == "false"
    assert core["GRAEN_GATEWAY_TOKEN"] == "x" * 40
    assert core["FOUNDATION_SHADOW_ENABLED"] == "false"

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
    assert replay["FOUNDATION_SHADOW_ENABLED"] == "false"

    execution = _child_env(
        ProcessSpec(
            "execution",
            "app.main:app",
            8101,
            critical=True,
            market_data_credentials=True,
        )
    )
    assert execution["FOUNDATION_SHADOW_ENABLED"] == "false"



def test_shadow_report_restores_latest_v15_state(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    activation = {
        "activation_id": "v15-a",
        "candidate_id": "V15-R1-BTC-R2H-BREAKOUT-42-15",
        "candidate_methodology": "graen-btc-r2h-breakout-v15",
    }
    state = {
        **activation,
        "last_processed_bar_end": now.isoformat(),
        "v15_shadow_position": 1.0,
    }
    store.ingest_events(
        [
            {
                "event_key": "shadow-activate",
                "event_type": "graen_candidate_shadow_activation",
                "occurred_at": (now - timedelta(seconds=2)).isoformat(),
                "source": "test",
                "payload": activation,
            },
            {
                "event_key": "shadow-state",
                "event_type": "graen_candidate_shadow_state",
                "occurred_at": now.isoformat(),
                "source": "test",
                "payload": state,
            },
        ]
    )

    report = store.report_read(
        {
            "latest": "graen_shadow",
            "shadow_candidate_id": activation["candidate_id"],
        }
    )

    assert report["ok"] is True
    assert report["activation"]["payload"]["activation_id"] == "v15-a"
    assert report["state"]["payload"]["v15_shadow_position"] == 1.0


def test_crypto_evidence_report_uses_compact_candidates_and_outcomes(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    store.ingest_events(
        [
            {
                "event_key": "cycle-crypto-report",
                "event_type": "decision_cycle",
                "occurred_at": observed.isoformat(),
                "run_id": "run-crypto",
                "strategy_version_id": "CRYPTO-V15",
                "source": "test",
                "payload": {
                    "cycle_key": "cycle-crypto-report",
                    "market_lane": "crypto",
                    "candidates": [
                        {
                            "candidate_id": "candidate-1",
                            "symbol": "BTC/USD",
                            "market_lane": "crypto",
                            "observed_at": observed.isoformat(),
                            "qualified": True,
                            "final_decision": "selected",
                            "features": {"momentum_pct": 0.02},
                        }
                    ],
                },
            },
            {
                "event_key": "outcome-crypto-report",
                "event_type": "candidate_forward_outcome",
                "occurred_at": (
                    observed + timedelta(minutes=10)
                ).isoformat(),
                "run_id": "run-crypto",
                "strategy_version_id": "CRYPTO-V15",
                "symbol": "BTC/USD",
                "source": "test",
                "payload": {
                    "candidate_id": "candidate-1",
                    "market_lane": "crypto",
                    "status": "complete",
                    "horizon_minutes": 10,
                    "forward_return": 0.01,
                },
            },
        ]
    )

    report = store.report_read(
        {"crypto_evidence_session": "2026-10-05"}
    )

    assert report["ok"] is True
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["candidate_id"] == "candidate-1"
    assert candidate["forward_outcomes"]["10"]["status"] == "complete"


def test_public_live_feed_uses_core_activity_without_private_trade_fields(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime.now(UTC)
    store.ingest_events(
        [
            {
                "event_key": "runtime-public-feed",
                "event_type": "runtime_start",
                "occurred_at": (observed - timedelta(seconds=20)).isoformat(),
                "run_id": "run-public",
                "strategy_version_id": "v-public",
                "source": "test",
                "payload": {
                    "strategy_name": "rolling_momentum_vwap",
                    "trading_mode": "live",
                },
            },
            {
                "event_key": "scan-public-feed",
                "event_type": "decision_cycle",
                "occurred_at": (observed - timedelta(seconds=5)).isoformat(),
                "run_id": "run-public",
                "strategy_version_id": "v-public",
                "symbol": "SECRET",
                "source": "test",
                "payload": {
                    "cycle_key": "cycle-public",
                    "market_session": "regular",
                    "cycle_outcome": "hold",
                    "data_status": "ready",
                    "candidates": [],
                },
            },
            {
                "event_key": "account-public-feed",
                "event_type": "account_snapshot",
                "occurred_at": observed.isoformat(),
                "run_id": "run-public",
                "strategy_version_id": "v-public",
                "source": "test",
                "payload": {
                    "equity": "123.45",
                    "cash": "77.00",
                    "buying_power": "154.00",
                },
            },
        ]
    )

    feed = store.public_live_feed(now=observed)
    encoded = json.dumps(feed)

    assert feed["ok"] is True
    assert feed["live"] is True
    assert feed["source"] == "rhen-core-sqlite"
    assert feed["telemetry"]["scan_events_10m"] == 1
    assert feed["systems"]["RHEN"]["runtime_state"] == "OBSERVING"
    assert feed["operational"]["latest_scan"]["market_session"] == "regular"
    assert feed["performance"]["account_return_pct"] == 0.0
    assert "SECRET" not in encoded
    assert "123.45" not in encoded
    assert "77.00" not in encoded
    assert "154.00" not in encoded
    assert "symbols" in feed["disclosure"]["excluded_fields"]
    assert "dollar_values" in feed["disclosure"]["excluded_fields"]


def test_reconciliation_is_fail_closed_for_unknown_broker_state(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    result = store.reconcile(
        {
            "action": "reconcile",
            "reconcile": {
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "observed_at": observed.isoformat(),
                "managed_symbols": ["BTC/USD"],
                "broker_positions": [
                    {
                        "symbol": "BTC/USD",
                        "qty": "0.001",
                        "market_value": "100",
                    }
                ],
                "open_orders": [
                    {
                        "id": "order-unknown",
                        "client_order_id": "anevum-unknown",
                        "symbol": "BTC/USD",
                        "side": "buy",
                        "status": "new",
                    }
                ],
            },
        }
    )

    evidence = result["result"]
    assert evidence["safe_to_enter"] is False
    assert "unknown_open_orders:1" in evidence["reason"]
    assert "untracked_positions:1" in evidence["reason"]


def test_reconciliation_allows_known_position_with_standing_hardstop(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    client_id = "anevum-tqqq-buy-rhen-abc123"
    store.ingest_events(
        [
            {
                "event_key": "order-known-buy",
                "event_type": "broker_order",
                "occurred_at": (
                    observed - timedelta(minutes=1)
                ).isoformat(),
                "run_id": "run-1",
                "symbol": "TQQQ",
                "source": "test",
                "payload": {
                    "order": {
                        "client_order_id": client_id,
                        "symbol": "TQQQ",
                        "side": "buy",
                        "filled_qty": "0.5",
                    }
                },
            },
        ]
    )

    result = store.reconcile(
        {
            "action": "reconcile",
            "reconcile": {
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "observed_at": observed.isoformat(),
                "managed_symbols": ["TQQQ"],
                "broker_positions": [
                    {
                        "symbol": "TQQQ",
                        "qty": "0.5",
                        "market_value": "45",
                    }
                ],
                "open_orders": [
                    {
                        "id": "hardstop-1",
                        "client_order_id": "anevum-tqqq-hardstop-rhen-def456",
                        "symbol": "TQQQ",
                        "side": "sell",
                        "status": "new",
                    }
                ],
            },
        }
    )

    evidence = result["result"]
    assert evidence["safe_to_enter"] is True
    assert evidence["reason"] == "reconciled"
    assert evidence["unknown_open_orders"] == []
    assert evidence["untracked_positions"] == []


def test_reconciliation_rejects_hardstop_without_known_filled_buy(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)

    result = store.reconcile(
        {
            "action": "reconcile",
            "reconcile": {
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "observed_at": observed.isoformat(),
                "managed_symbols": ["TQQQ"],
                "broker_positions": [
                    {
                        "symbol": "TQQQ",
                        "qty": "0.5",
                        "market_value": "45",
                    }
                ],
                "open_orders": [
                    {
                        "id": "hardstop-unknown",
                        "client_order_id": "anevum-tqqq-hardstop-rhen-def456",
                        "symbol": "TQQQ",
                        "side": "sell",
                        "status": "new",
                    }
                ],
            },
        }
    )

    evidence = result["result"]
    assert evidence["safe_to_enter"] is False
    assert "unknown_open_orders:1" in evidence["reason"]
    assert "untracked_positions:1" in evidence["reason"]


def test_reconciliation_resolves_known_intent_and_position(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    client_id = "anevum-known"
    store.ingest_events(
        [
            {
                "event_key": "intent-known",
                "event_type": "order_intent",
                "occurred_at": (
                    observed - timedelta(minutes=2)
                ).isoformat(),
                "run_id": "run-1",
                "symbol": "BTC/USD",
                "source": "test",
                "payload": {
                    "intent": {
                        "intent_id": "intent-1",
                        "idempotency_key": client_id,
                        "symbol": "BTC/USD",
                        "side": "buy",
                        "intended_at": (
                            observed - timedelta(minutes=2)
                        ).isoformat(),
                    }
                },
            },
            {
                "event_key": "order-known",
                "event_type": "broker_order",
                "occurred_at": (
                    observed - timedelta(minutes=1)
                ).isoformat(),
                "run_id": "run-1",
                "symbol": "BTC/USD",
                "source": "test",
                "payload": {
                    "order": {
                        "client_order_id": client_id,
                        "symbol": "BTC/USD",
                        "side": "buy",
                        "filled_qty": "0.001",
                    }
                },
            },
        ]
    )

    result = store.reconcile(
        {
            "action": "reconcile",
            "reconcile": {
                "run_id": "run-1",
                "strategy_version_id": "v1",
                "observed_at": observed.isoformat(),
                "managed_symbols": ["BTC/USD"],
                "broker_positions": [
                    {
                        "symbol": "BTC/USD",
                        "qty": "0.001",
                        "market_value": "100",
                    }
                ],
                "open_orders": [],
            },
        }
    )

    assert result["result"]["safe_to_enter"] is True
    assert result["result"]["reason"] == "reconciled"


def test_router_sends_foundation_compatibility_paths_to_core():
    from app.rhen_core.router import CORE_PREFIXES

    assert "/v1/trading-report-read" in CORE_PREFIXES
    assert "/v1/trading-reconcile" in CORE_PREFIXES



def test_weekly_range_read_returns_canonical_input_shape(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    observed = datetime(2026, 10, 2, 20, 10, tzinfo=UTC)
    store.ingest_events(
        [
            {
                "event_key": "runtime-weekly",
                "event_type": "runtime_start",
                "occurred_at": observed.isoformat(),
                "run_id": "run-weekly",
                "strategy_version_id": "v-weekly",
                "source": "test",
                "payload": {"strategy_name": "weekly-test"},
            },
            {
                "event_key": "daily-weekly",
                "event_type": "research_daily_report",
                "occurred_at": observed.isoformat(),
                "run_id": "run-weekly",
                "strategy_version_id": "v-weekly",
                "source": "test",
                "payload": {
                    "session": "2026-10-02",
                    "report_version": "rhen-daily-v1.4",
                    "generated_at": observed.isoformat(),
                    "metrics": {
                        "realized_pnl": "0",
                        "trade_count": 0,
                    },
                    "trades": [],
                },
            },
            {
                "event_key": "account-weekly",
                "event_type": "account_snapshot",
                "occurred_at": observed.isoformat(),
                "run_id": "run-weekly",
                "strategy_version_id": "v-weekly",
                "source": "test",
                "payload": {
                    "equity": "100",
                    "drawdown_pct": "0.01",
                },
            },
            {
                "event_key": "intent-weekly",
                "event_type": "order_intent",
                "occurred_at": observed.isoformat(),
                "run_id": "run-weekly",
                "strategy_version_id": "v-weekly",
                "source": "test",
                "payload": {
                    "intent": {
                        "side": "buy",
                        "symbol": "SPY",
                    }
                },
            },
        ]
    )

    report = store.report_read(
        {"start": "2026-09-28", "end": "2026-10-02"}
    )

    assert report["ok"] is True
    assert report["report_version"] == "rhen-weekly-v1.2"
    inputs = report["inputs"]
    assert inputs["earliest_daily_session"] == "2026-10-02"
    assert inputs["strategy_versions"] == [{"version_id": "v-weekly"}]
    assert inputs["runs"] == [{"run_id": "run-weekly"}]
    assert inputs["order_intents_by_session"][0]["entry_intents"] == 1
    assert inputs["account_weekly_drawdown"]["max_drawdown_pct"] == 0.01
    assert inputs["daily_reports"][0]["payload"]["session"] == "2026-10-02"


def test_weekly_range_read_rejects_invalid_period(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)

    report = store.report_read(
        {"start": "2026-10-05", "end": "2026-10-02"}
    )

    assert report == {"ok": False, "error": "invalid_period"}


def test_iren_work_queue_is_durable_in_rhen_core(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)

    created = store.iren_work_action(
        "iren_command_create",
        {
            "command_text": "status",
            "source": "command",
            "requested_by": "operator@example.com",
        },
    )
    command_id = created["command"]["command_id"]
    assert created["command"]["status"] == "QUEUED"

    claimed = store.iren_work_action(
        "iren_commands_claim",
        {"owner": "iren-work-engine", "limit": 5},
    )
    assert [row["command_id"] for row in claimed["commands"]] == [command_id]
    assert claimed["commands"][0]["status"] == "PROCESSING"

    completed = store.iren_work_action(
        "iren_command_complete",
        {
            "command_id": command_id,
            "status": "SUCCEEDED",
            "response": {"message": "RHEN native IREN is healthy."},
        },
    )
    assert completed["command"]["status"] == "SUCCEEDED"

    job = store.iren_work_action(
        "iren_job_create",
        {
            "job": {
                "title": "Verify runtime",
                "job_type": "CONTROL_VERIFY",
                "owner_system": "IREN",
                "status": "QUEUED",
            }
        },
    )["job"]
    claimed_jobs = store.iren_work_action(
        "iren_jobs_claim",
        {"owner": "iren-work-engine", "limit": 3},
    )
    assert claimed_jobs["jobs"][0]["job_id"] == job["job_id"]
    assert claimed_jobs["jobs"][0]["status"] == "RUNNING"

    updated = store.iren_work_action(
        "iren_job_update",
        {
            "job_id": job["job_id"],
            "status": "SUCCEEDED",
            "result": {"verified": True},
        },
    )
    assert updated["job"]["status"] == "SUCCEEDED"

    restored = RhenCoreStore(store.path).iren_work_snapshot()
    assert restored["commands"][0]["command_id"] == command_id
    assert restored["commands"][0]["status"] == "SUCCEEDED"
    assert restored["jobs"][0]["job_id"] == job["job_id"]
    assert restored["jobs"][0]["status"] == "SUCCEEDED"
    assert restored["job_events"]


def test_iren_work_queue_rejects_blank_command(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    try:
        store.iren_work_action(
            "iren_command_create",
            {"command_text": "   "},
        )
    except ValueError as exc:
        assert str(exc) == "command_required"
    else:
        raise AssertionError("blank IREN commands must be rejected")


def test_router_excludes_disabled_optional_modules(monkeypatch):
    monkeypatch.delenv("IREN_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("PREOPEN_STATE_ENABLED", raising=False)
    active = enabled_modules()
    assert "iren_executor" not in active
    assert "preopen" not in active
    assert {"iren", "graen_research", "crypto_research"} <= set(active)

    monkeypatch.setenv("PREOPEN_STATE_ENABLED", "true")
    assert "preopen" in enabled_modules()


def test_strategy_pipeline_research_links_candidate_validation_and_release_gate(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    created = store.graen_action(
        "create_problem",
        {
            "title": "BTC replacement candidate",
            "domain": "CRYPTO_STRATEGY",
            "priority": 90,
        },
    )
    problem_id = created["problem"]["problem_id"]
    claimed = store.graen_action(
        "claim_research_problem",
        {
            "worker_id": "graen-test",
            "domain": "CRYPTO_STRATEGY",
            "methodology_version": "test-method-v1",
        },
    )
    run_id = claimed["run"]["run_id"]
    store.graen_action(
        "queue_research_stage",
        {
            "problem_id": problem_id,
            "stage": "VELUM_REPLAY",
            "metadata": {
                "candidate_id": "BTC-CANDIDATE-2",
                "target_lane": "crypto",
                "supersedes_strategy_version_id": "RHEN-BTC-DIRECT-001",
                "release_requested": True,
            },
        },
    )
    store.graen_action(
        "complete_research_problem",
        {
            "problem_id": problem_id,
            "run_id": run_id,
            "status": "SUCCEEDED",
            "result_summary": {"candidate_id": "BTC-CANDIDATE-2"},
        },
    )
    observed = datetime.now(UTC)
    store.ingest_events(
        [
            {
                "event_key": "velum-pipeline-pass",
                "event_type": "velum_graen_candidate_replay",
                "occurred_at": observed.isoformat(),
                "strategy_version_id": "BTC-CANDIDATE-2",
                "source": "test",
                "payload": {
                    "problem_id": problem_id,
                    "candidate_id": "BTC-CANDIDATE-2",
                    "engineering_gate": {"passed": True},
                },
            }
        ]
    )

    pipeline = store.strategy_pipeline_research()

    assert pipeline["schema_version"] == "strategy_pipeline_research.v1"
    assert pipeline["candidate"]["problem_id"] == problem_id
    assert pipeline["candidate"]["candidate_id"] == "BTC-CANDIDATE-2"
    assert pipeline["candidate"]["lane"] == "crypto"
    assert pipeline["candidate"]["supersedes_strategy_version_id"] == "RHEN-BTC-DIRECT-001"
    assert pipeline["validation"]["status"] == "PASSED"
    assert pipeline["validation"]["problem_id"] == problem_id
    assert pipeline["release_gate"]["status"] == "REVIEW"
    assert pipeline["release_gate"]["target_strategy_version_id"] == "RHEN-BTC-DIRECT-001"
    assert pipeline["release_gate"]["automatic_promotion"] is False
    assert pipeline["release_gate"]["production_authority_changed"] is False
    assert pipeline["research"]["graen_problems"][0]["problem_id"] == problem_id
    assert pipeline["research"]["graen_runs"][0]["run_id"] == run_id
    assert pipeline["research"]["velum_replays"][0]["status"] == "PASSED"


def test_strategy_pipeline_does_not_infer_supersession_target(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    created = store.graen_action(
        "create_problem",
        {
            "title": "Unbound crypto research",
            "domain": "CRYPTO_STRATEGY",
            "priority": 90,
        },
    )
    problem_id = created["problem"]["problem_id"]
    claimed = store.graen_action(
        "claim_research_problem",
        {
            "worker_id": "graen-test",
            "domain": "CRYPTO_STRATEGY",
            "methodology_version": "test-method-v1",
        },
    )
    store.graen_action(
        "queue_research_stage",
        {
            "problem_id": problem_id,
            "stage": "VELUM_REPLAY",
            "metadata": {"candidate_id": "UNBOUND-CANDIDATE"},
        },
    )
    observed = datetime.now(UTC)
    store.ingest_events(
        [
            {
                "event_key": "velum-unbound-pass",
                "event_type": "velum_graen_candidate_replay",
                "occurred_at": observed.isoformat(),
                "strategy_version_id": "UNBOUND-CANDIDATE",
                "source": "test",
                "payload": {
                    "problem_id": problem_id,
                    "candidate_id": "UNBOUND-CANDIDATE",
                    "engineering_gate": {"passed": True},
                },
            }
        ]
    )

    pipeline = store.strategy_pipeline_research()

    assert pipeline["candidate"]["candidate_id"] == "UNBOUND-CANDIDATE"
    assert pipeline["candidate"]["supersedes_strategy_version_id"] is None
    assert pipeline["release_gate"]["status"] == "HOLD"
    assert pipeline["release_gate"]["target_strategy_version_id"] is None
    assert pipeline["release_gate"]["target_lane"] is None
    assert "no explicit production supersession target" in pipeline["release_gate"]["reason"].lower()
