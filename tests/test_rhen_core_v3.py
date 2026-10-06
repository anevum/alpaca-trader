from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from app.rhen_core.store import RhenCoreStore
from app.rhen_core.supervisor import PROCESSES, ProcessSpec, _child_env, _wait_tcp_ready
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
            "shadow_economics": {
                "methodology_version": "rhen-shadow-economics-v1",
                "research_only": True,
                "execution_authority": False,
                "estimate": {
                    "expected_gross_bps": str(10 + index),
                    "expected_net_bps": str(5 + index),
                    "gross_to_cost_ratio": "2.0",
                    "confidence": "0.8",
                },
                "shadow_admission": {
                    "would_admit": index < 2,
                    "reason": "economic_gate_passed" if index < 2 else "expected_net_edge_below_hurdle",
                },
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
                    "market_lane": "us_equity",
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
    assert payload["shadow_economics_candidate_count"] == 10
    assert payload["shadow_economics_admit_count"] == 2
    assert payload["shadow_economics_admit_rate_pct"] == 20.0
    assert payload["shadow_economics_mean_net_bps"] == 9.5
    assert payload["shadow_economics_best_net_bps"] == 14.0
    assert payload["shadow_economics_methodology_version"] == "rhen-shadow-economics-v1"
    assert len(rows) == 4
    assert any(row["candidate_key"] == "c-0" and row["qualified"] == 1 for row in rows)
    assert all("huge_duplicate_blob" not in row["feature_json"] for row in rows)
    selected = next(row for row in rows if row["candidate_key"] == "c-0")
    selected_features = json.loads(selected["feature_json"])
    shadow = selected_features["shadow_economics"]
    assert shadow["research_only"] is True
    assert shadow["execution_authority"] is False
    assert shadow["expected_net_bps"] == "5"
    assert shadow["would_admit"] is True


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
    store.set_kv(
        "iren",
        "state",
        {
            "state": "ATTENTION_REQUIRED",
            "observed_at": observed.isoformat(),
            "incidents": {
                "configuration.drift": {"status": "OPEN", "severity": "critical"}
            },
        },
    )

    feed = store.public_live_feed(now=observed)
    encoded = json.dumps(feed)

    assert feed["ok"] is True
    assert feed["live"] is True
    assert feed["source"] == "rhen-core-sqlite"
    assert feed["telemetry"]["scan_events_10m"] == 1
    assert feed["systems"]["RHEN"]["runtime_state"] == "OBSERVING"
    assert feed["systems"]["IREN"]["runtime_state"] == "READY"
    assert feed["systems"]["IREN"]["health_state"] == "ATTENTION_REQUIRED"
    assert feed["operational"]["latest_scan"]["market_session"] == "regular"
    assert feed["performance"]["account_return_pct"] == 0.0
    assert "SECRET" not in encoded
    assert "123.45" not in encoded
    assert "77.00" not in encoded
    assert "154.00" not in encoded
    assert "symbols" in feed["disclosure"]["excluded_fields"]
    assert "dollar_values" in feed["disclosure"]["excluded_fields"]


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


def test_router_sends_foundation_compatibility_paths_to_core():
    from app.rhen_core.router import CORE_PREFIXES

    assert "/v1/trading-report-read" in CORE_PREFIXES
    assert "/v1/trading-reconcile" in CORE_PREFIXES
    assert "/v1/strategy-pipeline" in CORE_PREFIXES


def test_router_exposes_sanitized_public_research_projections():
    from app.rhen_core.router import PUBLIC_MODULE_ROUTES

    assert PUBLIC_MODULE_ROUTES == {
        "/v1/research/readiness/public": (
            "http://127.0.0.1:8114/v1/readiness/public"
        ),
        "/v1/research/theory/public": (
            "http://127.0.0.1:8114/v1/theory/public"
        ),
    }


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


def test_storage_state_exposes_fragmentation_metrics(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    state = store.storage_state()

    assert state["allocated_db_bytes"] >= 0
    assert state["reclaimable_db_bytes"] >= 0
    assert state["fragmentation_pct"] >= 0
    assert state["auto_vacuum_mode"] in {0, 1, 2}
    assert state["maintenance_error"] is None


def test_prune_bounds_completed_scheduler_history(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 10, 6, 17, 0, tzinfo=UTC)
    old = (now - timedelta(days=31)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()

    with store.connect() as conn:
        conn.execute(
            """insert into scheduler_runs(
                job_key,status,payload_json,scheduled_at,started_at,
                completed_at,updated_at
            ) values(?,?,?,?,?,?,?)""",
            ("old-job", "SUCCEEDED", "{}", old, old, old, old),
        )
        conn.execute(
            """insert into scheduler_runs(
                job_key,status,payload_json,scheduled_at,started_at,
                completed_at,updated_at
            ) values(?,?,?,?,?,?,?)""",
            ("recent-job", "SUCCEEDED", "{}", recent, recent, recent, recent),
        )
        conn.execute(
            """insert into scheduler_runs(
                job_key,status,payload_json,scheduled_at,started_at,
                completed_at,updated_at
            ) values(?,?,?,?,?,?,?)""",
            ("running-job", "RUNNING", "{}", old, old, None, old),
        )
        conn.commit()

    deleted = store.prune(now=now)

    assert deleted["scheduler_runs"] == 1
    with store.connect() as conn:
        remaining = {
            row[0]
            for row in conn.execute(
                "select job_key from scheduler_runs order by job_key"
            ).fetchall()
        }
    assert remaining == {"recent-job", "running-job"}


def test_prune_removes_nonfinal_forward_evidence(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    with store.connect() as conn:
        for key, status in [
            ("complete-forward", "complete"),
            ("pending-forward", "insufficient_future_data"),
            ("error-forward", "error"),
        ]:
            conn.execute(
                """insert into events(
                    event_key,event_type,occurred_at,payload_json,critical,created_at
                ) values(?,?,?,?,0,?)""",
                (
                    key,
                    "candidate_forward_outcome",
                    now.isoformat(),
                    json.dumps({"status": status}, separators=(",", ":"), sort_keys=True),
                    now.isoformat(),
                ),
            )
        conn.commit()

    deleted = store.prune(now=now)

    assert deleted["incomplete_forward_outcomes"] == 2
    with store.connect() as conn:
        remaining = {
            row[0]
            for row in conn.execute(
                "select event_key from events order by event_key"
            ).fetchall()
        }
    assert "complete-forward" in remaining
    assert "pending-forward" not in remaining
    assert "error-forward" not in remaining



def test_retired_asset_research_is_historical_only(tmp_path, monkeypatch):
    import pytest

    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC).isoformat()

    with pytest.raises(ValueError, match="retired_asset_class_domain"):
        store._graen_create_problem(
            {
                "title": "retired",
                "statement": "historical only",
                "domain": "CRYPTO_STRATEGY_RESEARCH",
            }
        )

    # Preserve a representative legacy row as durable history.
    with store.connect() as conn:
        conn.execute(
            """insert into graen_problems(
                problem_id,problem_key,status,priority,domain,body_json,
                metadata_json,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?)""",
            (
                "legacy-retired",
                "legacy-retired",
                "WAITING",
                100,
                "CRYPTO_STRATEGY_RESEARCH",
                json.dumps({"title": "Legacy retired research"}),
                "{}",
                now,
                now,
            ),
        )
        conn.commit()

    current = store._graen_create_problem(
        {
            "title": "Current equity research",
            "statement": "Evaluate a liquid equity mechanism.",
            "domain": "EQUITY_STRATEGY_RESEARCH",
            "priority": 50,
        }
    )
    claimed = store._graen_claim({"worker_id": "test"}, research=False)

    assert claimed["problem"]["problem_id"] == current["problem"]["problem_id"]
    tracking = store.command_research_tracking()
    assert all(
        "CRYPTO" not in str(row.get("domain") or "").upper()
        and "BTC" not in str(row.get("domain") or "").upper()
        for row in tracking["graen_problems"]
    )
    pipeline = store.strategy_pipeline_research()
    assert pipeline["candidate"]["problem_id"] == current["problem"]["problem_id"]



def test_nostra_work_preserves_equity_candidate_lane(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    store.ingest_events(
        [
            {
                "event_key": "nostra-equity-cycle",
                "event_type": "decision_cycle",
                "occurred_at": now.isoformat(),
                "run_id": "run-equity",
                "strategy_version_id": "LIVE-TEST",
                "source": "test",
                "payload": {
                    "cycle_key": "nostra-equity-cycle",
                    "market_lane": "us_equity",
                    "candidate_count": 1,
                    "qualified_count": 1,
                    "rejected_count": 0,
                    "candidates": [
                        {
                            "candidate_id": "nostra-equity:AAPL",
                            "symbol": "AAPL",
                            "observed_at": now.isoformat(),
                            "qualified": True,
                            "final_decision": "selected",
                            "market_lane": "us_equity",
                            "strategy_version_id": "LIVE-TEST",
                            "features": {"momentum_pct": 0.001},
                        }
                    ],
                },
            }
        ]
    )

    work = store.nostra_work(now + timedelta(minutes=1))

    assert work["forecast_candidates"][0]["market_lane"] == "us_equity"


def test_supervisor_readiness_wait_uses_local_listener(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def connect(address, timeout):
        calls.append((address, timeout))
        return Connection()

    monkeypatch.setattr(
        "app.rhen_core.supervisor.socket.create_connection",
        connect,
    )

    _wait_tcp_ready(
        ProcessSpec("core-test", "example:app", 8123),
        timeout_seconds=0.5,
    )

    assert calls == [(("127.0.0.1", 8123), 0.25)]



def test_command_research_tracking_exposes_rhen_shadow_economics_run(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    events = []
    for index, (mean_net, best_net, admit_rate) in enumerate(
        [(3.0, 8.0, 25.0), (4.5, 11.0, 50.0)]
    ):
        stamp = now + timedelta(minutes=index)
        events.append(
            {
                "event_key": f"shadow-cycle-{index}",
                "event_type": "decision_cycle",
                "occurred_at": stamp.isoformat(),
                "run_id": "run-shadow",
                "strategy_version_id": "LIVE-TEST",
                "source": "test",
                "payload": {
                    "cycle_key": f"shadow-cycle-{index}",
                    "market_lane": "us_equity",
                    "candidate_count": 4,
                    "qualified_count": 1,
                    "rejected_count": 3,
                    "candidates": [
                        {
                            "candidate_id": f"shadow-{index}-{candidate_index}",
                            "symbol": f"S{candidate_index}",
                            "observed_at": stamp.isoformat(),
                            "qualified": candidate_index == 0,
                            "final_decision": (
                                "selected" if candidate_index == 0 else "rejected"
                            ),
                            "market_lane": "us_equity",
                            "strategy_version_id": "LIVE-TEST",
                            "features": {},
                            "shadow_economics": {
                                "methodology_version": "rhen-shadow-economics-v1",
                                "research_only": True,
                                "execution_authority": False,
                                "estimate": {
                                    "expected_gross_bps": str(best_net + 4),
                                    "expected_net_bps": str(
                                        best_net
                                        if candidate_index == 0
                                        else (
                                            (mean_net * 4 - best_net) / 3
                                        )
                                    ),
                                    "gross_to_cost_ratio": "2.0",
                                    "confidence": "0.8",
                                },
                                "shadow_admission": {
                                    "would_admit": (
                                        candidate_index
                                        < int(admit_rate / 25)
                                    ),
                                    "reason": (
                                        "economic_gate_passed"
                                        if candidate_index
                                        < int(admit_rate / 25)
                                        else "expected_net_edge_below_hurdle"
                                    ),
                                },
                            },
                        }
                        for candidate_index in range(4)
                    ],
                },
            }
        )
    store.ingest_events(events)

    tracking = store.command_research_tracking()
    run = next(
        row
        for row in tracking["observability"]["runs"]
        if row["run_id"] == "rhen-shadow-economics"
    )

    assert run["system"] == "RHEN"
    assert run["kind"] == "SHADOW_ECONOMICS"
    assert run["status"] == "OBSERVING"
    assert run["methodology_version"] == "rhen-shadow-economics-v1"
    assert run["metrics"]["mean_expected_net_bps"] == 4.5
    assert run["metrics"]["best_expected_net_bps"] == 11.0
    assert run["metrics"]["shadow_admission_rate_pct"] == 50.0
    assert run["detail"]["execution_authority"] is False
    assert len(run["series"]) == 3
    assert all(len(series["points"]) == 2 for series in run["series"])



def test_command_research_tracking_exposes_shadow_allocation_run(
    tmp_path, monkeypatch
):
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    events = []
    fixtures = [
        ("13.00", "10", "0.00005", True),
        ("0", "-2", "0", False),
    ]
    for index, (shadow_notional, net_bps, velocity, would_allocate) in enumerate(
        fixtures
    ):
        stamp = now + timedelta(minutes=index)
        events.append(
            {
                "event_key": f"shadow-allocation-{index}",
                "event_type": "order_intent",
                "occurred_at": stamp.isoformat(),
                "run_id": "run-shadow-allocation",
                "strategy_version_id": "LIVE-TEST",
                "source": "test",
                "payload": {
                    "intent": {
                        "side": "buy",
                        "payload": {
                            "candidate_snapshot": {
                                "shadow_allocation": {
                                    "methodology_version": "rhen-shadow-allocation-v1",
                                    "research_only": True,
                                    "execution_authority": False,
                                    "changes_live_decision": False,
                                    "bounded_by_live_safe_notional": True,
                                    "live_safe_notional": "20.00",
                                    "shadow_notional": shadow_notional,
                                    "expected_net_bps": net_bps,
                                    "capital_velocity_per_minute": velocity,
                                    "would_allocate": would_allocate,
                                    "reason": (
                                        "shadow_allocation_candidate"
                                        if would_allocate
                                        else "expected_net_edge_nonpositive"
                                    ),
                                }
                            }
                        },
                    }
                },
            }
        )
    store.ingest_events(events)

    tracking = store.command_research_tracking()
    run = next(
        row
        for row in tracking["observability"]["runs"]
        if row["run_id"] == "rhen-shadow-allocation"
    )

    assert run["system"] == "RHEN"
    assert run["kind"] == "SHADOW_ALLOCATION"
    assert run["status"] == "OBSERVING"
    assert run["methodology_version"] == "rhen-shadow-allocation-v1"
    assert run["metrics"]["selected_entry_count"] == 2.0
    assert run["metrics"]["would_allocate_count"] == 1.0
    assert run["metrics"]["shadow_allocation_rate_pct"] == 50.0
    assert run["metrics"]["mean_shadow_to_live_pct"] == 32.5
    assert run["metrics"]["mean_expected_net_bps"] == 4.0
    assert run["detail"]["research_only"] is True
    assert run["detail"]["execution_authority"] is False
    assert run["detail"]["bounded_by_live_safe_notional"] is True
    assert len(run["series"]) == 3
    assert all(len(series["points"]) == 2 for series in run["series"])



def test_compact_storage_prefers_incremental_vacuum_for_live_database():
    import threading

    store = RhenCoreStore.__new__(RhenCoreStore)
    store._lock = threading.RLock()
    commands = []

    before = {
        "bytes": 785_000_000,
        "allocated_db_bytes": 780_000_000,
        "reclaimable_db_bytes": 24 * 1024 * 1024,
        "auto_vacuum_mode": 2,
    }
    after = {
        **before,
        "bytes": 760_000_000,
        "reclaimable_db_bytes": 2 * 1024 * 1024,
    }
    states = [before, after]
    store.storage_state = lambda: states.pop(0)

    class Result:
        def fetchone(self):
            return (4096,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            commands.append(sql)
            return Result()

    store.connect = lambda: Connection()

    result = store.compact_storage()

    assert result["reason"] == "incremental_vacuum_completed"
    assert result["compacted"] is True
    assert any("incremental_vacuum" in sql for sql in commands)
    assert not any(sql.strip().lower() == "vacuum" for sql in commands)


def test_compact_storage_keeps_small_incremental_fragmentation_bounded():
    import threading

    store = RhenCoreStore.__new__(RhenCoreStore)
    store._lock = threading.RLock()
    commands = []
    state = {
        "bytes": 600_000_000,
        "allocated_db_bytes": 595_000_000,
        "reclaimable_db_bytes": 4 * 1024 * 1024,
        "auto_vacuum_mode": 2,
    }
    store.storage_state = lambda: state

    class Result:
        def fetchone(self):
            return (4096,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            commands.append(sql)
            return Result()

    store.connect = lambda: Connection()

    result = store.compact_storage()

    assert result["compacted"] is False
    assert result["reason"] == "fragmentation_below_threshold"
    assert not any("incremental_vacuum" in sql for sql in commands)
    assert not any(sql.strip().lower() == "vacuum" for sql in commands)
