import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.adaptive_policy import AdaptivePolicyController, PolicyContext, PolicyLibrary, fingerprint
from app.market_fabric.policy_state import PolicyStateStore
from app.command_visuals.visual_archive import VisualArchive
from app.command_visuals.performance_projection import AccountPerformance
from app.research_agent.policy_profile_release import evaluate_profile_release, REQUIRED_RUNTIME_GATES
from app.command_visuals.forecast_projection import project_nostra_forecast
from app.market_fabric.stream_manager import MarketStreamManager
from app.market_fabric.stream_state import MarketStateStore
import asyncio
from unittest.mock import AsyncMock

NOW = datetime(2026,10,7,14,0,tzinfo=timezone.utc)


def test_execution_archive_recovery_preserves_distinct_fills_and_source_without_future_leak():
    from app.command_visuals.replay_projection import replay_frame
    archive = VisualArchive(sqlite3.connect(":memory:"))
    marker = {"timestamp":NOW.isoformat(),"event_id":"f1","event_type":"FILL","order_ref":"o1",
        "symbol":"SPY","price":100,"quantity":1,"provenance":"OBSERVED",
        "source":"ALPACA/trade_updates","quality_state":"LIVE"}
    archive.append("executions:SPY",marker,NOW)
    archive.append("executions:SPY",{**marker,"event_id":"f2"},NOW)
    archive.append("executions:SPY",{**marker,"event_id":"late"},NOW+timedelta(seconds=1))
    archive.append("candles:SPY",{**marker,"event_id":"not-an-execution"},NOW)
    recovery=archive.executions(NOW,("SPY",))
    assert {p["event_id"] for p in recovery["points"]} == {"f1","f2"}
    assert all(p["quality_state"] == "HISTORICAL" for p in recovery["points"])
    assert not recovery["entry_authority"] and not archive.executions(NOW,("QQQ",))["points"]
    replay = replay_frame(recovery["points"],NOW)
    assert replay[0]["source"] == "ALPACA/trade_updates" and replay[0]["replay_source"] == "VELUM_REPLAY"


def controller():
    library = PolicyLibrary.load()
    return AdaptivePolicyController(library,baseline={"stop_pct":".004"},hard_limits={"stop_pct":".005"},configuration_fingerprint="cfg")


def context(controller, now):
    return PolicyContext(now,now,"REGULAR","TREND_EXPANSION",.9,.1,.9,True,True,True,False,"cfg",controller.library.fingerprint)


def test_policy_confirmation_dwell_survives_restart_without_execution_authority(tmp_path):
    path = tmp_path/"policy.db"
    original = controller()
    original.observe(context(original,NOW),enabled=True)
    db = sqlite3.connect(path)
    PolicyStateStore(db).save(original,"2026-10-07/REGULAR",NOW)
    db.close()
    recovered = controller()
    db = sqlite3.connect(path)
    storage = PolicyStateStore(db)
    assert storage.restore(recovered,"2026-10-07/REGULAR",NOW+timedelta(seconds=1)) == "RESTORED_SHADOW_ONLY"
    assert recovered.confirmed == 1 and recovered.last_observation == NOW
    snapshot = recovered.observe(context(recovered,NOW+timedelta(minutes=1)),enabled=True)
    assert snapshot.proposed_profile == "ASSERTIVE_TREND"
    assert dict(snapshot.execution_values) == recovered.baseline and not snapshot.entry_authority
    storage.save(recovered,"2026-10-07/REGULAR",NOW+timedelta(minutes=1))
    another = controller()
    assert storage.restore(another,"2026-10-07/REGULAR",NOW+timedelta(minutes=2)) == "RESTORED_SHADOW_ONLY"
    assert another.since == NOW+timedelta(minutes=1)
    assert storage.restore(controller(),"2026-10-08/REGULAR",NOW+timedelta(days=1)) == "REJECTED_LINEAGE"
    altered = controller(); altered.hard_limits["stop_pct"] = ".009"
    assert storage.restore(altered,"2026-10-07/REGULAR",NOW+timedelta(minutes=2)) == "REJECTED_LINEAGE"
    db.execute("UPDATE shadow_policy_state SET body='{}'")
    assert storage.restore(controller(),"2026-10-07/REGULAR",NOW) == "REJECTED_STATE"


def source_point(value=100):
    return {"timestamp":NOW.isoformat(),"value":value,"provenance":"OBSERVED","source":"ALPACA/iex","quality_state":"LIVE"}


