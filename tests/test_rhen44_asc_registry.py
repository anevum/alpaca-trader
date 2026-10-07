import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.adaptive_policy import AdaptivePolicyController, PolicyContext, PolicyLibrary, fingerprint
from app.market_fabric.profile_release_registry import TrustedProfileReleaseRegistry
from app.market_fabric.staging import ReadOnlyCanonicalResearchEvidence
from app.research_agent.policy_profile_release import REQUIRED_RUNTIME_GATES

NOW=datetime(2026,10,7,20,45,tzinfo=timezone.utc)


def fixture():
    library=PolicyLibrary.load()
    proposal={
        "release_id":"release-1","profile_id":"NORMAL","profile_version":"v1",
        "source_strategy_version":"LIVE-TEST","target_strategy_version":"4.4",
        "configuration_fingerprint":"sha256:"+"a"*64,
        "policy_library_fingerprint":library.fingerprint,
        "nostra_methodology_fingerprint":"nostra-v1",
        "profile_values":{"allocation_multiplier":".5","gross_envelope_fraction":".5"},
        "hard_envelope":{k:False for k in (
            "allow_margin","allow_short","allow_crypto","may_exceed_existing_risk_limits",
            "options_broker_write_authority","expanded_session_execution","expanded_leverage")},
        "rollback_profile":"BASELINE_LOCKED","rollback_version":"LIVE-TEST",
        "authorization_reference":"AUTH-RELEASE-1",
        "activation_at":(NOW+timedelta(hours=1)).isoformat(),
        "artifact_ids":{k:k+"-1" for k in ("shadow","velum","holdout","graen","runtime","authorization")},
    }
    bindings={k:proposal[k] for k in (
        "release_id","profile_id","profile_version","source_strategy_version",
        "target_strategy_version","configuration_fingerprint",
        "policy_library_fingerprint","nostra_methodology_fingerprint")}
    bindings["profile_fingerprint"]=fingerprint(proposal["profile_values"])
    artifacts={ref:{
        "artifact_id":ref,"kind":kind,"status":"PASSED","bindings":bindings,
        "frozen_at":(NOW-timedelta(days=30)).isoformat(),
        "evaluated_at":(NOW-timedelta(minutes=2)).isoformat(),
        "methodology_version":"test","execution_authority":False,
    } for kind,ref in proposal["artifact_ids"].items() if kind!="authorization"}
    artifacts["shadow-1"]["session_results"]=[{
        "source_session":"2026-09-01","evaluation_session":f"2026-09-{i:02d}",
        "baseline_fingerprint":proposal["configuration_fingerprint"],
        "complete_candidates":10,"candidate_scope":10,"differential_decisions":3,
        "adaptive_minus_fixed_utility":".0001",
    } for i in range(2,12)]
    artifacts["holdout-1"].update(
        independent_session_ids=[f"2026-09-{i:02d}" for i in range(20,25)],
        complete_candidates=50,coverage=1,quarantine_accessed_during_development=False)
    artifacts["runtime-1"]["gates"]={k:"PASSED" for k in REQUIRED_RUNTIME_GATES}
    artifacts["velum-1"]["no_lookahead_verified"]=True
    artifacts["graen-1"]["frozen_validation_passed"]=True
    decision={
        "decision_key":"AUTH-RELEASE-1","status":"final",
        "decision_type":"research_authorization","superseded_by_decision_id":None,
        "evidence":{
            "authorized_action":"authorize_policy_profile_release",
            "release_id":proposal["release_id"],"profile_id":proposal["profile_id"],
            "profile_version":proposal["profile_version"],
            "source_strategy_version":proposal["source_strategy_version"],
            "target_strategy_version":proposal["target_strategy_version"],
            "configuration_fingerprint":proposal["configuration_fingerprint"],
            "policy_library_fingerprint":proposal["policy_library_fingerprint"],
            "authorization_reference":proposal["authorization_reference"],
            "release_fingerprint":fingerprint(proposal),
            "authorized_by":"operator","authorized_at":NOW.isoformat(),"revoked":False,
        },
    }
    evidence={
        "profile_release_proposals":[proposal],
        "profile_release_artifacts":list(artifacts.values()),
        "research_decisions":[decision],
        "agent_runs":[],
    }
    return library,proposal,evidence


