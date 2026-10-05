from datetime import datetime, timezone

from foundation.command_iren import _btc_canary_activity, project_command
from foundation.cloudflare_access import normalize_team_domain


def test_cloudflare_access_team_domain_requires_https():
    assert normalize_team_domain("https://anevum.cloudflareaccess.com/") == (
        "https://anevum.cloudflareaccess.com"
    )


def test_command_projection_matches_fail_closed_stale_contract():
    snapshot = {
        "control": {
            "revision": 7,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-01T19:00:00+00:00",
                "topology": {
                    "services": [
                        {
                            "name": "RHEN",
                            "independent_runtime": True,
                            "status": "RUNNING",
                            "readiness": True,
                            "liveness": True,
                        }
                    ],
                    "dependencies": {"railway": {"status": "HEALTHY"}},
                },
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {
            "objectives": [
                {"objective_key": "foundation-v2", "status": "ACTIVE"}
            ],
            "jobs": [
                {"job_id": "1", "status": "RUNNING", "requires_human": False}
            ],
            "commands": [],
        },
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert projected["schema_version"] == "iren_command.v2"
    assert projected["stale"] is True
    assert projected["state"] == "STALE"
    assert projected["topology"]["services"][0]["status"] == "STALE"
    assert projected["topology"]["dependencies"]["railway"]["status"] == "STALE"
    assert projected["work"]["active_jobs"] == 1


def test_operator_projection_is_healthy_and_read_only():
    snapshot = {
        "control": {
            "revision": 9,
            "events": [{
                "event": {
                    "key": "service.GRAEN",
                    "transition": "RECOVERED",
                    "severity": "warning",
                    "reason": "healthy_observations_confirmed",
                },
                "created_at": "2026-10-02T20:59:00+00:00",
                "delivery_status": "delivered:test",
            }],
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-02T21:00:00+00:00",
                "topology": {
                    "inventory_complete": True,
                    "inventory_verified_at": "2026-10-02T21:00:00+00:00",
                    "inventory_gaps": {},
                    "services": [{
                        "service_id": "RHEN",
                        "independent_runtime": True,
                        "status": "RUNNING",
                        "readiness": True,
                    }],
                    "dependencies": {},
                },
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {
            "objectives": [{"objective_key": "stable", "status": "COMPLETE"}],
            "jobs": [],
            "commands": [],
        },
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 2, 21, 1, 0, tzinfo=timezone.utc),
    )
    operator = projected["operator"]
    assert operator["version"] == "anevum_operator.v1"
    assert operator["state"] == "HEALTHY"
    assert operator["inventory"]["ready"] == 1
    assert operator["guidance"] == []
    assert operator["authority"]["read_only_projection"] is True
    assert operator["authority"]["trading_mutations"] is False
    assert operator["recent_transitions"][0]["transition"] == "RECOVERED"


def test_operator_projection_routes_evidence_incident_to_foundation():
    snapshot = {
        "control": {
            "revision": 10,
            "state": {
                "state": "DEGRADED",
                "observed_at": "2026-10-02T21:00:00+00:00",
                "topology": {
                    "inventory_complete": True,
                    "services": [],
                    "dependencies": {},
                },
                "incidents": {
                    "evidence.delivery": {
                        "status": "OPEN",
                        "severity": "warning",
                        "reason": "evidence_delivery_error",
                    },
                },
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "work": {"objectives": [], "jobs": [], "commands": []},
    }
    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 2, 21, 1, 0, tzinfo=timezone.utc),
    )
    guidance = projected["operator"]["guidance"]
    assert guidance[0]["target"] == "Foundation evidence"
    assert "spool" in guidance[0]["action"]



def test_command_projection_promotes_human_decision_to_canonical_operating_state():
    snapshot = {
        "control": {
            "revision": 13,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-04T22:00:00+00:00",
                "topology": {"services": [], "dependencies": {}},
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "research": {
            "operating_summary": {
                "condition": "RESEARCHING",
                "productivity": "PRODUCTIVE",
                "engineering_required": 0,
                "next_autonomous_action": "Continue research.",
            }
        },
        "work": {
            "objectives": [{
                "objective_key": "decision.live-risk.auto-paper-01",
                "title": "Review live-risk charter",
                "status": "READY",
                "protected_action": True,
                "metadata": {
                    "classification": "HUMAN_DECISION_REQUIRED",
                    "job_type": "HUMAN_DECISION",
                },
            }],
            "jobs": [],
            "commands": [],
        },
    }

    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 4, 22, 1, 0, tzinfo=timezone.utc),
    )

    assert projected["state"] == "HEALTHY"
    assert projected["operating_state"] == "HUMAN_DECISION_REQUIRED"
    assert projected["action_required"] is True
    assert projected["operator"]["state"] == "HUMAN_DECISION_REQUIRED"
    assert projected["research"]["operating_summary"]["human_decision_required"] == 1
    assert "protected" in projected["operator"]["message"].lower()


def test_command_projection_preserves_durable_job_events():
    snapshot = {
        "control": {
            "revision": 11,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-04T15:40:00+00:00",
                "topology": {"services": [], "dependencies": {}},
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "research": {
            "graen_problems": [{
                "problem_id": "problem-r2h",
                "title": "BTC V14 R2H",
                "status": "RUNNING",
                "research_stage": "CRYPTO_BTC_4H_CONSENSUS_V14_R2H_VELUM_REPLAY",
                "updated_at": "2026-10-04T15:39:50+00:00",
            }],
            "graen_runs": [],
            "velum_replays": [{"status": "RUNNING", "started_at": "2026-10-04T15:39:52+00:00"}],
        },
        "work": {
            "objectives": [],
            "jobs": [],
            "commands": [],
            "job_events": [{
                "event_id": 41,
                "job_id": "00000000-0000-0000-0000-000000000041",
                "event_type": "RUNNING",
                "event": {"stage": "R2H"},
                "created_at": "2026-10-04T15:39:45+00:00",
                "owner_system": "VELUM",
                "objective_key": "btc-r2h",
                "title": "Replay frozen R2H candidate",
                "job_type": "REPLAY",
            }],
        },
    }

    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 4, 15, 40, 30, tzinfo=timezone.utc),
    )

    events = projected["work"]["job_events"]
    assert events[0]["event_type"] == "RUNNING"
    assert events[0]["owner_system"] == "VELUM"
    assert events[0]["event"]["stage"] == "R2H"
    assert projected["research"]["graen_problems"][0]["research_stage"].endswith("VELUM_REPLAY")
    assert projected["research"]["velum_replays"][0]["status"] == "RUNNING"

