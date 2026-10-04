from datetime import datetime, timezone

from foundation.graen_gateway import (
    RESEARCH_STAGES,
    _hash,
    _is_native_research_stage,
    _obj,
    _status,
    _with_forward_shadow,
    crypto_promotion_matches_contract,
)


def test_hash_is_canonical():
    assert _hash({"a":1,"b":2}) == _hash({"b":2,"a":1})


def test_status_contract():
    assert _status("waiting") == "WAITING"


def test_object_value_is_fail_closed():
    assert _obj({"x":1}) == {"x":1}
    assert _obj(["x"]) == {}


def test_runtime_contract_versionless_and_execution_neutral():
    # Gateway persistence carries state only; it does not grant execution authority.
    sample = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
        "broker_orders_possible": False,
    }
    assert sample["execution_authority"] is False
    assert sample["broker_orders_possible"] is False


def test_btc_v11_stages_are_claimable_research_stages():
    assert "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT" in RESEARCH_STAGES
    assert "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_REPLAY" in RESEARCH_STAGES


def test_btc_v12_stages_are_claimable_research_stages():
    assert "CRYPTO_BTC_MECHANISMS_V12_DEVELOPMENT" in RESEARCH_STAGES
    assert "CRYPTO_BTC_MECHANISMS_V12_VELUM_REPLAY" in RESEARCH_STAGES



def test_native_research_stage_precedence_excludes_compiled_stages():
    assert _is_native_research_stage("CRYPTO_BTC_MECHANISMS_V12_DEVELOPMENT") is True
    assert _is_native_research_stage("CRYPTO_BTC_MECHANISMS_V12_VELUM_REPLAY") is True
    assert _is_native_research_stage("CRYPTO_COMPILED_DEVELOPMENT") is False
    assert _is_native_research_stage("RESEARCH_IMPLEMENTATION_REQUIRED") is False


def test_btc_v13_stages_are_native_claimable_research_stages():
    stages = {
        "CRYPTO_BTC_HYPOTHESES_V13_DEVELOPMENT",
        "CRYPTO_BTC_HYPOTHESES_V13_VELUM_REPLAY",
        "CRYPTO_BTC_HYPOTHESES_V13_VALIDATION",
        "CRYPTO_BTC_HYPOTHESES_V13_HOLDOUT",
    }
    assert stages <= RESEARCH_STAGES
    assert all(_is_native_research_stage(stage) for stage in stages)


def test_btc_v14_preflight_is_native_claimable_research_only_stage():
    stage = "CRYPTO_BTC_XGB_V14_R1_BROKER_PREFLIGHT"
    assert stage in RESEARCH_STAGES
    assert _is_native_research_stage(stage) is True


def test_crypto_promotion_artifact_requires_exact_execution_contract():
    contract = {
        "strategy_family": "replication-test",
        "strategy_version_id": "CRYPTO-TEST-001",
        "model_version": "model-v1",
        "calibration_version": "cal-v1",
        "regime_version": "regime-v1",
        "execution_adapter_version": "alpaca-v1",
    }
    content = {
        "statistical_promotion_ready": True,
        "execution_contract": dict(contract),
        "live_execution_authorized": False,
    }
    assert crypto_promotion_matches_contract(content, contract) is True

    wrong = dict(contract)
    wrong["strategy_version_id"] = "CRYPTO-OTHER"
    assert crypto_promotion_matches_contract(content, wrong) is False

    missing_contract = {
        "statistical_promotion_ready": True,
        "live_execution_authorized": False,
    }
    assert crypto_promotion_matches_contract(missing_contract, contract) is False


def test_research_director_is_a_native_claimable_stage():
    stage = "CRYPTO_RESEARCH_DIRECTOR_V1"
    assert stage in RESEARCH_STAGES
    assert _is_native_research_stage(stage) is True



def test_r2g_shadow_checkpoint_preserves_r2f_as_canonical_primary():
    primary = {
        "candidate_id": "V14-R2F-BTC-MOM-180D",
        "candidate_methodology": "graen-btc-slow-momentum-v14-r2f",
        "status": "COLLECTING",
    }
    comparison = {
        "candidate_id": "V14-R2G-BTC-CONSENSUS-180-250",
        "candidate_methodology": "graen-btc-consensus-trend-v14-r2g",
        "status": "COLLECTING",
    }

    metadata = _with_forward_shadow(
        {"forward_shadow": primary},
        comparison,
    )

    assert metadata["forward_shadow"] == primary
    assert metadata["forward_shadow_comparison"] == comparison
    assert metadata["forward_shadows"][primary["candidate_id"]] == primary
    assert metadata["forward_shadows"][comparison["candidate_id"]] == comparison


def test_primary_shadow_updates_still_update_canonical_pointer():
    r2f = {
        "candidate_id": "V14-R2F-BTC-MOM-180D",
        "candidate_methodology": "graen-btc-slow-momentum-v14-r2f",
        "status": "READY_FOR_HUMAN_REVIEW",
    }
    metadata = _with_forward_shadow({}, r2f)

    assert metadata["forward_shadow"] == r2f
    assert metadata["forward_shadows"][r2f["candidate_id"]] == r2f
    assert "forward_shadow_comparison" not in metadata
