from datetime import datetime, timedelta, timezone

from app.config import Settings
import graen.crypto.candidate_shadow as candidate_shadow
from graen.crypto.activity_shock_v9 import (
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    candidate_specs,
)
from graen.crypto.trend_pullback_v10 import (
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    candidate_specs as v10_candidate_specs,
)
from graen.crypto.btc_trend_pullback_v11 import (
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    candidate_specs as v11_candidate_specs,
)
from graen.crypto.btc_mechanisms_v12 import (
    METHODOLOGY_VERSION as V12_METHODOLOGY_VERSION,
    candidate_specs as v12_candidate_specs,
)
from graen.crypto.btc_hypotheses_v13 import (
    METHODOLOGY_VERSION as V13_METHODOLOGY_VERSION,
    candidate_specs as v13_candidate_specs,
)
from graen.crypto.btc_slow_momentum_v14_r2f import (
    METHODOLOGY_VERSION as V14_R2F_METHODOLOGY_VERSION,
    candidate_spec as v14_r2f_candidate_spec,
)
from graen.crypto.candidate_shadow import CandidateForwardShadow


UTC = timezone.utc


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        CRYPTO_ALWAYS_INCLUDE="BTC/USD,ETH/USD,SOL/USD",
        CRYPTO_CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        EXECUTION_ENABLED=False,
        LIVE_TRADING=False,
        BOT_ARMED=False,
        CRYPTO_EXECUTION_ENABLED=False,
    )


def activation() -> dict:
    spec = candidate_specs()[0].to_dict()
    return {
        "schema_version": "graen.candidate_shadow.activation.v1",
        "activation_id": "activation-test-001",
        "problem_id": "11111111-1111-1111-1111-111111111111",
        "graen_run_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "crypto-activity-shock-v9",
        "epoch_index": 0,
        "generation": 1,
        "candidate_methodology": V9_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "velum_artifact_id": "artifact-velum",
        "activated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "promotion_authorized": False,
        "execution_authority": False,
        "broker_orders_possible": False,
    }


def test_candidate_shadow_is_broker_proof_and_restart_restorable():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    assert runtime.active is True
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False
    assert runtime.status()["crypto_execution_enabled"] is False

    snapshot = runtime.snapshot()
    restored = CandidateForwardShadow(settings())
    restored.restore(snapshot)

    assert restored.status()["activation"]["activation_id"] == "activation-test-001"
    assert restored.execution_authority is False
    assert restored.broker_orders_possible is False


