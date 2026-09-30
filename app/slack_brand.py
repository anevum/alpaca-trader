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
    "HEALTHY": ":iren_healthy:",
    "DEGRADED": ":iren_degraded:",
    "INCIDENT": ":iren_incident:",
}

_ROUTE_SYSTEM = {
    "iren-control": "IREN",
    "rhen-live": "RHEN",
    "rhen-daily": "RHEN",
    "rhen-research": "RHEN",
    "rhen-alerts": "RHEN",
}

_KNOWN_PREFIX = re.compile(
    r"^:(?:anevum|iren|rhen|nostra|graen|velum|iren_healthy|iren_degraded|iren_incident):\s*",
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
    """Map IREN operational wording to the installed IREN state emoji."""
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


def emoji_prefix(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    resolved_system = (system or infer_system(text, route=route)).upper()
    if resolved_system == "IREN":
        state = (iren_state or infer_iren_state(text) or "").upper()
        if state in IREN_STATE_EMOJI:
            return IREN_STATE_EMOJI[state]
    return SYSTEM_EMOJI.get(resolved_system, SYSTEM_EMOJI["ANEVUM"])


def decorate_slack_message(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    """Prefix a Slack message with one installed ANEVUM custom emoji.

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
