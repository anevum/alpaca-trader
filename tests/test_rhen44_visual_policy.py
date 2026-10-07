import asyncio
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.adaptive_policy import PolicyLibrary, PolicyContext, AdaptivePolicyController
from app.capital_governor import govern_capital
from app.command_visuals.forecast_projection import valid_forecast
from app.command_visuals.replay_projection import replay_frame
from app.command_visuals.publisher import LivePublisher
from app.command_visuals.series_buffers import SeriesBuffer
from app.command_visuals.visual_projector import VisualProjector
from app.nostra.runtime_regime import observe_regime
from app.research_agent.nostra_regime import build_market_state, classify_regime

NOW = datetime(2026,10,6,15,30,tzinfo=timezone.utc)


def point(at=NOW,value=100):
    return {"timestamp":at.isoformat(),"value":value,"source":"ALPACA/iex","provenance":"OBSERVED","quality_state":"LIVE"}


def test_ring_buffer_append_patch_and_no_synthetic_gaps():
    buffer = SeriesBuffer(3)
    assert buffer.apply(point()) == "series_append"
    assert buffer.apply(point()) is None
    assert buffer.apply(point(value=101)) == "series_patch_last"
    assert buffer.apply(point(NOW-timedelta(seconds=1))) is None
    assert buffer.apply(point(NOW+timedelta(minutes=10))) == "series_append"
    assert len(buffer.points) == 2
    for i in range(3): buffer.apply(point(NOW+timedelta(minutes=11+i)))
    assert len(buffer.points) == 3
    with pytest.raises(ValueError): buffer.apply({**point(),"provenance":"SIMULATED"})


def test_publisher_snapshot_then_batch_and_critical_immediate():
    pub = LivePublisher(lambda:{"system":{},"visual_schema":"command-visual.v1"})
    queue = pub.subscribe()
    assert queue.get_nowait()["message_type"] == "snapshot"
    sequence = pub.sequence
    bootstrap = pub.bootstrap()
    assert bootstrap["sequence"] == sequence == pub.sequence
    assert bootstrap["stream_generation"] == pub.generation
    assert bootstrap["payload"]["visual_schema"] == "command-visual.v1"
    assert len(pub.clients) == 1 and queue.empty()
    pub.stage("SPY","scanner_patch",{"symbol":"SPY","mid":100})
    pub.stage("SPY","scanner_patch",{"symbol":"SPY","mid":101})
    assert queue.empty()
    pub.send("execution_event",{"event_id":"broker-fill"})
    critical = queue.get_nowait()
    assert critical["message_type"] == "execution_event"
    pub.flush()
    batch = queue.get_nowait()
    assert batch["sequence"] > critical["sequence"]
    assert len(batch["payload"]["events"]) == 1
    assert batch["payload"]["events"][0]["payload"]["mid"] == 101


def test_client_backpressure_disconnects_and_resnapshots():
    pub = LivePublisher(lambda:{"system":{}},client_capacity=1)
    queue = pub.subscribe()
    pub.send("execution_event",{"event_id":"fill"})
    assert queue.get_nowait() is None and queue not in pub.clients
    next_queue = pub.subscribe()
    assert next_queue.get_nowait()["message_type"] == "snapshot"
    with pytest.raises(ValueError): LivePublisher(lambda:{},flush_ms=5000)


def forecast():
    points = [{"timestamp":(NOW+timedelta(seconds=60)).isoformat(),"value":101}]
    return {"forecast_id":"f1","symbol":"SPY","issued_at":NOW.isoformat(),"feature_as_of":NOW.isoformat(),
            "horizon_seconds":600,"expires_at":(NOW+timedelta(seconds=600)).isoformat(),
            "model_version":"frozen-v1","methodology_version":"test-v1","central_path":points,"quality_state":"LIVE",
            "lower_path":[{**points[0],"value":99}],"upper_path":[{**points[0],"value":103}],"confidence_level":.9}


def test_forecast_lineage_expiry_and_exact_band():
    f = forecast()
    assert valid_forecast(f,NOW)["lower_path"] == f["lower_path"]
    assert valid_forecast(f,NOW+timedelta(seconds=600)) is None
    with pytest.raises(ValueError): valid_forecast({**f,"feature_as_of":(NOW+timedelta(seconds=1)).isoformat()},NOW)
    with pytest.raises(ValueError): valid_forecast({**f,"horizon_seconds":30},NOW)
    with pytest.raises(ValueError): valid_forecast({**f,"model_version":""},NOW)
    with pytest.raises(ValueError): valid_forecast({**f,"upper_path":None},NOW)
    f["lower_path"][0]["value"] = 104
    with pytest.raises(ValueError): valid_forecast(f,NOW)


def test_replay_availability_clock_blocks_late_corrections_and_future():
    points = [{**point(NOW-timedelta(minutes=1)),"available_at":NOW.isoformat()},
              {**point(NOW-timedelta(minutes=2)),"available_at":(NOW+timedelta(seconds=1)).isoformat()},
              {**point(NOW+timedelta(seconds=1)),"available_at":NOW.isoformat()}, point()]
    frame = replay_frame(points,NOW)
    assert len(frame) == 1 and frame[0]["source"] == points[0]["source"] and not frame[0]["entry_authority"]
    assert frame[0]["replay_source"] == "VELUM_REPLAY"