def test_candidate_shadow_negative_forward_sample_rejects_without_promotion(monkeypatch):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    monkeypatch.setattr(
        candidate_shadow,
        "_moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 1.0},
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD")
    for index in range(60):
        runtime.closed.append({
            "symbol": symbols[index % len(symbols)],
            "exit_at": (start + timedelta(days=index % 30)).isoformat(),
            "stressed_cost_net_return": -0.001,
        })

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "SHADOW_REJECTED"
    assert checkpoint["trade_count"] == 60
    assert checkpoint["promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False


def test_candidate_shadow_positive_forward_sample_requires_fixed_gate(monkeypatch):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    monkeypatch.setattr(
        candidate_shadow,
        "_moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 0.01},
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD")
    for index in range(30):
        runtime.closed.append({
            "symbol": symbols[index % len(symbols)],
            "exit_at": (start + timedelta(days=index)).isoformat(),
            "stressed_cost_net_return": 0.002 if index % 5 else -0.001,
        })

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["independent_day_blocks"] == 30
    assert checkpoint["promotion_authorized"] is False


def test_candidate_shadow_accepts_v10_without_execution_authority():
    spec = v10_candidate_specs()[0].to_dict()
    payload = {
        **activation(),
        "activation_id": "activation-v10-001",
        "campaign_id": "crypto-trend-pullback-v10",
        "candidate_methodology": V10_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
    }
    runtime = CandidateForwardShadow(settings())
    runtime.activate(payload)

    assert runtime.active is True
    assert runtime.status()["candidate_methodology"] == V10_METHODOLOGY_VERSION
    assert runtime.status()["candidate_id"] == spec["candidate_id"]
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False


def test_candidate_shadow_accepts_v11_as_btc_only_without_execution_authority(monkeypatch):
    spec = v11_candidate_specs()[0].to_dict()
    payload = {
        **activation(),
        "activation_id": "activation-v11-001",
        "campaign_id": "btc-trend-pullback-forward-v11",
        "candidate_methodology": V11_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
    }
    runtime = CandidateForwardShadow(settings())
    runtime.activate(payload)

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime.status()["candidate_methodology"] == V11_METHODOLOGY_VERSION
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False

    monkeypatch.setattr(
        candidate_shadow,
        "_moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 0.01},
    )
    start = datetime(2026, 10, 3, tzinfo=UTC)
    for index in range(30):
        runtime.closed.append({
            "symbol": "BTC/USD",
            "exit_at": (start + timedelta(days=index)).isoformat(),
            "stressed_cost_net_return": 0.002 if index % 5 else -0.001,
        })

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["symbol_concentration_max_share"] == 1.0
    assert checkpoint["ready_gate"]["symbol_concentration_max_share"] == 1.0
    assert checkpoint["promotion_authorized"] is False


def test_candidate_shadow_accepts_v12_as_btc_only_without_execution_authority():
    spec = v12_candidate_specs()[0].to_dict()
    payload = {
        **activation(),
        "activation_id": "activation-v12-001",
        "campaign_id": "btc-mechanism-tournament-v12",
        "candidate_methodology": V12_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
    }
    runtime = CandidateForwardShadow(settings())
    runtime.activate(payload)

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime.status()["candidate_methodology"] == V12_METHODOLOGY_VERSION
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False


def test_candidate_shadow_accepts_v13_btc_phase_without_execution_authority():
    spec = v13_candidate_specs()[0].to_dict()
    payload = {
        **activation(),
        "activation_id": "activation-v13-validation-001",
        "campaign_id": "btc-hypothesis-tournament-v13",
        "candidate_methodology": V13_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "evidence_phase": "VALIDATION",
    }
    runtime = CandidateForwardShadow(settings())
    runtime.activate(payload)

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime._active_spec().candidate_id == spec["candidate_id"]
    assert runtime.status()["candidate_methodology"] == V13_METHODOLOGY_VERSION
    checkpoint = runtime._checkpoint()
    assert checkpoint["evidence_phase"] == "VALIDATION"
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False
    assert checkpoint["promotion_authorized"] is False

    # Regression: V12/V13 specs do not have activity_lookback_hours.
    _, series = runtime._build_active_series(
        {"BTC/USD": [], "ETH/USD": [], "SOL/USD": []},
        latest_end=datetime(2026, 10, 3, tzinfo=UTC),
    )
    assert isinstance(series, dict)



def r2f_activation(*, activated_at: datetime | None = None) -> dict:
    spec = v14_r2f_candidate_spec().to_dict()
    return {
        **activation(),
        "activation_id": "activation-v14-r2f-001",
        "campaign_id": "v14-r2f-btc-daily-180d-momentum",
        "candidate_methodology": V14_R2F_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "velum_artifact_id": "",
        "evidence_phase": "FORWARD_SHADOW",
        "activated_at": (
            activated_at or datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        ).isoformat(),
    }


def _daily_rows(start: datetime, count: int):
    rows = []
    price = 100.0
    for index in range(count):
        next_price = price * 1.002
        rows.append(
            {
                "t": (start + timedelta(days=index)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "o": price,
                "h": next_price * 1.001,
                "l": price * 0.999,
                "c": next_price,
                "v": 10.0,
            }
        )
        price = next_price
    return rows


def test_r2f_shadow_accepts_daily_candidate_without_execution_authority():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2f_activation())

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime._active_spec().lookback_days == 180
    assert runtime.status()["candidate_methodology"] == V14_R2F_METHODOLOGY_VERSION
    assert runtime.status()["r2f_daily_mark_count"] == 0
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False


def test_r2f_shadow_baseline_prevents_historical_backfill(monkeypatch):
    async def scenario():
        runtime = CandidateForwardShadow(settings())
        activated_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        runtime.activate(r2f_activation(activated_at=activated_at))

        rows = _daily_rows(
            datetime(2026, 3, 1, tzinfo=UTC),
            220,
        )

        class FakeMarketData:
            async def bars_many(self, symbols, *, timeframe, lookback_minutes):
                assert symbols == ["BTC/USD"]
                assert timeframe == "1Day"
                assert lookback_minutes >= 400 * 1440
                return {"BTC/USD": rows}

        runtime.market_data = FakeMarketData()

        first = await runtime.cycle(
            now=datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
        )
        baseline_events = [
            event
            for event in first
            if event["event_type"] == "graen_candidate_shadow_baseline"
        ]
        mark_events = [
            event
            for event in first
            if event["event_type"] == "graen_candidate_shadow_daily_mark"
        ]
        assert len(baseline_events) == 1
        assert mark_events == []
        assert runtime.r2f_daily_marks == []
        assert runtime.r2f_baseline_end > activated_at

        second = await runtime.cycle(
            now=datetime(2026, 10, 3, 1, 0, tzinfo=UTC)
        )
        marks = [
            event
            for event in second
            if event["event_type"] == "graen_candidate_shadow_daily_mark"
        ]
        assert len(marks) == 1
        assert len(runtime.r2f_daily_marks) == 1
        assert (
            datetime.fromisoformat(
                runtime.r2f_daily_marks[0]["bar_start"]
            )
            >= runtime.r2f_baseline_end
        )
        assert runtime.r2f_daily_marks[0]["fresh_evidence"] is True
        assert runtime.r2f_daily_marks[0]["execution_authority"] is False

    import asyncio
    asyncio.run(scenario())


def test_r2f_shadow_state_is_restart_restorable():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2f_activation())
    runtime.r2f_baseline_end = datetime(
        2026, 10, 2, tzinfo=UTC
    )
    runtime.last_processed_bar_end = datetime(
        2026, 10, 3, tzinfo=UTC
    )
    runtime.r2f_shadow_position = 1.0
    runtime.r2f_daily_marks.append(
        {
            "bar_end": "2026-10-03T00:00:00+00:00",
            "position": 1.0,
            "turnover_units": 1.0,
            "stressed_cost_net_return": 0.01,
        }
    )

    restored = CandidateForwardShadow(settings())
    restored.restore(runtime.snapshot())

    assert restored.r2f_shadow_position == 1.0
    assert len(restored.r2f_daily_marks) == 1
    assert restored.r2f_baseline_end == runtime.r2f_baseline_end
    assert restored.execution_authority is False


def test_r2f_shadow_positive_fresh_daily_marks_only_reach_human_review():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2f_activation())
    start = datetime(2026, 10, 2, tzinfo=UTC)
    runtime.r2f_baseline_end = start
    runtime.last_processed_bar_end = start
    runtime.r2f_shadow_position = 1.0

    for index in range(30):
        value = 0.003 if index % 5 else -0.001
        runtime.r2f_daily_marks.append(
            {
                "bar_end": (start + timedelta(days=index + 1)).isoformat(),
                "position": 1.0,
                "turnover_units": 0.0,
                "stressed_cost_net_return": value,
            }
        )

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["fresh_daily_mark_count"] == 30
    assert checkpoint["exposed_day_count"] == 30
    assert checkpoint["cumulative_stressed_cost_return"] > 0
    assert checkpoint["annualized_daily_sharpe"] > 0
    assert checkpoint["adaptive_historical_evidence_counts_as_fresh"] is False
    assert checkpoint["promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False
