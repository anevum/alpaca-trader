from __future__ import annotations

import re

# RHEN v3 notification language.
#
# Runtime notifications use one installed RHEN brand mark plus a small set of
# universal semantic emoji. The retired IREN/GRAEN/NOSTRA/VELUM names remain
# accepted as compatibility inputs, but they no longer select separate brands.
BRAND_EMOJI = {
    "ANEVUM": ":anevum:",
    "RHEN": ":rhen:",
}

LEGACY_SYSTEM_MODULE = {
    "RHEN": "EXECUTION",
    "IREN": "CONTROL",
    "GRAEN": "RESEARCH",
    "VELUM": "REPLAY",
    "NOSTRA": "FORECAST",
}

MODULE_EMOJI = {
    "EXECUTION": "↗️",
    "CONTROL": "🎛️",
    "RESEARCH": "🧪",
    "REPLAY": "↺",
    "FORECAST": "🔭",
    "CORE": "🗄️",
    "WORKER": "⚙️",
    "COMMAND": "🖥️",
}

STATUS_EMOJI = {
    "ACTIVE": "🔵",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "CRITICAL": "🔴",
    "WAITING": "⏳",
    "RISK": "🛡️",
    "EVIDENCE": "◇",
}

_ROUTE_SYSTEM = {
    "iren-control": "IREN",
    "rhen-live": "RHEN",
    "rhen-daily": "RHEN",
    "rhen-research": "GRAEN",
    "rhen-alerts": "RHEN",
}

_PREFIX_RE = re.compile(r"^:(?:anevum|rhen):\s*", re.IGNORECASE)


def infer_system(text: str, *, route: str | None = None) -> str:
    """Infer a legacy compatibility owner from message text or route."""
    upper = str(text or "").upper()
    for system in ("RHEN", "IREN", "GRAEN", "VELUM", "NOSTRA", "ANEVUM"):
        if (
            upper.startswith(f"*{system} //")
            or upper.startswith(f"{system} //")
            or f"*{system} //" in upper[:100]
        ):
            return system
    if route:
        return _ROUTE_SYSTEM.get(route, "ANEVUM")
    return "ANEVUM"