def test_visual_replay_hides_late_corrections_and_reports_retention_loss():
    archive = VisualArchive(sqlite3.connect(":memory:"),capacity=2)
    assert archive.append("SPY",source_point(),NOW)
    assert not archive.append("SPY",source_point(),NOW+timedelta(seconds=1))
    archive.append("SPY",source_point(101),NOW+timedelta(seconds=30))
    before = archive.history("SPY",NOW-timedelta(minutes=1),NOW,clock=NOW)
    assert before["points"][0]["value"] == 100
    after = archive.history("SPY",NOW-timedelta(minutes=1),NOW,clock=NOW+timedelta(seconds=30))
    assert after["points"][0]["value"] == 101 and not after["entry_authority"]
    archive.append("QQQ",source_point(),NOW+timedelta(minutes=1))
    assert archive.history("SPY",NOW-timedelta(minutes=1),NOW,clock=NOW+timedelta(minutes=1))["pruned_records"] == 1
    with pytest.raises(ValueError): archive.append("SPY",source_point(),NOW-timedelta(seconds=1))
    with pytest.raises(ValueError): archive.history("SPY",NOW-timedelta(days=2),NOW,clock=NOW)


def test_replay_limit_applies_to_source_points_after_latest_available_revision():
    archive = VisualArchive(sqlite3.connect(":memory:"))
    for i in range(5):
        archive.append("SPY",source_point(100+i),NOW+timedelta(seconds=i))
    archive.append("SPY",{**source_point(200),"timestamp":(NOW+timedelta(minutes=1)).isoformat()},NOW+timedelta(minutes=1))
    latest=archive.history("SPY",NOW-timedelta(minutes=1),NOW+timedelta(minutes=1),clock=NOW+timedelta(minutes=1),limit=1)
    assert latest["truncated"] and latest["points"][0]["value"] == 104
    before=archive.history("SPY",NOW-timedelta(minutes=1),NOW,clock=NOW+timedelta(seconds=2),limit=1)
    assert before["points"][0]["value"] == 102 and not before["truncated"]


def test_replay_preserves_distinct_fills_at_the_same_source_timestamp():
    archive = VisualArchive(sqlite3.connect(":memory:"))
    for identity in ("fill1","fill2"):
        archive.append("executions:SPY",{**source_point(),"event_id":identity},NOW)
    replay=archive.history("executions:SPY",NOW-timedelta(minutes=1),NOW,clock=NOW)
    assert {p["event_id"] for p in replay["points"]} == {"fill1","fill2"}


def test_account_diagnostics_preserve_observed_peak_across_restart(tmp_path):
    path = tmp_path/"account.db"
    db = sqlite3.connect(path)
    snapshot = {"account":{"id":"fixture-account","currency":"USD","equity":"100"},"positions":[{"market_value":"20"}]}
    first = AccountPerformance(db).observe(snapshot,NOW)
    assert first["points"]["normalized_equity"]["value"] == 100
    db.close(); db = sqlite3.connect(path)
    snapshot["account"]["equity"] = "90"
    second = AccountPerformance(db).observe(snapshot,NOW+timedelta(minutes=1))
    assert second["points"]["sampled_drawdown_pct"]["value"] == pytest.approx(-10)
    assert second["strategy_performance_state"] == "UNAVAILABLE"
    assert second["cash_flow_adjustment"] == "UNAVAILABLE"
    snapshot["account"]["id"] = "different-account"
    assert AccountPerformance(db).observe(snapshot,NOW+timedelta(minutes=2))["points"]["normalized_equity"]["value"] == 100