class _CanaryCursor:
    def __init__(self):
        self.calls = []
        self.rows = iter([
            (
                "BTC-CANARY-001-PAPER-20261004",
                datetime(2026, 10, 4, 21, 15, tzinfo=timezone.utc),
            ),
            (
                datetime(2026, 10, 4, 21, 15, tzinfo=timezone.utc),
                {
                    "cycle_outcome": "BTC canary position protected; waiting for frozen R2H exit",
                    "bar_interval": "4Hour",
                    "strategy_family": "btc_4h_momentum_or_sma_consensus_experimental_canary",
                    "model_version": "graen-btc-4h-consensus-v14-r2h",
                    "candidates": [{
                        "symbol": "BTC/USD",
                        "features": {
                            "signal_bar_at": "2026-10-04T20:00:00+00:00",
                            "signal_close": "123456.78",
                            "momentum_lookback_bars": 1080,
                            "sma_window_bars": 1500,
                            "momentum_return": "0.0842",
                            "sma": "118500.00",
                            "desired_long": True,
                            "completed_bar_count": 12592,
                        },
                    }],
                    "comparison_context": {
                        "execution_result": {
                            "action": "hold",
                            "reason": "BTC canary position protected; waiting for frozen R2H exit",
                        }
                    },
                },
            ),
            (
                datetime(2026, 10, 4, 21, 14, tzinfo=timezone.utc),
                {
                    "entry_price": "121000.00",
                    "current_price": "122512.50",
                    "current_return_pct": "0.0125",
                    "risk_stop_pct": "0.05",
                },
            ),
            (
                datetime(2026, 10, 4, 21, 14, tzinfo=timezone.utc),
                {
                    "positions": [
                        {"symbol": "BTC/USD", "qty": "0.000011"}
                    ]
                },
            ),
            (
                datetime(2026, 10, 4, 18, 40, tzinfo=timezone.utc),
                {
                    "order": {
                        "status": "new",
                        "client_order_id": "anevum-crypto-btc-usd-canary-hardstop-test",
                    }
                },
            ),
            [
                (
                    datetime(2026, 10, 4, 21, 15, tzinfo=timezone.utc),
                    "decision_cycle",
                    {
                        "cycle_outcome": "BTC canary position protected; waiting for frozen R2H exit",
                        "comparison_context": {
                            "execution_result": {
                                "action": "hold",
                                "reason": "BTC canary position protected; waiting for frozen R2H exit",
                            }
                        },
                    },
                ),
                (
                    datetime(2026, 10, 4, 21, 14, tzinfo=timezone.utc),
                    "position_metrics",
                    {"current_return_pct": "0.0125"},
                ),
            ],
        ])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, args):
        self.calls.append((query, args))

    def fetchone(self):
        row = next(self.rows)
        if isinstance(row, list):
            raise AssertionError("fetchone called for fetchall fixture row")
        return row

    def fetchall(self):
        row = next(self.rows)
        if not isinstance(row, list):
            raise AssertionError("fetchall fixture row missing")
        return row


