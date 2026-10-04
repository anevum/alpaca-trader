from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.graen.paper_host import GraenPaperCanaryHost
from app.research_agent.strategy_grammar import build_manifest
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
    compile_candidate,
)


UTC = timezone.utc


class EventSink:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)

    async def emit_critical(self, **event):
        self.events.append(event)
        return True

    def record_broker_order(self, *_args, **_kwargs):
        return None


class CleanPaperClient:
    async def positions(self):
        return []

    async def open_orders(self):
        return []


def settings() -> Settings:
    return Settings(
        ALPACA_API_KEY="paper-key",
        ALPACA_API_SECRET="paper-secret",
        TRADING_MODE="paper",
        EXECUTION_ENABLED=True,
        LIVE_TRADING=False,
        BOT_ARMED=True,
        CRYPTO_EXECUTION_ENABLED=False,
        FOUNDATION_SHADOW_ENABLED=False,
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        CRYPTO_ALWAYS_INCLUDE="BTC/USD,ETH/USD,SOL/USD",
        CRYPTO_CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
    )


def activation_payload() -> dict:
    manifest = build_manifest(
        hypothesis_id="AUTO-PAPER-HOST-01",
        family="cross_asset_diffusion",
        mechanism="BTC information diffusion into liquid crypto followers.",
        information_source="cross_asset_returns",
        feature="lead_lag_gap",
        transformation="residualize_btc",
        regime="dispersion_bucket",
        trigger="threshold",
        entry="market_next_bar",
        exit="time_60m",
        parameters={
            "hold_minutes": 60,
            "scan_minutes": 10,
            "lookback_minutes": 30,
            "concentration_limit": 0.70,
            "leader_symbol": "BTC/USD",
            "leader_threshold": 0.0035,
            "lag_gap_threshold": 0.0015,
            "min_target_return": -0.005,
            "max_target_return": 0.0035,
            "min_breadth_positive": 3,
            "require_btc_nonnegative": False,
        },
        symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        timeframe="5m",
        falsification_statement=(
            "Reject if stressed-cost forward expectancy is nonpositive."
        ),
    )
    spec = compile_candidate(manifest).to_dict()
    return {
        "problem_id": "11111111-1111-1111-1111-111111111111",
        "graen_run_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "strategy-manifest-autonomous-v1",
        "candidate_methodology": STRATEGY_RUNNER_VERSION,
        "candidate_spec": spec,
        "shadow_checkpoint": {
            "status": "READY_FOR_PAPER",
            "candidate_id": spec["candidate_id"],
            "execution_authority": False,
            "live_promotion_authorized": False,
        },
        "shadow_activation_id": "shadow-validated-001",
    }


@pytest.fixture
def paper_env(monkeypatch):
    monkeypatch.setenv("GRAEN_PAPER_ENABLED", "true")
    monkeypatch.setenv("I_ACKNOWLEDGE_GRAEN_AUTONOMOUS_PAPER", "YES")
    monkeypatch.setenv("GRAEN_PAPER_MIN_ROUND_TRIPS", "5")
    monkeypatch.setenv("GRAEN_PAPER_MIN_INDEPENDENT_DAYS", "2")
    monkeypatch.setenv("GRAEN_PAPER_MAX_REVIEW_ROUND_TRIPS", "10")
    monkeypatch.setenv("GRAEN_PAPER_MAX_REVIEW_DAYS", "14")


def test_paper_host_is_explicitly_paper_only(paper_env, tmp_path):
    host = GraenPaperCanaryHost(
        settings(),
        CleanPaperClient(),
        EventSink(),
        token="p" * 40,
        state_path=tmp_path / "paper.json",
    )
    assert host.paper_execution_authorized is True
    assert host.live_execution_authorized is False
    assert host.settings.trading_mode == "paper"
    assert host.settings.live_execution_authorized is False
    assert host.settings.crypto_execution_enabled is False


def test_paper_host_rejects_candidate_without_shadow_pass(paper_env, tmp_path):
    async def scenario():
        host = GraenPaperCanaryHost(
            settings(),
            CleanPaperClient(),
            EventSink(),
            token="p" * 40,
            state_path=tmp_path / "paper.json",
        )
        payload = activation_payload()
        payload["shadow_checkpoint"]["status"] = "COLLECTING"
        with pytest.raises(ValueError, match="READY_FOR_PAPER"):
            await host.activate(payload)

    asyncio.run(scenario())


