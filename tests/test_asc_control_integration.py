from pathlib import Path


def test_daily_scheduler_persists_full_asc_shadow_state():
    source = Path("app/research_scheduler.py").read_text()
    assert "compute_parameter_pressure" in source
    assert "transition_control_state" in source
    assert '"parameter_pressure": parameter_pressure' in source
    assert '"control_transition": control_transition' in source
    assert "build_strategy_family_registry" in source
    assert "rank_strategy_families" in source
    assert '"strategy_family_registry": family_registry' in source
    assert '"strategy_family_routing": family_routing' in source
    assert '"adaptive_strategy_control": adaptive_control' in source


def test_asc_control_remains_non_executing():
    source = Path("app/research_scheduler.py").read_text()
    assert '"automatic_application_authorized": False' in source
    assert '"execution_authority": False' in source
    assert '"risk_or_sizing_authority": False' in source
    assert '"live_configuration_changed": False' in source
    assert '"promotion_authorized": False' in source



def test_nostra_calibration_flows_into_strategy_health():
    source = Path("app/research_scheduler.py").read_text()
    assert '"transition_calibration": transition_calibration' in source
    assert '"calibration": transition_calibration' in source
    health = Path("app/research_agent/strategy_health.py").read_text()
    assert '"brier_skill_score": calibration.get("brier_skill_score")' in health
    assert "FORECAST_CALIBRATION_POSITIVE_SKILL" in health
    assert "FORECAST_CALIBRATION_SKILL_DEGRADED" in health


def test_asc_operational_notifications_are_state_change_only():
    scheduler = Path("app/research_scheduler.py").read_text()
    notifier = Path("app/slack_notifier.py").read_text()
    assert "_record_asc_notifications" in scheduler
    assert 'transition.get("changed") is True' in scheduler
    assert 'action="promotion_ready"' in scheduler
    assert 'kind == "asc"' in notifier
    assert '"health_snapshot"' not in notifier
