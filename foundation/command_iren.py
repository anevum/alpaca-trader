from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from foundation.iren_gateway import iren_read
from foundation.iren_work_gateway import command_create, snapshot


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

def _research_activity(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Private research trace plus autonomy/productivity state for Command."""
    from app.research_agent.autonomy import charter_snapshot
    from app.research_agent.hypothesis_graph import build_hypothesis_graph

    with conn.cursor() as cur:
        cur.execute(
            """
            select
                problem_id,problem_key,title,statement,domain,status,priority,
                metadata,created_at,updated_at,started_at,completed_at
            from graen.problems
            order by
              case status when 'RUNNING' then 0 when 'QUEUED' then 1
                          when 'WAITING' then 2 when 'BLOCKED' then 3 else 4 end,
              updated_at desc
            limit 200
            """
        )
        problem_columns = [column.name for column in cur.description]
        raw_problems = [
            dict(zip(problem_columns, row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select
                run_id,problem_id,status,methodology_version,input_snapshot,
                result_summary,model_usage,started_at,completed_at,created_at
            from graen.runs
            order by started_at desc
            limit 200
            """
        )
        run_columns = [column.name for column in cur.description]
        raw_runs = [
            dict(zip(run_columns, row))
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            select status,started_at,completed_at
            from velum.replays
            order by coalesce(completed_at,started_at) desc nulls last
            limit 80
            """
        )
        velum_replays = _rows([
            dict(zip([column.name for column in cur.description], row))
            for row in cur.fetchall()
        ])

        cur.execute(
            """
            select singleton,worker_id,runtime_version,source_commit,deployment_id,
                   heartbeat_at,last_claim_at,last_completion_at,active_problem_id,
                   queue_depth,last_error,updated_at
            from graen.runtime_state
            where singleton
            """
        )
        runtime_row = cur.fetchone()
        runtime_state = (
            dict(zip([column.name for column in cur.description], runtime_row))
            if runtime_row else None
        )

        cur.execute(
            """
            select occurred_at,event_type,correlation_id,payload
            from rhen.events
            where event_type in (
                'graen_candidate_shadow_activated',
                'graen_candidate_shadow_queued',
                'graen_candidate_shadow_event',
                'graen_candidate_shadow_terminal_checkpoint',
                'graen_paper_candidate_activated',
                'graen_paper_entry_intent',
                'graen_paper_entry_rejected_market_quality',
                'graen_paper_exit_intent',
                'graen_paper_round_trip_closed',
                'graen_paper_terminal_checkpoint'
            )
            order by occurred_at desc,event_id desc
            limit 240
            """
        )
        evidence_rows = [
            {
                "occurred_at": row[0],
                "event_type": str(row[1] or ""),
                "correlation_id": str(row[2] or ""),
                "payload": dict(row[3] or {}),
            }
            for row in cur.fetchall()
        ]

    graph = build_hypothesis_graph({
        "problems": raw_problems,
        "runs": raw_runs,
        "artifacts": [],
    })

    def stamp(value: Any) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else (
            str(value) if value not in (None, "") else None
        )

    graen_problems: list[dict[str, Any]] = []
    for row in raw_problems[:80]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        promotion = (
            metadata.get("code_promotion")
            if isinstance(metadata.get("code_promotion"), dict)
            else {}
        )
        requirement = (
            promotion.get("engineering_requirement")
            if isinstance(promotion.get("engineering_requirement"), dict)
            else None
        )
        graen_problems.append({
            "problem_id": str(row.get("problem_id") or ""),
            "title": row.get("title"),
            "status": row.get("status"),
            "research_stage": metadata.get("research_stage"),
            "candidate_id": metadata.get("candidate_id") or metadata.get("hypothesis_id"),
            "hypothesis": metadata.get("hypothesis"),
            "family": metadata.get("family") or metadata.get("candidate_family"),
            "mechanism": metadata.get("mechanism"),
            "campaign_id": metadata.get("campaign_id"),
            "engineering_requirement_id": (
                requirement.get("requirement_id") if requirement else None
            ),
            "updated_at": stamp(row.get("updated_at")),
            "started_at": stamp(row.get("started_at")),
            "completed_at": stamp(row.get("completed_at")),
        })

    graen_runs: list[dict[str, Any]] = []
    for row in raw_runs[:120]:
        summary = (
            row.get("result_summary")
            if isinstance(row.get("result_summary"), dict)
            else {}
        )
        graen_runs.append({
            "run_id": str(row.get("run_id") or ""),
            "problem_id": str(row.get("problem_id") or ""),
            "status": row.get("status"),
            "methodology_version": row.get("methodology_version"),
            "result_state": summary.get("state") or summary.get("status"),
            "decision": summary.get("decision"),
            "next_action": summary.get("next_action"),
            "candidate_id": summary.get("candidate_id"),
            "error": summary.get("error"),
            "started_at": stamp(row.get("started_at")),
            "completed_at": stamp(row.get("completed_at")),
            "created_at": stamp(row.get("created_at")),
        })

    engineering_requirements = [
        {
            "hypothesis_id": node.get("hypothesis_id"),
            "problem_id": node.get("problem_id"),
            **dict(node["engineering_requirement"]),
        }
        for node in graph.get("nodes") or []
        if isinstance(node, dict)
        and isinstance(node.get("engineering_requirement"), dict)
    ]

    active_states = {"ACTIVE", "VALIDATING", "HOLDOUT"}
    active_nodes = [
        node for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("state") in active_states
    ]
    running_runs = [
        row for row in raw_runs
        if str(row.get("status") or "").upper() == "RUNNING"
    ]
    blocked_nodes = [
        node for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("state") == "BLOCKED"
    ]

    def evidence_activation_id(row: dict[str, Any]) -> str:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        activation = (
            payload.get("activation")
            if isinstance(payload.get("activation"), dict)
            else {}
        )
        return str(
            payload.get("activation_id")
            or activation.get("activation_id")
            or row.get("correlation_id")
            or ""
        )

    shadow_latest: dict[str, dict[str, Any]] = {}
    paper_latest: dict[str, dict[str, Any]] = {}
    recent_evidence: list[dict[str, Any]] = []
    for row in evidence_rows:
        event_type = str(row.get("event_type") or "")
        activation_id = evidence_activation_id(row)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        checkpoint = (
            payload.get("checkpoint")
            if isinstance(payload.get("checkpoint"), dict)
            else {}
        )
        occurred = row.get("occurred_at")
        projection = {
            "activation_id": activation_id or None,
            "event_type": event_type,
            "occurred_at": stamp(occurred),
            "candidate_id": (
                checkpoint.get("candidate_id")
                or payload.get("candidate_id")
                or (
                    (payload.get("activation") or {}).get("candidate_id")
                    if isinstance(payload.get("activation"), dict)
                    else None
                )
            ),
            "status": None,
        }
        if event_type.startswith("graen_candidate_shadow_"):
            if "terminal_checkpoint" in event_type:
                projection["status"] = checkpoint.get("status") or "TERMINAL"
            elif event_type.endswith("_queued"):
                projection["status"] = "QUEUED"
            else:
                projection["status"] = "ACTIVE"
            if activation_id and activation_id not in shadow_latest:
                shadow_latest[activation_id] = projection
        elif event_type.startswith("graen_paper_"):
            if "terminal_checkpoint" in event_type:
                projection["status"] = checkpoint.get("status") or "TERMINAL"
            else:
                projection["status"] = "ACTIVE"
            if activation_id and activation_id not in paper_latest:
                paper_latest[activation_id] = projection
        if len(recent_evidence) < 40:
            recent_evidence.append(projection)

    shadow_active = [
        row for row in shadow_latest.values()
        if row.get("status") == "ACTIVE"
    ]
    shadow_queued = [
        row for row in shadow_latest.values()
        if row.get("status") == "QUEUED"
    ]
    paper_active = [
        row for row in paper_latest.values()
        if row.get("status") == "ACTIVE"
    ]
    paper_passed = [
        row for row in paper_latest.values()
        if row.get("status") == "PAPER_PASSED"
    ]

    progress_stamps: list[datetime] = []
    for row in raw_runs:
        for key in ("completed_at", "started_at", "created_at"):
            value = row.get(key)
            if isinstance(value, datetime):
                progress_stamps.append(
                    value if value.tzinfo else value.replace(tzinfo=UTC)
                )
                break
    for row in velum_replays:
        for key in ("completed_at", "started_at"):
            value = row.get(key)
            if isinstance(value, datetime):
                progress_stamps.append(
                    value if value.tzinfo else value.replace(tzinfo=UTC)
                )
                break
    for row in evidence_rows:
        value = row.get("occurred_at")
        if isinstance(value, datetime):
            progress_stamps.append(
                value if value.tzinfo else value.replace(tzinfo=UTC)
            )

    latest_progress = max(progress_stamps).astimezone(UTC) if progress_stamps else None
    age_seconds = (
        max(0.0, (datetime.now(UTC) - latest_progress).total_seconds())
        if latest_progress else None
    )
    active_forward_evidence = bool(shadow_active or paper_active)
    if engineering_requirements:
        condition = "ENGINEERING_REQUIRED"
        productivity = (
            "PRODUCTIVE_WITH_MANUAL_SOFTWARE"
            if active_nodes or running_runs or active_forward_evidence
            else "WAITING_FOR_MANUAL_SOFTWARE"
        )
    elif active_nodes or running_runs or active_forward_evidence:
        condition = "RESEARCHING"
        productivity = (
            "PRODUCTIVE"
            if age_seconds is not None and age_seconds <= 1800
            else "STALLED"
        )
    elif blocked_nodes:
        condition = "BLOCKED"
        productivity = "BLOCKED"
    else:
        condition = "IDLE"
        productivity = "IDLE"

    runtime_projection = None
    if runtime_state:
        runtime_projection = {
            key: stamp(value) if key.endswith("_at") else (
                str(value) if key == "active_problem_id" and value is not None else value
            )
            for key, value in runtime_state.items()
        }

    return {
        "operating_summary": {
            "objective": "Discover a reproducible, cost-aware crypto trading edge.",
            "condition": condition,
            "productivity": productivity,
            "productivity_is_health": True,
            "active_hypotheses": len(active_nodes),
            "experiments_running": len(running_runs),
            "hypotheses_falsified": int((graph.get("state_counts") or {}).get("FALSIFIED", 0)),
            "validation_candidates": int((graph.get("state_counts") or {}).get("VALIDATING", 0)),
            "holdout_candidates": int((graph.get("state_counts") or {}).get("HOLDOUT", 0)),
            "shadow_candidates_active": len(shadow_active),
            "shadow_candidates_queued": len(shadow_queued),
            "paper_candidates_active": len(paper_active),
            "paper_candidates_passed": len(paper_passed),
            "engineering_required": len(engineering_requirements),
            "blocked_hypotheses": len(blocked_nodes),
            "latest_progress_at": latest_progress.isoformat() if latest_progress else None,
            "latest_progress_age_seconds": age_seconds,
            "next_autonomous_action": (
                "Continue independent research and wait for manual software implementation."
                if engineering_requirements
                else "Continue highest-information research experiment."
                if condition == "RESEARCHING"
                else "IREN must derive and queue the next research objective."
                if condition == "IDLE"
                else "Resolve research evidence blocker."
            ),
            "service_health_alone_is_insufficient": True,
        },
        "autonomy_charter": charter_snapshot(),
        "engineering_requirements": engineering_requirements,
        "hypothesis_graph": {
            "schema_version": graph.get("schema_version"),
            "node_count": graph.get("node_count"),
            "state_counts": graph.get("state_counts"),
            "family_counts": graph.get("family_counts"),
            "failure_reason_counts": graph.get("failure_reason_counts"),
            "graph_hash": graph.get("graph_hash"),
            "recent_nodes": (graph.get("nodes") or [])[:30],
        },
        "graen_problems": graen_problems,
        "graen_runs": graen_runs,
        "velum_replays": [
            {
                key: stamp(value) if key in {"started_at", "completed_at"} else value
                for key, value in row.items()
            }
            for row in velum_replays
        ],
        "forward_evidence": {
            "shadow": {
                "active": list(shadow_active)[:12],
                "queued": list(shadow_queued)[:12],
                "recent": list(shadow_latest.values())[:30],
            },
            "paper": {
                "active": list(paper_active)[:4],
                "passed": list(paper_passed)[:8],
                "recent": list(paper_latest.values())[:20],
            },
            "recent_events": recent_evidence,
        },
        "graen_runtime": runtime_projection,
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

    operating_summary = (
        research.get("operating_summary")
        if isinstance(research.get("operating_summary"), dict)
        else {}
    )
    operating_condition = str(operating_summary.get("condition") or "UNKNOWN")
    productivity = str(operating_summary.get("productivity") or "UNKNOWN")
    engineering_required = int(operating_summary.get("engineering_required") or 0)
    human_decision_objectives = [
        row
        for row in objectives
        if str(row.get("status") or "").upper()
        not in {"COMPLETE", "CANCELLED", "CANCELED"}
        and isinstance(row.get("metadata"), dict)
        and (
            str((row.get("metadata") or {}).get("classification") or "").upper()
            == "HUMAN_DECISION_REQUIRED"
            or str((row.get("metadata") or {}).get("job_type") or "").upper()
            == "HUMAN_DECISION"
        )
    ]
    human_decision_required = len(human_decision_objectives)
    if human_decision_required:
        operating_condition = "HUMAN_DECISION_REQUIRED"
        if isinstance(operating_summary, dict):
            operating_summary["condition"] = operating_condition
            operating_summary["human_decision_required"] = human_decision_required
            operating_summary["next_autonomous_action"] = (
                "Continue independent safe work while the protected human decision waits."
            )
    research_attention = (
        operating_condition
        in {"ENGINEERING_REQUIRED", "HUMAN_DECISION_REQUIRED", "BLOCKED"}
        or productivity == "STALLED"
    )
    if human_decision_required:
        operator["state"] = "HUMAN_DECISION_REQUIRED"
        operator["message"] = (
            f"{human_decision_required} protected decision"
            + ("" if human_decision_required == 1 else "s")
            + " require explicit human authority. Independent safe work continues."
        )
    elif engineering_required:
        operator["state"] = "ENGINEERING_REQUIRED"
        operator["message"] = (
            f"{engineering_required} software requirement"
            + ("" if engineering_required == 1 else "s")
            + " require manual ChatGPT/Codex work. Independent research may continue."
        )
    elif productivity == "STALLED":
        operator["state"] = "DEGRADED_PRODUCTIVITY"
        operator["message"] = (
            "Services are responsive but research has stopped making measurable progress. "
            "IREN must derive or repair the next autonomous action."
        )
    elif operating_condition == "RESEARCHING" and current_state == "HEALTHY":
        operator["state"] = "RESEARCHING"
        operator["message"] = (
            "ANEVUM infrastructure is healthy and GRAEN is actively progressing research."
        )

    return {
        "schema_version": "iren_command.v2",
        "work_schema_version": "iren_work.v1",
        "revision": control.get("revision"),
        "observed_at": state.get("observed_at"),
        "stale": stale,
        "state": current_state,
        "operating_state": operating_condition,
        "productivity_state": productivity,
        "topology": topology,
        "incidents": incidents,
        "scheduler": state.get("scheduler"),
        "research": research,
        "btc_canary": btc_canary,
        "action_required": (
            stale
            or current_state != "HEALTHY"
            or bool(incidents)
            or research_attention
        ),
        "configuration_identity": baseline.get("fingerprint"),
        "operator": operator,
        "work": {
            **_summary({"objectives": objectives, "jobs": jobs}),
            "next_action": summary.get("next_action"),
            "execution_mode": "codex/manual software" if handoffs else mode(summary.get("next_action")),
            "handoffs": [r.get("result") | {"handoff_id": r.get("job_id"), "objective_key": r.get("objective_key")} for r in jobs if r.get("job_type") == "CODEX_HANDOFF" and isinstance(r.get("result"), dict)],
            "objectives": objectives,
            "jobs": jobs,
            "job_events": job_events,
            "commands": commands,
        },
    }
