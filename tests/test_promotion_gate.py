from app.research_agent.promotion_gate import evaluate_promotion_gate


def proposal(parameter="min_momentum_pct"):
    return {
        "proposal_id": "AP-READY",
        "source_strategy_version": "LIVE-TEST",
        "target_scope": "parameter",
        "parameter": parameter,
        "old_value": "0.0020",
        "proposed_value": "0.0018",
        "rollback_value": "0.0020",
        "authorization_required": True,
        "automatic_application_authorized": False,
        "execution_authority": False,
        "live_configuration_changed": False,
    }


def health():
    return {
        "control_state": "RESEARCH",
        "dimensions": {
            "evidence_integrity": {
                "status": "HEALTHY",
                "reason_codes": ["CANONICAL_EVIDENCE_USABLE"],
            }
        },
    }


def shadow():
    return {
        "validation_passed": True,
        "independent_sessions": 12,
        "complete_candidates": 500,
        "differential_decisions": 75,
        "forward_coverage": "0.99",
        "baseline_fingerprint": "BASELINE123",
    }


def evidence():
    return {
        "independent_sessions": 12,
        "eligible_candidates": 500,
        "directly_attributed_closed_trades": 40,
        "direct_attribution_coverage": "0.998",
        "forward_15m_coverage": "0.98",
    }


def graen():
    return {
        "frozen_validation_passed": True,
        "walk_forward_passed": True,
        "selection_bias_status": "CONTROLLED",
        "dependence_status": "CONTROLLED",
        "multiple_testing_status": "CONTROLLED",
    }


def authorization(**overrides):
    row = {
        "reference": "AUTH-001",
        "authorized_by": "human-owner",
        "authorized_at": "2026-10-15T18:00:00Z",
        "proposal_id": "AP-READY",
        "source_strategy_version": "LIVE-TEST",
        "parameter": "min_momentum_pct",
        "proposed_value": "0.0018",
    }
    row.update(overrides)
    return row


def test_passing_evidence_stops_at_human_authorization_gate():
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=graen(),
    )
    assert result["eligible_for_human_authorization"] is True
    assert result["human_authorization_valid"] is False
    assert result["activation_contract"] is None
    assert result["automatic_promotion_authorized"] is False
    assert result["deployment_authorized_by_gate"] is False


def test_valid_proposal_specific_authorization_creates_contract_not_deployment():
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=graen(),
        human_authorization=authorization(),
    )
    assert result["eligible_for_human_authorization"] is True
    assert result["human_authorization_valid"] is True
    assert result["operator_activation_eligible"] is True
    contract = result["activation_contract"]
    assert contract is not None
    assert contract["deployment_requires_separate_operator_action"] is True
    assert contract["production_mutation_performed"] is False
    assert contract["execution_authority"] is False
    assert result["deployment_authorized_by_gate"] is False


def test_general_or_mismatched_authorization_is_not_accepted():
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=graen(),
        human_authorization=authorization(proposal_id="OTHER"),
    )
    assert result["human_authorization_valid"] is False
    assert result["activation_contract"] is None
    assert "AUTHORIZATION_PROPOSAL_MISMATCH" in (
        result["authorization_reason_codes"]
    )


def test_safety_parameter_is_never_bounded_promotion_candidate():
    result = evaluate_promotion_gate(
        proposal=proposal(parameter="stop_pct"),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=graen(),
    )
    assert result["eligible_for_human_authorization"] is False
    assert "PARAMETER_NOT_ELIGIBLE_FOR_BOUNDED_ADAPTATION" in (
        result["gate_reason_codes"]
    )


def test_evidence_integrity_failure_blocks_promotion():
    broken = health()
    broken["dimensions"]["evidence_integrity"]["status"] = "BLOCKED"
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=broken,
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=graen(),
    )
    assert result["eligible_for_human_authorization"] is False
    assert "EVIDENCE_INTEGRITY_NOT_HEALTHY" in result["gate_reason_codes"]


def test_graen_frozen_validation_is_required():
    validation = graen()
    validation["frozen_validation_passed"] = False
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=evidence(),
        graen_validation=validation,
    )
    assert result["eligible_for_human_authorization"] is False
    assert "GRAEN_FROZEN_VALIDATION_REQUIRED" in result["gate_reason_codes"]


def test_attribution_and_forward_coverage_are_hard_gates():
    quality = evidence()
    quality["direct_attribution_coverage"] = "0.99"
    quality["forward_15m_coverage"] = "0.90"
    result = evaluate_promotion_gate(
        proposal=proposal(),
        source_strategy_version="LIVE-TEST",
        strategy_health=health(),
        shadow_validation=shadow(),
        evidence_quality=quality,
        graen_validation=graen(),
    )
    assert result["eligible_for_human_authorization"] is False
    assert "DIRECT_ATTRIBUTION_COVERAGE_GATE_FAILED" in (
        result["gate_reason_codes"]
    )
    assert "FORWARD_15M_COVERAGE_GATE_FAILED" in result["gate_reason_codes"]