class _CanaryConn:
    def __init__(self):
        self.cur = _CanaryCursor()

    def cursor(self):
        return self.cur


def test_btc_canary_activity_is_compact_read_only_projection():
    conn = _CanaryConn()
    result = _btc_canary_activity(conn)

    assert result["available"] is True
    assert result["run_id"] == "BTC-CANARY-001-PAPER-20261004"
    assert result["paper_only"] is True
    assert result["live_execution_authorized"] is False
    assert result["promotion_ready"] is False
    assert result["research_status"] == "NOT_PROMOTED"
    assert result["evidence_state"] == "COLLECTING_OPEN_POSITION"
    assert result["action"] == "hold"
    assert result["bar_interval"] == "4Hour"
    assert result["position_open"] is True
    assert result["entry_price"] == "121000.00"
    assert result["current_price"] == "122512.50"
    assert result["current_return_pct"] == "0.0125"
    assert result["risk_stop_pct"] == "0.05"
    assert result["protection_status"] == "new"
    assert result["signal"]["bar_at"] == "2026-10-04T20:00:00+00:00"
    assert result["signal"]["momentum_return"] == "0.0842"
    assert result["signal"]["momentum_positive"] is True
    assert result["signal"]["sma"] == "118500.00"
    assert result["signal"]["above_sma"] is True
    assert result["signal"]["desired_long"] is True
    assert result["signal"]["momentum_lookback_bars"] == 1080
    assert result["signal"]["sma_window_bars"] == 1500
    assert result["recent_cycles"][0]["action"] == "hold"
    assert result["return_history"][0]["return_pct"] == "0.0125"
    assert len(conn.cur.calls) == 6
    assert all(query.lstrip().lower().startswith("select") for query, _ in conn.cur.calls)


def test_command_projection_preserves_private_btc_canary_state():
    snapshot = {
        "control": {
            "revision": 12,
            "state": {
                "state": "HEALTHY",
                "observed_at": "2026-10-04T21:15:00+00:00",
                "topology": {"services": [], "dependencies": {}},
                "incidents": {},
                "configuration_baseline": {"fingerprint": "sha256:test"},
            },
        },
        "research": {},
        "btc_canary": {
            "available": True,
            "run_id": "BTC-CANARY-001-PAPER-20261004",
            "paper_only": True,
            "live_execution_authorized": False,
            "evidence_state": "COLLECTING_OPEN_POSITION",
            "current_return_pct": "0.0125",
        },
        "work": {"objectives": [], "jobs": [], "commands": []},
    }

    projected = project_command(
        snapshot,
        now=datetime(2026, 10, 4, 21, 16, 0, tzinfo=timezone.utc),
    )

    assert projected["btc_canary"]["available"] is True
    assert projected["btc_canary"]["run_id"] == "BTC-CANARY-001-PAPER-20261004"
    assert projected["btc_canary"]["live_execution_authorized"] is False