def test_exact_canonical_profile_release_allows_shadow_profile_only():
    library,proposal,evidence=fixture()
    result=TrustedProfileReleaseRegistry(library_fingerprint=library.fingerprint).review(
        evidence,now=NOW+timedelta(minutes=5),
        expected_configuration_fingerprint=proposal["configuration_fingerprint"],
        source_strategy_version="LIVE-TEST")
    assert result["quality_state"]=="LIVE"
    assert result["approved_profiles"]==["NORMAL"]
    assert result["evidence_healthy"] is True
    assert result["active_mode_authorized"] is False
    assert result["entry_authority"] is False


@pytest.mark.parametrize("mutation", ["generic","revoked","wrong_config","missing_artifact","duplicate_auth"])
def test_profile_release_registry_fails_closed_on_untrusted_or_incomplete_lineage(mutation):
    library,proposal,evidence=fixture()
    if mutation=="generic":
        evidence["research_decisions"][0]["evidence"]={"approved":True}
    elif mutation=="revoked":
        evidence["research_decisions"][0]["evidence"]["revoked"]=True
    elif mutation=="wrong_config":
        proposal["configuration_fingerprint"]="sha256:"+"b"*64
    elif mutation=="missing_artifact":
        evidence["profile_release_artifacts"]=[
            row for row in evidence["profile_release_artifacts"] if row["kind"]!="velum"]
    elif mutation=="duplicate_auth":
        evidence["research_decisions"].append(dict(evidence["research_decisions"][0],decision_key="AUTH-DUP"))
    result=TrustedProfileReleaseRegistry(library_fingerprint=library.fingerprint).review(
        evidence,now=NOW,expected_configuration_fingerprint="sha256:"+"a"*64,
        source_strategy_version="LIVE-TEST")
    assert result["approved_profiles"]==[]
    assert result["evidence_healthy"] is False
    assert result["active_mode_authorized"] is False


def test_adaptive_shadow_never_uses_unapproved_non_safety_profile():
    library=PolicyLibrary.load()
    baseline={"stop_pct":".004","target_pct":".006","max_hold_minutes":45,
              "reentry_cooldown_minutes":10,"max_spread_pct":".004",
              "net_edge_hurdle_bps":"5","min_quality_score":80}
    controller=AdaptivePolicyController(
        library,baseline=baseline,
        hard_limits={"stop_pct":".006","max_hold_minutes":60,
                     "minimum_net_edge_hurdle_bps":5},
        configuration_fingerprint="cfg",min_dwell_seconds=0,confirmations=1)
    context=PolicyContext(NOW,NOW,"REGULAR","ROTATION",.9,.1,.9,True,True,True,False,
                          "cfg",library.fingerprint)
    blocked=controller.observe(context,enabled=True,mode="shadow",approved_profiles=())
    assert blocked.proposed_profile=="BASELINE_LOCKED"
    assert "PROFILE_NOT_APPROVED" in blocked.reasons
    controller=AdaptivePolicyController(
        library,baseline=baseline,
        hard_limits={"stop_pct":".006","max_hold_minutes":60,
                     "minimum_net_edge_hurdle_bps":5},
        configuration_fingerprint="cfg",min_dwell_seconds=0,confirmations=1)
    approved=controller.observe(context,enabled=True,mode="shadow",approved_profiles=("NORMAL",))
    assert approved.proposed_profile=="NORMAL"
    assert dict(approved.execution_values)==baseline
    assert approved.entry_authority is False
    with pytest.raises(ValueError):
        controller.observe(context,enabled=True,mode="active",approved_profiles=("NORMAL",))


def test_canonical_research_reader_is_get_only_and_rejects_authority():
    from app.config import Settings
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={"ok":True,"evidence":{"research_decisions":[]},
            "execution_authority":False,"broker_orders_possible":False})
    settings=Settings(_env_file=None,TRADING_INGEST_TOKEN="fixture")
    result=asyncio.run(ReadOnlyCanonicalResearchEvidence(
        settings,transport=httpx.MockTransport(handle)).snapshot())
    assert result["research_decisions"]==[]
    assert len(calls)==1 and calls[0].method=="GET"
    assert calls[0].url.path=="/v1/research-agent-gateway"
    assert calls[0].headers["x-anevum-ingest-token"]=="fixture"

    bad=ReadOnlyCanonicalResearchEvidence(settings,transport=httpx.MockTransport(
        lambda request:httpx.Response(200,json={"ok":True,"evidence":{},
            "execution_authority":True,"broker_orders_possible":False})))
    with pytest.raises(ValueError):
        asyncio.run(bad.snapshot())
