from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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




def test_blocked_v14_pagination_failure_recovers_at_most_twice():
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

        first = await runtime._recover_blocked_v14_pagination(snapshot)
        assert first["recovered"] is True
        assert runtime.gateway.queued[-1]["metadata"]["v14_pagination_recovery_version"] == 1

        snapshot["problems"][0]["metadata"]["v14_pagination_recovery_version"] = 1
        snapshot["runs"].append(
            {
                "run_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "started_at": "2026-10-03T20:14:49+00:00",
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
        )
        second = await runtime._recover_blocked_v14_pagination(snapshot)
        assert second["recovered"] is True
        assert runtime.gateway.queued[-1]["metadata"]["v14_pagination_recovery_version"] == 2
        second_artifact = runtime.gateway.artifacts[-1]
        assert second_artifact["artifact_type"] == "CRYPTO_BTC_V14_R1_INFRA_REPAIR"
        assert second_artifact["content"]["pagination_max_pages"] == 768
        assert second_artifact["content"]["rate_limit_retry_cap"] == 6
        assert second_artifact["content"]["recovery_from_version"] == 1
        assert second_artifact["content"]["live_execution_authorized"] is False

        snapshot["problems"][0]["metadata"]["v14_pagination_recovery_version"] = 2
        third = await runtime._recover_blocked_v14_pagination(snapshot)
        assert third is None

    asyncio.run(scenario())





def test_blocked_v14_sklearn_dependency_failure_recovers_once():
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
                        "v14_feature_recovery_version": 1,
                        "v14_pagination_recovery_version": 2,
                    },
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-03T20:47:21+00:00",
                    "methodology_version": service.V14_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "decision": "REPAIR_REQUIRED",
                        "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
                        "error": "ImportError: sklearn needs to be installed in order to use this module",
                        "execution_authority": False,
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        first = await runtime._recover_blocked_v14_runtime_dependency(snapshot)
        assert first["recovered"] is True
        assert runtime.gateway.queued[-1]["stage"] == service.V14_PREFLIGHT_STAGE
        assert runtime.gateway.queued[-1]["metadata"]["v14_dependency_recovery_version"] == 1
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_R1_RUNTIME_DEPENDENCY_REPAIR"
        assert artifact["content"]["dependency"] == "scikit-learn==1.9.1"
        assert artifact["content"]["methodology_changed"] is False
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["problems"][0]["metadata"]["v14_dependency_recovery_version"] = 1
        second = await runtime._recover_blocked_v14_runtime_dependency(snapshot)
        assert second is None

    asyncio.run(scenario())


def test_blocked_v14_zero_row_feature_failure_recovers_once():
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
                        "v14_pagination_recovery_version": 2,
                    },
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-03T20:39:27+00:00",
                    "methodology_version": service.V14_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "decision": "REPAIR_REQUIRED",
                        "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
                        "error": "ValueError: v14_fold_insufficient_rows:train=0:validation=0:test=0",
                        "execution_authority": False,
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        first = await runtime._recover_blocked_v14_feature_pipeline(snapshot)
        assert first["recovered"] is True
        assert runtime.gateway.queued[-1]["stage"] == service.V14_PREFLIGHT_STAGE
        assert runtime.gateway.queued[-1]["metadata"]["v14_feature_recovery_version"] == 1
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_R1_FEATURE_PIPELINE_REPAIR"
        assert artifact["content"]["methodology_changed"] is False
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["problems"][0]["metadata"]["v14_feature_recovery_version"] = 1
        second = await runtime._recover_blocked_v14_feature_pipeline(snapshot)
        assert second is None

    asyncio.run(scenario())


