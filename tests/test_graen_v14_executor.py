from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.graen import research_executor_service as service


PROBLEM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
V13_RUN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
V14_RUN_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


class FakeGateway:
    configured = True

    def __init__(self, snapshot=None):
        self.snapshot_value = snapshot or {"problems": [], "runs": []}
        self.queued = []
        self.artifacts = []
        self.completions = []
        self.heartbeats = []

    async def snapshot(self):
        return self.snapshot_value

    async def queue_research_stage(self, **kwargs):
        self.queued.append(kwargs)
        return {"ok": True, "problem": {"problem_id": kwargs["problem_id"]}}

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {
            "ok": True,
            "artifact": {"artifact_id": f"artifact-{len(self.artifacts)}"},
        }

    async def complete_research_problem(self, **kwargs):
        self.completions.append(kwargs)
        return {"ok": True}

    async def executor_heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)
        return {"ok": True}


def _terminal_v13_snapshot():
    return {
        "problems": [
            {
                "problem_id": PROBLEM_ID,
                "status": "WAITING",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {"v13_campaign_id": service.V13_CAMPAIGN_ID},
            }
        ],
        "runs": [
            {
                "run_id": V13_RUN_ID,
                "problem_id": PROBLEM_ID,
                "status": "WAITING",
                "methodology_version": service.V13_METHODOLOGY_VERSION,
                "result_summary": {
                    "campaign_id": service.V13_CAMPAIGN_ID,
                    "state": "V13_NO_DEVELOPMENT_SURVIVOR",
                    "decision": "V13_HYPOTHESES_FALSIFIED",
                },
            }
        ],
    }


def _runtime(snapshot=None):
    runtime = service.GraenResearchExecutor()
    runtime.gateway = FakeGateway(snapshot)
    runtime.callback_base_url = ""
    runtime.callback_token = ""
    return runtime


def test_v13_terminal_state_advances_to_v14_external_replication_preflight():
    async def scenario():
        snapshot = _terminal_v13_snapshot()
        runtime = _runtime(snapshot)
        result = await runtime._recover_v13_into_v14(snapshot)
        assert result["next_research_stage"] == service.V14_PREFLIGHT_STAGE
        assert result["v14_campaign_id"] == service.V14_CAMPAIGN_ID
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.V14_PREFLIGHT_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_EXTERNAL_REPLICATION_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

    asyncio.run(scenario())


def test_v14_recovery_is_idempotent_after_any_v14_result():
    async def scenario():
        snapshot = _terminal_v13_snapshot()
        snapshot["runs"].insert(
            0,
            {
                "run_id": V14_RUN_ID,
                "problem_id": PROBLEM_ID,
                "status": "WAITING",
                "methodology_version": service.V14_METHODOLOGY_VERSION,
                "result_summary": {
                    "campaign_id": service.V14_CAMPAIGN_ID,
                    "state": "V14_R1_BROKER_FEASIBILITY_FAIL",
                    "decision": "V14_R1_DO_NOT_REPLICATE_FURTHER",
                },
            },
        )
        runtime = _runtime(snapshot)
        result = await runtime._recover_v13_into_v14(snapshot)
        assert result is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())