def release_evidence():
    # Synthetic unit fixture only, never runtime/research evidence.
    proposal = {"release_id":"release-fixture","profile_id":"NORMAL","profile_version":"v1",
        "source_strategy_version":"4.3","target_strategy_version":"4.4","configuration_fingerprint":"cfg",
        "policy_library_fingerprint":"lib","nostra_methodology_fingerprint":"nostra",
        "profile_values":{"allocation_multiplier":".5","gross_envelope_fraction":".5"},
        "hard_envelope":{k:False for k in ("allow_margin","allow_short","allow_crypto","may_exceed_existing_risk_limits","options_broker_write_authority","expanded_session_execution","expanded_leverage")},
        "rollback_profile":"BASELINE_LOCKED","rollback_version":"4.3","authorization_reference":"unit-test-only",
        "activation_at":(NOW+timedelta(hours=1)).isoformat(),
        "artifact_ids":{k:k+"-fixture" for k in ("shadow","velum","holdout","graen","runtime","authorization")}}
    bindings = {k:proposal[k] for k in ("release_id","profile_id","profile_version","source_strategy_version","target_strategy_version",
        "configuration_fingerprint","policy_library_fingerprint","nostra_methodology_fingerprint")}
    bindings["profile_fingerprint"] = fingerprint(proposal["profile_values"])
    artifacts = {ref:{"artifact_id":ref,"kind":kind,"status":"PASSED","bindings":bindings,
        "frozen_at":(NOW-timedelta(days=30)).isoformat(),"evaluated_at":NOW.isoformat(),
        "methodology_version":"unit-test","execution_authority":False} for kind,ref in proposal["artifact_ids"].items()}
    artifacts["shadow-fixture"]["session_results"] = [{"source_session":"2026-09-01","evaluation_session":f"2026-09-{i:02}",
        "baseline_fingerprint":"cfg","complete_candidates":10,"candidate_scope":10,"differential_decisions":3,
        "adaptive_minus_fixed_utility":".0001"} for i in range(2,12)]
    artifacts["holdout-fixture"].update(independent_session_ids=[f"2026-09-{i:02}" for i in range(20,25)],
        complete_candidates=50,coverage=1,quarantine_accessed_during_development=False)
    artifacts["runtime-fixture"]["gates"] = {k:"PASSED" for k in REQUIRED_RUNTIME_GATES}
    artifacts["velum-fixture"]["no_lookahead_verified"] = True
    artifacts["graen-fixture"]["frozen_validation_passed"] = True
    artifacts["authorization-fixture"]["reference"] = "unit-test-only"
    return proposal, artifacts


def test_profile_release_uses_canonical_artifact_lineage_and_all_package_gates():
    proposal, artifacts = release_evidence()
    result = evaluate_profile_release(proposal,resolve_artifact=artifacts.get,now=NOW)
    assert result["eligible_for_operator_activation"] and not result["broker_write_authority"]
    missing = evaluate_profile_release(proposal,resolve_artifact=lambda ref:None,now=NOW)
    assert not missing["eligible_for_operator_activation"]
    artifacts["shadow-fixture"]["session_results"] *= 2
    assert "DUPLICATE_INDEPENDENT_SESSIONS" in evaluate_profile_release(proposal,resolve_artifact=artifacts.get,now=NOW)["reason_codes"]


@pytest.mark.parametrize("kind,field,value", [
    ("shadow","status","UNVALIDATED"),("holdout","coverage",.94),
    ("holdout","quarantine_accessed_during_development",True),
    ("velum","no_lookahead_verified",False),("graen","frozen_validation_passed",False),
    ("authorization","reference","wrong-release")])
def test_profile_release_rejects_under_sampled_or_mismatched_evidence(kind,field,value):
    proposal, artifacts = release_evidence()
    artifacts[kind+"-fixture"][field] = value
    assert not evaluate_profile_release(proposal,resolve_artifact=artifacts.get,now=NOW)["eligible_for_operator_activation"]


def test_canonical_forecast_reference_horizon_expiry_and_no_invented_band():
    record = {"forecast_id":"unit-f","snapshot_id":"unit-s","symbol":"SPY","research_only":True,
        "execution_authority":False,"authority_state":"LOW_SUPPORT","target_kind":"return","generated_at":NOW.isoformat(),"as_of_timestamp":NOW.isoformat(),
        "horizon_minutes":10,"model_version":"test-v1","methodology_version":"FORECAST-001","forecast_payload":{"expected_return":.01}}
    reference = {**source_point(),"symbol":"SPY","snapshot_id":"unit-s"}
    projected = project_nostra_forecast(record,reference,NOW)
    assert projected["central_path"][-1]["value"] == 101
    assert projected["provenance"] == "FORECAST" and projected["uncertainty_state"] == "UNAVAILABLE"
    assert projected["lower_path"] is None and not projected["entry_authority"]
    assert project_nostra_forecast(record,reference,NOW+timedelta(minutes=10)) is None
    with pytest.raises(ValueError): project_nostra_forecast(record,{**reference,"snapshot_id":"another"},NOW)
    with pytest.raises(ValueError): project_nostra_forecast(record,{**reference,"timestamp":(NOW+timedelta(seconds=1)).isoformat()},NOW)


def test_disconnect_health_is_immediate_and_clears_subscription_coverage():
    store = MarketStateStore(("SPY",))
    store.subscribed.add("SPY")
    callback = AsyncMock()
    manager = MarketStreamManager(store,api_key="test",api_secret="test",on_event=AsyncMock(),on_status=callback)
    async def run():
        await manager.status("HEALTHY")
        store.subscribed.clear()
        await manager.status("DISCONNECTED")
    asyncio.run(run())
    assert callback.await_count == 2
    last = callback.call_args.args[0]
    assert last["subscribed_symbols"] == 0 and last["entry_authority"] is False