def test_v14_hourly_fetch_allows_more_than_128_pages(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

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
            next_token = f"page-{index + 1}" if index < 139 else None
            stamp = (
                datetime(2021, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
            ).isoformat().replace("+00:00", "Z")
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
        assert len(rows) == 140
        assert rows[0]["t"] == "2021-01-01T00:00:00Z"
        assert rows[-1]["t"] == "2021-01-06T19:00:00Z"

    asyncio.run(scenario())


def test_v14_hourly_fetch_retries_rate_limit_without_advancing_page(monkeypatch):
    class FakeResponse:
        def __init__(self, payload, *, status_code=200, headers=None):
            self._payload = payload
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

        def json(self):
            return self._payload

    class FakeAsyncClient:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, *, headers, params):
            type(self).calls += 1
            if type(self).calls == 1:
                return FakeResponse({}, status_code=429, headers={"Retry-After": "0"})
            return FakeResponse(
                {
                    "bars": {
                        "BTC/USD": [
                            {
                                "t": "2021-01-01T00:00:00Z",
                                "o": 100.0,
                                "h": 101.0,
                                "l": 99.0,
                                "c": 100.5,
                                "v": 1.0,
                            }
                        ]
                    },
                    "next_page_token": None,
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

        async def no_wait(_seconds):
            return None

        monkeypatch.setattr(service.asyncio, "sleep", no_wait)
        rows = await runtime._fetch_v14_hourly_btc()
        assert len(rows) == 1
        assert FakeAsyncClient.calls == 2

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



def test_v14_r1_failure_advances_once_to_r2a_queue_imbalance():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-03T20:53:26+00:00",
                    "methodology_version": service.V14_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_CAMPAIGN_ID,
                        "state": "V14_R1_BROKER_FEASIBILITY_FAIL",
                        "decision": "V14_R1_DO_NOT_REPLICATE_FURTHER",
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r1_fail_into_r2a(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2A_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2A_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_R2A_QUEUE_IMBALANCE_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["runs"].append(
            {
                "run_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                "problem_id": PROBLEM_ID,
                "status": "WAITING",
                "methodology_version": service.V14_R2A_METHODOLOGY_VERSION,
                "result_summary": {
                    "campaign_id": service.V14_R2A_CAMPAIGN_ID,
                    "state": "V14_R2A_BROKER_FEASIBILITY_FAIL",
                },
            }
        )
        runtime.gateway.queued.clear()
        second = await runtime._recover_v14_r1_fail_into_r2a(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v14_r2a_quote_fetch_paginates_without_order_authority(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

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
            if token is None:
                return FakeResponse(
                    {
                        "quotes": {
                            "BTC/USD": [
                                {
                                    "t": "2026-09-28T00:00:00Z",
                                    "bp": 100.0,
                                    "ap": 100.1,
                                    "bs": 2.0,
                                    "as": 1.0,
                                }
                            ]
                        },
                        "next_page_token": "page-2",
                    }
                )
            return FakeResponse(
                {
                    "quotes": {
                        "BTC/USD": [
                            {
                                "t": "2026-09-28T00:00:15Z",
                                "bp": 100.1,
                                "ap": 100.2,
                                "bs": 2.0,
                                "as": 1.0,
                            }
                        ]
                    },
                    "next_page_token": None,
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
        rows = await runtime._fetch_v14_r2a_quotes()
        assert len(rows) == 2

    asyncio.run(scenario())


def test_v14_r2a_failed_preflight_never_opens_live_gates(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return [{"t": "2026-09-28T00:00:00Z", "bp": 100, "ap": 101, "bs": 2, "as": 1}]

        runtime._fetch_v14_r2a_quotes = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2A_CAMPAIGN_ID,
                "oos": {
                    "config": {"config_id": "qi-0.40-60s"},
                    "scenarios": {
                        "taker_base_50bp": {
                            "trade_count": 30,
                            "sharpe": -0.1,
                            "total_return": -0.02,
                        }
                    },
                    "taker_base_50bp_positive_quarter_share": 0.25,
                },
                "broker_feasibility_gate": {"survives_to_live_l2_shadow": False},
            }

        monkeypatch.setattr(service, "evaluate_v14_r2a_queue_imbalance", fake_evaluate)
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2A_STAGE,
                "v14_r2a_campaign_id": service.V14_R2A_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_queue_imbalance_v14_r2a(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2A_BROKER_FEASIBILITY_FAIL"
        assert result["decision"] == "V14_R2A_DO_NOT_PROMOTE"
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False
        assert result["shadow_only"] is False
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v14_r2a_pass_still_stops_at_live_l2_shadow(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return [{"t": "2026-09-28T00:00:00Z", "bp": 100, "ap": 101, "bs": 2, "as": 1}]

        runtime._fetch_v14_r2a_quotes = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2A_CAMPAIGN_ID,
                "oos": {
                    "config": {"config_id": "qi-0.40-60s"},
                    "scenarios": {
                        "taker_base_50bp": {
                            "trade_count": 40,
                            "sharpe": 0.4,
                            "total_return": 0.03,
                        }
                    },
                    "taker_base_50bp_positive_quarter_share": 0.75,
                },
                "broker_feasibility_gate": {"survives_to_live_l2_shadow": True},
            }

        monkeypatch.setattr(service, "evaluate_v14_r2a_queue_imbalance", fake_evaluate)
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2A_STAGE,
                "v14_r2a_campaign_id": service.V14_R2A_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_queue_imbalance_v14_r2a(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2A_SURVIVES_TO_LIVE_L2_SHADOW"
        assert result["next_action"] == "START_V14_R2A_LIVE_L2_SHADOW_CONFIRMATION"
        assert result["shadow_only"] is True
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False
        assert runtime.gateway.queued == []

    asyncio.run(scenario())



def test_v14_r2a_failure_advances_once_to_r2b_passive_scalping():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-03T21:12:54+00:00",
                    "methodology_version": service.V14_R2A_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2A_CAMPAIGN_ID,
                        "state": "V14_R2A_BROKER_FEASIBILITY_FAIL",
                        "decision": "V14_R2A_DO_NOT_PROMOTE",
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r2a_fail_into_r2b(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2B_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2B_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V14_R2B_PASSIVE_SCALPING_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["runs"].append({
            "run_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "methodology_version": service.V14_R2B_METHODOLOGY_VERSION,
            "result_summary": {
                "campaign_id": service.V14_R2B_CAMPAIGN_ID,
                "state": "V14_R2B_BROKER_FEASIBILITY_FAIL",
            },
        })
        runtime.gateway.queued.clear()
        second = await runtime._recover_v14_r2a_fail_into_r2b(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v14_r2b_pass_still_stops_at_shadow(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return [{"t": "2026-09-01T00:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100}]

        runtime._fetch_v14_r2b_minute_btc = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2B_CAMPAIGN_ID,
                "oos": {
                    "config": {"config_id": "scalp-10m-20bp-10m"},
                    "scenarios": {
                        "taker_stress_50bp": {
                            "trade_count": 25,
                            "sharpe": 0.5,
                            "total_return": 0.04,
                        }
                    },
                    "taker_stress_50bp_positive_quarter_share": 0.75,
                },
                "broker_feasibility_gate": {"survives_to_shadow": True},
            }

        monkeypatch.setattr(service, "evaluate_v14_r2b_passive_scalping", fake_evaluate)
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2B_STAGE,
                "v14_r2b_campaign_id": service.V14_R2B_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_passive_scalping_v14_r2b(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2B_SURVIVES_TO_PASSIVE_SCALPING_SHADOW"
        assert result["next_action"] == "START_V14_R2B_PASSIVE_SCALPING_SHADOW"
        assert result["shadow_only"] is True
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False

    asyncio.run(scenario())



def test_v14_r2b_failure_advances_once_to_r2c_cross_sectional_momentum():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-03T21:17:03+00:00",
                    "methodology_version": service.V14_R2B_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2B_CAMPAIGN_ID,
                        "state": "V14_R2B_BROKER_FEASIBILITY_FAIL",
                        "decision": "V14_R2B_DO_NOT_PROMOTE",
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r2b_fail_into_r2c(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2C_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2C_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_V14_R2C_CROSS_SECTIONAL_MOMENTUM_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["runs"].append({
            "run_id": "abababab-abab-abab-abab-abababababab",
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "methodology_version": service.V14_R2C_METHODOLOGY_VERSION,
            "result_summary": {
                "campaign_id": service.V14_R2C_CAMPAIGN_ID,
                "state": "V14_R2C_BROKER_FEASIBILITY_FAIL",
            },
        })
        runtime.gateway.queued.clear()
        second = await runtime._recover_v14_r2b_fail_into_r2c(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v14_r2c_pass_still_stops_at_shadow(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return {symbol: [] for symbol in service.V14_R2C_UNIVERSE}

        runtime._fetch_v14_r2c_daily_crypto = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2C_CAMPAIGN_ID,
                "oos": {
                    "scenarios": {
                        "taker_switch_50bp": {
                            "day_count": 160,
                            "switch_count": 12,
                            "sharpe": 0.7,
                            "total_return": 0.09,
                        }
                    },
                    "taker_switch_50bp_positive_quarter_share": 0.75,
                },
                "broker_feasibility_gate": {"survives_to_shadow": True},
            }

        monkeypatch.setattr(service, "evaluate_v14_r2c_momentum", fake_evaluate)
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2C_STAGE,
                "v14_r2c_campaign_id": service.V14_R2C_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_cross_sectional_momentum_v14_r2c(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2C_SURVIVES_TO_CROSS_SECTIONAL_MOMENTUM_SHADOW"
        assert result["next_action"] == "START_V14_R2C_CROSS_SECTIONAL_MOMENTUM_SHADOW"
        assert result["shadow_only"] is True
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False

    asyncio.run(scenario())


def test_v14_r2c_failure_advances_once_to_r2d_triangular_arbitrage():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-03T21:40:00+00:00",
                    "methodology_version": service.V14_R2C_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2C_CAMPAIGN_ID,
                        "state": "V14_R2C_BROKER_FEASIBILITY_FAIL",
                        "decision": "V14_R2C_DO_NOT_PROMOTE",
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r2c_fail_into_r2d(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2D_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2D_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_V14_R2D_TRIANGULAR_ARBITRAGE_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["runs"].append({
            "run_id": "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "methodology_version": service.V14_R2D_METHODOLOGY_VERSION,
            "result_summary": {
                "campaign_id": service.V14_R2D_CAMPAIGN_ID,
                "state": "V14_R2D_BROKER_FEASIBILITY_FAIL",
            },
        })
        runtime.gateway.queued.clear()
        second = await runtime._recover_v14_r2c_fail_into_r2d(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v14_r2d_pass_still_stops_at_shadow(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return {symbol: [] for symbol in service.V14_R2D_PAIRS}

        runtime._fetch_v14_r2d_quotes = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2D_CAMPAIGN_ID,
                "scenarios": {
                    "taker_75bp": {
                        "snapshot_count": 500,
                        "profitable_snapshot_count": 12,
                        "max_net_edge_bps": 18.0,
                        "positive_time_quarter_share": 0.75,
                    }
                },
                "broker_feasibility_gate": {"survives_to_shadow": True},
            }

        monkeypatch.setattr(
            service,
            "evaluate_v14_r2d_triangular_arbitrage",
            fake_evaluate,
        )
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2D_STAGE,
                "v14_r2d_campaign_id": service.V14_R2D_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_triangular_arbitrage_v14_r2d(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2D_SURVIVES_TO_TRIANGULAR_ARBITRAGE_SHADOW"
        assert result["next_action"] == "START_V14_R2D_TRIANGULAR_ARBITRAGE_LIVE_QUOTE_SHADOW"
        assert result["shadow_only"] is True
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False

    asyncio.run(scenario())



def test_v14_r2d_failure_advances_once_to_r2e_btc_4h_trend():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-03T22:45:00+00:00",
                    "methodology_version": service.V14_R2D_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2D_CAMPAIGN_ID,
                        "state": "V14_R2D_BROKER_FEASIBILITY_FAIL",
                        "decision": "V14_R2D_DO_NOT_PROMOTE",
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r2d_fail_into_r2e(snapshot)
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2E_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2E_STAGE
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_V14_R2E_BTC_4H_TREND_SELECTION"
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["runs"].append({
            "run_id": "dededede-dede-dede-dede-dededededede",
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "methodology_version": service.V14_R2E_METHODOLOGY_VERSION,
            "result_summary": {
                "campaign_id": service.V14_R2E_CAMPAIGN_ID,
                "state": "V14_R2E_BROKER_FEASIBILITY_FAIL",
            },
        })
        runtime.gateway.queued.clear()
        second = await runtime._recover_v14_r2d_fail_into_r2e(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())



def test_v14_r2d_unusable_quote_corpus_advances_to_r2e_without_false_strategy_fail():
    async def scenario():
        error = "ValueError: v14_r2d_matched_quote_corpus_too_small:0"
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {"research_stage": service.V14_R2D_STAGE},
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-03T22:39:35+00:00",
                    "methodology_version": service.V14_R2D_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2D_CAMPAIGN_ID,
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "error": error,
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_v14_r2d_fail_into_r2e(snapshot)
        assert result["recovered"] is True
        assert result["origin_state"] == "RESEARCH_EXECUTION_BLOCKED"
        assert result["origin_error"] == error
        assert result["next_research_stage"] == service.V14_R2E_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2E_STAGE

        corpus = next(
            row for row in runtime.gateway.artifacts
            if row["artifact_type"] == "CRYPTO_V14_R2D_CORPUS_INFEASIBLE"
        )
        assert corpus["content"]["state"] == "V14_R2D_CORPUS_UNAVAILABLE"
        assert corpus["content"]["strategy_evaluation_performed"] is False
        assert corpus["content"]["corpus_error"] == error

        selection = runtime.gateway.artifacts[-1]
        assert selection["artifact_type"] == "CRYPTO_V14_R2E_BTC_4H_TREND_SELECTION"
        assert selection["content"]["origin_r2d_error"] == error
        assert selection["content"]["live_execution_authorized"] is False

    asyncio.run(scenario())


def test_v14_r2e_pass_still_stops_at_shadow(monkeypatch):
    async def scenario():
        runtime = _runtime()

        async def fake_fetch():
            return {"BTC/USD": []}

        runtime._fetch_v14_r2e_btc_4h = fake_fetch

        def fake_evaluate(_rows):
            return {
                "campaign_id": service.V14_R2E_CAMPAIGN_ID,
                "development": {"selected_window": 200},
                "oos": {
                    "scenarios": {
                        "taker_stress_30bp": {
                            "bar_count": 2500,
                            "entry_count": 8,
                            "total_return": 0.22,
                            "sharpe": 0.9,
                            "max_drawdown": -0.18,
                            "positive_time_quarter_share": 0.75,
                        }
                    },
                    "buy_hold": {
                        "total_return": 0.18,
                        "sharpe": 0.65,
                    },
                },
                "broker_feasibility_gate": {"survives_to_shadow": True},
            }

        monkeypatch.setattr(service, "evaluate_v14_r2e_btc_4h_trend", fake_evaluate)
        problem = {
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2E_STAGE,
                "v14_r2e_campaign_id": service.V14_R2E_CAMPAIGN_ID,
            },
        }
        result = await runtime._execute_btc_4h_trend_v14_r2e(
            problem,
            {"run_id": V14_RUN_ID},
        )
        assert result["state"] == "V14_R2E_SURVIVES_TO_BTC_4H_TREND_SHADOW"
        assert result["next_action"] == "START_V14_R2E_BTC_4H_TREND_FORWARD_SHADOW"
        assert result["shadow_only"] is True
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["crypto_execution_enabled"] is False
        assert result["live_execution_authorized"] is False

    asyncio.run(scenario())



def test_v14_r2e_fetch_uses_bounded_chunks_with_local_pagination(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        calls = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, *, headers, params):
            type(self).calls.append(dict(params))
            token = params.get("page_token")
            base = datetime.fromisoformat(
                str(params["start"]).replace("Z", "+00:00")
            )
            offset = 0 if token is None else 4
            stamp = (base + timedelta(hours=offset)).isoformat().replace(
                "+00:00", "Z"
            )
            return FakeResponse(
                {
                    "bars": {
                        "BTC/USD": [
                            {
                                "t": stamp,
                                "o": 100.0,
                                "h": 101.0,
                                "l": 99.0,
                                "c": 100.5,
                                "v": 1.0,
                            }
                        ]
                    },
                    "next_page_token": "local-page-2" if token is None else None,
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

        rows = await runtime._fetch_v14_r2e_btc_4h()

        assert len(rows["BTC/USD"]) > 60
        assert len(FakeAsyncClient.calls) % 2 == 0
        first_pages = [
            row for row in FakeAsyncClient.calls if "page_token" not in row
        ]
        second_pages = [
            row for row in FakeAsyncClient.calls if row.get("page_token") == "local-page-2"
        ]
        assert len(first_pages) == len(second_pages)
        assert len(first_pages) >= 30
        assert all(row["timeframe"] == "4Hour" for row in FakeAsyncClient.calls)

    asyncio.run(scenario())


def test_v14_r2e_blocked_pagination_recovers_once_without_methodology_change():
    async def scenario():
        error = "RuntimeError: v14_r2e_bar_pagination_exceeded_safety_limit"
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {
                        "research_stage": service.V14_R2E_STAGE,
                        "v14_r2e_campaign_id": service.V14_R2E_CAMPAIGN_ID,
                    },
                }
            ],
            "runs": [
                {
                    "run_id": V14_RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-03T22:53:04+00:00",
                    "methodology_version": service.V14_R2E_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "error": error,
                    },
                }
            ],
        }
        runtime = _runtime(snapshot)
        result = await runtime._recover_blocked_v14_r2e_pagination(snapshot)

        assert result["recovered"] is True
        assert result["blocked_error"] == error
        assert result["next_research_stage"] == service.V14_R2E_STAGE
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2E_STAGE
        assert (
            runtime.gateway.queued[-1]["metadata"][
                "v14_r2e_pagination_recovery_version"
            ]
            == 1
        )
        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_V14_R2E_CORPUS_FETCH_REPAIR"
        assert artifact["content"]["methodology_changed"] is False
        assert artifact["content"]["strategy_parameters_changed"] is False
        assert artifact["content"]["live_execution_authorized"] is False

        snapshot["problems"][0]["metadata"][
            "v14_r2e_pagination_recovery_version"
        ] = 1
        runtime.gateway.queued.clear()
        second = await runtime._recover_blocked_v14_r2e_pagination(snapshot)
        assert second is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())
