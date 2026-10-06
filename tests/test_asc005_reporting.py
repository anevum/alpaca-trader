from pathlib import Path


def test_report_read_preserves_candidate_fields_and_forward_outcomes():
    source = Path("foundation/report_read.py").read_text()
    assert "candidate = dict(raw)" in source
    assert '"decision_cycle_payload"' in source
    assert '"candidate_forward_outcome"' in source
    assert "candidate_identities" in source
    assert 'row["outcomes"]' in source
    assert 'row["forward_outcomes"]' in source


def test_daily_report_persists_asc005_session_and_rolling_artifacts():
    source = Path("app/research_scheduler.py").read_text()
    assert 'DAILY_REPORT_VERSION = "rhen-daily-v1.7"' in source
    assert "_build_counterfactual_lab" in source
    assert '"session_searches": session_searches' in source
    assert '"rolling_searches": rolling_searches' in source
    assert '"proposal_ready_parameters": ready' in source
    assert '"counterfactual_lab": counterfactual_lab' in source


def test_asc005_remains_research_only():
    source = Path("app/research_agent/counterfactual_lab.py").read_text()
    assert '"execution_authority": False' in source
    assert '"risk_or_sizing_authority": False' in source
    assert '"live_configuration_changed": False' in source
    assert '"promotion_authorized": False' in source



def test_daily_report_persists_nostra_and_adaptive_control_artifacts():
    source = Path("app/research_scheduler.py").read_text()
    assert "_build_nostra_research" in source
    assert "_build_adaptive_research" in source
    assert '"nostra": nostra' in source
    assert '"adaptive_strategy_control": adaptive_control' in source
    assert '"strategy_health": adaptive_control.get("strategy_health") or {}' in source
    assert '"graen_validation": adaptive_control.get("graen_validation") or {}' in source
    assert '"nostra": nostra,' in source
    assert '"adaptive_strategy_control": adaptive_control,' in source



def test_daily_report_persists_shadow_allocation_validation_without_authority():
    source = Path("app/research_scheduler.py").read_text()
    validator = Path(
        "app/research_agent/shadow_allocation_validation.py"
    ).read_text()

    assert "evaluate_shadow_allocation(candidates)" in source
    assert '"shadow_allocation_validation": shadow_allocation_validation' in source
    assert '"capital_scaling_authorized": False' in source
    assert '"automatic_application_authorized": False' in validator
    assert '"promotion_authorized": False' in validator
    assert '"execution_authority": False' in validator



def test_daily_v17_carries_forward_evidence_readiness_without_authority():
    scheduler = Path("app/research_scheduler.py").read_text()
    store = Path("app/rhen_core/store.py").read_text()

    assert '"evidence_readiness": payload.get("evidence_readiness") or {}' in scheduler
    assert '"readiness": evidence_readiness' in scheduler
    assert '"evidence_readiness": evidence_readiness' in scheduler
    assert '"candidate_evidence_readiness.v2"' in store
    assert '"execution_authority": False' in store
    assert '"changes_live_decision": False' in store
    assert '"AWAITING_MEASURABLE_COHORT"' in store