def test_degraded_forecasts_are_not_live_visuals():
    for state in ("OOD", "DATA_DEGRADED", "MODEL_DEGRADED", "ABSTAIN", None):
        assert project_nostra_forecast({"execution_authority":False,"research_only":True,"target_kind":"return","authority_state":state},{},NOW) is None


def test_champion_reader_uses_only_existing_private_health_get():
    import httpx
    from app.market_fabric.staging import ReadOnlyChampion
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={"ok":True,"execution":{"body":{"ok":True,"reconciliation_safe":True,
            "protected_configuration_identity":{"fingerprint":"sha256:fixture"},
            "persistence":{"strategy_version_id":"4.3"},"runtime_provenance":{"git_commit":"fixture"}}}})
    result = asyncio.run(ReadOnlyChampion(transport=httpx.MockTransport(handle)).snapshot())
    assert result["runtime_ok"] and result["reconciliation_safe"] and not result["broker_write_authority"]
    assert len(requests) == 1 and requests[0].method == "GET"
    assert str(requests[0].url) == "http://alpaca-trader.railway.internal:8080/health"
    assert "authorization" not in requests[0].headers and "apca-api-key-id" not in requests[0].headers


@pytest.mark.parametrize("value", ["null", '"corrupt"', "[]", "123"])
def test_policy_restore_rejects_non_object_checkpoint_without_mutation(value):
    db = sqlite3.connect(":memory:")
    storage = PolicyStateStore(db)
    db.execute("INSERT INTO shadow_policy_state VALUES (1,?)",(value,))
    state = controller()
    assert storage.restore(state,"2026-10-07/REGULAR",NOW) == "REJECTED_STATE"
    assert state.profile == "BASELINE_LOCKED" and state.last_observation is None


@pytest.mark.parametrize("capability", ["options_broker_write_authority", "expanded_session_execution", "expanded_leverage"])
def test_profile_release_never_approves_future_authority(capability):
    proposal, artifacts = release_evidence()
    proposal["hard_envelope"][capability] = True
    assert "FUTURE_AUTHORITY_ENVELOPE_VIOLATION" in evaluate_profile_release(proposal,resolve_artifact=artifacts.get,now=NOW)["reason_codes"]
    proposal, artifacts = release_evidence()
    proposal["profile_values"][capability] = True
    assert "UNSUPPORTED_PROFILE_VALUE_OR_AUTHORITY" in evaluate_profile_release(proposal,resolve_artifact=artifacts.get,now=NOW)["reason_codes"]


def test_champion_lineage_rejects_stale_invalid_and_mismatched_reads():
    from types import SimpleNamespace
    from app.market_fabric.runtime import ShadowFabric
    subject = SimpleNamespace(settings=SimpleNamespace(strategy_version_id="4.3"),champion_observation={
        "observed_at":NOW.isoformat(),"runtime_ok":True,"reconciliation_safe":True,"strategy_version":"4.3",
        "protected_configuration_fingerprint":"sha256:"+"a"*64})
    assert ShadowFabric.champion_ready(subject,NOW)
    assert not ShadowFabric.champion_ready(subject,NOW+timedelta(seconds=151))
    for field, bad in (("observed_at","malformed"),("strategy_version","4.4"),("reconciliation_safe",False),
                       ("protected_configuration_fingerprint","sha256:bad")):
        old = subject.champion_observation[field]
        subject.champion_observation[field] = bad
        assert not ShadowFabric.champion_ready(subject,NOW)
        subject.champion_observation[field] = old


def test_champion_reader_preserves_degraded_observation_without_safety_pass():
    import httpx
    from app.market_fabric.staging import ReadOnlyChampion
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(503, json={"ok":False,"module_failures":["graen","nostra"],
            "execution":{"body":{"ok":True,"reconciliation_safe":False,
                "protected_configuration_identity":{"fingerprint":"sha256:fixture"},
                "persistence":{"strategy_version_id":"4.3"},
                "runtime_provenance":{"git_commit":"fixture"}}}})
    result = asyncio.run(ReadOnlyChampion(transport=httpx.MockTransport(handle)).snapshot())
    assert result["source_commit"] == "fixture"
    assert result["protected_configuration_fingerprint"] == "sha256:fixture"
    assert result["aggregate_http_status"] == 503 and result["module_failures"] == ["graen","nostra"]
    assert not result["runtime_ok"] and not result["reconciliation_safe"]
    assert not result["broker_write_authority"]


