from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from foundation.iren_gateway import iren_read
from foundation.iren_work_gateway import command_create, snapshot
from foundation.report_read import _btc_aggressive_70_projection


UTC = timezone.utc


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _fresh(value: Any, *, now: datetime) -> bool:
    stamp = _parse_stamp(value)
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return 0 <= age <= 180


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value] if isinstance(value, list) else []


def _summary(work: dict[str, Any]) -> dict[str, int]:
    objectives = _rows(work.get("objectives"))
    jobs = _rows(work.get("jobs"))
    active = [
        row
        for row in jobs
        if str(row.get("status") or "") in {
            "QUEUED", "RUNNING", "WAITING", "BLOCKED", "NEEDS_APPROVAL"
        }
    ]
    blocked = [
        row for row in objectives if str(row.get("status") or "") == "BLOCKED"
    ]
    decisions = [
        row
        for row in jobs
        if str(row.get("status") or "") == "NEEDS_APPROVAL"
        or row.get("requires_human") is True
    ]
    complete = sum(
        1 for row in objectives if str(row.get("status") or "") == "COMPLETE"
    )
    return {
        "objective_count": len(objectives),
        "objectives_complete": complete,
        "active_jobs": len(active),
        "blocked_objectives": len(blocked),
        "requires_human": len(decisions),
    }



