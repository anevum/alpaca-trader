from datetime import datetime, timedelta, timezone

import asyncio
import pytest

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
from graen.crypto.btc_consensus_trend_v14_r2g import (
    METHODOLOGY_VERSION as V14_R2G_METHODOLOGY_VERSION,
    candidate_spec as v14_r2g_candidate_spec,
)
from graen.crypto.btc_r2h_breakout_v15 import (
    METHODOLOGY_VERSION as V15_METHODOLOGY_VERSION,
    candidate_spec as v15_candidate_spec,
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


def _r2f_runtime_with_rows(rows):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2f_activation())

    class FakeMarketData:
        async def bars_many(self, *args, **kwargs):
            return {"BTC/USD": rows}

    runtime.market_data = FakeMarketData()
    return runtime


@pytest.mark.parametrize("close", [float("nan"), float("inf"), float("-inf"), 0, -1, None, "bad"])
def test_r2f_invalid_completed_price_cannot_mutate_evidence(close):
    rows = _daily_rows(datetime(2026, 3, 1, tzinfo=UTC), 220)
    rows[200]["c"] = close
    runtime = _r2f_runtime_with_rows(rows)
    before = runtime.snapshot()
    with pytest.raises(ValueError, match="r2f_daily_close_invalid"):
        asyncio.run(runtime.cycle(now=datetime(2026, 10, 3, 1, tzinfo=UTC)))
    assert runtime.snapshot() == before


@pytest.mark.parametrize("defect,reason", [
    ("timestamp", "timestamp_invalid"),
    ("duplicate", "duplicate_conflict"),
    ("gap", "calendar_gap"),
    ("empty", "history_incomplete"),
    ("stale", "data_stale"),
])
def test_r2f_invalid_daily_corpus_fails_closed(defect, reason):
    rows = _daily_rows(datetime(2026, 3, 1, tzinfo=UTC), 220)
    if defect == "timestamp":
        rows[200]["t"] = "invalid"
    elif defect == "duplicate":
        rows.append({**rows[200], "c": rows[200]["c"] + 1})
    elif defect == "gap":
        del rows[200]
    elif defect == "empty":
        rows.clear()
    elif defect == "stale":
        del rows[210:]
    runtime = _r2f_runtime_with_rows(rows)
    before = runtime.snapshot()
    with pytest.raises(ValueError, match="r2f_daily_" + reason):
        asyncio.run(runtime.cycle(now=datetime(2026, 10, 3, 1, tzinfo=UTC)))
    assert runtime.snapshot() == before


def test_r2f_identical_duplicates_and_incomplete_prices_do_not_change_marks():
    async def scenario():
        now = datetime(2026, 10, 3, 1, tzinfo=UTC)
        rows = _daily_rows(datetime(2026, 3, 1, tzinfo=UTC), 220)
        clean = _r2f_runtime_with_rows(rows)
        expected = await clean.cycle(now=now)
        noisy_rows = [dict(row) for row in rows]
        for row in noisy_rows:
            if datetime.fromisoformat(row["t"].replace("Z", "+00:00")) + timedelta(days=1) > now:
                row["c"] = float("nan")
        noisy_rows.append(dict(noisy_rows[200]))
        noisy = _r2f_runtime_with_rows(list(reversed(noisy_rows)))
        assert await noisy.cycle(now=now) == expected
        assert noisy.snapshot() == clean.snapshot()
        assert len(noisy.r2f_daily_marks) == 1
        assert await noisy.cycle(now=now) == []
        assert len(noisy.r2f_daily_marks) == 1
    asyncio.run(scenario())


def test_r2f_gap_repair_resumes_exactly_once_after_restart():
    async def scenario():
        rows = _daily_rows(datetime(2026, 3, 1, tzinfo=UTC), 220)
        runtime = _r2f_runtime_with_rows(rows)
        await runtime.cycle(now=datetime(2026, 10, 2, 1, tzinfo=UTC))
        saved = runtime.snapshot()
        missing = rows.pop(215)  # October 2: first scoreable full day.
        with pytest.raises(ValueError, match="calendar_gap"):
            await runtime.cycle(now=datetime(2026, 10, 4, 1, tzinfo=UTC))
        assert runtime.snapshot() == saved
        rows.append(missing)
        restored = _r2f_runtime_with_rows(rows)
        restored.restore(saved)
        await restored.cycle(now=datetime(2026, 10, 4, 1, tzinfo=UTC))
        assert [mark["bar_end"] for mark in restored.r2f_daily_marks] == [
            "2026-10-03T00:00:00+00:00", "2026-10-04T00:00:00+00:00",
        ]
        assert await restored.cycle(now=datetime(2026, 10, 4, 1, tzinfo=UTC)) == []
    asyncio.run(scenario())