@pytest.mark.parametrize("body", [{"ok":False}, {"execution":{"body":{"ok":False}}}, []])
def test_champion_reader_rejects_unavailable_execution_on_503(body):
    import httpx
    from app.market_fabric.staging import ReadOnlyChampion
    with pytest.raises(ValueError):
        asyncio.run(ReadOnlyChampion(transport=httpx.MockTransport(
            lambda request: httpx.Response(503,json=body))).snapshot())


def test_canonical_ledger_reader_is_get_only_and_validates_authority():
    import httpx
    from app.config import Settings
    from app.market_fabric.staging import ReadOnlyCanonicalLedger
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={"ok":True,"ledger_version":"rhen-canonical-ledger-read-v1",
            "run_id":"run-live","event_count":0,"truncated":False,"events":[],
            "execution_authority":False,"broker_orders_possible":False})
    settings = Settings(_env_file=None, TRADING_INGEST_TOKEN="fixture-token")
    result = asyncio.run(ReadOnlyCanonicalLedger(settings,transport=httpx.MockTransport(handle)).snapshot("run-live"))
    assert result["run_id"] == "run-live"
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path == "/v1/trading-report-read"
    assert calls[0].url.params["latest"] == "ledger"
    assert calls[0].url.params["run_id"] == "run-live"
    assert calls[0].headers["x-anevum-ingest-token"] == "fixture-token"

    bad = ReadOnlyCanonicalLedger(settings,transport=httpx.MockTransport(
        lambda request:httpx.Response(200,json={**result,"execution_authority":True})))
    with pytest.raises(ValueError):
        asyncio.run(bad.snapshot("run-live"))


def test_canonical_ledger_parity_is_overlap_bounded_and_fail_closed():
    from app.market_fabric.runtime import ShadowFabric
    earlier = (NOW-timedelta(minutes=10)).isoformat()
    retained = NOW.isoformat()
    canonical = {"ok":True,"truncated":False,"events":[
        {"event_key":"old","event_type":"broker_order","occurred_at":earlier,
            "order":{"id":"old-order"}},
        {"event_key":"new","event_type":"broker_order","occurred_at":retained,
            "order":{"id":"order-1"}},
        {"event_key":"fill","event_type":"broker_fill","occurred_at":retained,
            "fill":{"id":"fill-1","order_id":"order-1"}},
    ]}
    observed = [{"event_id":"obs-1","timestamp":retained,"event_type":"FILL",
        "order_ref":"order-1","symbol":"SPY"}]
    parity = ShadowFabric.canonical_ledger_parity(canonical,observed)
    assert parity["quality_state"] == "LIVE"
    assert parity["parity_complete"] is True
    assert parity["canonical_order_count"] == 1
    assert parity["missing_observed_orders"] == 0
    assert parity["entry_authority"] is False
    assert parity["broker_write_authority"] is False

    diverged = ShadowFabric.canonical_ledger_parity(canonical,[{**observed[0],"order_ref":"unknown"}])
    assert diverged["quality_state"] == "DEGRADED"
    assert diverged["parity_complete"] is False
    assert diverged["missing_observed_orders"] == 1
    assert diverged["unknown_observed_orders"] == 1

    truncated = ShadowFabric.canonical_ledger_parity({**canonical,"truncated":True},observed)
    assert truncated["quality_state"] == "UNAVAILABLE"
    assert truncated["parity_complete"] is False


def test_reconcile_account_publishes_unavailable_ledger_without_authority(tmp_path):
    from app.config import Settings
    from app.market_fabric.runtime import ShadowFabric
    from types import SimpleNamespace
    async def account_reader():
        return {"account":{"id":"fixture","equity":"100","cash":"100","last_equity":"100"},
            "positions":[],"open_orders":[]}
    async def champion_reader():
        return {"run_id":"run-live","observed_at":NOW.isoformat(),"runtime_ok":True,
            "reconciliation_safe":True,"strategy_version":"LIVE-2026-09-25-003",
            "protected_configuration_fingerprint":"sha256:"+"a"*64}
    async def ledger_reader(run_id):
        assert run_id == "run-live"
        raise RuntimeError("fixture-unavailable")
    settings=Settings(_env_file=None,RHEN_MARKET_STREAM_CHECKPOINT_PATH=str(tmp_path/"ledger.db"))
    fabric=ShadowFabric(settings,SimpleNamespace(),account_reader=account_reader,
        champion_reader=champion_reader,ledger_reader=ledger_reader)
    asyncio.run(fabric.reconcile_account())
    parity=fabric.visual.system["canonical_ledger_parity"]
    assert parity["quality_state"]=="UNAVAILABLE"
    assert parity["parity_complete"] is False
    assert parity["entry_authority"] is False
    assert parity["broker_write_authority"] is False
    fabric.checkpoint.close()