def test_paper_activation_is_idempotent_and_durable(paper_env, tmp_path):
    async def scenario():
        path = tmp_path / "paper.json"
        host = GraenPaperCanaryHost(
            settings(),
            CleanPaperClient(),
            EventSink(),
            token="p" * 40,
            state_path=path,
        )
        first = await host.activate(activation_payload())
        second = await host.activate(activation_payload())
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert first["activation"]["activation_id"] == second["activation"]["activation_id"]
        assert first["live_execution_authority"] is False
        assert path.exists()

        restored = GraenPaperCanaryHost(
            settings(),
            CleanPaperClient(),
            EventSink(),
            token="p" * 40,
            state_path=path,
        )
        await restored.restore()
        assert restored.activation is not None
        assert restored.activation["activation_id"] == first["activation"]["activation_id"]
        assert restored.live_execution_authorized is False

    asyncio.run(scenario())


def test_terminal_paper_pass_releases_slot_but_never_authorizes_live(
    paper_env,
    tmp_path,
):
    async def scenario():
        host = GraenPaperCanaryHost(
            settings(),
            CleanPaperClient(),
            EventSink(),
            token="p" * 40,
            state_path=tmp_path / "paper.json",
        )
        first = await host.activate(activation_payload())
        activation_id = first["activation"]["activation_id"]
        started = datetime.now(UTC) - timedelta(days=4)
        host.activation["activated_at"] = started.isoformat()
        host.rounds = {
            f"round-{index}": {
                "round_id": f"round-{index}",
                "status": "CLOSED",
                "closed_evidence": {
                    "closed_at": (
                        started + timedelta(days=index % 3)
                    ).isoformat(),
                    "net_return": "0.003"
                    if index % 4
                    else "-0.0005",
                    "entry_slippage_pct": "0.0002",
                },
            }
            for index in range(6)
        }

        async def no_events():
            return []

        async def no_reconcile():
            return None

        host.shadow.cycle = no_events
        host._reconcile = no_reconcile

        before = host.checkpoint()
        assert before["status"] == "PAPER_PASSED"
        assert before["live_execution_authorized"] is False
        assert before["promotion_authorized"] is False

        await host.cycle_once()
        assert host.activation is None
        exact = host.checkpoint_for_activation(activation_id)
        assert exact is not None
        assert exact["completed"] is True
        assert exact["checkpoint"]["status"] == "PAPER_PASSED"
        assert exact["live_execution_authorized"] is False

    asyncio.run(scenario())


def test_terminal_paper_failure_releases_slot_for_next_candidate(
    paper_env,
    tmp_path,
):
    async def scenario():
        host = GraenPaperCanaryHost(
            settings(),
            CleanPaperClient(),
            EventSink(),
            token="p" * 40,
            state_path=tmp_path / "paper.json",
        )
        first = await host.activate(activation_payload())
        activation_id = first["activation"]["activation_id"]
        host.activation["activated_at"] = (
            datetime.now(UTC) - timedelta(days=20)
        ).isoformat()
        host.rounds = {}

        async def no_events():
            return []

        async def no_reconcile():
            return None

        host.shadow.cycle = no_events
        host._reconcile = no_reconcile
        assert host.checkpoint()["status"] == "PAPER_REJECTED"

        await host.cycle_once()
        assert host.activation is None
        assert host.checkpoint_for_activation(activation_id)["checkpoint"]["status"] == "PAPER_REJECTED"

        payload = activation_payload()
        payload["problem_id"] = "33333333-3333-3333-3333-333333333333"
        payload["graen_run_id"] = "44444444-4444-4444-4444-444444444444"
        second = await host.activate(payload)
        assert second["duplicate"] is False
        assert host.activation is not None
        assert host.activation["activation_id"] != activation_id

    asyncio.run(scenario())



def test_paper_activation_identity_ignores_transient_run_id():
    first = activation_payload()
    second = activation_payload()
    second["graen_run_id"] = "55555555-5555-5555-5555-555555555555"
    assert (
        GraenPaperCanaryHost._activation_id(first)
        == GraenPaperCanaryHost._activation_id(second)
    )
