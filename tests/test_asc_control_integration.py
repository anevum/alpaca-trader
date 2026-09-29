from pathlib import Path


def test_daily_scheduler_persists_full_asc_shadow_state():
    source = Path("app/research_scheduler.py").read_text()
    assert "compute_parameter_pressure" in source
    assert "transition_control_state" in source
    assert '"parameter_pressure": parameter_pressure' in source
    assert '"control_transition": control_transition' in source
    assert '"strategy_family_routing": rank_strategy_families(' in source
    assert '"adaptive_strategy_control": adaptive_control' in source


def test_asc_control_remains_non_executing():
    source = Path("app/research_scheduler.py").read_text()
    assert '"automatic_application_authorized": False' in source
    assert '"execution_authority": False' in source
    assert '"risk_or_sizing_authority": False' in source
    assert '"live_configuration_changed": False' in source
    assert '"promotion_authorized": False' in source