def _operator_guidance(
    *,
    stale: bool,
    current_state: str,
    incidents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if stale:
        return [{
            "severity": "critical",
            "target": "IREN / Foundation",
            "title": "Canonical operations state is stale",
            "action": (
                "Restore fresh IREN observations before trusting downstream status. "
                "Check the canonical scheduler, Foundation availability, and durable state first."
            ),
        }]

    rows: list[dict[str, str]] = []
    for incident in incidents:
        key = str(incident.get("key") or "")
        severity = (
            "critical"
            if str(incident.get("severity") or "").lower() == "critical"
            else "warning"
        )
        if key.startswith("service."):
            rows.append({
                "severity": severity,
                "target": key.removeprefix("service."),
                "title": "Runtime health is unavailable or unhealthy",
                "action": (
                    "Inspect the service deployment and logs, verify its health endpoint "
                    "and dependencies, then let IREN confirm recovery."
                ),
            })
        elif key.startswith("evidence."):
            rows.append({
                "severity": severity,
                "target": "Foundation evidence",
                "title": "Durable evidence delivery is degraded",
                "action": (
                    "Check Foundation ingest and the RHEN evidence spool. Confirm new events "
                    "are durably landing before evaluating research or retrying downstream work."
                ),
            })
        elif key.startswith("scheduler.") or key.startswith("workflow."):
            rows.append({
                "severity": severity,
                "target": "IREN scheduler",
                "title": "Scheduled work is degraded",
                "action": (
                    "Inspect the latest scheduler run and failure classification. Retry only "
                    "idempotent work after the underlying dependency is healthy."
                ),
            })
        elif key.startswith("configuration."):
            rows.append({
                "severity": severity,
                "target": "Protected configuration",
                "title": "Configuration identity changed",
                "action": (
                    "Compare the current protected configuration with the recorded baseline "
                    "and explain the drift before accepting a new baseline."
                ),
            })
        elif key.startswith("safety."):
            rows.append({
                "severity": "critical",
                "target": "Safety boundary",
                "title": "Protected runtime invariant failed",
                "action": (
                    "Keep execution authority unchanged. Restore the expected safe state "
                    "before promotion, execution, or configuration changes."
                ),
            })
        else:
            rows.append({
                "severity": severity,
                "target": key or "ANEVUM",
                "title": str(incident.get("reason") or "Operational incident").replace("_", " "),
                "action": (
                    "Inspect the affected subsystem and its latest deployment evidence, "
                    "repair the root cause, then wait for IREN to verify recovery."
                ),
            })

    if not rows and current_state != "HEALTHY":
        rows.append({
            "severity": "warning",
            "target": "ANEVUM",
            "title": "Control state is not healthy",
            "action": (
                "Review runtime readiness and dependency freshness, then allow the next "
                "IREN observation cycle to confirm the state."
            ),
        })
    return rows


def _operator_projection(
    *,
    state: dict[str, Any],
    control: dict[str, Any],
    work: dict[str, Any],
    stale: bool,
    incidents: list[dict[str, Any]],
    current_state: str,
) -> dict[str, Any]:
    topology = state.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    services = _rows(topology.get("services"))
    independent = [
        row for row in services if row.get("independent_runtime") is True
    ]
    ready = [
        row
        for row in independent
        if row.get("readiness") is True
        and str(row.get("status") or "") in {"RUNNING", "IDLE"}
    ]
    problems = [
        row
        for row in independent
        if str(row.get("status") or "") not in {"RUNNING", "IDLE"}
        or row.get("readiness") is not True
    ]

    summary = _summary(work)
    transitions = []
    for row in _rows(control.get("events"))[:20]:
        event = row.get("event")
        if not isinstance(event, dict):
            continue
        transitions.append({
            "key": event.get("key"),
            "transition": event.get("transition"),
            "severity": event.get("severity"),
            "reason": event.get("reason"),
            "created_at": row.get("created_at"),
            "delivery_status": row.get("delivery_status"),
        })

    guidance = _operator_guidance(
        stale=stale,
        current_state=current_state,
        incidents=incidents,
    )
    if stale:
        message = (
            "Canonical ANEVUM operations state is stale. Restore IREN/Foundation "
            "before trusting downstream health."
        )
    elif current_state == "HEALTHY" and not guidance:
        message = (
            f"ANEVUM healthy. {len(ready)}/{len(independent)} independent runtimes "
            "ready. No operator action required."
        )
    else:
        message = (
            f"{len(guidance)} operator item"
            + ("" if len(guidance) == 1 else "s")
            + " require review."
        )

    return {
        "version": "anevum_operator.v1",
        "state": "STALE" if stale else current_state,
        "message": message,
        "inventory": {
            "independent_runtimes": len(independent),
            "ready": len(ready),
            "problems": len(problems),
            "complete": topology.get("inventory_complete") is True,
            "gaps": topology.get("inventory_gaps") or {},
            "verified_at": topology.get("inventory_verified_at"),
        },
        "work": summary,
        "guidance": guidance,
        "recent_transitions": transitions,
        "authority": {
            "read_only_projection": True,
            "trading_mutations": False,
            "protected_actions_bypassed": False,
        },
    }

def _as_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


_RESEARCH_METRIC_ALIASES = {
    "trade_count": "trades",
    "trades": "trades",
    "closed_trades": "trades",
    "independent_days": "independent_days",
    "independent_day_blocks": "independent_days",
    "expectancy_per_trade": "expectancy",
    "expectancy_per_trade_pct": "expectancy_pct",
    "net_expectancy": "expectancy",
    "profit_factor": "profit_factor",
    "max_drawdown": "max_drawdown",
    "max_drawdown_pct": "max_drawdown_pct",
    "net_return": "net_return",
    "return_pct": "return_pct",
    "win_rate": "win_rate",
    "win_rate_pct": "win_rate_pct",
    "sample_count": "sample_count",
    "candidate_count": "candidate_count",
}


def _research_metrics(value: Any, *, depth: int = 0) -> dict[str, float]:
    """Extract a bounded set of display metrics from persisted research output."""
    metrics: dict[str, float] = {}
    if depth > 4:
        return metrics
    if isinstance(value, dict):
        for key, raw in value.items():
            alias = _RESEARCH_METRIC_ALIASES.get(str(key))
            if alias and alias not in metrics:
                number = _as_number(raw)
                if number is not None:
                    metrics[alias] = number
            if isinstance(raw, (dict, list)):
                nested = _research_metrics(raw, depth=depth + 1)
                for nested_key, nested_value in nested.items():
                    metrics.setdefault(nested_key, nested_value)
    elif isinstance(value, list):
        for raw in value[:24]:
            nested = _research_metrics(raw, depth=depth + 1)
            for nested_key, nested_value in nested.items():
                metrics.setdefault(nested_key, nested_value)
    return metrics


def _find_research_list(value: Any, key: str, *, depth: int = 0) -> list[Any] | None:
    if depth > 5:
        return None
    if isinstance(value, dict):
        direct = value.get(key)
        if isinstance(direct, list):
            return direct
        preferred = (
            "baseline", "result", "development", "validation", "holdout",
            "primary", "high", "base", "low", "scenarios", "stage_results",
        )
        for child_key in preferred:
            child = value.get(child_key)
            found = _find_research_list(child, key, depth=depth + 1)
            if found is not None:
                return found
        for child in value.values():
            if isinstance(child, (dict, list)):
                found = _find_research_list(child, key, depth=depth + 1)
                if found is not None:
                    return found
    elif isinstance(value, list):
        for child in value[:12]:
            found = _find_research_list(child, key, depth=depth + 1)
            if found is not None:
                return found
    return None


def _research_series(value: Any) -> list[dict[str, Any]]:
    """Build real chart points from persisted replay equity/trade evidence."""
    curve = _find_research_list(value, "equity_curve")
    if curve:
        rows: list[tuple[str | None, float]] = []
        for index, point in enumerate(curve):
            if not isinstance(point, dict):
                continue
            equity = _as_number(point.get("equity"))
            if equity is None:
                continue
            rows.append((str(point.get("at") or index), equity))
        if rows:
            base = rows[0][1]
            if base:
                step = max(1, len(rows) // 120)
                sampled = rows[::step]
                if sampled[-1] != rows[-1]:
                    sampled.append(rows[-1])
                return [{
                    "key": "normalized_return",
                    "label": "Normalized return",
                    "unit": "%",
                    "points": [
                        {
                            "at": stamp,
                            "value": round(((equity / base) - 1.0) * 100.0, 6),
                        }
                        for stamp, equity in sampled
                    ],
                }]

    trades = _find_research_list(value, "trades")
    if trades:
        equity = 1.0
        points: list[dict[str, Any]] = [{"at": "start", "value": 0.0}]
        for index, trade in enumerate(trades[:500]):
            if not isinstance(trade, dict):
                continue
            trade_return = _as_number(trade.get("net_return"))
            if trade_return is None:
                trade_return = _as_number(trade.get("return_pct"))
            if trade_return is None:
                continue
            # VELUM stores return_pct as a unit fraction in replay results.
            equity *= 1.0 + trade_return
            points.append({
                "at": str(
                    trade.get("exit_at")
                    or trade.get("entry_at")
                    or index + 1
                ),
                "value": round((equity - 1.0) * 100.0, 6),
            })
        if len(points) > 1:
            step = max(1, len(points) // 120)
            sampled = points[::step]
            if sampled[-1] != points[-1]:
                sampled.append(points[-1])
            return [{
                "key": "trade_return",
                "label": "Cumulative net return",
                "unit": "%",
                "points": sampled,
            }]
    return []


def _serialize_research(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "hex") and not isinstance(value, (str, bytes, dict, list)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_research(raw) for key, raw in value.items()}
    if isinstance(value, list):
        return [_serialize_research(raw) for raw in value]
    return value


def _research_activity(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Private research/replay observability for the 3-second Command poll."""
    now = datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                problem_id,title,status,metadata->>'research_stage' as research_stage,
                metadata->>'candidate_id' as candidate_id,
                metadata->>'hypothesis' as hypothesis,
                metadata->>'family' as family,
                metadata->>'mechanism' as mechanism,
                metadata->>'campaign_id' as campaign_id,
                updated_at,started_at,completed_at
            from graen.problems
            order by
              case status when 'RUNNING' then 0 when 'QUEUED' then 1
                          when 'WAITING' then 2 when 'BLOCKED' then 3 else 4 end,
              updated_at desc
            limit 50
            """
        )
        graen_problems = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select
                run_id,problem_id,status,methodology_version,result_summary,
                started_at,completed_at,created_at
            from graen.runs
            order by started_at desc
            limit 80
            """
        )
        graen_runs = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select
                artifact_id,problem_id,run_id,artifact_type,methodology_version,
                content,created_at
            from graen.artifacts
            order by created_at desc
            limit 100
            """
        )
        graen_artifacts = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select
                replay.replay_id,replay.asset_class,replay.methodology_version,
                replay.strategy_version_id,replay.range_start,replay.range_end,
                replay.status,replay.started_at,replay.completed_at,
                result.result_type,result.summary
            from velum.replays replay
            left join lateral (
                select result_type,summary
                from velum.results
                where replay_id=replay.replay_id
                order by created_at desc
                limit 1
            ) result on true
            order by coalesce(replay.completed_at,replay.started_at) desc nulls last
            limit 50
            """
        )
        velum_replays = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select event_id,event_key,event_type,occurred_at,source,run_id,
                   strategy_version_id,payload
            from rhen.events
            where event_type in (
                'velum_replay_progress',
                'velum_replay_result',
                'velum_graen_candidate_replay'
            )
               or strategy_version_id like 'CRYPTO-XSECT-PAPER-%'
            order by occurred_at desc,event_id desc
            limit 320
            """
        )
        research_events = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select calibration_id,model_version,methodology_version,range_start,
                   range_end,sample_count,metrics,created_at
            from nostra.calibration_runs
            order by created_at desc
            limit 30
            """
        )
        nostra_calibrations = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select f.forecast_id,f.forecast_key,f.model_version,f.methodology_version,
                   f.subject,f.horizon_start,f.horizon_end,f.issued_at,f.prediction,
                   o.observed_at,o.outcome,o.scoring
            from nostra.forecasts f
            left join nostra.outcomes o on o.forecast_id=f.forecast_id
            order by f.issued_at desc
            limit 40
            """
        )
        nostra_forecasts = [
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select worker_id,runtime_version,deployment_id,heartbeat_at,
                   active_problem_id,queue_depth,last_error,updated_at
            from graen.runtime_state
            where singleton=true
            limit 1
            """
        )
        runtime_row = cur.fetchone()
        graen_runtime = (
            dict(zip([column.name for column in cur.description], runtime_row))
            if runtime_row else None
        )

    problems_by_id = {
        str(row.get("problem_id")): row
        for row in graen_problems
        if row.get("problem_id") is not None
    }
    artifacts_by_run: dict[str, list[dict[str, Any]]] = {}
    for artifact in reversed(graen_artifacts):
        run_id = artifact.get("run_id")
        if run_id is None:
            continue
        artifacts_by_run.setdefault(str(run_id), []).append(artifact)

    runs: list[dict[str, Any]] = []
    for row in graen_runs:
        run_id = str(row.get("run_id") or "")
        summary = dict(row.get("result_summary") or {})
        problem = problems_by_id.get(str(row.get("problem_id") or ""), {})
        artifacts = artifacts_by_run.get(run_id, [])
        richest = summary
        for artifact in reversed(artifacts):
            content = artifact.get("content")
            if isinstance(content, dict) and (
                _research_series(content) or len(_research_metrics(content)) > len(_research_metrics(richest))
            ):
                richest = content
                break
        status = str(row.get("status") or "UNKNOWN").upper()
        if status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            progress = 100
        elif status == "WAITING":
            progress = 80
        elif status == "BLOCKED":
            progress = 70
        elif status == "RUNNING":
            progress = min(85, 25 + len(artifacts) * 12)
        else:
            progress = min(60, len(artifacts) * 10)
        runs.append({
            "run_id": run_id,
            "system": "GRAEN",
            "kind": "RESEARCH",
            "title": problem.get("title") or summary.get("research_batch_id") or "GRAEN research run",
            "status": status,
            "stage": problem.get("research_stage") or summary.get("state") or summary.get("status"),
            "progress_pct": progress,
            "problem_id": str(row.get("problem_id") or "") or None,
            "candidate_id": problem.get("candidate_id") or summary.get("candidate_id"),
            "methodology_version": row.get("methodology_version") or summary.get("methodology_version"),
            "strategy_version_id": summary.get("strategy_version_id"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "updated_at": row.get("completed_at") or row.get("started_at") or row.get("created_at"),
            "metrics": _research_metrics(richest),
            "series": _research_series(richest),
            "artifact_count": len(artifacts),
            "detail": {
                "decision": summary.get("decision"),
                "next_action": summary.get("next_action"),
                "hypothesis": problem.get("hypothesis"),
                "family": problem.get("family"),
                "mechanism": problem.get("mechanism"),
                "campaign_id": problem.get("campaign_id"),
            },
        })

    for replay in velum_replays:
        summary = dict(replay.get("summary") or {})
        status = str(replay.get("status") or "UNKNOWN").upper()
        runs.append({
            "run_id": str(replay.get("replay_id") or ""),
            "system": "VELUM",
            "kind": "REPLAY",
            "title": (
                str(replay.get("asset_class") or "research").upper()
                + " replay"
            ),
            "status": status,
            "stage": replay.get("result_type") or "REPLAY",
            "progress_pct": 100 if status in {"SUCCEEDED", "COMPLETED", "FAILED"} else 55,
            "methodology_version": replay.get("methodology_version"),
            "strategy_version_id": replay.get("strategy_version_id"),
            "started_at": replay.get("started_at"),
            "completed_at": replay.get("completed_at"),
            "updated_at": replay.get("completed_at") or replay.get("started_at"),
            "metrics": _research_metrics(summary),
            "series": _research_series(summary),
            "detail": {
                "asset_class": replay.get("asset_class"),
                "range_start": replay.get("range_start"),
                "range_end": replay.get("range_end"),
            },
        })

    # VELUM candidate replays persist full result evidence in rhen.events even when
    # they are not represented by the generic velum.replays table.
    velum_event_runs: dict[str, dict[str, Any]] = {}
    paper_events: dict[str, list[dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    for row in reversed(research_events):
        payload = dict(row.get("payload") or {})
        event_type = str(row.get("event_type") or "")
        occurred_at = row.get("occurred_at")
        strategy_version_id = row.get("strategy_version_id")
        if event_type.startswith("velum_"):
            run_id = str(
                payload.get("graen_run_id")
                or (payload.get("run_manifest") or {}).get("velum_run_id")
                or payload.get("problem_id")
                or row.get("event_key")
                or row.get("event_id")
            )
            current = velum_event_runs.setdefault(run_id, {
                "run_id": run_id,
                "system": "VELUM",
                "kind": "CANDIDATE_REPLAY",
                "title": payload.get("candidate_id") or "VELUM candidate replay",
                "status": "RUNNING",
                "stage": "QUEUED",
                "progress_pct": 0,
                "problem_id": payload.get("problem_id"),
                "candidate_id": payload.get("candidate_id"),
                "methodology_version": payload.get("candidate_methodology") or payload.get("methodology_version"),
                "strategy_version_id": strategy_version_id,
                "started_at": occurred_at,
                "completed_at": None,
                "updated_at": occurred_at,
                "metrics": {},
                "series": [],
                "detail": {},
            })
            current["updated_at"] = occurred_at
            if event_type == "velum_replay_progress":
                current["stage"] = payload.get("phase") or current["stage"]
                current["progress_pct"] = int(_as_number(payload.get("progress_pct")) or 0)
                current["status"] = payload.get("status") or "RUNNING"
                current["detail"] = {
                    **dict(current.get("detail") or {}),
                    "bar_coverage": payload.get("bar_coverage"),
                    "replay_timeframe": payload.get("replay_timeframe"),
                }
            else:
                current["status"] = "COMPLETED"
                current["stage"] = "COMPLETE"
                current["progress_pct"] = 100
                current["completed_at"] = occurred_at
                current["candidate_id"] = payload.get("candidate_id") or current.get("candidate_id")
                current["title"] = current["candidate_id"] or current["title"]
                current["metrics"] = _research_metrics(payload)
                current["series"] = _research_series(payload)
                current["detail"] = {
                    **dict(current.get("detail") or {}),
                    "engineering_gate": payload.get("engineering_gate"),
                    "evidence_role": payload.get("evidence_role"),
                    "bar_coverage": payload.get("bar_coverage"),
                }
            events.append({
                "event_id": str(row.get("event_id") or row.get("event_key") or ""),
                "at": occurred_at,
                "system": "VELUM",
                "run_id": run_id,
                "event_type": event_type,
                "stage": payload.get("phase") or current.get("stage"),
                "status": payload.get("status") or current.get("status"),
                "progress_pct": payload.get("progress_pct"),
                "title": payload.get("candidate_id") or current.get("title"),
                "detail": payload.get("message") or payload.get("reason"),
            })
        if str(strategy_version_id or "").startswith("CRYPTO-XSECT-PAPER-"):
            key = str(row.get("run_id") or strategy_version_id)
            paper_events.setdefault(key, []).append(row)

    known_velum_ids = {run["run_id"] for run in runs if run["system"] == "VELUM"}
    runs.extend(
        run for run_id, run in velum_event_runs.items()
        if run_id not in known_velum_ids
    )

    for run_id, rows in paper_events.items():
        latest = rows[-1]
        latest_at = latest.get("occurred_at")
        series_points: list[dict[str, Any]] = []
        fills = 0
        for event in rows:
            payload = dict(event.get("payload") or {})
            event_type = str(event.get("event_type") or "")
            if event_type == "position_metrics":
                value = _as_number(payload.get("current_return_pct"))
                if value is not None:
                    series_points.append({
                        "at": _serialize_research(event.get("occurred_at")),
                        "value": round(value * 100.0, 6),
                    })
            if event_type == "broker_fill":
                fills += 1
        age = (
            (now - latest_at).total_seconds()
            if isinstance(latest_at, datetime)
            else 10_000
        )
        runs.append({
            "run_id": run_id,
            "system": "RHEN",
            "kind": "PAPER_TEST",
            "title": str(latest.get("strategy_version_id") or "Crypto paper strategy"),
            "status": "RUNNING" if 0 <= age <= 180 else "IDLE",
            "stage": "PAPER_FORWARD",
            "progress_pct": 50 if 0 <= age <= 180 else 100,
            "strategy_version_id": latest.get("strategy_version_id"),
            "started_at": rows[0].get("occurred_at"),
            "completed_at": None,
            "updated_at": latest_at,
            "metrics": {"fills": float(fills), "events": float(len(rows))},
            "series": ([{
                "key": "paper_return",
                "label": "Open-position return",
                "unit": "%",
                "points": series_points[-240:],
            }] if series_points else []),
            "detail": {"paper_only": True, "live_authority": False},
        })
        for event in rows[-80:]:
            payload = dict(event.get("payload") or {})
            events.append({
                "event_id": str(event.get("event_id") or event.get("event_key") or ""),
                "at": event.get("occurred_at"),
                "system": "RHEN",
                "run_id": run_id,
                "event_type": event.get("event_type"),
                "stage": "PAPER_FORWARD",
                "status": "OBSERVED",
                "title": str(event.get("strategy_version_id") or "Paper strategy"),
                "detail": payload.get("cycle_outcome") or payload.get("reason"),
            })

    for row in nostra_calibrations:
        metrics = dict(row.get("metrics") or {})
        metrics.setdefault("sample_count", row.get("sample_count"))
        runs.append({
            "run_id": str(row.get("calibration_id") or ""),
            "system": "NOSTRA",
            "kind": "CALIBRATION",
            "title": str(row.get("model_version") or "NOSTRA calibration"),
            "status": "COMPLETED",
            "stage": "CALIBRATION",
            "progress_pct": 100,
            "methodology_version": row.get("methodology_version"),
            "started_at": row.get("range_start"),
            "completed_at": row.get("created_at"),
            "updated_at": row.get("created_at"),
            "metrics": _research_metrics(metrics),
            "series": [],
            "detail": {
                "range_start": row.get("range_start"),
                "range_end": row.get("range_end"),
            },
        })

    for row in nostra_forecasts:
        prediction = dict(row.get("prediction") or {})
        scoring = dict(row.get("scoring") or {})
        runs.append({
            "run_id": str(row.get("forecast_id") or ""),
            "system": "NOSTRA",
            "kind": "FORECAST",
            "title": str(row.get("subject") or row.get("forecast_key") or "NOSTRA forecast"),
            "status": "SCORED" if row.get("observed_at") else "OPEN",
            "stage": "OUTCOME_SCORED" if row.get("observed_at") else "FORECAST_OPEN",
            "progress_pct": 100 if row.get("observed_at") else 50,
            "methodology_version": row.get("methodology_version"),
            "started_at": row.get("issued_at"),
            "completed_at": row.get("observed_at"),
            "updated_at": row.get("observed_at") or row.get("issued_at"),
            "metrics": _research_metrics(scoring or prediction),
            "series": [],
            "detail": {
                "model_version": row.get("model_version"),
                "horizon_start": row.get("horizon_start"),
                "horizon_end": row.get("horizon_end"),
                "prediction": prediction,
                "outcome": row.get("outcome"),
            },
        })


    for row in graen_runs:
        run_id = str(row.get("run_id") or "")
        summary = dict(row.get("result_summary") or {})
        problem = problems_by_id.get(str(row.get("problem_id") or ""), {})
        events.append({
            "event_id": "graen-run:" + run_id,
            "at": row.get("completed_at") or row.get("started_at") or row.get("created_at"),
            "system": "GRAEN",
            "run_id": run_id,
            "event_type": "graen_run",
            "stage": problem.get("research_stage") or summary.get("state") or summary.get("status"),
            "status": row.get("status"),
            "progress_pct": next(
                (
                    run.get("progress_pct")
                    for run in runs
                    if run.get("system") == "GRAEN" and run.get("run_id") == run_id
                ),
                None,
            ),
            "title": problem.get("title") or summary.get("research_batch_id") or "GRAEN research run",
            "detail": summary.get("decision") or summary.get("next_action") or summary.get("error"),
        })
    for artifact in graen_artifacts:
        run_id = str(artifact.get("run_id") or "")
        if not run_id:
            continue
        events.append({
            "event_id": "graen-artifact:" + str(artifact.get("artifact_id") or ""),
            "at": artifact.get("created_at"),
            "system": "GRAEN",
            "run_id": run_id,
            "event_type": "artifact_persisted",
            "stage": artifact.get("artifact_type"),
            "status": "PERSISTED",
            "title": artifact.get("artifact_type") or "GRAEN artifact",
            "detail": artifact.get("methodology_version"),
        })
    for replay in velum_replays:
        replay_id = str(replay.get("replay_id") or "")
        events.append({
            "event_id": "velum-replay:" + replay_id,
            "at": replay.get("completed_at") or replay.get("started_at"),
            "system": "VELUM",
            "run_id": replay_id,
            "event_type": "velum_replay",
            "stage": replay.get("result_type") or "REPLAY",
            "status": replay.get("status"),
            "progress_pct": (
                100
                if str(replay.get("status") or "").upper()
                in {"SUCCEEDED", "COMPLETED", "FAILED"}
                else 55
            ),
            "title": str(replay.get("asset_class") or "research").upper() + " replay",
            "detail": replay.get("strategy_version_id") or replay.get("methodology_version"),
        })
    for row in nostra_calibrations:
        run_id = str(row.get("calibration_id") or "")
        events.append({
            "event_id": "nostra-calibration:" + run_id,
            "at": row.get("created_at"),
            "system": "NOSTRA",
            "run_id": run_id,
            "event_type": "calibration_completed",
            "stage": "CALIBRATION",
            "status": "COMPLETED",
            "progress_pct": 100,
            "title": row.get("model_version") or "NOSTRA calibration",
            "detail": "sample_count=" + str(row.get("sample_count") or 0),
        })
    for row in nostra_forecasts:
        run_id = str(row.get("forecast_id") or "")
        events.append({
            "event_id": "nostra-forecast:" + run_id,
            "at": row.get("observed_at") or row.get("issued_at"),
            "system": "NOSTRA",
            "run_id": run_id,
            "event_type": "forecast_scored" if row.get("observed_at") else "forecast_issued",
            "stage": "OUTCOME_SCORED" if row.get("observed_at") else "FORECAST_OPEN",
            "status": "SCORED" if row.get("observed_at") else "OPEN",
            "progress_pct": 100 if row.get("observed_at") else 50,
            "title": row.get("subject") or row.get("forecast_key") or "NOSTRA forecast",
            "detail": row.get("model_version"),
        })

    def sort_stamp(run: dict[str, Any]) -> float:
        value = run.get("updated_at") or run.get("started_at")
        if isinstance(value, datetime):
            return value.timestamp()
        parsed = _parse_stamp(value)
        return parsed.timestamp() if parsed else 0.0

    runs.sort(key=sort_stamp, reverse=True)
    events.sort(
        key=lambda row: (
            _parse_stamp(_serialize_research(row.get("at"))) or datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )

    def serialized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_serialize_research(row) for row in rows]

    return {
        "graen_problems": serialized(graen_problems),
        "graen_runs": serialized([
            {
                "run_id": row.get("run_id"),
                "problem_id": row.get("problem_id"),
                "status": row.get("status"),
                "methodology_version": row.get("methodology_version"),
                "result_state": dict(row.get("result_summary") or {}).get("state"),
                "error": dict(row.get("result_summary") or {}).get("error"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "created_at": row.get("created_at"),
            }
            for row in graen_runs
        ]),
        "velum_replays": serialized([
            {
                "replay_id": row.get("replay_id"),
                "asset_class": row.get("asset_class"),
                "methodology_version": row.get("methodology_version"),
                "strategy_version_id": row.get("strategy_version_id"),
                "range_start": row.get("range_start"),
                "range_end": row.get("range_end"),
                "status": row.get("status"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "result_type": row.get("result_type"),
            }
            for row in velum_replays
        ]),
        "graen_runtime": _serialize_research(graen_runtime),
        "observability": {
            "schema_version": "research_observability.v1",
            "updated_at": now.isoformat(),
            "poll_seconds": 3,
            "runs": serialized(runs[:80]),
            "events": serialized(events[:160]),
            "authority": {
                "read_only": True,
                "research_only": True,
                "live_trading_performance_mixed": False,
            },
        },
    }

def _btc_canary_activity(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Compact private BTC canary state for the fast Command observation poll."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select run_id, max(occurred_at) as latest_at
            from rhen.events
            where strategy_version_id = %s
              and run_id like 'BTC-CANARY-%%'
            group by run_id
            order by latest_at desc
            limit 1
            """,
            ("BTC-CANARY-001",),
        )
        run_row = cur.fetchone()
        if not run_row:
            return {
                "available": False,
                "strategy_version_id": "BTC-CANARY-001",
                "paper_only": True,
                "live_execution_authorized": False,
                "promotion_ready": False,
                "evidence_state": "NO_CANONICAL_RUN",
            }

        run_id = str(run_row[0])
        latest_at = run_row[1]

        def latest_payload(event_type: str) -> tuple[Any, dict[str, Any]] | None:
            cur.execute(
                """
                select occurred_at, payload
                from rhen.events
                where run_id = %s
                  and strategy_version_id = %s
                  and event_type = %s
                order by occurred_at desc, event_id desc
                limit 1
                """,
                (run_id, "BTC-CANARY-001", event_type),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row[0], dict(row[1] or {})

        decision = latest_payload("decision_cycle")
        position_metrics = latest_payload("position_metrics")
        account = latest_payload("account_snapshot")

        cur.execute(
            """
            select occurred_at, payload
            from rhen.events
            where run_id = %s
              and strategy_version_id = %s
              and event_type = 'broker_order'
              and lower(coalesce(payload->'order'->>'client_order_id', ''))
                  like '%%hardstop%%'
            order by occurred_at desc, event_id desc
            limit 1
            """,
            (run_id, "BTC-CANARY-001"),
        )
        protection_row = cur.fetchone()

        cur.execute(
            """
            select occurred_at, event_type, payload
            from rhen.events
            where run_id = %s
              and strategy_version_id = %s
              and event_type in ('decision_cycle', 'position_metrics')
            order by occurred_at desc, event_id desc
            limit 40
            """,
            (run_id, "BTC-CANARY-001"),
        )
        recent_evidence_rows = cur.fetchall()

        cur.execute(
            """
            select occurred_at, payload
            from rhen.events
            where run_id = %s
              and strategy_version_id = %s
              and event_type = 'broker_fill'
            order by occurred_at asc, event_id asc
            limit 5000
            """,
            (run_id, "BTC-CANARY-001"),
        )
        fill_rows = cur.fetchall()

        cur.execute(
            """
            select occurred_at, payload
            from rhen.events
            where run_id = %s
              and strategy_version_id = %s
              and event_type = 'position_metrics'
            order by occurred_at asc, event_id asc
            limit 5000
            """,
            (run_id, "BTC-CANARY-001"),
        )
        all_position_rows = cur.fetchall()

    decision_at, decision_payload = decision if decision else (None, {})
    comparison = (
        dict(decision_payload.get("comparison_context") or {})
        if isinstance(decision_payload, dict)
        else {}
    )
    execution_result = dict(comparison.get("execution_result") or {})

    candidate_features: dict[str, Any] = {}
    raw_candidates = decision_payload.get("candidates") if isinstance(decision_payload, dict) else None
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("symbol") or "").upper().replace("/", "").replace("-", "")
            if symbol != "BTCUSD":
                continue
            features = candidate.get("features")
            if isinstance(features, dict):
                candidate_features = dict(features)
            break

    position_at, position_payload = (
        position_metrics if position_metrics else (None, {})
    )
    account_at, account_payload = account if account else (None, {})

    def nonzero(value: Any) -> bool:
        if value in (None, ""):
            return False
        try:
            return Decimal(str(value)) != Decimal("0")
        except (InvalidOperation, TypeError, ValueError):
            return False

    positions = (
        list(account_payload.get("positions") or [])
        if isinstance(account_payload, dict)
        else []
    )
    position_open = any(
        isinstance(position, dict)
        and str(position.get("symbol") or "")
            .upper().replace("/", "").replace("-", "") == "BTCUSD"
        and nonzero(position.get("qty"))
        for position in positions
    )

    protection_at = None
    protection_status = None
    if protection_row:
        protection_at = protection_row[0]
        protection_payload = dict(protection_row[1] or {})
        protection_order = dict(protection_payload.get("order") or {})
        protection_status = protection_order.get("status")

    if position_open:
        evidence_state = "COLLECTING_OPEN_POSITION"
    elif execution_result.get("action") in {"exit", "closed", "flat"}:
        evidence_state = "EXIT_OBSERVED"
    elif decision:
        evidence_state = "OBSERVING"
    else:
        evidence_state = "AWAITING_DECISION_EVIDENCE"

    def stamp(value: Any) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else (
            str(value) if value not in (None, "") else None
        )

    def decimal_value(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    signal_close = decimal_value(candidate_features.get("signal_close"))
    sma = decimal_value(candidate_features.get("sma"))
    momentum_return = decimal_value(candidate_features.get("momentum_return"))

    recent_cycles: list[dict[str, Any]] = []
    return_history: list[dict[str, Any]] = []
    for occurred_at, event_type, payload_value in recent_evidence_rows:
        payload = dict(payload_value or {})
        if event_type == "decision_cycle" and len(recent_cycles) < 10:
            context = dict(payload.get("comparison_context") or {})
            result = dict(context.get("execution_result") or {})
            recent_cycles.append({
                "at": stamp(occurred_at),
                "action": result.get("action"),
                "reason": result.get("reason") or payload.get("cycle_outcome"),
            })
        elif event_type == "position_metrics" and len(return_history) < 24:
            value = payload.get("current_return_pct")
            if decimal_value(value) is not None:
                return_history.append({
                    "at": stamp(occurred_at),
                    "return_pct": str(value),
                })

    recent_cycles.reverse()
    return_history.reverse()

    fill_events = [
        {
            "occurred_at": stamp(occurred_at),
            "payload": dict(payload or {}),
        }
        for occurred_at, payload in fill_rows
    ]
    all_returns = [
        decimal_value(dict(payload or {}).get("current_return_pct"))
        for _occurred_at, payload in all_position_rows
    ]
    all_returns = [value for value in all_returns if value is not None]
    current_return = decimal_value(position_payload.get("current_return_pct"))
    aggressive_70 = _btc_aggressive_70_projection(
        fill_events=fill_events,
        position_open=position_open,
        current_return=current_return,
        max_favorable_return=max(all_returns) if all_returns else None,
        max_adverse_return=min(all_returns) if all_returns else None,
    )

    return {
        "available": True,
        "run_id": run_id,
        "strategy_version_id": "BTC-CANARY-001",
        "paper_only": True,
        "live_execution_authorized": False,
        "promotion_ready": False,
        "research_status": "NOT_PROMOTED",
        "evidence_state": evidence_state,
        "observed_at": stamp(latest_at),
        "decision_at": stamp(decision_at),
        "action": execution_result.get("action"),
        "reason": execution_result.get("reason")
            or decision_payload.get("cycle_outcome"),
        "bar_interval": decision_payload.get("bar_interval"),
        "strategy_family": decision_payload.get("strategy_family"),
        "model_version": decision_payload.get("model_version"),
        "position_open": position_open,
        "position_observed_at": stamp(position_at),
        "entry_price": position_payload.get("entry_price"),
        "current_price": position_payload.get("current_price"),
        "current_return_pct": position_payload.get("current_return_pct"),
        "risk_stop_pct": position_payload.get("risk_stop_pct"),
        "account_observed_at": stamp(account_at),
        "protection_status": protection_status,
        "protection_observed_at": stamp(protection_at),
        "signal": {
            "bar_at": candidate_features.get("signal_bar_at"),
            "close": candidate_features.get("signal_close"),
            "momentum_return": candidate_features.get("momentum_return"),
            "momentum_positive": (
                momentum_return > Decimal("0")
                if momentum_return is not None
                else None
            ),
            "momentum_lookback_bars": candidate_features.get("momentum_lookback_bars"),
            "sma": candidate_features.get("sma"),
            "above_sma": (
                signal_close > sma
                if signal_close is not None and sma is not None
                else None
            ),
            "sma_window_bars": candidate_features.get("sma_window_bars"),
            "desired_long": candidate_features.get("desired_long"),
            "completed_bar_count": candidate_features.get("completed_bar_count"),
        },
        "recent_cycles": recent_cycles,
        "return_history": return_history,
        "aggressive_70": aggressive_70,
    }


def read_command_snapshot(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        return {
            "control": iren_read(conn),
            "work": snapshot(conn),
            "research": _research_activity(conn),
            "btc_canary": _btc_canary_activity(conn),
        }


def enqueue_command(
    database_url: str,
    *,
    command: str,
    requested_by: str,
) -> dict[str, Any]:
    text = str(command or "").strip()[:4000]
    if not text:
        raise ValueError("command_required")
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        return command_create(
            conn,
            {
                "command_text": text,
                "source": "command",
                "requested_by": str(requested_by or "")[:160],
                "context": {"surface": "ANEVUM Command"},
            },
        )


def project_command(
    snapshot_value: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    control = (
        dict(snapshot_value.get("control"))
        if isinstance(snapshot_value.get("control"), dict)
        else {}
    )
    work = (
        dict(snapshot_value.get("work"))
        if isinstance(snapshot_value.get("work"), dict)
        else {}
    )
    research = (
        dict(snapshot_value.get("research"))
        if isinstance(snapshot_value.get("research"), dict)
        else {}
    )
    btc_canary = (
        dict(snapshot_value.get("btc_canary"))
        if isinstance(snapshot_value.get("btc_canary"), dict)
        else {}
    )
    raw_state = (
        dict(control.get("state"))
        if isinstance(control.get("state"), dict)
        else {}
    )
    state = deepcopy(raw_state)
    stale = not _fresh(state.get("observed_at"), now=current)
    topology = (
        state.get("topology")
        if isinstance(state.get("topology"), dict)
        else None
    )

    if stale:
        state["state"] = "STALE"
        if topology:
            services = topology.get("services")
            if isinstance(services, list):
                for item in services:
                    if isinstance(item, dict) and item.get("independent_runtime"):
                        item.update({
                            "status": "STALE",
                            "readiness": False,
                            "liveness": None,
                        })
            dependencies = topology.get("dependencies")
            if isinstance(dependencies, dict):
                for dependency in dependencies.values():
                    if isinstance(dependency, dict):
                        dependency["status"] = "STALE"

    incident_map = (
        state.get("incidents")
        if isinstance(state.get("incidents"), dict)
        else {}
    )
    incidents = []
    for key, value in incident_map.items():
        if not isinstance(value, dict) or value.get("status") != "OPEN":
            continue
        incidents.append({
            "key": key,
            "severity": value.get("severity"),
            "reason": value.get("reason"),
            "opened_at": value.get("opened_at"),
        })

    objectives = _rows(work.get("objectives"))
    jobs = _rows(work.get("jobs"))
    job_events = _rows(work.get("job_events"))
    commands = _rows(work.get("commands"))
    current_state = str(state.get("state") or "UNKNOWN")

    from app.iren.work import status_summary
    from app.iren.codex_handoff import active_handoffs, mode
    summary = status_summary(work, state)
    handoffs = active_handoffs(work)

    baseline = state.get("configuration_baseline")
    baseline = baseline if isinstance(baseline, dict) else {}

    operator = _operator_projection(
        state=state,
        control=control,
        work={"objectives": objectives, "jobs": jobs},
        stale=stale,
        incidents=incidents,
        current_state=current_state,
    )

    return {
        "schema_version": "iren_command.v2",
        "work_schema_version": "iren_work.v1",
        "revision": control.get("revision"),
        "observed_at": state.get("observed_at"),
        "stale": stale,
        "state": current_state,
        "topology": topology,
        "incidents": incidents,
        "scheduler": state.get("scheduler"),
        "research": research,
        "btc_canary": btc_canary,
        "action_required": stale or current_state != "HEALTHY" or bool(incidents),
        "configuration_identity": baseline.get("fingerprint"),
        "operator": operator,
        "work": {
            **_summary({"objectives": objectives, "jobs": jobs}),
            "next_action": summary.get("next_action"),
            "execution_mode": (
                mode(summary.get("next_action"))
                if summary.get("next_action")
                else "idle"
            ),
            "active_handoff_count": len(handoffs),
            "handoffs": [r.get("result") | {"handoff_id": r.get("job_id"), "objective_key": r.get("objective_key")} for r in jobs if r.get("job_type") == "CODEX_HANDOFF" and isinstance(r.get("result"), dict)],
            "objectives": objectives,
            "jobs": jobs,
            "job_events": job_events,
            "commands": commands,
        },
    }
