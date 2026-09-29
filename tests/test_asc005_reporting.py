from pathlib import Path


def test_report_read_exposes_candidate_checks_and_forward_outcomes():
    source = Path("supabase/functions/trading-report-read/index.ts").read_text()
    assert "'checks', c.checks" in source
    assert "'rejection_reason_codes', c.rejection_reason_codes" in source
    assert "private.trading_candidate_forward_outcomes fo" in source
    assert "fo.methodology_version='candidate-forward-v2'" in source
    assert "coalesce(outcomes.rows,'[]'::jsonb) as outcomes" in source


def test_daily_report_persists_asc005_session_and_rolling_artifacts():
    source = Path("app/research_scheduler.py").read_text()
    assert 'DAILY_REPORT_VERSION = "rhen-daily-v1.3"' in source
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
