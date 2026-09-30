from app.slack_brand import (
    decorate_slack_message,
    emoji_prefix,
    event_system,
    infer_iren_state,
    infer_system,
)


def test_system_prefixes_match_installed_slack_emoji():
    assert decorate_slack_message("*RHEN // ONLINE*") == ":rhen_live: *RHEN // ONLINE*"
    assert decorate_slack_message("*GRAEN // RESEARCH COMPLETE*") == ":graen_research: *GRAEN // RESEARCH COMPLETE*"
    assert decorate_slack_message("*VELUM // EQUITY REPLAY COMPLETE*") == ":velum_replay: *VELUM // EQUITY REPLAY COMPLETE*"
    assert decorate_slack_message("*NOSTRA // STATE UPDATE*") == ":nostra_up: *NOSTRA // STATE UPDATE*"
    assert decorate_slack_message("*ANEVUM // DEPLOYED*") == ":anevum_deploy: *ANEVUM // DEPLOYED*"


def test_iren_health_state_uses_installed_state_emoji():
    assert decorate_slack_message("*IREN // job // SUCCEEDED*") == ":iren: *IREN // job // SUCCEEDED*"
    assert decorate_slack_message("*IREN // control // DEGRADED*") == ":iren_alert: *IREN // control // DEGRADED*"
    assert decorate_slack_message("*IREN // job // FAILED*") == ":iren_failed: *IREN // job // FAILED*"
    assert decorate_slack_message("*IREN // OPEN // evidence.loss*") == ":iren_alert: *IREN // OPEN // evidence.loss*"
    assert decorate_slack_message("*IREN // RECOVERED // evidence.loss*") == ":iren: *IREN // RECOVERED // evidence.loss*"


def test_route_fallback_selects_correct_subsystem():
    assert infer_system("scheduled report", route="iren-control") == "IREN"
    assert infer_system("scheduled report", route="rhen-daily") == "RHEN"
    assert emoji_prefix("scheduled report", route="rhen-research") == ":rhen:"


def test_branding_is_idempotent_for_specific_custom_emoji():
    message = ":graen_research: *GRAEN // CRYPTO EDGE DISCOVERY*"
    assert decorate_slack_message(message, system="GRAEN") == message


def test_iren_state_inference():
    assert infer_iren_state("*IREN // x // SUCCEEDED*") == "HEALTHY"
    assert infer_iren_state("*IREN // x // DEGRADED*") == "DEGRADED"
    assert infer_iren_state("*IREN // x // FAILED*") == "INCIDENT"
    assert infer_iren_state("*IREN // x // RUNNING*") is None


def test_runtime_event_kind_maps_to_subsystem_identity():
    assert event_system("execution") == "RHEN"
    assert event_system("research_agent") == "GRAEN"
    assert event_system("crypto_promotion") == "GRAEN"
    assert event_system("asc") == "IREN"
    assert event_system("nostra_forecast") == "NOSTRA"
    assert event_system("velum_replay") == "VELUM"


def test_rhen_semantic_icons():
    assert decorate_slack_message("*RHEN // EXECUTION BUY*") == ":rhen_buy: *RHEN // EXECUTION BUY*"
    assert decorate_slack_message("*RHEN // EXECUTION SELL*") == ":rhen_sell: *RHEN // EXECUTION SELL*"
    assert decorate_slack_message("*RHEN // EVIDENCE WARNING*") == ":rhen_evidence: *RHEN // EVIDENCE WARNING*"
    assert decorate_slack_message("*RHEN // RISK BLOCKED*") == ":rhen_risk: *RHEN // RISK BLOCKED*"
    assert decorate_slack_message("*RHEN // SCAN STARTED*") == ":rhen_scan: *RHEN // SCAN STARTED*"


def test_graen_nostra_velum_semantic_icons():
    assert decorate_slack_message("*GRAEN // HYPOTHESIS CREATED*") == ":graen_hypothesis: *GRAEN // HYPOTHESIS CREATED*"
    assert decorate_slack_message("*GRAEN // VALIDATED*") == ":graen_validated: *GRAEN // VALIDATED*"
    assert decorate_slack_message("*NOSTRA // FORECAST UPDATE*") == ":nostra_forecast: *NOSTRA // FORECAST UPDATE*"
    assert decorate_slack_message("*NOSTRA // SIGNAL NEUTRAL*") == ":nostra_signal: *NOSTRA // SIGNAL NEUTRAL*"
    assert decorate_slack_message("*VELUM // REPLAY FAILED*") == ":velum_fail: *VELUM // REPLAY FAILED*"
    assert decorate_slack_message("*VELUM // DATA ARCHIVE*") == ":velum_archive: *VELUM // DATA ARCHIVE*"


def test_anevum_semantic_icons():
    assert decorate_slack_message("*ANEVUM // MAINTENANCE STARTED*") == ":anevum_maintenance: *ANEVUM // MAINTENANCE STARTED*"
    assert decorate_slack_message("*ANEVUM // WARNING*") == ":anevum_warning: *ANEVUM // WARNING*"
    assert decorate_slack_message("*ANEVUM // COMPLETE*") == ":anevum_complete: *ANEVUM // COMPLETE*"
