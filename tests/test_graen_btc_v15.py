from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.graen import research_executor_service as service
from graen.crypto.btc_r2h_breakout_v15 import (
    ENTRY_LOOKBACK_BARS,
    EXIT_LOOKBACK_BARS,
    HARD_STOP_PCT,
    HOLDOUT_END,
    HOLDOUT_START,
    MOMENTUM_LOOKBACK_BARS,
    SMA_WINDOW_BARS,
    campaign_manifest,
    candidate_spec,
    evaluate_btc_r2h_breakout,
    spec_from_dict,
)

UTC = timezone.utc


def _bars(count: int = 5000):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for index in range(count):
        cycle = index % 360
        drift = 0.0012 if cycle < 230 else -0.0008
        next_price = max(price * (1.0 + drift), 1.0)
        rows.append(
            {
                "t": (start + timedelta(hours=4 * index)).isoformat(),
                "o": price,
                "h": max(price, next_price) * 1.0015,
                "l": min(price, next_price) * 0.9985,
                "c": next_price,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_v15_candidate_is_frozen_and_research_only():
    spec = candidate_spec()
    manifest = campaign_manifest()

    assert spec.candidate_id == "V15-R1-BTC-R2H-BREAKOUT-42-15"
    assert spec.regime_momentum_bars == MOMENTUM_LOOKBACK_BARS
    assert spec.regime_sma_bars == SMA_WINDOW_BARS
    assert spec.entry_lookback_bars == ENTRY_LOOKBACK_BARS == 42
    assert spec.exit_lookback_bars == EXIT_LOOKBACK_BARS == 15
    assert spec.hard_stop_pct == HARD_STOP_PCT == 0.05

    assert manifest["selection_protocol"]["frozen_before_holdout"] is True
    assert manifest["selection_protocol"]["holdout_window"]["start"] == HOLDOUT_START.isoformat()
    assert manifest["selection_protocol"]["holdout_window"]["end"] == HOLDOUT_END.isoformat()
    assert manifest["research_only"] is True
    assert manifest["promotion_eligible"] is False
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_v15_evaluation_emits_frozen_development_and_holdout_evidence():
    result = evaluate_btc_r2h_breakout(_bars())

    assert result["methodology_version"] == "graen-btc-r2h-breakout-v15-r1"
    assert result["candidate_spec"]["entry_lookback_bars"] == 42
    assert result["candidate_spec"]["exit_lookback_bars"] == 15
    assert len(result["development"]["grid"]) == 9
    assert set(result["holdout"]["scenarios"]) == {
        "taker_25bp",
        "taker_stress_30bp",
        "severe_stress_50bp",
    }
    assert len(result["holdout"]["neighborhood"]["cells"]) == 9
    assert result["holdout"]["one_bar_execution_delay"]["signal_delay_bars"] == 1
    assert result["research_only"] is True
    assert result["promotion_eligible"] is False
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False


def test_v15_rejects_insufficient_history():
    with pytest.raises(ValueError, match="v15_r1_corpus_too_small"):
        evaluate_btc_r2h_breakout(
            {
                "BTC/USD": [
                    {
                        "t": "2026-01-01T00:00:00+00:00",
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100,
                    }
                ]
            }
        )


class _Gateway:
    configured = True

    def __init__(self):
        self.queued = []
        self.artifacts = []

    async def queue_research_stage(self, **kwargs):
        self.queued.append(kwargs)
        return {"ok": True, "problem": {"problem_id": kwargs["problem_id"]}}

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {
            "artifact": {
                "artifact_id": f"artifact-{len(self.artifacts)}"
            }
        }


def test_r2h_velum_pass_queues_v15_without_execution_authority():
    async def scenario():
        problem_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        origin_run_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        snapshot = {
            "problems": [
                {
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": origin_run_id,
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "started_at": "2026-10-04T16:50:45+00:00",
                    "methodology_version": service.V14_R2H_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": "V14_R2H_VELUM_PASS",
                        "decision": "ACTIVATE_4H_FORWARD_SHADOW",
                        "velum_artifact_id": "artifact-r2h-velum",
                    },
                }
            ],
            "artifacts": [],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = _Gateway()

        result = await runtime._recover_v14_r2h_velum_pass_into_v15(snapshot)

        assert result["recovered"] is True
        assert result["next_research_stage"] == service.V15_STAGE
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["live_execution_authorized"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.V15_STAGE
        metadata = runtime.gateway.queued[-1]["metadata"]
        assert metadata["v15_campaign_id"] == service.V15_CAMPAIGN_ID
        assert metadata["v15_origin_v14_r2h_run_id"] == origin_run_id
        assert metadata["v15_origin_v14_r2h_velum_artifact_id"] == "artifact-r2h-velum"
        assert metadata["v15_candidate_spec"]["candidate_id"] == (
            "V15-R1-BTC-R2H-BREAKOUT-42-15"
        )

    asyncio.run(scenario())



def test_v15_spec_parser_rejects_parameter_mutation():
    frozen = candidate_spec().to_dict()
    assert spec_from_dict(frozen) == candidate_spec()

    altered = dict(frozen)
    altered["entry_lookback_bars"] = 41
    with pytest.raises(ValueError, match="v15_candidate_spec_not_frozen"):
        spec_from_dict(altered)


def test_v15_pass_activates_only_broker_proof_forward_shadow():
    async def scenario():
        problem_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        run_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        spec = candidate_spec().to_dict()
        snapshot = {
            "problems": [
                {
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {},
                }
            ],
            "runs": [
                {
                    "run_id": run_id,
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "started_at": "2026-10-05T11:00:00+00:00",
                    "methodology_version": service.V15_METHODOLOGY_VERSION,
                    "result_summary": {
                        "state": (
                            "V15_R1_HOLDOUT_PASS_READY_FOR_"
                            "ISOLATED_FORWARD_PAPER"
                        ),
                        "decision": "START_ISOLATED_FORWARD_PAPER",
                        "candidate_spec": spec,
                        "result_artifact_id": "artifact-v15-result",
                    },
                }
            ],
            "artifacts": [],
        }
        runtime = service.GraenResearchExecutor()
        runtime.gateway = _Gateway()
        runtime.shadow_base_url = "http://shadow"
        runtime.shadow_token = "x" * 32
        runtime._activate_forward_shadow = AsyncMock(
            return_value={
                "activation": {
                    "activation_id": "activation-v15-shadow"
                },
                "duplicate": False,
                "execution_authority": False,
                "broker_orders_possible": False,
                "promotion_authorized": False,
            }
        )

        result = await runtime._recover_v15_pass_into_forward_shadow(
            snapshot
        )

        assert result["recovered"] is True
        assert result["candidate_id"] == spec["candidate_id"]
        assert result["activation_id"] == "activation-v15-shadow"
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["live_execution_authorized"] is False

        runtime._activate_forward_shadow.assert_awaited_once()
        kwargs = runtime._activate_forward_shadow.await_args.kwargs
        assert kwargs["candidate_methodology"] == (
            service.V15_METHODOLOGY_VERSION
        )
        assert kwargs["candidate_spec"] == spec
        assert kwargs["evidence_phase"] == "FORWARD_SHADOW"

        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == (
            "CRYPTO_V15_R1_FORWARD_SHADOW_ACTIVATION"
        )
        content = artifact["content"]
        assert content["starts_flat_after_activation"] is True
        assert content["historical_holdout_counts_as_fresh"] is False
        assert content["promotion_authorized"] is False
        assert content["execution_authority"] is False
        assert content["broker_orders_possible"] is False
        assert content["live_execution_authorized"] is False

    asyncio.run(scenario())