def test_r2f_cannot_skip_lost_history_after_extended_outage():
    runtime = _r2f_runtime_with_rows(
        _daily_rows(datetime(2026, 3, 1, tzinfo=UTC), 220)
    )
    runtime.last_processed_bar_end = datetime(2026, 2, 28, tzinfo=UTC)
    before = runtime.snapshot()
    with pytest.raises(ValueError, match="r2f_daily_resume_gap"):
        asyncio.run(runtime.cycle(now=datetime(2026, 10, 3, 1, tzinfo=UTC)))
    assert runtime.snapshot() == before



def r2g_activation(*, activated_at: datetime | None = None) -> dict:
    spec = v14_r2g_candidate_spec().to_dict()
    return {
        **activation(),
        "activation_id": "activation-v14-r2g-001",
        "campaign_id": "v14-r2g-btc-daily-consensus-trend",
        "candidate_methodology": V14_R2G_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "velum_artifact_id": "",
        "evidence_phase": "FORWARD_SHADOW",
        "activated_at": (
            activated_at or datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        ).isoformat(),
    }


def _r2g_runtime_with_rows(rows):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2g_activation())

    class FakeMarketData:
        async def bars_many(self, *args, **kwargs):
            return {"BTC/USD": rows}

    runtime.market_data = FakeMarketData()
    return runtime


def test_r2g_shadow_accepts_consensus_candidate_without_execution_authority():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2g_activation())

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime._active_spec().momentum_lookback_days == 180
    assert runtime._active_spec().sma_window_days == 250
    assert runtime.status()["candidate_methodology"] == V14_R2G_METHODOLOGY_VERSION
    assert runtime.status()["r2g_daily_mark_count"] == 0
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False


def test_r2g_shadow_baseline_and_signal_are_fresh_and_causal():
    async def scenario():
        activated_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        rows = _daily_rows(datetime(2026, 1, 1, tzinfo=UTC), 300)
        runtime = _r2g_runtime_with_rows(rows)
        runtime.activate(r2g_activation(activated_at=activated_at))

        first = await runtime.cycle(
            now=datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
        )
        assert [
            event for event in first
            if event["event_type"] == "graen_candidate_shadow_baseline"
        ]
        assert [
            event for event in first
            if event["event_type"] == "graen_candidate_shadow_daily_mark"
        ] == []
        assert runtime.r2g_daily_marks == []

        second = await runtime.cycle(
            now=datetime(2026, 10, 3, 1, 0, tzinfo=UTC)
        )
        marks = [
            event for event in second
            if event["event_type"] == "graen_candidate_shadow_daily_mark"
        ]
        assert len(marks) == 1
        mark = runtime.r2g_daily_marks[0]
        assert mark["candidate_methodology"] == V14_R2G_METHODOLOGY_VERSION
        assert mark["momentum_lookback_days"] == 180
        assert mark["sma_window_days"] == 250
        assert mark["signal_rule"] == "OR"
        assert mark["momentum_positive"] is True
        assert mark["above_sma"] is True
        assert mark["position"] == 1.0
        assert mark["fresh_evidence"] is True
        assert mark["execution_authority"] is False

    asyncio.run(scenario())


def test_r2g_shadow_state_is_restart_restorable():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2g_activation())
    runtime.r2g_baseline_end = datetime(2026, 10, 2, tzinfo=UTC)
    runtime.last_processed_bar_end = datetime(2026, 10, 3, tzinfo=UTC)
    runtime.r2g_shadow_position = 1.0
    runtime.r2g_daily_marks.append(
        {
            "bar_end": "2026-10-03T00:00:00+00:00",
            "position": 1.0,
            "turnover_units": 1.0,
            "stressed_cost_net_return": 0.01,
        }
    )

    restored = CandidateForwardShadow(settings())
    restored.restore(runtime.snapshot())

    assert restored.r2g_shadow_position == 1.0
    assert len(restored.r2g_daily_marks) == 1
    assert restored.r2g_baseline_end == runtime.r2g_baseline_end
    assert restored._candidate_methodology() == V14_R2G_METHODOLOGY_VERSION
    assert restored.execution_authority is False