def infer_iren_state(text: str) -> str | None:
    """Compatibility helper for old IREN/control health wording."""
    upper = str(text or "").upper()
    if any(
        token in upper
        for token in ("// RECOVERED //", "// SUCCEEDED", " HEALTHY", "STATE: HEALTHY")
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
    if any(token in upper for token in ("DEGRADED", "// WARNING", " WARNING")):
        return "DEGRADED"
    return None


def _word(upper: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", upper) is not None


def system_module(system: str | None) -> str:
    """Map legacy subsystem keys to their RHEN v3 module identity."""
    return LEGACY_SYSTEM_MODULE.get(str(system or "RHEN").upper(), "EXECUTION")


def infer_semantic_emoji(
    text: str,
    *,
    system: str,
    iren_state: str | None = None,
) -> str:
    """Return one stable semantic emoji for an ANEVUM/RHEN notification."""
    upper = str(text or "").upper()
    resolved = str(system or "RHEN").upper()

    if any(token in upper for token in ("FAILED", "FAILURE", "ERROR", "CRITICAL")):
        return STATUS_EMOJI["CRITICAL"]
    if any(token in upper for token in ("BLOCKED", "BREAKER", "STOP", "RISK", "PROTECTION")):
        return STATUS_EMOJI["RISK"]
    if any(token in upper for token in ("DEGRADED", "WARNING", "ATTENTION_REQUIRED", "// OPEN //", "// ESCALATED //")):
        return STATUS_EMOJI["WARNING"]
    if any(token in upper for token in ("WAITING", "MISSED", "STALE", "PENDING", "QUEUED")):
        return STATUS_EMOJI["WAITING"]
    if any(token in upper for token in ("RECOVERED", "SUCCEEDED", "COMPLETE", "COMPLETED", "VALIDATED", "PASSED", " SAFE")):
        return STATUS_EMOJI["SUCCESS"]

    if resolved == "ANEVUM":
        if any(token in upper for token in ("DEPLOY", "RELEASE", "MAINTENANCE", "REPAIR", "HOTFIX")):
            return STATUS_EMOJI["ACTIVE"]
        return "◆"

    if _word(upper, "BUY") or any(token in upper for token in ("EXECUTION", "SUBMITTED", "ORDER", "SCAN")):
        return MODULE_EMOJI["EXECUTION"]
    if _word(upper, "SELL"):
        return "↘️"
    if any(token in upper for token in ("EVIDENCE", "PERSISTENCE", "TELEMETRY", "DATA")):
        return STATUS_EMOJI["EVIDENCE"]
    if any(token in upper for token in ("RESEARCH", "DISCOVERY", "EXPERIMENT", "HYPOTHESIS", "TEST", "PROMOTION_READY")):
        return MODULE_EMOJI["RESEARCH"]
    if "REPLAY" in upper or "ARCHIVE" in upper:
        return MODULE_EMOJI["REPLAY"]
    if any(token in upper for token in ("FORECAST", "SIGNAL", "REGIME", "CALIBRATION")):
        return MODULE_EMOJI["FORECAST"]
    if any(token in upper for token in ("CONFIG", "DRIFT", "CONTROL", "ASC")):
        return MODULE_EMOJI["CONTROL"]
    if any(token in upper for token in ("CORE", "STORE", "SQLITE")):
        return MODULE_EMOJI["CORE"]
    if any(token in upper for token in ("WORKER", "AGENT")):
        return MODULE_EMOJI["WORKER"]
    if "COMMAND" in upper:
        return MODULE_EMOJI["COMMAND"]
    if any(token in upper for token in ("LIVE", "ONLINE", "MARKET OPEN", "MARKET CLOSED", "RUNNING", "STARTED", "STARTING")):
        return STATUS_EMOJI["ACTIVE"]

    state = (iren_state or infer_iren_state(text) or "").upper()
    if state == "HEALTHY":
        return STATUS_EMOJI["SUCCESS"]
    if state == "DEGRADED":
        return STATUS_EMOJI["WARNING"]
    if state == "INCIDENT":
        return STATUS_EMOJI["CRITICAL"]
    return MODULE_EMOJI[system_module(resolved)]


def emoji_prefix(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    resolved_system = (system or infer_system(text, route=route)).upper()
    brand = BRAND_EMOJI["ANEVUM"] if resolved_system == "ANEVUM" else BRAND_EMOJI["RHEN"]
    semantic = infer_semantic_emoji(
        text,
        system=resolved_system,
        iren_state=iren_state,
    )
    return f"{brand} {semantic}"


def decorate_slack_message(
    text: str,
    *,
    system: str | None = None,
    route: str | None = None,
    iren_state: str | None = None,
) -> str:
    """Prefix a Slack message with the canonical ANEVUM/RHEN v3 notification mark.

    Existing v3 branded messages are returned unchanged so routing layers may
    safely call this helper more than once.
    """
    message = str(text or "").strip()
    if not message or _PREFIX_RE.match(message):
        return message
    return f"{emoji_prefix(message, system=system, route=route, iren_state=iren_state)} {message}"


def event_module(kind: str | None) -> str:
    """Resolve a runtime event to a RHEN v3 module label."""
    normalized = str(kind or "").strip().lower()
    if normalized.startswith("nostra"):
        return "FORECAST"
    if normalized.startswith("velum"):
        return "REPLAY"
    if normalized.startswith("graen") or normalized in {
        "research_agent",
        "research_reporting",
        "research_scheduler",
        "crypto_promotion",
    }:
        return "RESEARCH"
    if normalized.startswith("iren") or normalized == "asc":
        return "CONTROL"
    if normalized in {"persistence", "core", "store"}:
        return "CORE"
    return "EXECUTION"


def event_system(kind: str | None) -> str:
    """All production runtime events now belong to the RHEN product/runtime."""
    _ = kind
    return "RHEN"
