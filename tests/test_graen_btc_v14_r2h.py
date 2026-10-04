from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.graen import research_executor_service as service
from graen.crypto.btc_4h_consensus_v14_r2h import (
    COST_SCENARIOS,
    MOMENTUM_LOOKBACK_BARS,
    SMA_WINDOW_BARS,
    campaign_manifest,
    evaluate_btc_4h_consensus_replay,
    evaluate_btc_4h_consensus_transfer,
)

UTC = timezone.utc
PROBLEM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RUN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _bars(*, count: int = 12600):
    start = datetime(2021, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for index in range(count):
        cycle = index % 3000
        drift = 0.0015 if cycle < 1600 else -0.0010
        next_price = price * (1.0 + drift)
        rows.append(
            {
                "t": (start + timedelta(hours=4 * index)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "o": price,
                "h": max(price, next_price) * 1.001,
                "l": min(price, next_price) * 0.999,
                "c": next_price,
                "v": 10.0,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_r2h_manifest_is_faststart_but_research_only():
    manifest = campaign_manifest()
    assert manifest["bar_timeframe"] == "4Hour"
    assert manifest["momentum_lookback_bars"] == MOMENTUM_LOOKBACK_BARS
    assert manifest["sma_window_bars"] == SMA_WINDOW_BARS
    assert manifest["next_stage_if_pass"] == "VELUM_REPLAY_THEN_4H_FORWARD_SHADOW"
    assert manifest["independent_historical_validation"] is False
    assert manifest["promotion_eligible"] is False
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_r2h_cost_scenarios_include_stressed_alpaca_fees():
    assert COST_SCENARIOS["maker_15bp"] < COST_SCENARIOS["taker_25bp"]
    assert COST_SCENARIOS["taker_25bp"] < COST_SCENARIOS["taker_stress_30bp"]
    assert COST_SCENARIOS["taker_stress_30bp"] < COST_SCENARIOS["severe_stress_50bp"]


def test_r2h_trending_synthetic_history_survives_only_to_velum_and_shadow():
    result = evaluate_btc_4h_consensus_transfer(_bars())
    decisive = result["adaptive_transfer_window"]["scenarios"]["taker_stress_30bp"]
    severe = result["adaptive_transfer_window"]["scenarios"]["severe_stress_50bp"]

    assert decisive["bar_count"] >= 1800
    assert decisive["entry_count"] >= 2
    assert decisive["total_return"] > 0
    assert decisive["sharpe"] >= 0.5
    assert severe["total_return"] > 0
    assert result["transfer_gate"]["survives_to_velum_and_forward_shadow"] is True
    assert result["shadow_only"] is True
    assert result["promotion_eligible"] is False
    assert result["live_execution_authorized"] is False


def test_r2h_velum_replay_uses_bounded_recent_window_and_remains_research_only():
    result = evaluate_btc_4h_consensus_replay(
        _bars(),
        start=datetime(2026, 4, 4, tzinfo=UTC),
        end=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert result["engineering_gate"]["passed"] is True
    assert result["scenarios"]["taker_stress_30bp"]["bar_count"] >= 720
    assert result["scenarios"]["taker_stress_30bp"]["total_return"] > 0
    assert result["scenarios"]["severe_stress_50bp"]["total_return"] > 0
    assert result["one_bar_execution_delay"]["total_return"] > 0
    assert result["independent_confirmatory_evidence"] is False
    assert result["promotion_authorized"] is False
    assert result["execution_authority"] is False


class FakeGateway:
    configured = True

    def __init__(self):
        self.queued = []
        self.artifacts = []

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {"artifact": {"artifact_id": "artifact-recovery"}}

    async def queue_research_stage(self, **kwargs):
        self.queued.append(kwargs)
        return {"ok": True, "problem": {"problem_id": kwargs["problem_id"]}}


def test_r2g_pass_queues_r2h_faststart_without_execution_authority():
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
                    "run_id": RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-04T04:06:47+00:00",
                    "methodology_version": service.V14_R2G_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2G_CAMPAIGN_ID,
                        "state": (
                            "V14_R2G_ADAPTIVE_DISCOVERY_"
                            "SURVIVES_TO_FORWARD_SHADOW_COMPARISON"
                        ),
                    },
                }
            ],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()

        result = await runtime._recover_v14_r2g_pass_into_r2h_faststart(snapshot)

        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2H_STAGE
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2H_STAGE
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["r2f_forward_shadow_preserved"] is True
        assert metadata["r2g_forward_shadow_preserved"] is True

    asyncio.run(scenario())


def test_r2h_transfer_pass_queues_velum_once_without_execution_authority():
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
                    "run_id": RUN_ID,
                    "problem_id": PROBLEM_ID,
                    "status": "WAITING",
                    "started_at": "2026-10-04T15:32:10+00:00",
                    "methodology_version": service.V14_R2H_METHODOLOGY_VERSION,
                    "result_summary": {
                        "campaign_id": service.V14_R2H_CAMPAIGN_ID,
                        "state": (
                            "V14_R2H_4H_TRANSFER_SURVIVES_TO_"
                            "VELUM_AND_FORWARD_SHADOW"
                        ),
                        "decision": "ADVANCE_TO_VELUM_AND_4H_SHADOW",
                        "candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                        "result_artifact_id": "artifact-r2h-transfer",
                    },
                }
            ],
            "artifacts": [],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()

        result = await runtime._recover_v14_r2h_pass_into_velum(snapshot)

        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V14_R2H_VELUM_STAGE
        assert result["execution_authority"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2H_VELUM_STAGE
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_transfer_artifact_id"] == "artifact-r2h-transfer"
        assert metadata["r2f_forward_shadow_preserved"] is True
        assert metadata["r2g_forward_shadow_preserved"] is True

    asyncio.run(scenario())


def test_blocked_r2h_velum_transport_recovers_once_after_velum_is_healthy():
    async def scenario():
        snapshot = {
            "problems": [
                {
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {
                        "research_stage": service.V14_R2H_VELUM_STAGE,
                        "v14_r2h_candidate_spec": (
                            service.v14_r2h_candidate_spec().to_dict()
                        ),
                        "v14_r2h_origin_run_id": RUN_ID,
                        "v14_r2h_transfer_artifact_id": "artifact-transfer",
                    },
                }
            ],
            "runs": [
                {
                    "run_id": "blocked-run",
                    "problem_id": PROBLEM_ID,
                    "status": "BLOCKED",
                    "started_at": "2026-10-04T15:43:49+00:00",
                    "result_summary": {
                        "state": "RESEARCH_EXECUTION_BLOCKED",
                        "error": "ConnectError: All connection attempts failed",
                    },
                }
            ],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        result = await runtime._recover_blocked_r2h_velum_transport(snapshot)

        assert result["recovered"] is True
        assert result["retry_count"] == 1
        assert runtime.gateway.queued[-1]["stage"] == service.V14_R2H_VELUM_STAGE
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_velum_transport_recovery_version"] == 1
        assert metadata["v14_r2h_recovered_blocked_run_id"] == "blocked-run"
        assert metadata["v14_r2h_transport_recovery_artifact_id"] == "artifact-recovery"
        assert runtime.gateway.artifacts[-1]["artifact_type"] == (
            "CRYPTO_V14_R2H_VELUM_TRANSPORT_RECOVERY"
        )

    asyncio.run(scenario())


def test_velum_transport_readiness_accepts_reachable_replay_service(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": False,
                "system": "VELUM",
                "mode": "research_replay_only",
                "last_error": "unrelated_periodic_replay_error",
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    runtime = service.GraenResearchExecutor()
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **_kwargs: Client())
    runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
    runtime.velum_token = "x" * 32

    assert asyncio.run(runtime._velum_health_ready()) is True


def test_r2h_private_network_repair_allows_exactly_second_transport_retry():
    async def scenario():
        snapshot = {
            "problems": [{
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "research_stage": service.V14_R2H_VELUM_STAGE,
                    "v14_r2h_candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                    "v14_r2h_velum_transport_recovery_version": 1,
                },
            }],
            "runs": [{
                "run_id": "blocked-run-2",
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "started_at": "2026-10-04T15:54:12+00:00",
                "result_summary": {
                    "state": "RESEARCH_EXECUTION_BLOCKED",
                    "error": "ConnectError: All connection attempts failed",
                },
            }],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"

        result = await runtime._recover_blocked_r2h_velum_transport(snapshot)

        assert result["recovered"] is True
        assert result["retry_count"] == 2
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_velum_transport_recovery_version"] == 2
        assert runtime.gateway.artifacts[-1]["content"]["repair"] == (
            "retry_after_railway_private_network_repair"
        )

    asyncio.run(scenario())


def test_r2h_second_transport_retry_requires_internal_velum_url():
    async def scenario():
        snapshot = {
            "problems": [{
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "research_stage": service.V14_R2H_VELUM_STAGE,
                    "v14_r2h_candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                    "v14_r2h_velum_transport_recovery_version": 1,
                },
            }],
            "runs": [{
                "run_id": "blocked-run-2",
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "started_at": "2026-10-04T15:54:12+00:00",
                "result_summary": {
                    "state": "RESEARCH_EXECUTION_BLOCKED",
                    "error": "ConnectError: All connection attempts failed",
                },
            }],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "https://stale.example.com"

        result = await runtime._recover_blocked_r2h_velum_transport(snapshot)

        assert result is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_r2h_port_8080_repair_allows_exactly_third_transport_retry():
    async def scenario():
        snapshot = {
            "problems": [{
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "research_stage": service.V14_R2H_VELUM_STAGE,
                    "v14_r2h_candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                    "v14_r2h_velum_transport_recovery_version": 2,
                },
            }],
            "runs": [{
                "run_id": "blocked-run-3",
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "started_at": "2026-10-04T16:22:34+00:00",
                "result_summary": {
                    "state": "RESEARCH_EXECUTION_BLOCKED",
                    "error": "ConnectError: All connection attempts failed",
                },
            }],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"

        result = await runtime._recover_blocked_r2h_velum_transport(snapshot)

        assert result["recovered"] is True
        assert result["retry_count"] == 3
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_velum_transport_recovery_version"] == 3
        assert runtime.gateway.artifacts[-1]["content"]["repair"] == (
            "retry_after_velum_port_8080_repair"
        )

    asyncio.run(scenario())


def test_r2h_third_transport_retry_requires_internal_8080_url():
    async def scenario():
        snapshot = {
            "problems": [{
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "research_stage": service.V14_R2H_VELUM_STAGE,
                    "v14_r2h_candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                    "v14_r2h_velum_transport_recovery_version": 2,
                },
            }],
            "runs": [{
                "run_id": "blocked-run-3",
                "problem_id": PROBLEM_ID,
                "status": "BLOCKED",
                "started_at": "2026-10-04T16:22:34+00:00",
                "result_summary": {
                    "state": "RESEARCH_EXECUTION_BLOCKED",
                    "error": "ConnectError: All connection attempts failed",
                },
            }],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:80"

        result = await runtime._recover_blocked_r2h_velum_transport(snapshot)

        assert result is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def _r2h_ipv6_blocked_snapshot(*, version: int = 3):
    return {
        "problems": [{
            "problem_id": PROBLEM_ID,
            "status": "BLOCKED",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V14_R2H_VELUM_STAGE,
                "v14_r2h_candidate_spec": service.v14_r2h_candidate_spec().to_dict(),
                "v14_r2h_velum_transport_recovery_version": version,
            },
        }],
        "runs": [{
            "run_id": "blocked-run-ipv6",
            "problem_id": PROBLEM_ID,
            "status": "BLOCKED",
            "started_at": "2026-10-04T16:27:45+00:00",
            "result_summary": {
                "state": "RESEARCH_EXECUTION_BLOCKED",
                "error": (
                    "HTTPStatusError: Server error '500 Internal Server Error' "
                    "for url 'http://rhen-velum.railway.internal:8080/"
                    "v1/graen/candidate-replay'"
                ),
            },
        }],
    }


def test_r2h_ipv6_repair_requires_private_health_before_version_four():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
        runtime._velum_health_ready = AsyncMock(return_value=True)

        result = await runtime._recover_blocked_r2h_velum_transport(
            _r2h_ipv6_blocked_snapshot()
        )

        assert result["recovered"] is True
        assert result["retry_count"] == 4
        runtime._velum_health_ready.assert_awaited_once()
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_velum_transport_recovery_version"] == 4
        assert runtime.gateway.artifacts[-1]["content"]["repair"] == (
            "retry_after_velum_ipv6_bind_repair"
        )
        assert runtime.gateway.artifacts[-1]["content"]["private_health_verified"] is True

    asyncio.run(scenario())


def test_r2h_ipv6_repair_does_not_consume_retry_when_private_health_fails():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
        runtime._velum_health_ready = AsyncMock(return_value=False)

        result = await runtime._recover_blocked_r2h_velum_transport(
            _r2h_ipv6_blocked_snapshot()
        )

        assert result["recovered"] is False
        assert result["state"] == "WAITING_FOR_VELUM_PRIVATE_HEALTH"
        assert result["retry_count"] == 3
        assert runtime.gateway.queued == []
        assert runtime.gateway.artifacts == []

    asyncio.run(scenario())


def test_r2h_4h_fetch_repair_requires_verified_velum_commit_before_version_five():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
        runtime._velum_health_snapshot = AsyncMock(return_value={
            "system": "VELUM",
            "mode": "research_replay_only",
            "runtime_provenance": {
                "git_commit": service.V14_R2H_VELUM_4H_FETCH_FIX_COMMIT,
            },
        })

        result = await runtime._recover_blocked_r2h_velum_transport(
            _r2h_ipv6_blocked_snapshot(version=4)
        )

        assert result["recovered"] is True
        assert result["retry_count"] == 5
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v14_r2h_velum_transport_recovery_version"] == 5
        artifact = runtime.gateway.artifacts[-1]["content"]
        assert artifact["repair"] == "retry_after_velum_4h_fetch_repair"
        assert artifact["verified_velum_git_commit"] == (
            service.V14_R2H_VELUM_4H_FETCH_FIX_COMMIT
        )

    asyncio.run(scenario())


def test_r2h_4h_fetch_repair_does_not_consume_retry_on_old_velum_commit():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
        runtime._velum_health_snapshot = AsyncMock(return_value={
            "system": "VELUM",
            "mode": "research_replay_only",
            "runtime_provenance": {"git_commit": "old-velum-commit"},
        })

        result = await runtime._recover_blocked_r2h_velum_transport(
            _r2h_ipv6_blocked_snapshot(version=4)
        )

        assert result["recovered"] is False
        assert result["state"] == "WAITING_FOR_VELUM_4H_FETCH_FIX"
        assert result["retry_count"] == 4
        assert runtime.gateway.queued == []
        assert runtime.gateway.artifacts == []

    asyncio.run(scenario())


def test_r2h_transport_recovery_version_five_is_terminal():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime.velum_base_url = "http://rhen-velum.railway.internal:8080"
        runtime._velum_health_snapshot = AsyncMock()

        result = await runtime._recover_blocked_r2h_velum_transport(
            _r2h_ipv6_blocked_snapshot(version=5)
        )

        assert result is None
        runtime._velum_health_snapshot.assert_not_awaited()
        assert runtime.gateway.queued == []
        assert runtime.gateway.artifacts == []

    asyncio.run(scenario())
