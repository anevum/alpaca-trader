from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.graen import research_executor_service as service
from graen.crypto.btc_4h_consensus_v14_r2h import (
    COST_SCENARIOS,
    MOMENTUM_LOOKBACK_BARS,
    SMA_WINDOW_BARS,
    campaign_manifest,
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


class FakeGateway:
    configured = True

    def __init__(self):
        self.queued = []

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