def controller():
    library = PolicyLibrary.load()
    baseline = {"stop_pct":"0.004","target_pct":"0.006","max_hold_minutes":45,"reentry_cooldown_minutes":10,
                "max_spread_pct":"0.004","net_edge_hurdle_bps":"5","min_quality_score":80}
    c = AdaptivePolicyController(library,baseline=baseline,hard_limits={"stop_pct":"0.006","max_hold_minutes":60,"minimum_net_edge_hurdle_bps":5},configuration_fingerprint="cfg")
    context = PolicyContext(NOW,NOW,"REGULAR","BROAD_ADVANCE",.9,.1,.9,True,True,True,False,"cfg",library.fingerprint)
    return c,context,baseline


def test_disabled_and_shadow_preserve_exact_execution_values():
    c,context,baseline = controller()
    disabled = c.observe(context)
    assert dict(disabled.execution_values) == baseline and disabled.mode == "DISABLED"
    one = c.observe(context,enabled=True)
    two = c.observe(replace(context,observed_at=NOW+timedelta(seconds=300),feature_as_of=NOW+timedelta(seconds=300)),enabled=True)
    assert two.proposed_profile == "ASSERTIVE_TREND"
    assert dict(one.execution_values) == dict(two.execution_values) == baseline
    assert two.counterfactual_values["net_edge_hurdle_bps"] == "5"
    assert Decimal(two.counterfactual_values["stop_pct"]) <= Decimal(".006")
    with pytest.raises(TypeError): two.execution_values["stop_pct"] = "99"
    with pytest.raises(ValueError): c.observe(context,enabled=True,mode="active")


def test_duplicate_observation_cannot_confirm_upgrade_safety_bypasses_dwell():
    c,context,_ = controller()
    c.observe(context,enabled=True)
    assert c.observe(context,enabled=True).proposed_profile == "BASELINE_LOCKED"
    next_context = replace(context,observed_at=NOW+timedelta(seconds=300),feature_as_of=NOW+timedelta(seconds=300))
    assert c.observe(next_context,enabled=True).proposed_profile == "ASSERTIVE_TREND"
    unsafe = replace(next_context,observed_at=NOW+timedelta(seconds=301),reconciliation_safe=False)
    assert c.observe(unsafe,enabled=True).proposed_profile == "NO_TRADE"


def test_stale_unknown_lineage_and_probability_veto():
    c,context,_ = controller()
    stale = c.observe(replace(context,feature_as_of=NOW-timedelta(seconds=601)),enabled=True)
    assert stale.mode == "FALLBACK" and stale.proposed_profile == "BASELINE_LOCKED"
    assert c.observe(replace(context,library_fingerprint="other"),enabled=True).mode == "FALLBACK"
    assert c.observe(replace(context,regime="UNKNOWN"),enabled=True).proposed_profile == "BASELINE_LOCKED"
    with pytest.raises(ValueError): c.observe(replace(context,confidence=float("nan")),enabled=True)


def test_capital_randomized_hard_caps_and_monotonic_factors():
    rng = random.Random(44)
    for _ in range(1000):
        base,cash,order = [rng.uniform(0,10000) for i in range(3)]
        factor = rng.random()
        decision = govern_capital(base_safe_notional=base,cash=cash,hard_gross_envelope=20000,hard_caps={"order":order},health_factor=factor)
        assert decision.notional <= min(Decimal(str(base)),Decimal(str(cash)),Decimal(str(order)))
        worse = govern_capital(base_safe_notional=base,cash=cash,hard_gross_envelope=20000,hard_caps={"order":order},health_factor=factor/2)
        assert worse.notional <= decision.notional
    with pytest.raises(ValueError): govern_capital(base_safe_notional=100,cash=100,allocation_multiplier=1.01)
    with pytest.raises(ValueError): govern_capital(base_safe_notional=100,cash=100,allow_margin=True)
    with pytest.raises(ValueError): govern_capital(base_safe_notional=float("nan"),cash=100)


def test_nostra_classifier_parity_and_pit():
    kwargs = {"benchmark_returns":{"SPY_5m":.001,"QQQ_5m":.002,"IWM_5m":.001,"SPY_15m":.003},
              "candidate_summary":{"breadth_above_vwap":.7,"breadth_positive_5m":.8,"median_fast_slow_spread_pct":.001,
                                   "median_vwap_edge_pct":.002,"median_abs_return_5m":.001,"cross_sectional_dispersion_5m":.003}}
    reference = classify_regime(build_market_state(**kwargs))
    runtime = observe_regime(observed_at=NOW,feature_as_of=NOW,**kwargs)
    assert runtime["probabilities"] == reference["probabilities"] and runtime["methodology_version"] == reference["methodology_version"]
    with pytest.raises(ValueError): observe_regime(observed_at=NOW,feature_as_of=NOW+timedelta(seconds=1),**kwargs)
    assert observe_regime(observed_at=NOW,feature_as_of=NOW-timedelta(seconds=601),**kwargs)["primary_regime"] == "UNKNOWN"
    assert observe_regime(observed_at=NOW,feature_as_of=NOW,benchmark_returns={},candidate_summary={})["primary_regime"] == "UNKNOWN"
