from __future__ import annotations

import re

SYSTEM_EMOJI = {
    "ANEVUM": ":anevum:",
    "IREN": ":iren:",
    "RHEN": ":rhen:",
    "NOSTRA": ":nostra:",
    "GRAEN": ":graen:",
    "VELUM": ":velum:",
}

IREN_STATE_EMOJI = {
    "HEALTHY": ":iren:",
    "DEGRADED": ":iren_alert:",
    "INCIDENT": ":iren_alert:",
}

_ROUTE_SYSTEM = {
    "iren-control": "IREN",
    "rhen-live": "RHEN",
    "rhen-daily": "RHEN",
    "rhen-research": "RHEN",
    "rhen-alerts": "RHEN",
}

_KNOWN_PREFIX = re.compile(
    r"^:(?:anevum|iren|rhen|nostra|graen|velum)(?:_[a-z0-9_]+)?:\s*",
    re.IGNORECASE,
)


def infer_system(text: str, *, route: str | None = None) -> str:
    """Infer the subsystem represented by an operational Slack message."""
    upper = str(text or "").upper()
    for system in ("IREN", "RHEN", "NOSTRA", "GRAEN", "VELUM", "ANEVUM"):
        if (
            upper.startswith(f"*{system} //")
            or upper.startswith(f"{system} //")
            or f"*{system} //" in upper[:80]
        ):
            return system
    if route:
        return _ROUTE_SYSTEM.get(route, "ANEVUM")
    return "ANEVUM"


def infer_iren_state(text: str) -> str | None:
    """Map IREN operational wording into broad health states."""
    upper = str(text or "").upper()
    if any(
        token in upper
        for token in (
            "// RECOVERED //",
            "// SUCCEEDED",
            " HEALTHY",
            "STATE: HEALTHY",
        )
    ):
        return "HEALTHY"
    if any(
        token in upper
        for token in (
            "// OPEN //",
            "// ESCALATED //",
            "// FAILED",
            "// MISSED",
            "// STALE",
            "ATTENTION_REQUIRED",
            " INCIDENT",
        )
    ):
        return "INCIDENT"
    if any(
        token in upper
        for token in (
            "DEGRADED",
            "// WARNING",
            " WARNING",
        )
    ):
        return "DEGRADED"
    return None


