from app.slack_brand import decorate_slack_message, emoji_prefix, infer_iren_state, infer_system


def test_system_prefixes_match_installed_slack_emoji():
    assert decorate_slack_message("*RHEN // ONLINE*") == ":rhen: *RHEN // ONLINE*"
    assert decorate_slack_message("*GRAEN // RESEARCH COMPLETE*") == ":graen: *GRAEN // RESEARCH COMPLETE*"
    assert decorate_slack_message("*VELUM // EQUITY REPLAY COMPLETE*") == ":velum: *VELUM // EQUITY REPLAY COMPLETE*"
    assert decorate_slack_message("*NOSTRA // STATE UPDATE*") == ":nostra: *NOSTRA // STATE UPDATE*"
    assert decorate_slack_message("*ANEVUM // DEPLOYED*") == ":anevum: *ANEVUM // DEPLOYED*"


def test_iren_health_state_uses_state_specific_emoji():
    assert decorate_slack_message("*IREN // job // SUCCEEDED*") == ":iren_healthy: *IREN // job // SUCCEEDED*"
    assert decorate_slack_message("*IREN // control // DEGRADED*") == ":iren_degraded: *IREN // control // DEGRADED*"
    assert decorate_slack_message("*IREN // job // FAILED*") == ":iren_incident: *IREN // job // FAILED*"
    assert decorate_slack_message("*IREN // OPEN // evidence.loss*") == ":iren_incident: *IREN // OPEN // evidence.loss*"
    assert decorate_slack_message("*IREN // RECOVERED // evidence.loss*") == ":iren_healthy: *IREN // RECOVERED // evidence.loss*"


def test_route_fallback_selects_correct_subsystem():
    assert infer_system("scheduled report", route="iren-control") == "IREN"
    assert infer_system("scheduled report", route="rhen-daily") == "RHEN"
    assert emoji_prefix("scheduled report", route="rhen-research") == ":rhen:"


def test_branding_is_idempotent():
    message = ":graen: *GRAEN // CRYPTO EDGE DISCOVERY*"
    assert decorate_slack_message(message, system="GRAEN") == message


def test_iren_state_inference():
    assert infer_iren_state("*IREN // x // SUCCEEDED*") == "HEALTHY"
    assert infer_iren_state("*IREN // x // DEGRADED*") == "DEGRADED"
    assert infer_iren_state("*IREN // x // FAILED*") == "INCIDENT"
    assert infer_iren_state("*IREN // x // RUNNING*") is None
