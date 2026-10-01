from pathlib import Path


def test_ads002_v2_uses_persisted_daily_report_without_legacy_projection():
    source = Path("foundation/report_read.py").read_text()
    assert '"ads002_v2": {}' in source
    assert "latest_daily_report" in source
    assert "research_daily_report" in source
    assert "rhen_ads002_v2_daily_inputs" not in source


def test_ads002_v2_is_persisted_in_daily_report():
    source = Path("app/research_scheduler.py").read_text()
    assert '"ads002_v2": payload.get("ads002_v2") or {}' in source
    assert 'ads002_v2 = canonical.get("ads002_v2") or {}' in source
    assert '"ads002_v2": ads002_v2' in source


def test_ads002_v2_reporting_stays_research_only():
    migration = Path("database/20260929052121_ads002_v2_daily_evidence_projection.sql").read_text()
    assert "'research_only',true" in migration
    assert "'live_configuration_changed',false" in migration
    assert "'promotion_authorized',false" in migration
