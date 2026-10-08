from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


UTC = timezone.utc
SCHEMA_VERSION = "rhen-research-package-v1"


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _daily_summary(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"available": False}
    performance = report.get("performance")
    performance = performance if isinstance(performance, dict) else {}
    evidence = report.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    return {
        "available": True,
        "session": report.get("session"),
        "strategy_version_id": report.get("strategy_version_id"),
        "classification": report.get("classification")
        or report.get("decision")
        or report.get("status"),
        "closed_trades": performance.get("closed_trades")
        or performance.get("trades"),
        "net_pnl": performance.get("net_pnl"),
        "return_pct": performance.get("return_pct"),
        "win_rate": performance.get("win_rate"),
        "expectancy": performance.get("expectancy"),
        "profit_factor": performance.get("profit_factor"),
        "max_drawdown_pct": performance.get("max_drawdown_pct"),
        "candidate_count": evidence.get("candidate_count"),
        "qualified_count": evidence.get("qualified_count"),
        "blocker_count": evidence.get("blocker_count"),
    }


def _markdown(
    *,
    generated_at: str,
    strategy: dict[str, Any],
    evidence_cutoff: str | None,
    readiness: dict[str, Any],
    daily: dict[str, Any],
    forecast_count: int,
    recent_runs: int,
    hypothesis_count: int,
) -> str:
    questions = readiness.get("strategy_questions")
    questions = questions if isinstance(questions, list) else []
    lines = [
        "# RHEN Research Handoff",
        "",
        f"Generated: {generated_at}",
        f"Strategy: {strategy.get('strategy_version_id') or strategy.get('version_id') or 'UNKNOWN'}",
        f"Evidence cutoff: {evidence_cutoff or 'NONE'}",
        f"Readiness: {readiness.get('state') or 'UNKNOWN'}",
        (
            "Evidence state: "
            f"{int(readiness.get('blocker_count') or 0)} blockers · "
            f"{int(readiness.get('limitation_count') or 0)} limitations · "
            f"{int(readiness.get('strategy_question_count') or 0)} strategy questions"
        ),
        f"Open forward forecasts: {forecast_count}",
        f"Recent deterministic review runs: {recent_runs}",
        f"Recorded research hypotheses: {hypothesis_count}",
        "",
        "## Latest session",
        (
            f"Session: {daily.get('session') or 'unavailable'} · "
            f"classification: {daily.get('classification') or 'unavailable'} · "
            f"closed trades: {daily.get('closed_trades') if daily.get('closed_trades') is not None else 'unavailable'}"
        ),
        "",
        "## Bounded questions",
    ]
    if questions:
        for row in questions[:10]:
            if not isinstance(row, dict):
                continue
            question = (
                row.get("question")
                or row.get("title")
                or row.get("research_question_id")
                or "Unnamed question"
            )
            state = row.get("semantic_readiness") or row.get("status") or "UNKNOWN"
            lines.append(f"- [{state}] {question}")
    else:
        lines.append("- No strategy question is currently recorded.")

    lines.extend(
        [
            "",
            "## Authority",
            "- Research-only package.",
            "- No broker-write authority.",
            "- No live strategy/risk mutation authority.",
            "- No automatic promotion authority.",
            "- AI/model reasoning is optional and operator-invoked outside the live order path.",
            "",
            "## Operator instruction",
            (
                "Use the attached structured evidence to identify at most one highest-information "
                "bounded experiment. State the observed problem, evidence, hypothesis, expected "
                "mechanism, replay/validation plan, success threshold, rejection condition, "
                "confounders, and affected code/config. Do not authorize a live promotion."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def build_research_package(
    canonical: dict[str, Any],
    readiness: dict[str, Any],
    forecasts: dict[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = (generated_at or datetime.now(UTC)).astimezone(UTC)
    stamp = now.isoformat()
    strategy = dict(canonical.get("current_strategy") or {})
    agent_runs = list(canonical.get("agent_runs") or [])
    ledger = dict(canonical.get("search_ledger") or {})
    exposure = dict(ledger.get("exposure") or {})
    daily_report = canonical.get("latest_daily_report")
    weekly_report = canonical.get("latest_weekly_report")
    daily = _daily_summary(daily_report)
    forecast_rows = list(forecasts.get("forecasts") or [])

    structured = {
        "current_strategy": strategy,
        "evidence_cutoff": canonical.get("evidence_cutoff"),
        "latest_daily_report": daily_report,
        "latest_weekly_report": weekly_report,
        "deterministic_readiness": readiness,
        "active_nostra_forecasts": forecast_rows,
        "recent_deterministic_runs": agent_runs[:20],
        "research_ledger": {
            "exposure": exposure,
            "recent_hypotheses": list(ledger.get("recent_hypotheses") or [])[:25],
            "recent_events": list(ledger.get("recent_events") or [])[:50],
        },
    }

    package = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": stamp,
        "research_only": True,
        "execution_authority": False,
        "broker_write_authority": False,
        "live_strategy_mutation_authority": False,
        "production_promotion_authority": False,
        "model_required": False,
        "summary": {
            "strategy_version_id": strategy.get("strategy_version_id")
            or strategy.get("version_id"),
            "evidence_cutoff": canonical.get("evidence_cutoff"),
            "readiness_state": readiness.get("state"),
            "blocker_count": int(readiness.get("blocker_count") or 0),
            "limitation_count": int(readiness.get("limitation_count") or 0),
            "strategy_question_count": int(
                readiness.get("strategy_question_count") or 0
            ),
            "active_forecast_count": len(forecast_rows),
            "recent_review_run_count": len(agent_runs[:20]),
            "hypothesis_count": int(
                exposure.get("hypothesis_count")
                if exposure.get("hypothesis_count") is not None
                else _count(ledger.get("recent_hypotheses"))
            ),
            "latest_session": daily,
        },
        "evidence": structured,
    }
    package["handoff_markdown"] = _markdown(
        generated_at=stamp,
        strategy=strategy,
        evidence_cutoff=canonical.get("evidence_cutoff"),
        readiness=readiness,
        daily=daily,
        forecast_count=len(forecast_rows),
        recent_runs=len(agent_runs[:20]),
        hypothesis_count=int(package["summary"]["hypothesis_count"]),
    )
    package["content_fingerprint"] = __import__("hashlib").sha256(
        json.dumps(structured, sort_keys=True, default=str).encode()
    ).hexdigest()
    return package