def test_blocked_v14_pagination_failure_recovers_once():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {
                        "research_stage": service.V14_PREFLIGHT_STAGE,
                        "v14_campaign_id": service.V14_CAMPAIGN_ID,
                    },
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-03T20:08:54+00:00",
                    "methodology_version": service.V14_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "decision": "REPAIR_REQUIRED",
                        "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
                        "error": (
                            "RuntimeError: "
                            "v14_hourly_btc_pagination_exceeded_safety_limit"
                        ),
                        "execution_authority": False,
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_blocked_v14_pagination(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_PREFLIGHT_STAGE
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.V14_PREFLIGHT_STAGE
        assert (
            runtime.gateway.queued[-1]["metadata"]["v14_pagination_recovery_version"]
            == 1
        )
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_R1_INFRA_REPAIR"
        assert artifact["content"]["pagination_max_pages"] == 128
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["problems"][0]["metadata"]["v14_pagination_recovery_version"] = 1
        second = await runtime._recover_blocked_v14_pagination(snapshot)
        assert second is None

    asyncio.run(scenario())


def test_v14_hourly_fetch_allows_more_than_twelve_pages(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, *, headers, params):
            token = params.get("page_token")
            index = 0 if token is None else int(str(token).replace("page-", ""))
            next_token = f"page-{index + 1}" if index < 13 else None
            stamp = f"2021-01-01T{index:02d}:00:00Z"
            return FakeResponse(
                {
                    "bars": {
                        "BTC/USD": [
                            {
                                "t": stamp,
                                "o": 100.0 + index,
                                "h": 101.0 + index,
                                "l": 99.0 + index,
                                "c": 100.5 + index,
                                "v": 1.0,
                            }
                        ]
                    },
                    "next_page_token": next_token,
                }
            )

    async def scenario():
        runtime = _runtime()
        runtime.settings = SimpleNamespace(
            credentials_configured=True,
            data_base_url="https://example.test",
            crypto_location="us",
        )
        runtime.market_data = SimpleNamespace(headers={"X-Test": "1"})
        monkeypatch.setattr(service.httpx, "AsyncClient", FakeAsyncClient)
        rows = await runtime._fetch_v14_hourly_btc()
        assert len(rows) == 14
        assert rows[0]["t"] == "2021-01-01T00:00:00Z"
        assert rows[-1]["t"] == "2021-01-01T13:00:00Z"

    asyncio.run(scenario())


def test_v14_failed_broker_screen_never_opens_protected_or_live_gates(monkeypatch):
    async def scenario():
        snapshot = _terminal_v13_snapshot()
        runtime = _runtime(snapshot)

        async def fake_fetch():
            return [{"t": "2021-01-01T06:00:00Z", "c": 1.0}]

        runtime._fetch_v14_hourly_btc = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_CAMPAIGN_ID,
                "aggregate": {
                    "paper_10bp": {"sharpe": 0.8},
                    "alpaca_base_30bp": {"sharpe": -0.2, "total_return": -0.05},
                },
                "broker_feasibility_gate": {
                    "worth_full_original_replication": False,
                    "alpaca_base_positive_fold_share": 0.33,
                },
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            }

        monkeypatch.setattr(service, "evaluate_v14_alpaca_transfer_screen", fake_evaluate)
        problem = {
            **snapshot["problems"][0],
            "status": "RUNNING",
            "metadata": {
                **snapshot["problems"][0]["metadata"],
                "research_stage": service.V14_PREFLIGHT_STAGE,
                "v14_campaign_id": service.V14_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_xgb_v14(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R1_BROKER_FEASIBILITY_FAIL"
        assert result["development_opened"] is False
        assert result["validation_opened"] is False
        assert result["holdout_opened"] is False
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False
        assert runtime.gateway.queued == []
        assert runtime.gateway.completions[-1]["status"] == "WAITING"

    asyncio.run(scenario())


def test_v14_surviving_preflight_still_does_not_skip_original_replication(monkeypatch):
    async def scenario():
        snapshot = _terminal_v13_snapshot()
        runtime = _runtime(snapshot)

        async def fake_fetch():
            return [{"t": "2021-01-01T06:00:00Z", "c": 1.0}]

        runtime._fetch_v14_hourly_btc = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_CAMPAIGN_ID,
                "aggregate": {
                    "paper_10bp": {"sharpe": 1.0},
                    "alpaca_base_30bp": {"sharpe": 0.4, "total_return": 0.12},
                },
                "broker_feasibility_gate": {
                    "worth_full_original_replication": True,
                    "alpaca_base_positive_fold_share": 0.67,
                },
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            }

        monkeypatch.setattr(service, "evaluate_v14_alpaca_transfer_screen", fake_evaluate)
        problem = {
            **snapshot["problems"][0],
            "status": "RUNNING",
            "metadata": {
                **snapshot["problems"][0]["metadata"],
                "research_stage": service.V14_PREFLIGHT_STAGE,
                "v14_campaign_id": service.V14_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_xgb_v14(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R1_BROKER_FEASIBILITY_SURVIVES"
        assert result["original_replication_complete"] is False
        assert result["development_opened"] is False
        assert result["next_action"] == "VERIFY_SOURCE_AND_RUN_V14_R1_ORIGINAL_REPLICATION"
        assert result["live_execution_authorized"] is False
        assert runtime.gateway.queued == []

    asyncio.run(scenario())
