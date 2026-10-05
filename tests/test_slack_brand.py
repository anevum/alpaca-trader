from app.slack_brand import (
    decorate_slack_message,
    emoji_prefix,
    event_module,
    event_system,
    infer_iren_state,
    infer_system,
    system_module,
)


def test_runtime_notifications_use_one_rhen_brand():
    assert decorate_slack_message("*RHEN // ONLINE*") == ":rhen: 🔵 *RHEN // ONLINE*"
    assert decorate_slack_message("*GRAEN // RESEARCH COMPLETE*") == ":rhen: ✅ *GRAEN // RESEARCH COMPLETE*"
    assert decorate_slack_message("*VELUM // EQUITY REPLAY COMPLETE*") == ":rhen: ✅ *VELUM // EQUITY REPLAY COMPLETE*"
    assert decorate_slack_message("*NOSTRA // FORECAST UPDATE*") == ":rhen: 🔭 *NOSTRA // FORECAST UPDATE*"
    assert decorate_slack_message("*ANEVUM // DEPLOYED*") == ":anevum: 🔵 *ANEVUM // DEPLOYED*"


def test_control_health_uses_shared_status_language():
    assert decorate_slack_message("*IREN // job // SUCCEEDED*") == ":rhen: ✅ *IREN // job // SUCCEEDED*"
    assert decorate_slack_message("*IREN // control // DEGRADED*") == ":rhen: ⚠️ *IREN // control // DEGRADED*"
    assert decorate_slack_message("*IREN // job // FAILED*") == ":rhen: 🔴 *IREN // job // FAILED*"
    assert decorate_slack_message("*IREN // OPEN // evidence.loss*") == ":rhen: ⚠️ *IREN // OPEN // evidence.loss*"
    assert decorate_slack_message("*IREN // RECOVERED // evidence.loss*") == ":rhen: ✅ *IREN // RECOVERED // evidence.loss*"


def test_route_fallback_maps_legacy_routes_to_rhen_modules():
    assert infer_system("scheduled report", route="iren-control") == "IREN"
    assert infer_system("scheduled report", route="rhen-daily") == "RHEN"
    assert infer_system("scheduled report", route="rhen-research") == "GRAEN"
    assert emoji_prefix("scheduled report", route="rhen-research") == ":rhen: 🧪"


def test_branding_is_idempotent_for_v3_and_legacy_prefixes():
    v3 = ":rhen: 🧪 *RHEN // RESEARCH // DISCOVERY*"
    assert decorate_slack_message(v3, system="GRAEN") == v3

    legacy = ":graen_research: *GRAEN // CRYPTO EDGE DISCOVERY*"
    assert decorate_slack_message(legacy, system="GRAEN") == legacy


def test_iren_state_inference_remains_compatible():
    assert infer_iren_state("*IREN // x // SUCCEEDED*") == "HEALTHY"
    assert infer_iren_state("*IREN // x // DEGRADED*") == "DEGRADED"
    assert infer_iren_state("*IREN // x // FAILED*") == "INCIDENT"
    assert infer_iren_state("*IREN // x // RUNNING*") is None


def test_legacy_systems_map_to_rhen_modules():
    assert system_module("RHEN") == "EXECUTION"
    assert system_module("IREN") == "CONTROL"
    assert system_module("GRAEN") == "RESEARCH"
    assert system_module("VELUM") == "REPLAY"
    assert system_module("NOSTRA") == "FORECAST"


def test_runtime_event_kind_maps_to_rhen_product_and_module():
    for kind in ("execution", "research_agent", "crypto_promotion", "asc", "nostra_forecast", "velum_replay"):
        assert event_system(kind) == "RHEN"

    assert event_module("execution") == "EXECUTION"
    assert event_module("research_agent") == "RESEARCH"
    assert event_module("crypto_promotion") == "RESEARCH"
    assert event_module("asc") == "CONTROL"
    assert event_module("nostra_forecast") == "FORECAST"
    assert event_module("velum_replay") == "REPLAY"
    assert event_module("persistence") == "CORE"


def test_execution_semantics():
    assert decorate_slack_message("*RHEN // EXECUTION BUY*") == ":rhen: ↗️ *RHEN // EXECUTION BUY*"
    assert decorate_slack_message("*RHEN // EXECUTION SELL*") == ":rhen: ↘️ *RHEN // EXECUTION SELL*"
    assert decorate_slack_message("*RHEN // EVIDENCE WARNING*") == ":rhen: ⚠️ *RHEN // EVIDENCE WARNING*"
    assert decorate_slack_message("*RHEN // RISK BLOCKED*") == ":rhen: 🛡️ *RHEN // RISK BLOCKED*"
    assert decorate_slack_message("*RHEN // SCAN STARTED*") == ":rhen: ↗️ *RHEN // SCAN STARTED*"


def test_module_and_status_semantics():
    assert decorate_slack_message("*GRAEN // HYPOTHESIS CREATED*") == ":rhen: 🧪 *GRAEN // HYPOTHESIS CREATED*"
    assert decorate_slack_message("*GRAEN // VALIDATED*") == ":rhen: ✅ *GRAEN // VALIDATED*"
    assert decorate_slack_message("*NOSTRA // FORECAST UPDATE*") == ":rhen: 🔭 *NOSTRA // FORECAST UPDATE*"
    assert decorate_slack_message("*VELUM // REPLAY FAILED*") == ":rhen: 🔴 *VELUM // REPLAY FAILED*"
    assert decorate_slack_message("*VELUM // DATA ARCHIVE*") == ":rhen: ◇ *VELUM // DATA ARCHIVE*"


def test_anevum_company_notifications_remain_distinct():
    assert decorate_slack_message("*ANEVUM // MAINTENANCE STARTED*") == ":anevum: 🔵 *ANEVUM // MAINTENANCE STARTED*"
    assert decorate_slack_message("*ANEVUM // WARNING*") == ":anevum: ⚠️ *ANEVUM // WARNING*"
    assert decorate_slack_message("*ANEVUM // COMPLETE*") == ":anevum: ✅ *ANEVUM // COMPLETE*"
