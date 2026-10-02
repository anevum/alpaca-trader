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
    assert 'DAILY_REPORT_VERSION = "rhen-daily-v1.4"' in source
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