def test_r2g_shadow_uses_same_fresh_review_gate_as_r2f():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(r2g_activation())
    start = datetime(2026, 10, 2, tzinfo=UTC)
    runtime.r2g_baseline_end = start
    runtime.last_processed_bar_end = start
    runtime.r2g_shadow_position = 1.0

    for index in range(30):
        value = 0.003 if index % 5 else -0.001
        runtime.r2g_daily_marks.append(
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
    assert checkpoint["comparison_against"] == "V14-R2F-BTC-MOM-180D"
    assert checkpoint["promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False


def test_r2g_daily_gap_fails_closed_without_mutating_evidence():
    rows = _daily_rows(datetime(2026, 1, 1, tzinfo=UTC), 300)
    del rows[260]
    runtime = _r2g_runtime_with_rows(rows)
    before = runtime.snapshot()
    with pytest.raises(ValueError, match="r2g_daily_calendar_gap"):
        asyncio.run(
            runtime.cycle(now=datetime(2026, 10, 3, 1, tzinfo=UTC))
        )
    assert runtime.snapshot() == before


@pytest.mark.parametrize("candidate,cursor_index", [
    ("r2f", 0), ("r2f", 100), ("r2f", 179),
    ("r2g", 0), ("r2g", 100), ("r2g", 249),
])
def test_daily_restart_cannot_skip_unseen_days_without_signal_warmup(candidate, cursor_index):
    start = datetime(2025, 9, 1, tzinfo=UTC)
    rows = _daily_rows(start, 400)
    factory = _r2f_runtime_with_rows if candidate == "r2f" else _r2g_runtime_with_rows
    runtime = factory(rows)
    runtime.last_processed_bar_end = start + timedelta(days=cursor_index + 1)
    before = runtime.snapshot()
    with pytest.raises(ValueError, match=candidate + "_daily_resume_warmup_incomplete"):
        asyncio.run(runtime.cycle(now=start + timedelta(days=400, hours=1)))
    assert runtime.snapshot() == before


@pytest.mark.parametrize("candidate,cursor_index", [("r2f", 180), ("r2g", 250)])
def test_daily_restart_at_exact_warmup_boundary_scores_every_unseen_day(candidate, cursor_index):
    async def scenario():
        start = datetime(2025, 9, 1, tzinfo=UTC)
        rows = _daily_rows(start, 400)
        factory = _r2f_runtime_with_rows if candidate == "r2f" else _r2g_runtime_with_rows
        runtime = factory(rows)
        cursor = start + timedelta(days=cursor_index + 1)
        runtime.last_processed_bar_end = cursor
        await runtime.cycle(now=start + timedelta(days=400, hours=1))
        marks = getattr(runtime, candidate + "_daily_marks")
        assert len(marks) == 400 - cursor_index - 1
        assert datetime.fromisoformat(marks[0]["bar_start"]) == cursor
        assert [datetime.fromisoformat(mark["bar_end"]) for mark in marks] == [
            cursor + timedelta(days=offset + 1) for offset in range(len(marks))
        ]
        assert await runtime.cycle(now=start + timedelta(days=400, hours=1)) == []
    asyncio.run(scenario())



def v15_activation(*, activated_at: datetime | None = None) -> dict:
    spec = v15_candidate_spec().to_dict()
    return {
        **activation(),
        "activation_id": "activation-v15-001",
        "campaign_id": "v15-r1-btc-r2h-breakout",
        "candidate_methodology": V15_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "velum_artifact_id": "artifact-v15-holdout",
        "evidence_phase": "FORWARD_SHADOW",
        "activated_at": (
            activated_at or datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        ).isoformat(),
    }


def _v15_rows(*, count: int = 1560):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for index in range(count):
        next_price = price * 1.001
        rows.append(
            {
                "t": (start + timedelta(hours=4 * index)).isoformat(),
                "o": price,
                "h": max(price, next_price),
                "l": min(price, next_price),
                "c": next_price,
            }
        )
        price = next_price
    return rows


def _v15_runtime_with_rows(rows, *, activated_at: datetime):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(v15_activation(activated_at=activated_at))

    class FakeMarketData:
        async def bars_many(self, symbols, *, timeframe, lookback_minutes):
            assert symbols == ["BTC/USD"]
            assert timeframe == "4Hour"
            assert lookback_minutes >= 1500 * 240
            return {"BTC/USD": rows}

    runtime.market_data = FakeMarketData()
    return runtime


def test_v15_shadow_accepts_only_frozen_candidate_without_execution_authority():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(v15_activation())

    assert runtime._symbols() == ("BTC/USD",)
    assert runtime._active_spec().candidate_id == "V15-R1-BTC-R2H-BREAKOUT-42-15"
    assert runtime.status()["candidate_methodology"] == V15_METHODOLOGY_VERSION
    assert runtime.status()["v15_4h_mark_count"] == 0
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False


def test_v15_shadow_starts_flat_and_never_backfills_pre_activation_position():
    async def scenario():
        rows = _v15_rows()
        activation_index = 1520
        activated_at = (
            datetime.fromisoformat(rows[activation_index]["t"])
            + timedelta(hours=2)
        )
        runtime = _v15_runtime_with_rows(
            rows,
            activated_at=activated_at,
        )

        first_now = (
            datetime.fromisoformat(rows[activation_index]["t"])
            + timedelta(hours=4, seconds=1)
        )
        first = await runtime.cycle(now=first_now)
        assert [
            event for event in first
            if event["event_type"] == "graen_candidate_shadow_baseline"
        ]
        assert runtime.v15_4h_marks == []
        assert runtime.v15_shadow_position == 0.0
        assert runtime.v15_entry_price is None

        second_now = (
            datetime.fromisoformat(rows[activation_index + 1]["t"])
            + timedelta(hours=4, seconds=1)
        )
        second = await runtime.cycle(now=second_now)
        marks = [
            event for event in second
            if event["event_type"] == "graen_candidate_shadow_4h_mark"
        ]
        entries = [
            event for event in second
            if event["event_type"] == "graen_candidate_shadow_entry"
        ]
        assert len(marks) == 1
        assert len(entries) == 1
        mark = runtime.v15_4h_marks[0]
        assert mark["fresh_evidence"] is True
        assert mark["execution_model"] == "prior_completed_signal_next_4h_open"
        assert mark["entry_fired"] is True
        assert mark["entry_price"] == rows[activation_index + 1]["o"]
        assert runtime.v15_shadow_position == 1.0
        assert runtime.entry_count == 1
        assert runtime.execution_authority is False
        assert runtime.broker_orders_possible is False

    asyncio.run(scenario())


def test_v15_shadow_gap_fails_closed_before_evidence_mutation():
    rows = _v15_rows()
    del rows[1510]
    now = datetime.fromisoformat(rows[-1]["t"]) + timedelta(hours=4)
    with pytest.raises(ValueError, match="v15_4h_calendar_gap"):
        candidate_shadow._v15_completed_4h_rows(rows, now=now)


def test_v15_shadow_state_is_restart_restorable_and_still_broker_proof():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(v15_activation())
    runtime.v15_baseline_end = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    runtime.last_processed_bar_end = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)
    runtime.v15_shadow_position = 1.0
    runtime.v15_entry_price = 100.0
    runtime.entry_count = 1
    runtime.v15_4h_marks.append(
        {
            "bar_end": "2026-10-05T16:00:00+00:00",
            "position": 1.0,
            "turnover_units": 1.0,
            "stressed_cost_net_return": 0.01,
        }
    )

    restored = CandidateForwardShadow(settings())
    restored.restore(runtime.snapshot())

    assert restored.v15_shadow_position == 1.0
    assert restored.v15_entry_price == 100.0
    assert len(restored.v15_4h_marks) == 1
    assert restored.v15_baseline_end == runtime.v15_baseline_end
    assert restored.execution_authority is False
    assert restored.broker_orders_possible is False


def test_v15_shadow_ready_gate_still_requires_human_review():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(v15_activation())
    runtime.entry_count = 1
    runtime.v15_shadow_position = 1.0
    runtime.v15_entry_price = 100.0
    start = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    for index in range(42):
        runtime.v15_4h_marks.append(
            {
                "bar_end": (
                    start + timedelta(hours=4 * (index + 1))
                ).isoformat(),
                "position": 1.0 if index >= 1 else 0.0,
                "turnover_units": 1.0 if index == 1 else 0.0,
                "stressed_cost_net_return": (
                    -0.003 if index == 0 else 0.0005
                ),
            }
        )

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["fresh_4h_mark_count"] == 42
    assert checkpoint["entry_count"] == 1
    assert checkpoint["cumulative_stressed_cost_return"] > 0
    assert checkpoint["promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False