def _word(upper: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", upper) is not None


def infer_semantic_emoji(
    text: str,
    *,
    system: str,
    iren_state: str | None = None,
) -> str:
    """Choose the most specific installed ANEVUM Slack emoji for a message."""
    upper = str(text or "").upper()
    resolved = system.upper()

    if resolved == "IREN":
        if any(token in upper for token in ("FAILED", "FAILURE", "ERROR")):
            return ":iren_failed:"
        if any(token in upper for token in ("WAITING", "MISSED", "STALE", "PENDING", "QUEUED")):
            return ":iren_waiting:"
        if any(token in upper for token in ("CONFIG", "DRIFT")):
            return ":iren_config:"
        if any(token in upper for token in ("WORKING", "RUNNING", "STARTED", "STARTING")):
            return ":iren_working:"
        if any(
            token in upper
            for token in (
                "ALERT",
                "DEGRADED",
                "WARNING",
                "CRITICAL",
                "INCIDENT",
                "// OPEN //",
                "// ESCALATED //",
                "ATTENTION_REQUIRED",
            )
        ):
            return ":iren_alert:"
        state = (iren_state or infer_iren_state(text) or "").upper()
        if state in IREN_STATE_EMOJI:
            return IREN_STATE_EMOJI[state]
        return ":iren:"

    if resolved == "RHEN":
        if _word(upper, "BUY"):
            return ":rhen_buy:"
        if _word(upper, "SELL"):
            return ":rhen_sell:"
        if any(token in upper for token in ("EVIDENCE", "PERSISTENCE", "TELEMETRY")):
            return ":rhen_evidence:"
        if any(token in upper for token in ("RISK", "PROTECTION", "BREAKER", "BLOCKED", "STOP")):
            return ":rhen_risk:"
        if "SCAN" in upper:
            return ":rhen_scan:"
        if any(token in upper for token in ("EXECUTION", "SUBMITTED", "ORDER")):
            return ":rhen_execution:"
        if any(token in upper for token in ("LIVE", "ONLINE", "MARKET OPEN", "MARKET CLOSED")):
            return ":rhen_live:"
        return ":rhen:"

    if resolved == "GRAEN":
        if any(token in upper for token in ("REJECTED", "REJECT")):
            return ":graen_rejected:"
        if any(token in upper for token in ("VALIDATED", "VALIDATION", "PROMOTION_READY")):
            return ":graen_validated:"
        if "HYPOTHESIS" in upper:
            return ":graen_hypothesis:"
        if "TEST" in upper:
            return ":graen_test:"
        if any(token in upper for token in ("RESEARCH", "DISCOVERY", "EXPERIMENT")):
            return ":graen_research:"
        return ":graen:"

    if resolved == "NOSTRA":
        if "FORECAST" in upper:
            return ":nostra_forecast:"
        if "SIGNAL" in upper:
            return ":nostra_signal:"
        if _word(upper, "UP"):
            return ":nostra_up:"
        if _word(upper, "DOWN"):
            return ":nostra_down:"
        if "NEUTRAL" in upper:
            return ":nostra_neutral:"
        return ":nostra:"

    if resolved == "VELUM":
        if any(token in upper for token in ("FAILED", "FAILURE", "ERROR")):
            return ":velum_fail:"
        if "ARCHIVE" in upper:
            return ":velum_archive:"
        if "DATA" in upper:
            return ":velum_data:"
        if "REPLAY" in upper:
            return ":velum_replay:"
        if any(token in upper for token in ("PASS", "PASSED", "SUCCEEDED", "COMPLETE", "COMPLETED")):
            return ":velum_pass:"
        return ":velum:"

    if resolved == "ANEVUM":
        if any(token in upper for token in ("DEPLOY", "RELEASE")):
            return ":anevum_deploy:"
        if any(token in upper for token in ("MAINTENANCE", "REPAIR", "HOTFIX")):
            return ":anevum_maintenance:"
        if any(token in upper for token in ("WARNING", "FAILED", "FAILURE", "ERROR", "DEGRADED")):
            return ":anevum_warning:"
        if any(token in upper for token in ("PRIORITY", "ATTENTION_REQUIRED", "URGENT")):
            return ":anevum_priority:"
        if any(token in upper for token in ("COMPLETE", "COMPLETED", "SUCCEEDED", "RECOVERED")):
            return ":anevum_complete:"
        if "MESSAGE" in upper:
            return ":anevum_message:"
        return ":anevum:"

    return SYSTEM_EMOJI.get(resolved, SYSTEM_EMOJI["ANEVUM"])


def emoji_prefix(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    resolved_system = (system or infer_system(text, route=route)).upper()
    return infer_semantic_emoji(
        text,
        system=resolved_system,
        iren_state=iren_state,
    )


def decorate_slack_message(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    """Prefix a Slack message with the most specific installed ANEVUM emoji.

    Existing branded messages are returned unchanged so routing layers may safely
    call this helper more than once.
    """
    message = str(text or "").strip()
    if not message or _KNOWN_PREFIX.match(message):
        return message
    return f"{emoji_prefix(message, system=system, route=route, iren_state=iren_state)} {message}"


def event_system(kind: str | None) -> str:
    """Resolve the subsystem identity for direct runtime event notifications."""
    normalized = str(kind or "").strip().lower()
    if normalized.startswith("nostra"):
        return "NOSTRA"
    if normalized.startswith("velum"):
        return "VELUM"
    if normalized.startswith("graen") or normalized in {
        "research_agent",
        "research_reporting",
        "research_scheduler",
        "crypto_promotion",
    }:
        return "GRAEN"
    if normalized.startswith("iren") or normalized == "asc":
        return "IREN"
    return "RHEN"
